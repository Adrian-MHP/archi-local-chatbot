from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import time
from typing import Any, Dict, List, Tuple

from openai import APIStatusError, BadRequestError, OpenAI, RateLimitError

from .config import Settings
from .mcp_client import McpClient, McpTool


class ChatService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.mcp = McpClient(
            server_url=settings.mcp_server_url,
            bearer_token=settings.mcp_bearer_token,
            timeout_seconds=settings.request_timeout_seconds,
        )
        self.openai = OpenAI(
            api_key=settings.azure_openai_api_key,
            base_url=settings.azure_openai_base_url,
        )

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _preview_json(data: Any, limit: int = 700) -> str:
        try:
            text = json.dumps(data, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            text = str(data)
        if len(text) <= limit:
            return text
        return text[:limit] + "...(truncated)"

    @staticmethod
    def _tool_to_openai_format(tool: McpTool) -> Dict[str, Any]:
        schema = tool.input_schema if isinstance(tool.input_schema, dict) else {"type": "object", "properties": {}}
        if "type" not in schema:
            schema = {"type": "object", **schema}

        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or f"MCP tool: {tool.name}",
                "parameters": schema,
            },
        }

    def _build_messages(self, history: List[Dict[str, str]], message: str, system_prompt: str | None) -> List[Dict[str, Any]]:
        ui_formatting_rules = (
            "Output format for end users: plain text only. "
            "Do not use markdown headings, bullet lists, or numbered lists. "
            "Use short paragraphs separated by blank lines. "
            "If you need structure, use simple labels like 'Model summary:' in plain text."
        )
        execution_policy = (
            "When the user requests model changes, prefer one consolidated execution plan: "
            "batch create/update relationships and view placement together, avoid asking follow-up questions unless critical data is missing, "
            "and use bulk-mutate where possible to minimize fragmented mutation proposals."
        )
        messages: List[Dict[str, Any]] = [
            {
                "role": "system",
                "content": (system_prompt or self.settings.default_system_prompt)
                + " "
                + ui_formatting_rules
                + " "
                + execution_policy,
            }
        ]

        for item in history:
            role = item.get("role")
            content = item.get("content", "")
            if role in {"user", "assistant"}:
                messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": message})
        return messages

    @staticmethod
    def _extract_retry_after_seconds(exc: Exception) -> float | None:
        response = getattr(exc, "response", None)
        if response is None:
            return None
        headers = getattr(response, "headers", None) or {}

        ms_keys = ("retry-after-ms", "x-ms-retry-after-ms")
        for k in ms_keys:
            v = headers.get(k)
            if v:
                try:
                    return max(0.0, float(v) / 1000.0)
                except ValueError:
                    pass

        sec_keys = ("retry-after", "x-ms-retry-after")
        for k in sec_keys:
            v = headers.get(k)
            if v:
                try:
                    return max(0.0, float(v))
                except ValueError:
                    pass
        return None

    def _call_model_once(self, create_kwargs: Dict[str, Any]):
        try:
            return self.openai.chat.completions.create(**create_kwargs)
        except BadRequestError as exc:
            msg = str(exc)
            if "temperature" in msg and "Unsupported value" in msg:
                create_kwargs = dict(create_kwargs)
                create_kwargs.pop("temperature", None)
                return self.openai.chat.completions.create(**create_kwargs)
            raise

    def _call_model_with_retries(self, create_kwargs: Dict[str, Any]) -> Tuple[Any, str, int]:
        models = [self.settings.azure_openai_model]
        fallback = self.settings.azure_openai_fallback_model
        if fallback and fallback != self.settings.azure_openai_model:
            models.append(fallback)

        last_rate_limit_error: Exception | None = None

        for model in models:
            kwargs_for_model = dict(create_kwargs)
            kwargs_for_model["model"] = model

            for attempt in range(self.settings.max_model_retries + 1):
                try:
                    return self._call_model_once(kwargs_for_model), model, attempt
                except RateLimitError as exc:
                    last_rate_limit_error = exc
                    if attempt >= self.settings.max_model_retries:
                        break

                    retry_after = self._extract_retry_after_seconds(exc)
                    if retry_after is None:
                        retry_after = self.settings.model_retry_backoff_seconds * (2 ** attempt)
                    # Prevent unbounded waits on bad headers.
                    retry_after = min(max(retry_after, 0.2), 30.0)
                    time.sleep(retry_after)
                except APIStatusError as exc:
                    status = getattr(exc, "status_code", None)
                    if status != 429:
                        raise
                    last_rate_limit_error = exc
                    if attempt >= self.settings.max_model_retries:
                        break

                    retry_after = self._extract_retry_after_seconds(exc)
                    if retry_after is None:
                        retry_after = self.settings.model_retry_backoff_seconds * (2 ** attempt)
                    retry_after = min(max(retry_after, 0.2), 30.0)
                    time.sleep(retry_after)

        if last_rate_limit_error is not None:
            if len(models) > 1:
                raise RuntimeError(
                    "Azure rate limit exceeded for both primary and fallback model deployments. "
                    "Please wait and retry, or reduce request concurrency."
                ) from last_rate_limit_error
            raise RuntimeError(
                "Azure rate limit exceeded for the configured model deployment. "
                "Please wait and retry, or configure AZURE_OPENAI_FALLBACK_MODEL."
            ) from last_rate_limit_error

        # Should not happen, but keep a clear failure mode.
        raise RuntimeError("Model request failed without a recoverable response.")

    @staticmethod
    def _normalize_name(raw: str, *, fallback: str, max_len: int = 120) -> str:
        value = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not value:
            value = fallback
        return value[:max_len]

    @staticmethod
    def _norm_key(raw: str) -> str:
        return re.sub(r"\s+", " ", str(raw or "")).strip().lower()

    @staticmethod
    def _as_float(raw: Any, default: float = 0.0) -> float:
        try:
            return float(raw)
        except Exception:
            return default

    @staticmethod
    def _tokens(raw: str) -> List[str]:
        return re.findall(r"[a-zA-Z0-9]{3,}", str(raw or "").lower())

    def _has_text_evidence(self, candidate: str, source_text: str) -> bool:
        candidate_norm = self._norm_key(candidate)
        source_norm = self._norm_key(source_text)
        if not candidate_norm or not source_norm:
            return False
        if candidate_norm in source_norm:
            return True

        tokens = [t for t in self._tokens(candidate_norm) if t not in {"and", "the", "for", "with", "from"}]
        if not tokens:
            return False
        required = 1 if len(tokens) <= 2 else 2
        hits = sum(1 for token in tokens if token in source_norm)
        return hits >= required

    @staticmethod
    def _extract_json_object(raw: str) -> Dict[str, Any]:
        text = (raw or "").strip()
        if not text:
            raise RuntimeError("Model returned an empty payload while JSON was expected.")
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < 0 or end <= start:
            raise RuntimeError("Could not parse JSON object from model output.")
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Could not decode JSON model output: {exc}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("Model output JSON was not an object.")
        return parsed

    @staticmethod
    def _try_parse_json_text(raw: str) -> Any:
        text = str(raw or "").strip()
        if not text:
            return None
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3:
                text = "\n".join(lines[1:-1]).strip()
        try:
            return json.loads(text)
        except Exception:
            return None

    def _unwrap_mcp_result(self, result: Any) -> Dict[str, Any]:
        if isinstance(result, dict):
            structured = result.get("structuredContent")
            if isinstance(structured, dict):
                return structured
            if isinstance(structured, list):
                return {"items": structured}

            nested = result.get("result")
            if isinstance(nested, dict):
                return nested
            if isinstance(nested, list):
                return {"items": nested}

            content = result.get("content")
            if isinstance(content, list):
                for block in reversed(content):
                    if not isinstance(block, dict):
                        continue
                    if isinstance(block.get("json"), dict):
                        return block["json"]
                    if isinstance(block.get("json"), list):
                        return {"items": block["json"]}
                    parsed = self._try_parse_json_text(block.get("text", ""))
                    if isinstance(parsed, dict):
                        return parsed
                    if isinstance(parsed, list):
                        return {"items": parsed}
            return result

        if isinstance(result, list):
            return {"items": result}
        return {"raw": result}

    def _result_list(self, result: Any, preferred_key: str) -> List[Dict[str, Any]]:
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        if not isinstance(result, dict):
            return []

        candidate = result.get(preferred_key)
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]

        for key in ("items", "data", "results", "views", "elements", "relationships"):
            value = result.get(key)
            if isinstance(value, list):
                dict_items = [item for item in value if isinstance(item, dict)]
                if dict_items:
                    return dict_items

        content = result.get("content")
        if isinstance(content, list):
            for block in reversed(content):
                if not isinstance(block, dict):
                    continue
                parsed = self._try_parse_json_text(block.get("text", ""))
                if parsed is not None:
                    nested = self._result_list(parsed, preferred_key)
                    if nested:
                        return nested
        return []

    @staticmethod
    def _max_automation_ops() -> int:
        return 150

    def _call_model_for_json(self, *, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        create_kwargs: Dict[str, Any] = dict(
            model=self.settings.azure_openai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )
        response, _model_used, _retries = self._call_model_with_retries(create_kwargs)
        content = response.choices[0].message.content or ""
        return self._extract_json_object(content)

    def _mcp_call(self, tool_name: str, args: Dict[str, Any], used_tools: List[str]) -> Dict[str, Any]:
        result = self.mcp.call_tool(tool_name, args)
        used_tools.append(tool_name)
        return self._unwrap_mcp_result(result)

    def _find_existing_view_id(
        self,
        *,
        view_name: str,
        used_tools: List[str],
    ) -> str | None:
        views_result = self._mcp_call("get-views", {"name": view_name, "limit": 200}, used_tools)
        views = self._result_list(views_result, "views")
        for view in views:
            existing_name = str(view.get("name", "")).strip()
            if existing_name.lower() == view_name.lower():
                view_id = str(view.get("id", view.get("viewId", ""))).strip()
                if view_id:
                    return view_id
        return None

    def _collect_existing_elements(self, used_tools: List[str]) -> Dict[tuple[str, str], str]:
        result = self._mcp_call("search-elements", {"query": "", "limit": 500}, used_tools)
        elements = self._result_list(result, "elements")
        out: Dict[tuple[str, str], str] = {}
        for item in elements:
            element_id = str(item.get("id", item.get("elementId", ""))).strip()
            element_type = str(item.get("type", item.get("conceptType", ""))).strip()
            name = str(item.get("name", "")).strip()
            if not element_id or not element_type or not name:
                continue
            out[(self._norm_key(element_type), self._norm_key(name))] = element_id
        return out

    def _collect_existing_relationships(self, used_tools: List[str]) -> Dict[tuple[str, str, str], str]:
        result = self._mcp_call("search-relationships", {"query": "", "limit": 500}, used_tools)
        relationships = self._result_list(result, "relationships")
        out: Dict[tuple[str, str, str], str] = {}
        for item in relationships:
            rel_id = str(item.get("id", item.get("relationshipId", ""))).strip()
            rel_type = str(item.get("type", "")).strip()
            source = item.get("source")
            target = item.get("target")
            source_id = str(
                item.get("sourceId", item.get("source_id", source.get("id", "") if isinstance(source, dict) else ""))
            ).strip()
            target_id = str(
                item.get("targetId", item.get("target_id", target.get("id", "") if isinstance(target, dict) else ""))
            ).strip()
            if not rel_id or not rel_type or not source_id or not target_id:
                continue
            out[(self._norm_key(rel_type), source_id, target_id)] = rel_id
        return out

    def _collect_view_state(
        self,
        *,
        view_id: str,
        used_tools: List[str],
    ) -> tuple[Dict[str, str], set[str]]:
        result = self._mcp_call("get-view-contents", {"viewId": view_id}, used_tools)
        element_to_view_obj: Dict[str, str] = {}
        visual_connections: set[str] = set()

        for element in self._result_list(result, "elements"):
            element_id = str(element.get("id", "")).strip()
            if not element_id:
                continue
            visual_meta = element.get("visualMetadata")
            if isinstance(visual_meta, dict):
                view_obj = str(visual_meta.get("viewObjectId", "")).strip()
                if view_obj:
                    element_to_view_obj[element_id] = view_obj
            elif isinstance(visual_meta, list):
                for meta_item in visual_meta:
                    if not isinstance(meta_item, dict):
                        continue
                    view_obj = str(meta_item.get("viewObjectId", "")).strip()
                    if view_obj:
                        element_to_view_obj[element_id] = view_obj
                        break

        visual_meta_items = result.get("visualMetadata")
        if isinstance(visual_meta_items, list):
            for meta_item in visual_meta_items:
                if not isinstance(meta_item, dict):
                    continue
                element_id = str(meta_item.get("elementId", meta_item.get("id", ""))).strip()
                view_obj = str(meta_item.get("viewObjectId", "")).strip()
                if element_id and view_obj:
                    element_to_view_obj.setdefault(element_id, view_obj)

        connections = result.get("connections")
        if isinstance(connections, list):
            for conn in connections:
                if not isinstance(conn, dict):
                    continue
                rel_id = str(conn.get("relationshipId", conn.get("relationship_id", ""))).strip()
                if rel_id:
                    visual_connections.add(rel_id)

        return element_to_view_obj, visual_connections

    def _execute_view_plan(
        self,
        *,
        action_label: str,
        source_name: str,
        view_name: str,
        elements_by_key: Dict[str, Dict[str, str]],
        relationships: List[Dict[str, str]],
        layout_positions: Dict[str, Dict[str, int]] | None = None,
    ) -> Dict[str, Any]:
        used_tools: List[str] = []
        existing_view_id = self._find_existing_view_id(view_name=view_name, used_tools=used_tools)
        existing_elements = self._collect_existing_elements(used_tools)
        existing_relationships = self._collect_existing_relationships(used_tools)
        if existing_view_id:
            view_objects, existing_visual_relationships = self._collect_view_state(view_id=existing_view_id, used_tools=used_tools)
        else:
            view_objects, existing_visual_relationships = {}, set()

        operations: List[Dict[str, Any]] = []
        view_ref = existing_view_id
        if not view_ref:
            operations.append({"tool": "create-view", "params": {"name": view_name}})
            view_ref = f"${len(operations) - 1}.id"

        element_id_ref: Dict[str, str] = {}
        view_obj_ref: Dict[str, str] = {}
        existing_element_id_for_key: Dict[str, str] = {}
        created_elements = 0
        created_relationships = 0
        added_to_view = 0
        added_connections = 0

        for key, item in elements_by_key.items():
            element_type = item["type"]
            element_name = item["name"]
            found_id = existing_elements.get((self._norm_key(element_type), self._norm_key(element_name)))
            if found_id:
                existing_element_id_for_key[key] = found_id
                continue
            operations.append(
                {
                    "tool": "create-element",
                    "params": {
                        "type": element_type,
                        "name": element_name,
                        "documentation": item.get("documentation", ""),
                    },
                }
            )
            element_id_ref[key] = f"${len(operations) - 1}.id"
            created_elements += 1

        for key in elements_by_key:
            existing_id = existing_element_id_for_key.get(key)
            if existing_id and existing_id in view_objects:
                view_obj_ref[key] = view_objects[existing_id]
                continue

            element_ref = element_id_ref.get(key) or existing_id
            if not element_ref:
                continue
            pos = (layout_positions or {}).get(key, {})
            operations.append(
                {
                    "tool": "add-to-view",
                    "params": {
                        "viewId": view_ref,
                        "elementId": element_ref,
                        "x": int(pos.get("x", 0)),
                        "y": int(pos.get("y", 0)),
                        "width": int(pos.get("width", 180)),
                        "height": int(pos.get("height", 70)),
                        "autoSize": True,
                    },
                }
            )
            view_obj_ref[key] = f"${len(operations) - 1}.id"
            added_to_view += 1

        if layout_positions:
            existing_positions: List[Dict[str, int | str]] = []
            for key, pos in layout_positions.items():
                existing_id = existing_element_id_for_key.get(key)
                if not existing_id:
                    continue
                view_object_id = view_objects.get(existing_id)
                if not view_object_id:
                    continue
                existing_positions.append(
                    {
                        "viewObjectId": view_object_id,
                        "x": int(pos.get("x", 0)),
                        "y": int(pos.get("y", 0)),
                        "width": int(pos.get("width", 180)),
                        "height": int(pos.get("height", 70)),
                    }
                )
            if existing_positions:
                operations.append(
                    {
                        "tool": "apply-positions",
                        "params": {
                            "viewId": view_ref,
                            "positions": existing_positions,
                        },
                    }
                )

        for rel in relationships:
            rel_type = rel["type"]
            source_key = rel["source_key"]
            target_key = rel["target_key"]

            source_ref = element_id_ref.get(source_key) or existing_element_id_for_key.get(source_key)
            target_ref = element_id_ref.get(target_key) or existing_element_id_for_key.get(target_key)
            if not source_ref or not target_ref:
                continue

            existing_rel_id = None
            if source_key not in element_id_ref and target_key not in element_id_ref:
                existing_rel_id = existing_relationships.get((self._norm_key(rel_type), str(source_ref), str(target_ref)))

            if existing_rel_id:
                relationship_ref = existing_rel_id
            else:
                params: Dict[str, Any] = {
                    "type": rel_type,
                    "sourceId": source_ref,
                    "targetId": target_ref,
                }
                rel_name = rel.get("name")
                if rel_name:
                    params["name"] = rel_name
                access_type = rel.get("accessType")
                if access_type and rel_type == "AccessRelationship":
                    params["accessType"] = access_type
                operations.append({"tool": "create-relationship", "params": params})
                relationship_ref = f"${len(operations) - 1}.id"
                created_relationships += 1

            source_view_ref = view_obj_ref.get(source_key)
            target_view_ref = view_obj_ref.get(target_key)
            if not source_view_ref or not target_view_ref:
                continue
            if existing_rel_id and existing_rel_id in existing_visual_relationships:
                continue

            operations.append(
                {
                    "tool": "add-connection-to-view",
                    "params": {
                        "viewId": view_ref,
                        "relationshipId": relationship_ref,
                        "sourceViewObjectId": source_view_ref,
                        "targetViewObjectId": target_view_ref,
                    },
                }
            )
            added_connections += 1

        if len(operations) > self._max_automation_ops():
            raise RuntimeError(
                f"Automation plan generated {len(operations)} operations, exceeding the MCP bulk limit of 150. "
                "Please reduce the scope of the uploaded document."
            )

        if operations:
            self._mcp_call(
                "bulk-mutate",
                {
                    "operations": operations,
                    "description": f"{action_label} from {source_name}",
                    "intent": f"One-shot {action_label} model build",
                },
                used_tools,
            )

        return {
            "view_name": view_name,
            "view_id": existing_view_id,
            "created_elements": created_elements,
            "created_relationships": created_relationships,
            "added_to_view": added_to_view,
            "added_connections": added_connections,
            "used_tools": used_tools,
        }

    @staticmethod
    def _grid_layout_positions(
        elements_by_key: Dict[str, Dict[str, str]],
        *,
        columns: int = 4,
    ) -> Dict[str, Dict[str, int]]:
        """Arrange elements without explicit coordinates into a readable grid, grouped by type.

        Without this, elements added to a view default to (0, 0) and stack directly on top
        of each other in Archi.
        """
        width, height = 200, 80
        gap_x, gap_y = 260, 140
        start_x, start_y = 100, 100

        grouped: Dict[str, List[str]] = {}
        for key, item in elements_by_key.items():
            grouped.setdefault(item.get("type", ""), []).append(key)

        positions: Dict[str, Dict[str, int]] = {}
        row_offset = 0
        for keys in grouped.values():
            rows_used = 0
            for index, key in enumerate(keys):
                col = index % columns
                sub_row = index // columns
                rows_used = max(rows_used, sub_row + 1)
                positions[key] = {
                    "x": start_x + col * gap_x,
                    "y": start_y + (row_offset + sub_row) * gap_y,
                    "width": width,
                    "height": height,
                }
            row_offset += rows_used
        return positions

    def _format_plan_response(
        self,
        *,
        action: str,
        source_name: str,
        extracted: Dict[str, Any],
    ) -> Dict[str, Any]:
        elements_list = [
            {
                "key": key,
                "type": item["type"],
                "name": item["name"],
                "documentation": item.get("documentation", ""),
                "include": True,
            }
            for key, item in extracted["elements"].items()
        ]
        relationships_list = [
            {
                "key": f"rel-{index}",
                "type": rel["type"],
                "source_key": rel["source_key"],
                "target_key": rel["target_key"],
                "name": rel.get("name"),
                "accessType": rel.get("accessType"),
                "include": True,
            }
            for index, rel in enumerate(extracted["relationships"])
        ]
        layout_positions = {key: dict(pos) for key, pos in extracted.get("layout_positions", {}).items()}

        return {
            "action": action,
            "source_name": source_name,
            "view_name": extracted["view_name"],
            "elements": elements_list,
            "relationships": relationships_list,
            "layout_positions": layout_positions,
            "steps_processed": extracted["steps_processed"],
            "steps_truncated": extracted["steps_truncated"],
            "warnings": extracted.get("warnings", []),
        }

    def apply_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        action = str(plan.get("action", ""))
        view_name = self._normalize_name(plan.get("view_name", ""), fallback="Automation View")
        source_name = str(plan.get("source_name") or "edited plan")

        included_elements: Dict[str, Dict[str, str]] = {}
        for item in plan.get("elements", []):
            if not isinstance(item, dict) or not item.get("include", True):
                continue
            key = str(item.get("key", "")).strip()
            name = self._normalize_name(item.get("name", ""), fallback="")
            element_type = self._normalize_name(item.get("type", ""), fallback="")
            if not key or not name or not element_type:
                continue
            included_elements[key] = {
                "type": element_type,
                "name": name,
                "documentation": str(item.get("documentation") or ""),
            }

        relationships: List[Dict[str, str]] = []
        for item in plan.get("relationships", []):
            if not isinstance(item, dict) or not item.get("include", True):
                continue
            source_key = str(item.get("source_key", "")).strip()
            target_key = str(item.get("target_key", "")).strip()
            if source_key not in included_elements or target_key not in included_elements:
                continue
            rel_type = self._normalize_name(item.get("type", ""), fallback="AssociationRelationship")
            rel_item: Dict[str, str] = {
                "type": rel_type,
                "source_key": source_key,
                "target_key": target_key,
            }
            rel_name = item.get("name")
            if rel_name:
                rel_item["name"] = self._normalize_name(rel_name, fallback="", max_len=120)
            access_type = item.get("accessType")
            if access_type and rel_type == "AccessRelationship":
                rel_item["accessType"] = str(access_type)
            relationships.append(rel_item)

        if not included_elements:
            raise RuntimeError("No elements were selected to apply.")

        layout_positions: Dict[str, Dict[str, int]] = {}
        for key, pos in (plan.get("layout_positions") or {}).items():
            if key not in included_elements or not isinstance(pos, dict):
                continue
            layout_positions[key] = {
                "x": int(pos.get("x", 0)),
                "y": int(pos.get("y", 0)),
                "width": int(pos.get("width", 180)),
                "height": int(pos.get("height", 70)),
            }
        if not layout_positions:
            layout_positions = self._grid_layout_positions(included_elements)

        action_label = "business-process automation" if action == "business-process-upload" else "requirements automation"
        execution = self._execute_view_plan(
            action_label=action_label,
            source_name=source_name,
            view_name=view_name,
            elements_by_key=included_elements,
            relationships=relationships,
            layout_positions=layout_positions,
        )
        execution["steps_processed"] = len(included_elements)
        execution["steps_truncated"] = False
        return execution

    def _extract_business_process_plan(
        self,
        *,
        content_text: str,
        source_name: str,
        view_name: str | None = None,
    ) -> Dict[str, Any]:
        preferred_view_name = self._normalize_name(view_name or "Business Process View", fallback="Business Process View")
        user_prompt = (
            "Extract business process steps and their primary business objects from the input.\n"
            "Return strict JSON only with this schema:\n"
            "{\n"
            '  "view_name": "string",\n'
            '  "steps": [{"process": "string", "object": "string", "evidence": "exact short quote from input", "confidence": 0.0}]\n'
            "}\n"
            "Rules:\n"
            "- Keep process names short and concrete.\n"
            "- Keep business object names concise nouns.\n"
            "- Preserve the process order.\n"
            "- evidence must be copied from the source text exactly.\n"
            "- confidence must be between 0 and 1.\n"
            "- Do not include explanations.\n\n"
            f"Preferred view name: {preferred_view_name}\n\n"
            "Input:\n"
            f"{content_text[: self.settings.max_upload_text_chars]}"
        )
        parsed = self._call_model_for_json(
            system_prompt="You are an enterprise architect extraction engine. Output strict JSON only.",
            user_prompt=user_prompt,
        )

        raw_steps = parsed.get("steps")
        if not isinstance(raw_steps, list):
            raw_steps = parsed.get("processes")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise RuntimeError("Could not extract process steps from upload content.")

        max_steps = max(1, self.settings.max_action_steps)
        source_text = content_text[: self.settings.max_upload_text_chars]
        elements: Dict[str, Dict[str, str]] = {}
        relationships: List[Dict[str, str]] = []
        ordered_process_keys: List[str] = []
        step_pairs: List[tuple[str, str]] = []
        quality_warnings: List[str] = []
        truncated = False
        discarded_low_evidence = 0

        accepted_count = 0
        for step in raw_steps:
            if not isinstance(step, dict):
                continue
            if accepted_count >= max_steps:
                truncated = True
                break

            process_name = self._normalize_name(step.get("process", ""), fallback="")
            object_name = self._normalize_name(step.get("object", ""), fallback="")
            evidence = self._normalize_name(step.get("evidence", ""), fallback="", max_len=240)
            confidence = self._as_float(step.get("confidence", 0.0), default=0.0)

            if not process_name or not object_name:
                continue
            process_evidence_ok = self._has_text_evidence(process_name, source_text) or self._has_text_evidence(evidence, source_text)
            object_evidence_ok = self._has_text_evidence(object_name, source_text) or self._has_text_evidence(evidence, source_text)
            if (not process_evidence_ok or not object_evidence_ok) and confidence < 0.65:
                discarded_low_evidence += 1
                continue

            process_name = self._normalize_name(process_name, fallback=f"Process {accepted_count + 1}")
            object_name = self._normalize_name(object_name, fallback=f"{process_name} Object")

            process_key = f"BusinessProcess::{self._norm_key(process_name)}"
            object_key = f"BusinessObject::{self._norm_key(object_name)}"

            elements.setdefault(
                process_key,
                {"type": "BusinessProcess", "name": process_name},
            )
            elements.setdefault(
                object_key,
                {"type": "BusinessObject", "name": object_name},
            )
            ordered_process_keys.append(process_key)
            step_pairs.append((process_key, object_key))
            relationships.append(
                {
                    "type": "AccessRelationship",
                    "source_key": process_key,
                    "target_key": object_key,
                    "accessType": "readwrite",
                }
            )
            accepted_count += 1

        if not ordered_process_keys and raw_steps:
            quality_warnings.append(
                "Evidence quality gate filtered all extracted steps. Falling back to top extracted entries with reduced confidence."
            )
            for index, step in enumerate(raw_steps[:max_steps]):
                if not isinstance(step, dict):
                    continue
                process_name = self._normalize_name(step.get("process", ""), fallback=f"Process {index + 1}")
                object_name = self._normalize_name(step.get("object", ""), fallback=f"{process_name} Object")
                process_key = f"BusinessProcess::{self._norm_key(process_name)}"
                object_key = f"BusinessObject::{self._norm_key(object_name)}"
                elements.setdefault(process_key, {"type": "BusinessProcess", "name": process_name})
                elements.setdefault(object_key, {"type": "BusinessObject", "name": object_name})
                ordered_process_keys.append(process_key)
                step_pairs.append((process_key, object_key))
                relationships.append(
                    {
                        "type": "AccessRelationship",
                        "source_key": process_key,
                        "target_key": object_key,
                        "accessType": "readwrite",
                    }
                )

        if discarded_low_evidence:
            quality_warnings.append(
                f"Discarded {discarded_low_evidence} low-evidence step candidates from PDF extraction."
            )

        for idx in range(len(ordered_process_keys) - 1):
            if ordered_process_keys[idx] == ordered_process_keys[idx + 1]:
                continue
            relationships.append(
                {
                    "type": "TriggeringRelationship",
                    "source_key": ordered_process_keys[idx],
                    "target_key": ordered_process_keys[idx + 1],
                }
            )

        layout_positions: Dict[str, Dict[str, int]] = {}
        process_x: Dict[str, int] = {}
        process_index = 0
        start_x = 100
        step_gap = 340
        process_y = 110
        object_y = 285
        for process_key, object_key in step_pairs:
            if process_key not in process_x:
                process_x[process_key] = start_x + (process_index * step_gap)
                process_index += 1
            x = process_x[process_key]
            layout_positions[process_key] = {"x": x, "y": process_y, "width": 220, "height": 80}
            layout_positions.setdefault(object_key, {"x": x, "y": object_y, "width": 220, "height": 80})

        resolved_view_name = self._normalize_name(parsed.get("view_name", preferred_view_name), fallback=preferred_view_name)
        return {
            "view_name": resolved_view_name,
            "elements": elements,
            "relationships": relationships,
            "layout_positions": layout_positions,
            "steps_processed": len(ordered_process_keys),
            "steps_truncated": truncated,
            "warnings": quality_warnings,
        }

    def plan_business_process_automation(
        self,
        *,
        content_text: str,
        source_name: str,
        view_name: str | None = None,
    ) -> Dict[str, Any]:
        extracted = self._extract_business_process_plan(
            content_text=content_text, source_name=source_name, view_name=view_name
        )
        return self._format_plan_response(action="business-process-upload", source_name=source_name, extracted=extracted)

    def run_business_process_automation(
        self,
        *,
        content_text: str,
        source_name: str,
        view_name: str | None = None,
    ) -> Dict[str, Any]:
        extracted = self._extract_business_process_plan(
            content_text=content_text, source_name=source_name, view_name=view_name
        )
        execution = self._execute_view_plan(
            action_label="business-process automation",
            source_name=source_name,
            view_name=extracted["view_name"],
            elements_by_key=extracted["elements"],
            relationships=extracted["relationships"],
            layout_positions=extracted["layout_positions"],
        )
        execution["steps_processed"] = extracted["steps_processed"]
        execution["steps_truncated"] = extracted["steps_truncated"]
        execution["warnings"] = extracted["warnings"]
        return execution

    def _extract_requirements_plan(
        self,
        *,
        content_text: str,
        source_name: str,
        view_name: str | None = None,
    ) -> Dict[str, Any]:
        preferred_view_name = self._normalize_name(view_name or "Product Architecture", fallback="Product Architecture")
        allowed_element_types = {
            "BusinessActor",
            "BusinessProcess",
            "ApplicationComponent",
            "ApplicationService",
            "DataObject",
            "Node",
            "TechnologyService",
            "SystemSoftware",
            "Device",
        }
        allowed_relationship_types = {
            "ServingRelationship",
            "RealizationRelationship",
            "FlowRelationship",
            "AccessRelationship",
            "AssignmentRelationship",
            "CompositionRelationship",
            "AggregationRelationship",
            "AssociationRelationship",
            "TriggeringRelationship",
        }

        user_prompt = (
            "Create a product architecture blueprint from the requirement input.\n"
            "Return strict JSON only with this schema:\n"
            "{\n"
            '  "view_name": "string",\n'
            '  "elements": [{"name": "string", "type": "ApplicationComponent"}],\n'
            '  "relationships": [{"type": "ServingRelationship", "source": "element name", "target": "element name", "name": "optional"}]\n'
            "}\n"
            "Rules:\n"
            "- Use unique element names.\n"
            "- Use only architecture-significant elements.\n"
            "- Keep between 6 and 30 elements.\n"
            "- Keep relationships directed.\n"
            "- No explanation text.\n\n"
            f"Preferred view name: {preferred_view_name}\n\n"
            "Input:\n"
            f"{content_text[: self.settings.max_upload_text_chars]}"
        )
        parsed = self._call_model_for_json(
            system_prompt="You are an enterprise architect extraction engine. Output strict JSON only.",
            user_prompt=user_prompt,
        )

        raw_elements = parsed.get("elements")
        raw_relationships = parsed.get("relationships")
        if not isinstance(raw_elements, list) or not raw_elements:
            raise RuntimeError("Could not extract architecture elements from requirement input.")
        if not isinstance(raw_relationships, list):
            raw_relationships = []

        elements: Dict[str, Dict[str, str]] = {}
        key_by_name: Dict[str, str] = {}
        max_elements = 40

        for item in raw_elements:
            if len(elements) >= max_elements:
                break
            if not isinstance(item, dict):
                continue
            name = self._normalize_name(item.get("name", ""), fallback="")
            if not name:
                continue
            raw_type = self._normalize_name(item.get("type", "ApplicationComponent"), fallback="ApplicationComponent")
            element_type = raw_type if raw_type in allowed_element_types else "ApplicationComponent"
            key = f"{element_type}::{self._norm_key(name)}"
            if key in elements:
                continue
            elements[key] = {
                "type": element_type,
                "name": name,
                "documentation": self._normalize_name(item.get("description", ""), fallback="", max_len=400),
            }
            key_by_name.setdefault(self._norm_key(name), key)

        relationships: List[Dict[str, str]] = []
        max_relationships = 34
        for item in raw_relationships:
            if len(relationships) >= max_relationships:
                break
            if not isinstance(item, dict):
                continue
            source_label = self._norm_key(item.get("source", ""))
            target_label = self._norm_key(item.get("target", ""))
            if not source_label or not target_label:
                continue
            source_key = key_by_name.get(source_label)
            target_key = key_by_name.get(target_label)
            if not source_key or not target_key or source_key == target_key:
                continue

            raw_type = self._normalize_name(item.get("type", "AssociationRelationship"), fallback="AssociationRelationship")
            relationship_type = raw_type if raw_type in allowed_relationship_types else "AssociationRelationship"
            rel_item: Dict[str, str] = {
                "type": relationship_type,
                "source_key": source_key,
                "target_key": target_key,
            }
            rel_name = self._normalize_name(item.get("name", ""), fallback="", max_len=120)
            if rel_name:
                rel_item["name"] = rel_name
            if relationship_type == "AccessRelationship":
                access_type = self._normalize_name(item.get("accessType", "readwrite"), fallback="readwrite", max_len=16).lower()
                if access_type not in {"access", "read", "write", "readwrite"}:
                    access_type = "readwrite"
                rel_item["accessType"] = access_type
            relationships.append(rel_item)

        resolved_view_name = self._normalize_name(parsed.get("view_name", preferred_view_name), fallback=preferred_view_name)
        return {
            "view_name": resolved_view_name,
            "elements": elements,
            "relationships": relationships,
            "layout_positions": self._grid_layout_positions(elements),
            "steps_processed": len(elements),
            "steps_truncated": len(raw_elements) > len(elements),
            "warnings": [],
        }

    def plan_requirements_automation(
        self,
        *,
        content_text: str,
        source_name: str,
        view_name: str | None = None,
    ) -> Dict[str, Any]:
        extracted = self._extract_requirements_plan(
            content_text=content_text, source_name=source_name, view_name=view_name
        )
        return self._format_plan_response(action="requirements-upload", source_name=source_name, extracted=extracted)

    def run_requirements_automation(
        self,
        *,
        content_text: str,
        source_name: str,
        view_name: str | None = None,
    ) -> Dict[str, Any]:
        extracted = self._extract_requirements_plan(
            content_text=content_text, source_name=source_name, view_name=view_name
        )
        execution = self._execute_view_plan(
            action_label="requirements automation",
            source_name=source_name,
            view_name=extracted["view_name"],
            elements_by_key=extracted["elements"],
            relationships=extracted["relationships"],
            layout_positions=extracted["layout_positions"],
        )
        execution["steps_processed"] = extracted["steps_processed"]
        execution["steps_truncated"] = extracted["steps_truncated"]
        return execution

    def _chat_internal(
        self,
        history: List[Dict[str, str]],
        message: str,
        system_prompt: str | None = None,
        include_trace: bool = False,
    ) -> Tuple[str, List[str], Dict[str, Any] | None]:
        if not self.settings.azure_openai_api_key:
            raise RuntimeError("AZURE_OPENAI_API_KEY is not configured.")
        if not self.settings.azure_openai_base_url:
            raise RuntimeError("AZURE_OPENAI_BASE_URL is not configured.")

        mcp_tools = self.mcp.list_tools()
        tools = [self._tool_to_openai_format(t) for t in mcp_tools]

        messages = self._build_messages(history=history, message=message, system_prompt=system_prompt)
        used_tools: List[str] = []
        trace_start = time.perf_counter()
        trace: Dict[str, Any] | None = None
        used_models: set[str] = set()
        if include_trace:
            trace = {
                "started_at": self._now_iso(),
                "completed_at": self._now_iso(),
                "duration_ms": 0,
                "fallback_used": False,
                "rounds": [],
                "notes": [],
            }

        for round_index in range(self.settings.max_tool_roundtrips):
            round_start = time.perf_counter()
            create_kwargs: Dict[str, Any] = dict(
                model=self.settings.azure_openai_model,
                messages=messages,
                tools=tools if tools else None,
                tool_choice="auto" if tools else None,
            )
            # Some newer models only allow default temperature and reject explicit values.
            create_kwargs["temperature"] = self.settings.model_temperature
            response, model_used, retry_attempts = self._call_model_with_retries(create_kwargs)
            used_models.add(model_used)

            assistant = response.choices[0].message
            assistant_content = assistant.content or ""
            round_trace = {
                "round": round_index + 1,
                "model": model_used,
                "retry_attempts": retry_attempts,
                "latency_ms": int((time.perf_counter() - round_start) * 1000),
                "assistant_content_chars": len(assistant_content),
                "tool_calls": [],
                "tool_errors": [],
                "finish_reason": "final",
            }
            if assistant.tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": assistant_content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in assistant.tool_calls
                        ],
                    }
                )

                round_trace["finish_reason"] = "tool_calls"
                for tc in assistant.tool_calls:
                    tool_name = tc.function.name
                    round_trace["tool_calls"].append(tool_name)
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}

                    tool_started = time.perf_counter()
                    try:
                        tool_result = self.mcp.call_tool(tool_name, args)
                        tool_error = None
                    except Exception as exc:  # noqa: BLE001
                        tool_error = str(exc)
                        tool_result = {
                            "error": tool_error,
                            "tool_name": tool_name,
                            "arguments": args,
                        }

                    used_tools.append(tool_name)
                    if tool_error:
                        round_trace["tool_errors"].append(tool_name)
                    if include_trace and trace is not None:
                        trace["notes"].append(
                            (
                                f"Round {round_index + 1} | tool={tool_name} | "
                                f"ok={tool_error is None} | duration_ms={int((time.perf_counter() - tool_started) * 1000)} | "
                                f"args={self._preview_json(args, limit=250)}"
                            )
                        )
                        if tool_error:
                            trace["notes"].append(f"Round {round_index + 1} | tool_error={tool_error}")

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(tool_result, ensure_ascii=False),
                        }
                    )
                if include_trace and trace is not None:
                    trace["rounds"].append(round_trace)
                continue

            if include_trace and trace is not None:
                round_trace["finish_reason"] = "final"
                trace["rounds"].append(round_trace)
                trace["completed_at"] = self._now_iso()
                trace["duration_ms"] = int((time.perf_counter() - trace_start) * 1000)
                trace["fallback_used"] = len(used_models) > 1
            return assistant_content, used_tools, trace

        answer = "I hit the tool round-trip limit before producing a final answer. Please refine the question."
        if include_trace and trace is not None:
            trace["rounds"].append(
                {
                    "round": self.settings.max_tool_roundtrips,
                    "model": self.settings.azure_openai_model,
                    "retry_attempts": 0,
                    "latency_ms": 0,
                    "assistant_content_chars": 0,
                    "tool_calls": [],
                    "tool_errors": [],
                    "finish_reason": "limit",
                }
            )
            trace["completed_at"] = self._now_iso()
            trace["duration_ms"] = int((time.perf_counter() - trace_start) * 1000)
            trace["fallback_used"] = len(used_models) > 1
        return answer, used_tools, trace

    def chat(self, history: List[Dict[str, str]], message: str, system_prompt: str | None = None) -> Tuple[str, List[str]]:
        answer, used_tools, _trace = self._chat_internal(
            history=history,
            message=message,
            system_prompt=system_prompt,
            include_trace=False,
        )
        return answer, used_tools

    def chat_with_trace(
        self,
        history: List[Dict[str, str]],
        message: str,
        system_prompt: str | None = None,
    ) -> Tuple[str, List[str], Dict[str, Any]]:
        answer, used_tools, trace = self._chat_internal(
            history=history,
            message=message,
            system_prompt=system_prompt,
            include_trace=True,
        )
        return answer, used_tools, trace or {}
