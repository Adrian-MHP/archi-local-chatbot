from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import re
import time
from typing import Any, Dict, List, Tuple

from openai import APIStatusError, BadRequestError, OpenAI, RateLimitError

from . import meta_model
from . import pdf_diagram
from .config import Settings
from .mcp_client import McpClient, McpTool


_EVIDENCE_STOPWORDS = frozenset(
    {
        "and", "the", "for", "with", "from", "this", "that", "these", "those", "not",
        "are", "was", "were", "been", "being", "have", "has", "had", "will", "shall",
        "should", "would", "could", "can", "may", "might", "must", "into", "onto",
        "upon", "also", "then", "than", "when", "where", "which", "while", "about",
        "after", "before", "between", "during", "through", "under", "over", "out",
        "off", "only", "just", "such", "some", "any", "all", "each", "every", "other",
        "another", "more", "most", "much", "many", "own", "same", "too", "very",
        "its", "their", "there", "here", "who", "whom", "whose", "what", "how", "why",
        "you", "your", "yours", "our", "ours", "his", "her", "hers", "them", "they",
    }
)


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
                + execution_policy
                + "\n\n"
                + meta_model.render_prompt_block(),
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

    def _call_model_with_retries(
        self, create_kwargs: Dict[str, Any], *, primary_model: str | None = None
    ) -> Tuple[Any, str, int]:
        models: List[str] = []
        for candidate in (primary_model, self.settings.azure_openai_model, self.settings.azure_openai_fallback_model):
            if candidate and candidate not in models:
                models.append(candidate)

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

        tokens = [t for t in self._tokens(candidate_norm) if t not in _EVIDENCE_STOPWORDS]
        if not tokens:
            return False
        # Exact whole-token membership (not raw substring) so short tokens like "not" can't
        # spuriously match inside unrelated longer words.
        source_tokens = set(self._tokens(source_norm))
        hits = sum(1 for token in tokens if token in source_tokens)
        if len(tokens) <= 2:
            required = len(tokens)  # require every informative token to hit
        else:
            required = max(2, -(-(len(tokens) * 3) // 5))  # ceil(60% of tokens)
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
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )
        extraction_model = self.settings.azure_openai_extraction_model or self.settings.azure_openai_model
        response, _model_used, _retries = self._call_model_with_retries(create_kwargs, primary_model=extraction_model)
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
            create_params: Dict[str, Any] = {
                "type": element_type,
                "name": element_name,
                "documentation": item.get("documentation", ""),
            }
            properties = item.get("properties")
            if properties:
                create_params["properties"] = properties
            operations.append({"tool": "create-element", "params": create_params})
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
                "properties": item.get("properties", {}),
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
            raw_properties = item.get("properties")
            properties = (
                {str(k): str(v) for k, v in raw_properties.items()} if isinstance(raw_properties, dict) else {}
            )
            included_elements[key] = {
                "type": element_type,
                "name": name,
                "documentation": str(item.get("documentation") or ""),
                "properties": properties,
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

    def _extract_business_process_plan_from_text(
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
            '  "steps": [{"process": "string", "object": "string or null", "evidence": "exact short quote from input", '
            '"confidence": 0.0, "sequential_with_previous": true}]\n'
            "}\n"
            "Rules:\n"
            "- object is OPTIONAL. Set it to null/omit it when a step has no clearly associated business object "
            "or document nearby -- do not invent a plausible-sounding object just to fill the field. Many process "
            "steps in a diagram legitimately have no attached object.\n"
            "- process and object must be copied VERBATIM from the source text. Do not shorten, paraphrase, "
            "merge words, drop words, or summarize the label. Copy the full wording exactly as it appears "
            "(for example, if the source says 'Create Order out of Offer', the process must be exactly "
            "'Create Order out of Offer', not 'Create Offer' or any other shortened form).\n"
            "- Do not invent, guess, or infer a process or object that is not literally present in the input text.\n"
            "- This input may be a flattened text export of a diagram, where each box's label was originally "
            "wrapped across two or more short lines. Lines with NO blank line between them are usually "
            "CONTINUATIONS of the same box label, not separate concepts -- join them with a single space into "
            "one label (e.g. 'Create Order out of' immediately followed by 'Offer' on the next line, with no "
            "blank line between them, is the single label 'Create Order out of Offer', not a process named "
            "'Create Order out of' paired with an object named 'Offer'). A blank line, a clearly different "
            "sentence, or a different indentation/column usually marks a genuinely separate box or concept. "
            "Only split text into a process and a matching object when they are clearly two distinct concepts "
            "(e.g. a process box and a separately drawn data/document box it touches), never merely because "
            "the label wrapped onto a second line.\n"
            "- evidence must be an exact short quote copied from the source text that contains the process name.\n"
            "- confidence must be between 0 and 1, reflecting how certain you are this is a real process step "
            "explicitly present in the text (not inferred or assumed).\n"
            "- sequential_with_previous must be true ONLY when the source text gives a clear, explicit linear "
            "indication that this step directly follows the previous one (a numbered/lettered list, a single "
            "table's row order, or words like 'then'/'next'/'after'/'followed by'). Set it to false whenever "
            "the input looks like a flattened export of a diagram, flowchart, or swimlane process map (labels "
            "with no surrounding sentence structure, labels repeated across lanes/systems, an order that does "
            "not read like prose), or when this step could be a branch, decision outcome, or parallel path "
            "rather than a direct continuation of the previous step. When in doubt, set it to false: a missing "
            "connection is far better than a fabricated one.\n"
            "- Preserve the order steps appear in the source text.\n"
            "- Do not include explanations, only the JSON object.\n\n"
            f"Preferred view name: {preferred_view_name}\n\n"
            f"{meta_model.render_prompt_block()}\n"
            "For this extraction, only use BusinessProcess for 'process' and BusinessObject for 'object'.\n\n"
            "Input:\n"
            f"{content_text[: self.settings.max_upload_text_chars]}"
        )
        parsed = self._call_model_for_json(
            system_prompt="You are an enterprise architect extraction engine. Output strict JSON only. Never invent facts not present in the input.",
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
        sequential_flags: List[bool] = []
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
            raw_object = step.get("object")
            object_name = self._normalize_name(raw_object, fallback="") if raw_object else ""
            evidence = self._normalize_name(step.get("evidence", ""), fallback="", max_len=240)
            confidence = self._as_float(step.get("confidence", 0.0), default=0.0)
            sequential_with_previous = bool(step.get("sequential_with_previous", True))

            if not process_name:
                continue
            process_evidence_ok = self._has_text_evidence(process_name, source_text) or self._has_text_evidence(evidence, source_text)
            object_evidence_ok = (
                not object_name
                or self._has_text_evidence(object_name, source_text)
                or self._has_text_evidence(evidence, source_text)
            )
            if (not process_evidence_ok or not object_evidence_ok) and confidence < 0.65:
                discarded_low_evidence += 1
                continue

            process_key = f"BusinessProcess::{self._norm_key(process_name)}"
            elements.setdefault(
                process_key,
                {"type": "BusinessProcess", "name": process_name},
            )
            ordered_process_keys.append(process_key)
            sequential_flags.append(sequential_with_previous)

            if object_name:
                object_key = f"BusinessObject::{self._norm_key(object_name)}"
                elements.setdefault(
                    object_key,
                    {"type": "BusinessObject", "name": object_name},
                )
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

        if not ordered_process_keys:
            if discarded_low_evidence:
                raise RuntimeError(
                    f"Extraction produced {discarded_low_evidence} candidate step(s), but none could be verified "
                    "against the source text. This can happen when the source is a flattened text export of a "
                    "diagram or flowchart, where labels no longer read as prose. Try re-exporting the process as "
                    "a numbered list, a table, or a plain text description instead of a diagram-based PDF."
                )
            raise RuntimeError("Could not extract any verifiable process steps from the upload content.")

        if discarded_low_evidence:
            quality_warnings.append(
                f"Discarded {discarded_low_evidence} low-evidence step candidates from extraction."
            )

        skipped_sequential = 0
        for idx in range(1, len(ordered_process_keys)):
            if not sequential_flags[idx]:
                skipped_sequential += 1
                continue
            if ordered_process_keys[idx] == ordered_process_keys[idx - 1]:
                continue
            relationships.append(
                {
                    "type": "TriggeringRelationship",
                    "source_key": ordered_process_keys[idx - 1],
                    "target_key": ordered_process_keys[idx],
                }
            )
        if skipped_sequential:
            quality_warnings.append(
                f"{skipped_sequential} step(s) were not linked to the previous step because the source text did "
                "not give a clear indication of sequence (likely a diagram/flowchart export). Review the preview "
                "and connect steps manually in Archi if needed."
            )

        object_key_by_process: Dict[str, str] = dict(step_pairs)
        layout_positions: Dict[str, Dict[str, int]] = {}
        start_x = 100
        step_gap = 340
        row_gap = 340
        row_height = 175
        box_width, box_height = 220, 80
        # Wrap into a grid instead of one unbounded row -- a long process (dozens of steps) in a
        # single row produces an absurdly wide, near-invisible layout both in the preview and in
        # the actual Archi view.
        columns_per_row = max(1, math.ceil(math.sqrt(max(1, len(ordered_process_keys)))))
        for process_index, process_key in enumerate(ordered_process_keys):
            col = process_index % columns_per_row
            row = process_index // columns_per_row
            x = start_x + (col * step_gap)
            process_y = 110 + row * row_gap
            object_y = process_y + row_height
            layout_positions[process_key] = {"x": x, "y": process_y, "width": box_width, "height": box_height}
            object_key = object_key_by_process.get(process_key)
            if object_key:
                layout_positions.setdefault(object_key, {"x": x, "y": object_y, "width": box_width, "height": box_height})

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

    def _extract_business_process_plan_from_geometry(
        self,
        *,
        diagram: Dict[str, Any],
        source_name: str,
        view_name: str | None = None,
    ) -> Dict[str, Any] | None:
        """Build a plan directly from PDF vector geometry (real boxes + real detected arrows).

        Unlike the text-based path, connections here come from actual drawn lines/arrows in the
        document rather than model guesswork, so TriggeringRelationship edges reflect the diagram's
        real arrows instead of being inferred (or withheld) from flattened, order-ambiguous text.
        """
        boxes = diagram.get("boxes") or []
        connections = diagram.get("connections") or []
        if not boxes:
            return None

        preferred_view_name = self._normalize_name(view_name or "Business Process View", fallback="Business Process View")
        box_lines = "\n".join(f"{box['id']}: {box['text']}" for box in boxes)
        user_prompt = (
            "Each line below is a box extracted directly from a diagram's vector geometry, given as "
            '"<id>: <exact text>". Classify the ROLE of each box. Do not rename, translate, shorten, or '
            "alter the text in any way -- it is used verbatim regardless of your classification.\n"
            "Return strict JSON only with this schema:\n"
            "{\n"
            '  "view_name": "string",\n'
            '  "boxes": [{"id": 0, "role": "process", "confidence": 0.0}]\n'
            "}\n"
            "Rules:\n"
            "- role must be exactly one of: process, object, ignore.\n"
            "- \"process\" = a business process, activity, or task step (an action being performed).\n"
            "- \"object\" = a business object, document, record, or data artifact being referenced (a noun).\n"
            "- \"ignore\" = a swimlane/lane name, system name, page title, legend, decision-branch label "
            "like 'yes'/'no', or any other non-process, non-object label.\n"
            "- Classify every id listed below exactly once. Do not add ids that are not listed.\n"
            "- confidence must be between 0 and 1.\n\n"
            f"Preferred view name: {preferred_view_name}\n\n"
            f"{meta_model.render_prompt_block()}\n"
            "For this extraction, only use BusinessProcess for 'process' and BusinessObject for 'object'.\n\n"
            "Boxes:\n"
            f"{box_lines}"
        )
        parsed = self._call_model_for_json(
            system_prompt="You are an enterprise architect extraction engine. Output strict JSON only. Classify only, never rename.",
            user_prompt=user_prompt,
        )

        raw_roles = parsed.get("boxes")
        if not isinstance(raw_roles, list) or not raw_roles:
            return None

        role_by_id: Dict[int, str] = {}
        for item in raw_roles:
            if not isinstance(item, dict):
                continue
            try:
                box_id = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            role = str(item.get("role", "")).strip().lower()
            if role in {"process", "object", "ignore"}:
                role_by_id[box_id] = role

        max_steps = max(1, self.settings.max_action_steps)
        process_box_ids = [box["id"] for box in boxes if role_by_id.get(box["id"]) == "process"]
        accepted_process_ids = set(process_box_ids[:max_steps])
        truncated = len(process_box_ids) > len(accepted_process_ids)

        xs = [box["bbox"][0] for box in boxes]
        ys = [box["bbox"][1] for box in boxes]
        min_x, min_y = (min(xs), min(ys)) if xs and ys else (0.0, 0.0)

        elements: Dict[str, Dict[str, str]] = {}
        layout_positions: Dict[str, Dict[str, int]] = {}
        key_by_box_id: Dict[int, str] = {}

        for box in boxes:
            role = role_by_id.get(box["id"])
            if role == "process" and box["id"] not in accepted_process_ids:
                continue
            if role not in {"process", "object"}:
                continue
            name = self._normalize_name(box["text"], fallback="")
            if not name:
                continue
            element_type = "BusinessProcess" if role == "process" else "BusinessObject"
            key = f"{element_type}::{self._norm_key(name)}"
            elements.setdefault(key, {"type": element_type, "name": name})
            key_by_box_id[box["id"]] = key

            bx0, by0, bx1, by1 = box["bbox"]
            layout_positions.setdefault(
                key,
                {
                    "x": int(round(bx0 - min_x)) + 100,
                    "y": int(round(by0 - min_y)) + 100,
                    "width": max(120, int(round(bx1 - bx0))),
                    "height": max(60, int(round(by1 - by0))),
                },
            )

        if not elements:
            return None

        relationships: List[Dict[str, str]] = []
        seen_pairs: set[tuple[str, str, str]] = set()
        for conn in connections:
            source_key = key_by_box_id.get(conn.get("source_id"))
            target_key = key_by_box_id.get(conn.get("target_id"))
            if not source_key or not target_key or source_key == target_key:
                continue
            source_type = elements[source_key]["type"]
            target_type = elements[target_key]["type"]

            if source_type == "BusinessProcess" and target_type == "BusinessProcess":
                rel_type = "TriggeringRelationship"
            elif source_type == "BusinessProcess" and target_type == "BusinessObject":
                rel_type = "AccessRelationship"
            elif source_type == "BusinessObject" and target_type == "BusinessProcess":
                source_key, target_key = target_key, source_key
                rel_type = "AccessRelationship"
            else:
                continue

            pair_key = (rel_type, source_key, target_key)
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            rel_item: Dict[str, str] = {"type": rel_type, "source_key": source_key, "target_key": target_key}
            if rel_type == "AccessRelationship":
                rel_item["accessType"] = "readwrite"
            relationships.append(rel_item)

        resolved_view_name = self._normalize_name(parsed.get("view_name", preferred_view_name), fallback=preferred_view_name)
        process_count = sum(1 for item in elements.values() if item["type"] == "BusinessProcess")
        warnings = [
            f"Extracted directly from the PDF's drawn shapes: {len(boxes)} boxes and {len(relationships)} "
            "connections detected from real lines/arrows in the document (not inferred from text order)."
        ]
        if truncated:
            warnings.append(f"Process steps were truncated to the configured maximum of {max_steps}.")

        return {
            "view_name": resolved_view_name,
            "elements": elements,
            "relationships": relationships,
            "layout_positions": layout_positions,
            "steps_processed": process_count,
            "steps_truncated": truncated,
            "warnings": warnings,
        }

    def _extract_business_process_plan(
        self,
        *,
        content_text: str,
        source_name: str,
        view_name: str | None = None,
        pdf_bytes: bytes | None = None,
    ) -> Dict[str, Any]:
        if pdf_bytes:
            try:
                diagram = pdf_diagram.extract_diagram_structure(pdf_bytes)
            except Exception:  # noqa: BLE001
                diagram = None
            if diagram:
                geometry_plan = self._extract_business_process_plan_from_geometry(
                    diagram=diagram, source_name=source_name, view_name=view_name
                )
                if geometry_plan:
                    return geometry_plan
        return self._extract_business_process_plan_from_text(
            content_text=content_text, source_name=source_name, view_name=view_name
        )

    def plan_business_process_automation(
        self,
        *,
        content_text: str,
        source_name: str,
        view_name: str | None = None,
        pdf_bytes: bytes | None = None,
    ) -> Dict[str, Any]:
        extracted = self._extract_business_process_plan(
            content_text=content_text, source_name=source_name, view_name=view_name, pdf_bytes=pdf_bytes
        )
        return self._format_plan_response(action="business-process-upload", source_name=source_name, extracted=extracted)

    def run_business_process_automation(
        self,
        *,
        content_text: str,
        source_name: str,
        view_name: str | None = None,
        pdf_bytes: bytes | None = None,
    ) -> Dict[str, Any]:
        extracted = self._extract_business_process_plan(
            content_text=content_text, source_name=source_name, view_name=view_name, pdf_bytes=pdf_bytes
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
        allowed_element_types = set(meta_model.allowed_element_types())
        allowed_relationship_types = set(meta_model.allowed_relationship_types())

        user_prompt = (
            "Create a product architecture blueprint from the requirement input, following the governance "
            "meta-model below exactly.\n"
            "Return strict JSON only with this schema:\n"
            "{\n"
            '  "view_name": "string",\n'
            '  "elements": [{"name": "string", "type": "ApplicationComponent", "evidence": "exact short quote from input"}],\n'
            '  "relationships": [{"type": "ServingRelationship", "source": "element name", "target": "element name", '
            '"name": "optional", "evidence": "exact short quote from input showing this relationship"}]\n'
            "}\n"
            "Rules:\n"
            "- name must be copied verbatim from the source text wherever possible; do not paraphrase or invent names.\n"
            "- Only extract elements and relationships that are explicitly supported by the input text. Do not "
            "invent elements or relationships that are not present in the input, even to make the diagram look "
            "more complete.\n"
            "- evidence must be an exact short quote copied from the source text.\n"
            "- Use unique element names.\n"
            "- Keep between 6 and 30 elements.\n"
            "- Keep relationships directed (source -> target) and only include ones with clear textual support.\n"
            "- No explanation text, only the JSON object.\n\n"
            f"Preferred view name: {preferred_view_name}\n\n"
            f"{meta_model.render_prompt_block()}\n\n"
            "Input:\n"
            f"{content_text[: self.settings.max_upload_text_chars]}"
        )
        parsed = self._call_model_for_json(
            system_prompt="You are an enterprise architect extraction engine. Output strict JSON only. Never invent facts not present in the input.",
            user_prompt=user_prompt,
        )

        raw_elements = parsed.get("elements")
        raw_relationships = parsed.get("relationships")
        if not isinstance(raw_elements, list) or not raw_elements:
            raise RuntimeError("Could not extract architecture elements from requirement input.")
        if not isinstance(raw_relationships, list):
            raw_relationships = []

        source_text = content_text[: self.settings.max_upload_text_chars]
        elements: Dict[str, Dict[str, str]] = {}
        key_by_name: Dict[str, str] = {}
        max_elements = 40
        discarded_low_evidence_elements = 0

        for item in raw_elements:
            if len(elements) >= max_elements:
                break
            if not isinstance(item, dict):
                continue
            name = self._normalize_name(item.get("name", ""), fallback="")
            if not name:
                continue
            evidence = self._normalize_name(item.get("evidence", ""), fallback="", max_len=240)
            if not (self._has_text_evidence(name, source_text) or self._has_text_evidence(evidence, source_text)):
                discarded_low_evidence_elements += 1
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

        if not elements:
            if discarded_low_evidence_elements:
                raise RuntimeError(
                    f"Extraction produced {discarded_low_evidence_elements} candidate element(s), but none could "
                    "be verified against the source text. Try providing the requirements as plain text or a "
                    "structured list instead of a diagram-based document."
                )
            raise RuntimeError("Could not extract any verifiable architecture elements from the requirement input.")

        relationships: List[Dict[str, str]] = []
        max_relationships = 34
        discarded_low_evidence_relationships = 0
        coerced_relationship_types = 0
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

            evidence = self._normalize_name(item.get("evidence", ""), fallback="", max_len=240)
            if not self._has_text_evidence(evidence, source_text):
                discarded_low_evidence_relationships += 1
                continue

            raw_type = self._normalize_name(item.get("type", "AssociationRelationship"), fallback="AssociationRelationship")
            relationship_type = raw_type if raw_type in allowed_relationship_types else "AssociationRelationship"

            meta_type = meta_model.find_relationship_type(elements[source_key]["type"], elements[target_key]["type"])
            if meta_type:
                if meta_type != relationship_type:
                    relationship_type = meta_type
                    coerced_relationship_types += 1
            elif relationship_type != "AssociationRelationship":
                # This source/target type pair isn't declared anywhere in the governance meta-model;
                # fall back to a safe, semantically-neutral relationship rather than an unsanctioned type.
                relationship_type = "AssociationRelationship"
                coerced_relationship_types += 1

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

        warnings: List[str] = []
        if discarded_low_evidence_elements:
            warnings.append(
                f"Discarded {discarded_low_evidence_elements} element candidate(s) without textual evidence in the source."
            )
        if discarded_low_evidence_relationships:
            warnings.append(
                f"Discarded {discarded_low_evidence_relationships} relationship candidate(s) without textual evidence in the source."
            )
        if coerced_relationship_types:
            warnings.append(
                f"Adjusted {coerced_relationship_types} relationship type(s) to match the governance meta-model."
            )

        resolved_view_name = self._normalize_name(parsed.get("view_name", preferred_view_name), fallback=preferred_view_name)
        return {
            "view_name": resolved_view_name,
            "elements": elements,
            "relationships": relationships,
            "layout_positions": self._grid_layout_positions(elements),
            "steps_processed": len(elements),
            "steps_truncated": len(raw_elements) > len(elements),
            "warnings": warnings,
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

    # ---------------- Architecture Assessment workflow ----------------
    # Setup -> Ist-Aufnahme -> Soll-Architektur -> Mapping & Gap-Analyse -> Summary.
    # Ist-Aufnahme reuses run_/plan_business_process_automation as-is (targeted at the Ist
    # view name). The stages below cover the rest of the workflow.

    def assessment_setup(self, *, ist_view_name: str, soll_view_name: str) -> Dict[str, Any]:
        used_tools: List[str] = []
        ist_view_id = self._find_existing_view_id(view_name=ist_view_name, used_tools=used_tools)
        soll_view_id = self._find_existing_view_id(view_name=soll_view_name, used_tools=used_tools)
        result = self._mcp_call("search-elements", {"query": "", "layer": "Business", "limit": 500}, used_tools)
        business_elements = self._result_list(result, "elements")

        notes: List[str] = []
        notes.append(
            f"Found existing Ist view '{ist_view_name}' -- Ist-Aufnahme will add to it."
            if ist_view_id
            else f"No existing '{ist_view_name}' view found -- it will be created during Ist-Aufnahme."
        )
        notes.append(
            f"Found existing Soll view '{soll_view_name}' -- Soll-Architektur will add to it."
            if soll_view_id
            else f"No existing '{soll_view_name}' view found -- it will be created during Soll-Architektur."
        )
        notes.append(f"{len(business_elements)} existing Business layer element(s) found in the model.")

        return {
            "ist_view_name": ist_view_name,
            "soll_view_name": soll_view_name,
            "ist_view_exists": bool(ist_view_id),
            "soll_view_exists": bool(soll_view_id),
            "existing_business_element_count": len(business_elements),
            "notes": notes,
        }

    def plan_soll_architecture(
        self,
        *,
        content_text: str,
        source_name: str,
        view_name: str | None = None,
    ) -> Dict[str, Any]:
        extracted = self._extract_requirements_plan(
            content_text=content_text,
            source_name=source_name,
            view_name=view_name or "Soll-Architektur",
        )
        tagged_elements = {
            key: {**item, "properties": {**item.get("properties", {}), "status": "target"}}
            for key, item in extracted["elements"].items()
        }
        extracted = {**extracted, "elements": tagged_elements}
        return self._format_plan_response(action="assessment-soll-upload", source_name=source_name, extracted=extracted)

    @staticmethod
    def _property_value(properties: Any, key: str) -> str:
        if isinstance(properties, dict):
            return str(properties.get(key, "")).strip()
        if isinstance(properties, list):
            for prop in properties:
                if isinstance(prop, dict) and str(prop.get("key", "")).strip().lower() == key.lower():
                    return str(prop.get("value", "")).strip()
        return ""

    def run_mapping_gap_analysis(self, *, ist_view_name: str, soll_view_name: str) -> Dict[str, Any]:
        used_tools: List[str] = []
        result = self._mcp_call("search-elements", {"query": "", "type": "BusinessProcess", "limit": 500}, used_tools)
        all_processes = self._result_list(result, "elements")

        ist_processes: List[Dict[str, str]] = []
        soll_processes: List[Dict[str, str]] = []
        for item in all_processes:
            element_id = str(item.get("id", "")).strip()
            name = str(item.get("name", "")).strip()
            if not element_id or not name:
                continue
            status = self._property_value(item.get("properties"), "status").lower()
            entry = {"id": element_id, "name": name}
            (soll_processes if status == "target" else ist_processes).append(entry)

        if not ist_processes:
            raise RuntimeError(
                "No Ist business processes found in the model. Run Ist-Aufnahme (business process upload) first."
            )
        if not soll_processes:
            raise RuntimeError(
                "No Soll business processes found (elements tagged status=target). Run Soll-Architektur first."
            )

        ist_by_id = {p["id"]: p["name"] for p in ist_processes}
        soll_by_id = {p["id"]: p["name"] for p in soll_processes}
        ist_lines = "\n".join(f"{p['id']}: {p['name']}" for p in ist_processes)
        soll_lines = "\n".join(f"{p['id']}: {p['name']}" for p in soll_processes)

        user_prompt = (
            "You are comparing an AS-IS (Ist) business process list to a TO-BE (Soll) business process list "
            "for an architecture assessment gap analysis.\n"
            "Return strict JSON only with this schema:\n"
            "{\n"
            '  "mappings": [{"ist_id": "string or null", "soll_id": "string or null", "match_type": '
            '"full|partial|gap_new|legacy_no_soll", "confidence": 0.0, "rationale": "short reason"}],\n'
            '  "gaps": [{"category": "missing_process|redundancy|structural_difference|tooling_data_gap", '
            '"criticality": "high|medium|low", "description": "string", "related_ist_id": "string or null", '
            '"related_soll_id": "string or null"}]\n'
            "}\n"
            "Rules:\n"
            "- match_type 'full' = the Ist and Soll process describe essentially the same activity.\n"
            "- match_type 'partial' = related but meaningfully different scope or steps.\n"
            "- match_type 'legacy_no_soll' = an Ist process with no reasonable Soll equivalent (soll_id null).\n"
            "- match_type 'gap_new' = a Soll process with no Ist equivalent -- a new process not yet "
            "implemented (ist_id null).\n"
            "- Every listed Ist id and every listed Soll id must appear in exactly one mapping entry.\n"
            "- Only compare the process NAMES given; do not invent details not implied by the names.\n"
            "- gaps should summarize the most notable mapping outcomes (especially legacy_no_soll and gap_new "
            "entries, and any 'partial' entries with a meaningful difference), grouped by category and rated "
            "by business criticality. Not every mapping needs its own gap entry.\n"
            "- No explanation text, only the JSON object.\n\n"
            f"Ist processes (from '{ist_view_name}'):\n{ist_lines}\n\n"
            f"Soll processes (from '{soll_view_name}'):\n{soll_lines}"
        )
        parsed = self._call_model_for_json(
            system_prompt="You are an enterprise architecture assessment engine. Output strict JSON only.",
            user_prompt=user_prompt,
        )

        raw_mappings = parsed.get("mappings")
        mappings: List[Dict[str, Any]] = []
        if isinstance(raw_mappings, list):
            for index, item in enumerate(raw_mappings):
                if not isinstance(item, dict):
                    continue
                ist_id = item.get("ist_id")
                soll_id = item.get("soll_id")
                ist_id = str(ist_id).strip() if ist_id else None
                soll_id = str(soll_id).strip() if soll_id else None
                if ist_id and ist_id not in ist_by_id:
                    ist_id = None
                if soll_id and soll_id not in soll_by_id:
                    soll_id = None
                match_type = str(item.get("match_type", "")).strip()
                if match_type not in {"full", "partial", "gap_new", "legacy_no_soll"}:
                    continue
                if not ist_id and not soll_id:
                    continue
                mappings.append(
                    {
                        "key": f"map-{index}",
                        "ist_key": ist_id,
                        "ist_name": ist_by_id.get(ist_id) if ist_id else None,
                        "soll_key": soll_id,
                        "soll_name": soll_by_id.get(soll_id) if soll_id else None,
                        "match_type": match_type,
                        "confidence": self._as_float(item.get("confidence", 0.0), default=0.0),
                        "rationale": self._normalize_name(item.get("rationale", ""), fallback="", max_len=400),
                        "include": True,
                    }
                )

        raw_gaps = parsed.get("gaps")
        gaps: List[Dict[str, Any]] = []
        if isinstance(raw_gaps, list):
            for item in raw_gaps:
                if not isinstance(item, dict):
                    continue
                category = str(item.get("category", "")).strip()
                criticality = str(item.get("criticality", "")).strip()
                if category not in {"missing_process", "redundancy", "structural_difference", "tooling_data_gap"}:
                    continue
                if criticality not in {"high", "medium", "low"}:
                    continue
                related_ist_id = item.get("related_ist_id")
                related_soll_id = item.get("related_soll_id")
                gaps.append(
                    {
                        "category": category,
                        "criticality": criticality,
                        "description": self._normalize_name(item.get("description", ""), fallback="", max_len=400),
                        "related_ist_name": ist_by_id.get(str(related_ist_id).strip()) if related_ist_id else None,
                        "related_soll_name": soll_by_id.get(str(related_soll_id).strip()) if related_soll_id else None,
                    }
                )

        if not mappings:
            raise RuntimeError("Mapping analysis did not produce any usable mapping entries.")

        warnings: List[str] = []
        mapped_ist_ids = {m["ist_key"] for m in mappings if m["ist_key"]}
        mapped_soll_ids = {m["soll_key"] for m in mappings if m["soll_key"]}
        unmapped_ist = len(ist_processes) - len(mapped_ist_ids)
        unmapped_soll = len(soll_processes) - len(mapped_soll_ids)
        if unmapped_ist > 0:
            warnings.append(f"{unmapped_ist} Ist process(es) were not covered by any mapping entry.")
        if unmapped_soll > 0:
            warnings.append(f"{unmapped_soll} Soll process(es) were not covered by any mapping entry.")

        return {
            "ist_view_name": ist_view_name,
            "soll_view_name": soll_view_name,
            "mappings": mappings,
            "gaps": gaps,
            "warnings": warnings,
            "used_tools": used_tools,
        }

    def apply_mapping_relationships(self, *, mappings: List[Dict[str, Any]]) -> Dict[str, Any]:
        included = [m for m in mappings if m.get("include", True) and m.get("ist_key") and m.get("soll_key")]
        if not included:
            raise RuntimeError("No mapping pairs with both an Ist and a Soll process were selected to apply.")

        used_tools: List[str] = []
        existing_relationships = self._collect_existing_relationships(used_tools)
        operations: List[Dict[str, Any]] = []
        created = 0
        match_labels = {"full": "full match", "partial": "partial match"}

        for mapping in included:
            ist_id = str(mapping["ist_key"])
            soll_id = str(mapping["soll_key"])
            existing = existing_relationships.get((self._norm_key("AssociationRelationship"), ist_id, soll_id))
            if existing:
                continue
            label = match_labels.get(str(mapping.get("match_type", "")), "mapped to")
            operations.append(
                {
                    "tool": "create-relationship",
                    "params": {
                        "type": "AssociationRelationship",
                        "sourceId": ist_id,
                        "targetId": soll_id,
                        "name": f"Assessment mapping ({label})",
                    },
                }
            )
            created += 1

        if operations:
            self._mcp_call(
                "bulk-mutate",
                {
                    "operations": operations,
                    "description": "Architecture assessment Ist-Soll mapping",
                    "intent": "Create traceability links between Ist and Soll business processes",
                },
                used_tools,
            )

        return {
            "summary": f"Created {created} Ist-Soll mapping relationship(s).",
            "created_relationships": created,
            "used_tools": used_tools,
        }

    def generate_assessment_summary(
        self, *, mappings: List[Dict[str, Any]], gaps: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        ist_ids = {m["ist_key"] for m in mappings if m.get("ist_key")}
        soll_ids = {m["soll_key"] for m in mappings if m.get("soll_key")}
        full_matches = sum(1 for m in mappings if m.get("match_type") == "full")
        partial_matches = sum(1 for m in mappings if m.get("match_type") == "partial")
        legacy_count = sum(1 for m in mappings if m.get("match_type") == "legacy_no_soll")
        gap_new_count = sum(1 for m in mappings if m.get("match_type") == "gap_new")
        gap_count = len(gaps)
        critical_gap_count = sum(1 for g in gaps if g.get("criticality") == "high")

        confidences = [
            self._as_float(m.get("confidence", 0.0), default=0.0)
            for m in mappings
            if m.get("match_type") in {"full", "partial"}
        ]
        average_similarity = (sum(confidences) / len(confidences) * 100.0) if confidences else 0.0
        total_mappings = max(1, len(mappings))
        maturity_score = ((full_matches + 0.5 * partial_matches) / total_mappings) * 100.0

        summary_prompt = (
            "Write a short executive summary (4-6 sentences, plain text, no markdown, no headings or bullet "
            "points) of this architecture assessment gap analysis for a management audience.\n"
            f"Ist processes mapped: {len(ist_ids)}. Soll processes mapped: {len(soll_ids)}.\n"
            f"Full matches: {full_matches}. Partial matches: {partial_matches}. "
            f"Legacy processes with no Soll equivalent: {legacy_count}. "
            f"New Soll processes with no Ist equivalent: {gap_new_count}.\n"
            f"Total gaps identified: {gap_count}, of which {critical_gap_count} are high criticality.\n"
            f"Average mapping confidence/similarity: {average_similarity:.0f}%. "
            f"Overall maturity score: {maturity_score:.0f}%.\n"
            "Mention the maturity score, the volume of gaps, and flag whether critical gaps need near-term "
            "attention."
        )
        create_kwargs: Dict[str, Any] = dict(
            messages=[
                {
                    "role": "system",
                    "content": "You write concise, plain-text executive summaries for architecture consulting engagements.",
                },
                {"role": "user", "content": summary_prompt},
            ],
            temperature=0.3,
        )
        extraction_model = self.settings.azure_openai_extraction_model or self.settings.azure_openai_model
        response, _model, _retries = self._call_model_with_retries(create_kwargs, primary_model=extraction_model)
        executive_summary = (response.choices[0].message.content or "").strip()

        return {
            "ist_process_count": len(ist_ids),
            "soll_process_count": len(soll_ids),
            "full_matches": full_matches,
            "partial_matches": partial_matches,
            "gap_count": gap_count,
            "critical_gap_count": critical_gap_count,
            "average_similarity": round(average_similarity, 1),
            "maturity_score": round(maturity_score, 1),
            "executive_summary": executive_summary,
        }

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
