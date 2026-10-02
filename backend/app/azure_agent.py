from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
import functools
import json
import math
import re
import threading
import time
from typing import Any, Callable, Dict, List, Tuple

import httpx
from openai import APIStatusError, BadRequestError, OpenAI, RateLimitError

from . import dashboard, meta_model, steering
from . import pdf_diagram
from . import pdf_table
from .config import Settings
from .mcp_client import McpClient, McpTool


_BACKREF_RE = re.compile(r"^\$(\d+)\.id$")

# Receives the chat's live progress events: (event name, payload).
ChatEventSink = Callable[[str, Dict[str, Any]], None]


class ChatStopped(RuntimeError):
    """The client stopped the chat request; no further model or tool call is made."""


def _tracks_proposals(method):
    """Record the approval proposals Archi creates while a write operation runs, and return them as
    result["proposals"] -- the only reliable signal that Archi's approval mode is on for this write."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._collect_proposals() as proposals:
            result = method(self, *args, **kwargs)
            if isinstance(result, dict):
                result.setdefault("proposals", list(proposals))
        return result

    return wrapper


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
        self._proposal_local = threading.local()
        self.mcp = McpClient(
            server_url=settings.mcp_server_url,
            bearer_token=settings.mcp_bearer_token,
            timeout_seconds=settings.request_timeout_seconds,
            host_header=settings.mcp_host_header,
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
            "Output format: the chat renders GitHub-flavoured Markdown. Lead with the answer, then the "
            "detail. Use short paragraphs, bullet or numbered lists for enumerations, a table when you compare "
            "several elements on the same attributes and **bold** for key figures. Refer to elements by name and "
            "mention the type only where the context does not make it clear; show element ids only when the user "
            "asks for them, then as `code`. "
            "Use headings (### at most) only in long answers. No HTML, no emojis."
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
        last_error: Exception | None = None

        for model in models:
            kwargs_for_model = dict(create_kwargs)
            kwargs_for_model["model"] = model

            for attempt in range(self.settings.max_model_retries + 1):
                try:
                    return self._call_model_once(kwargs_for_model), model, attempt
                except RateLimitError as exc:
                    last_rate_limit_error = exc
                    last_error = exc
                    if attempt >= self.settings.max_model_retries:
                        break

                    retry_after = self._extract_retry_after_seconds(exc)
                    if retry_after is None:
                        retry_after = self.settings.model_retry_backoff_seconds * (2 ** attempt)
                    # Prevent unbounded waits on bad headers.
                    retry_after = min(max(retry_after, 0.2), 30.0)
                    time.sleep(retry_after)
                except APIStatusError as exc:
                    last_error = exc
                    status = getattr(exc, "status_code", None)
                    if status == 429:
                        last_rate_limit_error = exc
                        if attempt >= self.settings.max_model_retries:
                            break
                        retry_after = self._extract_retry_after_seconds(exc)
                        if retry_after is None:
                            retry_after = self.settings.model_retry_backoff_seconds * (2 ** attempt)
                        retry_after = min(max(retry_after, 0.2), 30.0)
                        time.sleep(retry_after)
                        continue
                    # A non-rate-limit API error (misconfigured deployment name, deployment
                    # doesn't support this operation, etc.) won't be fixed by retrying the SAME
                    # model -- but a different configured model/deployment might work, so move on
                    # to the next fallback candidate instead of failing the whole request outright.
                    break

        # last_error is last_rate_limit_error (object identity) only when the FINAL failure
        # encountered across every model tried was a rate limit -- if a later, non-rate-limit
        # failure occurred on a subsequent fallback model, last_error has moved on and this is
        # False, so the generic message below (with the real last error) is raised instead.
        if last_rate_limit_error is not None and last_error is last_rate_limit_error:
            if len(models) > 1:
                raise RuntimeError(
                    "Azure rate limit exceeded for both primary and fallback model deployments. "
                    "Please wait and retry, or reduce request concurrency."
                ) from last_rate_limit_error
            raise RuntimeError(
                "Azure rate limit exceeded for the configured model deployment. "
                "Please wait and retry, or configure AZURE_OPENAI_FALLBACK_MODEL."
            ) from last_rate_limit_error

        if last_error is not None:
            tried = ", ".join(models)
            raise RuntimeError(
                f"All configured model deployments failed (tried: {tried}). Last error: {last_error}"
            ) from last_error

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
        # strict=False tolerates raw line breaks inside string values, which models occasionally emit
        # in long free-text fields (rationales) even when asked for strict JSON.
        try:
            parsed = json.loads(text, strict=False)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < 0 or end <= start:
            raise RuntimeError("Could not parse JSON object from model output.")
        try:
            parsed = json.loads(text[start : end + 1], strict=False)
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
                        # This server always wraps its actual payload one level deeper as
                        # {"result": ...}, same as the outer envelope checked above. Tools that
                        # return a flat list (search-elements, get-views) happen to work without
                        # this because _result_list's fallback separately checks for a list under
                        # "result" -- but richer object-shaped payloads (get-view-contents'
                        # {"result": {"elements": [...], ...}}) need the dict unwrapped here too,
                        # or every caller silently sees an empty result.
                        nested_parsed = parsed.get("result")
                        if isinstance(nested_parsed, dict):
                            return nested_parsed
                        if isinstance(nested_parsed, list):
                            return {"items": nested_parsed}
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

        # This MCP server wraps every search/list tool's payload as {"result": [...]} inside
        # a text content block (never structuredContent) -- "result" (singular) must be checked
        # or every search-elements/search-relationships/get-views call silently returns nothing.
        for key in ("result", "items", "data", "results", "views", "elements", "relationships"):
            value = result.get(key)
            if isinstance(value, list):
                dict_items = [item for item in value if isinstance(item, dict)]
                if dict_items:
                    return dict_items

        # Last resort: a single list-valued field, whatever it's called.
        list_valued = [v for v in result.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
        if len(list_valued) == 1:
            return list_valued[0]

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
        """Per-bulk-mutate-call cap. This must match the Archi MCP server's own limit -- its
        bulk-mutate tool schema declares "maxItems": 150 on the operations array, so anything
        over that is rejected by the server itself, not a number we get to pick. (This used to
        say 200, which is why realistically-sized mappings -- e.g. a few dozen matched process
        pairs plus their carried-over relationships and gap elements -- would sail past the
        server's real 150 limit undetected and get rejected by Archi instead of by us.)"""
        return 150

    @staticmethod
    def _max_total_automation_ops() -> int:
        """Overall safety ceiling across ALL chunked bulk-mutate calls for one action, so a truly
        pathological input fails fast with a clear message instead of silently issuing hundreds
        of sequential MCP round-trips."""
        return 3000

    @staticmethod
    def _approval_wait_attempts() -> int:
        """How many times to re-check for a just-submitted change before giving up. Only matters
        when a plan needs more than one bulk-mutate call (see _execute_view_plan_chunked): if
        Archi's human-approval mode is on, a submitted proposal isn't applied -- and so invisible
        to reads -- until a human approves it inside Archi, which takes a few seconds for anyone
        actively watching for it."""
        return 6

    @staticmethod
    def _approval_wait_delay_seconds() -> float:
        return 3.0

    @staticmethod
    def _offset_backrefs(op: Dict[str, Any], offset: int) -> Dict[str, Any]:
        """Renumber a group's own-local $N.id back-references (0-based within that group) to
        their absolute position once the group is packed into a shared bulk-mutate call at
        `offset`. Only top-level string params are ever back-references in this codebase."""
        if not offset:
            return op
        params = op.get("params") or {}
        new_params = dict(params)
        for param_key, value in params.items():
            if isinstance(value, str):
                match = _BACKREF_RE.match(value)
                if match:
                    new_params[param_key] = f"${int(match.group(1)) + offset}.id"
        return {"tool": op["tool"], "params": new_params}

    def _run_grouped_bulk_mutate(
        self,
        *,
        groups: List[List[Dict[str, Any]]],
        action_label: str,
        source_name: str,
        used_tools: List[str],
    ) -> None:
        """Execute self-contained operation groups via bulk-mutate, packing as many groups as fit
        into each call while staying under the MCP server's per-call operation limit. A group
        (e.g. one element's create+place, or one relationship's create+connect) is never split
        across calls, so its internal back-references stay valid after renumbering to the call's
        offset -- this is what lets an arbitrarily large mapping/upload succeed via several
        sequential calls instead of failing outright the moment one call would exceed the limit."""
        per_call_limit = self._max_automation_ops()
        total_limit = self._max_total_automation_ops()
        total_ops = sum(len(g) for g in groups if g)
        if total_ops > total_limit:
            raise RuntimeError(
                f"'{action_label}' would require {total_ops} Archi operations, exceeding the safety "
                f"limit of {total_limit}. Please reduce the scope (fewer elements/relationships) and "
                "try again."
            )

        chunk: List[Dict[str, Any]] = []
        for group in groups:
            if not group:
                continue
            if len(group) > per_call_limit:
                raise RuntimeError(
                    f"A single item in '{action_label}' required {len(group)} operations, exceeding "
                    f"the MCP bulk limit of {per_call_limit} per call."
                )
            if chunk and len(chunk) + len(group) > per_call_limit:
                self._mcp_call(
                    "bulk-mutate",
                    {
                        "operations": chunk,
                        "description": f"{action_label} from {source_name}",
                        "intent": f"Batch of {action_label}",
                    },
                    used_tools,
                )
                chunk = []
            offset = len(chunk)
            chunk.extend(self._offset_backrefs(op, offset) for op in group)
        if chunk:
            self._mcp_call(
                "bulk-mutate",
                {
                    "operations": chunk,
                    "description": f"{action_label} from {source_name}",
                    "intent": f"Batch of {action_label}",
                },
                used_tools,
            )

    def _call_model_for_json(
        self, *, system_prompt: str, user_prompt: str, model_override: str | None = None
    ) -> Dict[str, Any]:
        create_kwargs: Dict[str, Any] = dict(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )
        # Three model tiers: day-to-day chat (azure_openai_model), reasoning-heavy extraction/
        # mapping/proposal work (azure_openai_extraction_model), and the assessment's final
        # synthesis step (azure_openai_summary_model, passed via model_override) -- each falls
        # back to the tier below it if unset, down to the base chat model.
        effective_model = (
            model_override
            or self.settings.azure_openai_extraction_model
            or self.settings.azure_openai_model
        )
        response, _model_used, _retries = self._call_model_with_retries(create_kwargs, primary_model=effective_model)
        content = response.choices[0].message.content or ""
        return self._extract_json_object(content)

    def _call_model_for_json_via_responses_api(
        self, *, model: str, system_prompt: str, user_prompt: str
    ) -> Dict[str, Any]:
        """Some Azure OpenAI deployments -- confirmed live for this project's gpt-5.4-pro-4 tier --
        are reasoning models only exposed via the newer Responses API (/openai/responses), not the
        Chat Completions API every other call in this file uses. The installed openai SDK (1.51.2)
        predates .responses.create() support, so this calls it directly over HTTP rather than
        bumping a core dependency for one model tier. Raises on any failure; callers are expected
        to catch and fall back to _call_model_for_json (which never uses this path)."""
        base = self.settings.azure_openai_base_url.rstrip("/")
        if base.endswith("/openai/v1"):
            responses_url = base[: -len("v1")] + "responses"
        else:
            responses_url = f"{base}/openai/responses"
        responses_url += "?api-version=2025-04-01-preview"

        response = httpx.post(
            responses_url,
            json={
                "model": model,
                "instructions": system_prompt,
                "input": user_prompt,
            },
            headers={"Content-Type": "application/json", "api-key": self.settings.azure_openai_api_key},
            timeout=self.settings.request_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()

        if data.get("status") != "completed":
            error = data.get("error") or {}
            raise RuntimeError(f"Responses API call did not complete: {error.get('message', data.get('status'))}")

        for item in data.get("output", []):
            if item.get("type") != "message":
                continue
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    return self._extract_json_object(str(block.get("text", "")))

        raise RuntimeError("Responses API returned no output text.")

    def _mcp_call(self, tool_name: str, args: Dict[str, Any], used_tools: List[str]) -> Dict[str, Any]:
        result = self.mcp.call_tool(tool_name, args)
        used_tools.append(tool_name)
        unwrapped = self._unwrap_mcp_result(result)

        # This MCP server reports tool-level failures (e.g. bulk-mutate's ArchiMate validation
        # rejecting an invalid relationship pairing) as a plain {"error": {...}} payload rather
        # than an MCP protocol error, so call_tool() never raises on its own -- without this check
        # a rejected bulk-mutate (nothing created, no proposal, no error) looks identical to a
        # real success to every caller, which is silently misreported as applied.
        error = unwrapped.get("error") if isinstance(unwrapped, dict) else None
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("code") or "MCP tool call failed")
            correction = error.get("suggestedCorrection")
            if correction:
                message = f"{message} ({correction})"
            raise RuntimeError(f"{tool_name} failed: {message}")
        if isinstance(unwrapped, dict) and unwrapped.get("allSucceeded") is False:
            raise RuntimeError(f"{tool_name} did not fully succeed: {json.dumps(unwrapped)[:500]}")

        self._record_proposal(unwrapped)
        return unwrapped

    def _record_proposal(self, unwrapped: Any) -> str | None:
        """With Archi's approval mode on, a mutating tool returns a proposal instead of applying the
        change: remember its id for the running write operation and return it."""
        proposal = unwrapped.get("proposal") if isinstance(unwrapped, dict) else None
        if not (isinstance(proposal, dict) and proposal.get("proposalId")):
            return None
        proposal_id = str(proposal["proposalId"])
        sink = getattr(self._proposal_local, "ids", None)
        if sink is not None:
            sink.append(proposal_id)
        return proposal_id

    @contextmanager
    def _collect_proposals(self):
        """Collect approval-proposal ids of this request's bulk-mutate calls; nested write operations
        share the outermost collector."""
        existing = getattr(self._proposal_local, "ids", None)
        if existing is not None:
            yield existing
            return
        self._proposal_local.ids = []
        try:
            yield self._proposal_local.ids
        finally:
            self._proposal_local.ids = None

    def _current_proposals(self) -> List[str]:
        return list(getattr(self._proposal_local, "ids", None) or [])

    @staticmethod
    def approval_note(proposals: List[str] | None) -> str:
        """Only when Archi actually queued the change: approval mode is on for this write."""
        if not proposals:
            return ""
        return (
            f" Archi approval mode is on: the change is waiting as proposal {', '.join(proposals)}. "
            "Approve it in Archi (MCP Server > Pending approvals) to apply it."
        )

    def _find_existing_view_id(
        self,
        *,
        view_name: str,
        used_tools: List[str],
    ) -> str | None:
        # Archi trims a view's name on creation, so a caller-supplied name with incidental
        # leading/trailing whitespace (e.g. a stray space typed into a Setup field) would never
        # match the stored (trimmed) name -- reporting "no such view" even though it genuinely
        # exists. Worse, the MCP server's own get-views name filter appears to require the query
        # to be a substring of the stored name, so a trailing space on the query alone is enough
        # to make the real view never even come back as a candidate -- it must be stripped before
        # the search, not just before the comparison below.
        normalized_view_name = view_name.strip().lower()
        views_result = self._mcp_call("get-views", {"name": view_name.strip(), "limit": 200}, used_tools)
        views = self._result_list(views_result, "views")
        for view in views:
            existing_name = str(view.get("name", "")).strip()
            if existing_name.lower() == normalized_view_name:
                view_id = str(view.get("id", view.get("viewId", ""))).strip()
                if view_id:
                    return view_id
        return None

    def _list_all(self, tool_name: str, args: Dict[str, Any], used_tools: List[str]) -> List[Dict[str, Any]]:
        """Every item of a paginated search tool. The plugin pages at 500 items: reading only the first
        page misses existing elements/relationships in larger models, which are then created twice."""
        used_tools.append(tool_name)
        return [item for item in dashboard._fetch_all(self.mcp, tool_name, args) if isinstance(item, dict)]

    def _collect_existing_elements(self, used_tools: List[str]) -> Dict[tuple[str, str], str]:
        elements = self._list_all("search-elements", {"query": "", "exclude": ["documentation"]}, used_tools)
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
        relationships = self._list_all("search-relationships", {"query": "", "exclude": ["documentation"]}, used_tools)
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

    def _collect_view_business_processes(self, *, view_id: str, used_tools: List[str]) -> List[Dict[str, str]]:
        """BusinessProcess elements actually placed on a specific view -- NOT every BusinessProcess
        in the model. Searching the whole model (as this used to) mixes every assessment cycle ever
        run, plus any unrelated BusinessProcess elements the user's Archi model already contained,
        into every mapping/proposal run. Scoping to the view the user actually configured in Setup
        is what makes a fresh view name in a new cycle actually behave like a fresh cycle."""
        result = self._mcp_call("get-view-contents", {"viewId": view_id}, used_tools)
        out: List[Dict[str, str]] = []
        seen_ids: set[str] = set()
        for item in self._result_list(result, "elements"):
            if str(item.get("type", "")).strip() != "BusinessProcess":
                continue
            element_id = str(item.get("id", "")).strip()
            name = str(item.get("name", "")).strip()
            if not element_id or not name or element_id in seen_ids:
                continue
            seen_ids.add(element_id)
            out.append({"id": element_id, "name": name})
        return out

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
        """Builds a flat, single-bulk-mutate-call plan (exactly the original, proven approach --
        one proposal, resolved entirely server-side) whenever it fits in one call. Archi's
        optional human-approval mode treats one bulk-mutate call as one all-or-nothing proposal
        that a person approves inside Archi; the client never needs to know real ids while that's
        pending, since back-references resolve server-side as part of applying that one proposal.
        Only a plan too large for one call falls back to _execute_view_plan_chunked, which must
        peek at real ids between calls and therefore has to tolerate an approval-mode delay."""
        used_tools: List[str] = []
        existing_view_id = self._find_existing_view_id(view_name=view_name, used_tools=used_tools)
        existing_elements = self._collect_existing_elements(used_tools)
        existing_relationships = self._collect_existing_relationships(used_tools)
        if existing_view_id:
            view_objects, existing_visual_relationships = self._collect_view_state(view_id=existing_view_id, used_tools=used_tools)
        else:
            view_objects, existing_visual_relationships = {}, set()

        operations, counts = self._build_flat_view_operations(
            view_name=view_name,
            existing_view_id=existing_view_id,
            elements_by_key=elements_by_key,
            relationships=relationships,
            layout_positions=layout_positions,
            existing_elements=existing_elements,
            existing_relationships=existing_relationships,
            view_objects=view_objects,
            existing_visual_relationships=existing_visual_relationships,
        )

        if len(operations) <= self._max_automation_ops():
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
                **counts,
                "used_tools": used_tools,
            }

        return self._execute_view_plan_chunked(
            action_label=action_label,
            source_name=source_name,
            view_name=view_name,
            elements_by_key=elements_by_key,
            relationships=relationships,
            layout_positions=layout_positions,
            used_tools=used_tools,
            existing_view_id=existing_view_id,
            existing_elements=existing_elements,
            existing_relationships=existing_relationships,
            view_objects=view_objects,
            existing_visual_relationships=existing_visual_relationships,
        )

    def _build_flat_view_operations(
        self,
        *,
        view_name: str,
        existing_view_id: str | None,
        elements_by_key: Dict[str, Dict[str, str]],
        relationships: List[Dict[str, str]],
        layout_positions: Dict[str, Dict[str, int]] | None,
        existing_elements: Dict[tuple[str, str], str],
        existing_relationships: Dict[tuple[str, str, str], str],
        view_objects: Dict[str, str],
        existing_visual_relationships: set[str],
    ) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
        """Original single-call plan builder: one flat operations list using $N.id
        back-references throughout, meant to be sent as one bulk-mutate call."""
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
            for key, pos in layout_positions.items():
                existing_id = existing_element_id_for_key.get(key)
                if not existing_id:
                    continue
                view_object_id = view_objects.get(existing_id)
                if not view_object_id:
                    continue
                # There is no bulk "apply many positions in one op" tool -- update-view-object
                # repositions exactly one view object per call, so this is genuinely one
                # operation per pre-existing element being repositioned.
                operations.append(
                    {
                        "tool": "update-view-object",
                        "params": {
                            "viewObjectId": view_object_id,
                            "x": int(pos.get("x", 0)),
                            "y": int(pos.get("y", 0)),
                            "width": int(pos.get("width", 180)),
                            "height": int(pos.get("height", 70)),
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

        counts = {
            "created_elements": created_elements,
            "reused_elements": len(existing_element_id_for_key),
            "created_relationships": created_relationships,
            "added_to_view": added_to_view,
            "added_connections": added_connections,
        }
        return operations, counts

    def _execute_view_plan_chunked(
        self,
        *,
        action_label: str,
        source_name: str,
        view_name: str,
        elements_by_key: Dict[str, Dict[str, str]],
        relationships: List[Dict[str, str]],
        layout_positions: Dict[str, Dict[str, int]] | None,
        used_tools: List[str],
        existing_view_id: str | None,
        existing_elements: Dict[tuple[str, str], str],
        existing_relationships: Dict[tuple[str, str, str], str],
        view_objects: Dict[str, str],
        existing_visual_relationships: set[str],
    ) -> Dict[str, Any]:
        """Fallback for plans too large for one bulk-mutate call (see _execute_view_plan): builds
        elements, then relationships, as separate chunked rounds via _run_grouped_bulk_mutate,
        resolving real ids between rounds since bulk-mutate back-references cannot cross calls.
        If Archi's human-approval mode is on, each round's proposal must actually be approved
        before the next round's lookups can see it -- so real-id resolution here polls briefly
        rather than checking once, and fails with an actionable message (not a generic error) if
        nothing was approved in time. A failure partway through leaves earlier rounds applied;
        re-running the same plan is safe and resumes from there, since every step below looks up
        "does this already exist" before creating anything."""
        created_elements = 0
        created_relationships = 0
        added_to_view = 0
        added_connections = 0
        attempts = self._approval_wait_attempts()
        delay = self._approval_wait_delay_seconds()

        # --- Phase 1: the view itself must be a real id before anything below can reference it,
        # since it may end up in a different bulk-mutate call than whatever places elements on it. ---
        view_id = existing_view_id
        if not view_id:
            self._run_grouped_bulk_mutate(
                groups=[[{"tool": "create-view", "params": {"name": view_name}}]],
                action_label=action_label,
                source_name=source_name,
                used_tools=used_tools,
            )
            for attempt in range(attempts):
                view_id = self._find_existing_view_id(view_name=view_name, used_tools=used_tools)
                if view_id:
                    break
                if attempt < attempts - 1:
                    time.sleep(delay)
            if not view_id:
                raise RuntimeError(
                    f"Archi hasn't confirmed the new view '{view_name}' yet. If Archi's human-approval "
                    f"mode is on, switch to Archi, approve the pending '{action_label}' change, then "
                    "try again -- it will safely continue from here."
                )

        # --- Phase 2: create every missing element and place every element on the view. Each
        # element is its own self-contained group (create + place, local back-reference), so
        # groups can be freely packed into calls of up to the MCP bulk limit regardless of how
        # many elements there are in total. ---
        existing_element_id_for_key: Dict[str, str] = {}
        created_this_run: set[str] = set()
        element_groups: List[List[Dict[str, Any]]] = []

        for key, item in elements_by_key.items():
            element_type = item["type"]
            element_name = item["name"]
            found_id = existing_elements.get((self._norm_key(element_type), self._norm_key(element_name)))
            pos = (layout_positions or {}).get(key, {})
            if found_id:
                existing_element_id_for_key[key] = found_id
                if found_id in view_objects:
                    continue
                element_groups.append(
                    [
                        {
                            "tool": "add-to-view",
                            "params": {
                                "viewId": view_id,
                                "elementId": found_id,
                                "x": int(pos.get("x", 0)),
                                "y": int(pos.get("y", 0)),
                                "width": int(pos.get("width", 180)),
                                "height": int(pos.get("height", 70)),
                                "autoSize": True,
                            },
                        }
                    ]
                )
                added_to_view += 1
                continue

            created_this_run.add(key)
            create_params: Dict[str, Any] = {
                "type": element_type,
                "name": element_name,
                "documentation": item.get("documentation", ""),
            }
            properties = item.get("properties")
            if properties:
                create_params["properties"] = properties
            element_groups.append(
                [
                    {"tool": "create-element", "params": create_params},
                    {
                        "tool": "add-to-view",
                        "params": {
                            "viewId": view_id,
                            "elementId": "$0.id",
                            "x": int(pos.get("x", 0)),
                            "y": int(pos.get("y", 0)),
                            "width": int(pos.get("width", 180)),
                            "height": int(pos.get("height", 70)),
                            "autoSize": True,
                        },
                    },
                ]
            )
            created_elements += 1
            added_to_view += 1

        # Repositioning for elements that were already on the view before this run -- uses the
        # pre-run view_objects snapshot deliberately, since it's only concerned with objects that
        # predate anything this run just placed.
        if layout_positions:
            for key, pos in layout_positions.items():
                existing_id = existing_element_id_for_key.get(key)
                if not existing_id:
                    continue
                view_object_id = view_objects.get(existing_id)
                if not view_object_id:
                    continue
                # There is no bulk "apply many positions in one op" tool -- update-view-object
                # repositions exactly one view object per call, so each pre-existing element
                # being repositioned is its own independent (backref-free) group.
                element_groups.append(
                    [
                        {
                            "tool": "update-view-object",
                            "params": {
                                "viewObjectId": view_object_id,
                                "x": int(pos.get("x", 0)),
                                "y": int(pos.get("y", 0)),
                                "width": int(pos.get("width", 180)),
                                "height": int(pos.get("height", 70)),
                            },
                        }
                    ]
                )

        if element_groups:
            self._run_grouped_bulk_mutate(
                groups=element_groups,
                action_label=action_label,
                source_name=source_name,
                used_tools=used_tools,
            )

        # --- Phase 3: re-read real ids, now that every element (including ones created just
        # above) should exist and be placed -- so relationships can reference them directly
        # instead of needing cross-call back-references, which bulk-mutate cannot resolve. Poll
        # briefly for approval-mode; this step is best-effort -- any key that still doesn't
        # resolve just means relationships touching it get skipped below, same as this function
        # has always done for any reference it can't resolve. ---
        if created_this_run:
            for attempt in range(attempts):
                existing_elements = self._collect_existing_elements(used_tools)
                still_missing = False
                for key in created_this_run:
                    if key in existing_element_id_for_key:
                        continue
                    item = elements_by_key[key]
                    found_id = existing_elements.get((self._norm_key(item["type"]), self._norm_key(item["name"])))
                    if found_id:
                        existing_element_id_for_key[key] = found_id
                    else:
                        still_missing = True
                if not still_missing:
                    break
                if attempt < attempts - 1:
                    time.sleep(delay)
        if element_groups:
            view_objects, existing_visual_relationships = self._collect_view_state(view_id=view_id, used_tools=used_tools)

        # --- Phase 4: create every missing relationship and connect every relationship on the
        # view. Each relationship is its own self-contained group (create + connect, local
        # back-reference), same packing story as phase 2. ---
        relationship_groups: List[List[Dict[str, Any]]] = []
        for rel in relationships:
            rel_type = rel["type"]
            source_key = rel["source_key"]
            target_key = rel["target_key"]

            source_ref = existing_element_id_for_key.get(source_key)
            target_ref = existing_element_id_for_key.get(target_key)
            if not source_ref or not target_ref:
                continue

            existing_rel_id = None
            if source_key not in created_this_run and target_key not in created_this_run:
                existing_rel_id = existing_relationships.get((self._norm_key(rel_type), str(source_ref), str(target_ref)))

            source_view_ref = view_objects.get(source_ref)
            target_view_ref = view_objects.get(target_ref)
            if not source_view_ref or not target_view_ref:
                continue

            if existing_rel_id:
                if existing_rel_id in existing_visual_relationships:
                    continue
                relationship_groups.append(
                    [
                        {
                            "tool": "add-connection-to-view",
                            "params": {
                                "viewId": view_id,
                                "relationshipId": existing_rel_id,
                                "sourceViewObjectId": source_view_ref,
                                "targetViewObjectId": target_view_ref,
                            },
                        }
                    ]
                )
                added_connections += 1
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
                relationship_groups.append(
                    [
                        {"tool": "create-relationship", "params": params},
                        {
                            "tool": "add-connection-to-view",
                            "params": {
                                "viewId": view_id,
                                "relationshipId": "$0.id",
                                "sourceViewObjectId": source_view_ref,
                                "targetViewObjectId": target_view_ref,
                            },
                        },
                    ]
                )
                created_relationships += 1
                added_connections += 1

        if relationship_groups:
            self._run_grouped_bulk_mutate(
                groups=relationship_groups,
                action_label=action_label,
                source_name=source_name,
                used_tools=used_tools,
            )

        return {
            "view_name": view_name,
            "view_id": view_id,
            "created_elements": created_elements,
            "reused_elements": len(existing_element_id_for_key),
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

    @_tracks_proposals
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

        def _left_margin_pct(box: Dict[str, Any]) -> int | None:
            page_width = box.get("page_width")
            if not page_width:
                return None
            return round(box["bbox"][0] / page_width * 100)

        box_lines = "\n".join(
            f"{box['id']}: {box['text']}"
            + (f" [left-margin: {pct}%]" if (pct := _left_margin_pct(box)) is not None else "")
            for box in boxes
        )
        user_prompt = (
            "Each line below is a box extracted directly from a diagram's vector geometry, given as "
            '"<id>: <exact text>", optionally followed by how far its left edge sits from the page\'s '
            "left margin as a percentage of page width. Classify the ROLE of each box. Do not rename, "
            "translate, shorten, or alter the text in any way -- it is used verbatim regardless of your "
            "classification.\n"
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
            "- A swimlane/row label sits in a narrow strip flush against the page's left margin (a low "
            "left-margin percentage, clustered together with other row labels at roughly the same value) "
            "and is repeated once per row purely to name that row/department. A box with the SAME or "
            "similar wording that sits further right (a meaningfully larger left-margin percentage, in the "
            "diagram's main content area) is a real activity or deliverable, not a restated lane name -- "
            "classify it as process/object on its own merits even if it echoes its row's label (e.g. a row "
            "labeled 'Master Layout' legitimately contains a real task box also called 'MAIN LAYOUT' further "
            "into the content area; only the narrow left-margin one is the lane label to ignore).\n"
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

        # Reading order (top-to-bottom lanes, left-to-right within a lane) rather than raw PDF
        # coordinates: real diagrams are often drawn tightly packed or with swimlane offsets that,
        # copied 1:1, produce an unreadable/overlapping layout in Archi and in the preview.
        def reading_order_key(box: Dict[str, Any]) -> tuple[float, float]:
            bx0, by0 = box["bbox"][0], box["bbox"][1]
            return (round(by0 / 80.0), bx0)

        ordered_boxes = sorted(boxes, key=reading_order_key)
        process_box_ids = [box["id"] for box in ordered_boxes if role_by_id.get(box["id"]) == "process"]
        accepted_process_ids = set(process_box_ids[:max_steps])
        truncated = len(process_box_ids) > len(accepted_process_ids)

        elements: Dict[str, Dict[str, str]] = {}
        key_by_box_id: Dict[int, str] = {}
        ordered_process_keys: List[str] = []

        for box in ordered_boxes:
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
            is_new = key not in elements
            elements.setdefault(key, {"type": element_type, "name": name})
            key_by_box_id[box["id"]] = key
            if role == "process" and is_new:
                ordered_process_keys.append(key)

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

        detected_connection_count = len(relationships)
        sequence_inferred = False
        if not any(r["type"] == "TriggeringRelationship" for r in relationships) and len(ordered_process_keys) > 1:
            # No process-to-process arrows were geometrically detected (a drawing style my heuristics
            # didn't recognize, or the diagram genuinely has none) -- fall back to the document's
            # reading order rather than leaving every step disconnected.
            sequence_inferred = True
            for idx in range(1, len(ordered_process_keys)):
                source_key = ordered_process_keys[idx - 1]
                target_key = ordered_process_keys[idx]
                pair_key = ("TriggeringRelationship", source_key, target_key)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                relationships.append({"type": "TriggeringRelationship", "source_key": source_key, "target_key": target_key})

        # Preserve the PDF's own swimlane/table arrangement instead of reshaping into an
        # arbitrary sqrt grid: every accepted element still has the real box it came from, so
        # cluster those boxes back into rows (lanes) and columns (phase/time groups) by position
        # -- elements that lined up vertically or horizontally in the source diagram still line
        # up here. Pages are laid out as separate stacked blocks since raw PDF coordinates reset
        # per page and aren't comparable across pages.
        box_by_key: Dict[str, Dict[str, Any]] = {}
        for box in ordered_boxes:
            key = key_by_box_id.get(box["id"])
            if key:
                box_by_key.setdefault(key, box)

        def cluster_axis(pairs: List[tuple[str, float]], *, gap_threshold: float) -> Dict[str, int]:
            cluster_by_key: Dict[str, int] = {}
            cluster = 0
            prev_value: float | None = None
            for key, value in sorted(pairs, key=lambda pair: pair[1]):
                if prev_value is not None and (value - prev_value) > gap_threshold:
                    cluster += 1
                cluster_by_key[key] = cluster
                prev_value = value
            return cluster_by_key

        start_x = 100
        col_gap = 260
        # Sequential gap-threshold clustering can occasionally chain two visually-distinct
        # columns together through a run of borderline gaps, landing more than one element in
        # the same (row, col) cell; those get stacked vertically within the cell below. row_gap
        # must comfortably fit a few stacked levels (each box_height + 16) or a stacked box can
        # bleed down into the next row's boxes.
        row_gap = 340
        page_gap_rows = 1
        box_width, box_height = 220, 80

        layout_positions: Dict[str, Dict[str, int]] = {}
        y_offset = 110
        pages_present = sorted({box["page"] for box in box_by_key.values()})
        for page_no in pages_present:
            page_keys = [key for key in box_by_key if box_by_key[key]["page"] == page_no]
            page_boxes = [box_by_key[key] for key in page_keys]
            heights = sorted(b["bbox"][3] - b["bbox"][1] for b in page_boxes)
            widths = sorted(b["bbox"][2] - b["bbox"][0] for b in page_boxes)
            row_threshold = max(20.0, heights[len(heights) // 2] * 1.8)
            col_threshold = max(20.0, widths[len(widths) // 2] * 1.5)

            row_of_key = cluster_axis([(key, box_by_key[key]["bbox"][1]) for key in page_keys], gap_threshold=row_threshold)
            col_of_key = cluster_axis([(key, box_by_key[key]["bbox"][0]) for key in page_keys], gap_threshold=col_threshold)

            occupied: Dict[tuple[int, int], int] = {}
            max_row = 0
            for key in page_keys:
                row, col = row_of_key[key], col_of_key[key]
                max_row = max(max_row, row)
                cell = (row, col)
                stack_index = occupied.get(cell, 0)
                occupied[cell] = stack_index + 1
                layout_positions[key] = {
                    "x": start_x + col * col_gap,
                    "y": y_offset + row * row_gap + stack_index * (box_height + 16),
                    "width": box_width,
                    "height": box_height,
                }

            y_offset += (max_row + 1 + page_gap_rows) * row_gap

        resolved_view_name = self._normalize_name(parsed.get("view_name", preferred_view_name), fallback=preferred_view_name)
        process_count = sum(1 for item in elements.values() if item["type"] == "BusinessProcess")
        warnings = [
            f"Extracted directly from the PDF's drawn shapes: {len(boxes)} boxes and {detected_connection_count} "
            "connections detected from real lines/arrows in the document (not inferred from text order)."
        ]
        if sequence_inferred:
            warnings.append(
                "No process-to-process arrows could be geometrically detected in this PDF, so steps were "
                "connected in the document's reading order (left-to-right, top-to-bottom) instead. Review the "
                "preview and adjust the flow in Archi if this doesn't match the real process."
            )
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

    def _extract_business_process_plan_from_sipoc(
        self,
        *,
        sipoc: Dict[str, Any],
        source_name: str,
        view_name: str | None = None,
    ) -> Dict[str, Any] | None:
        """Build a plan straight from a SIPOC table's step column (ID/Process Step/Supplier/
        Input/Process/Output/Customer rows), rather than the diagram box/arrow heuristics.

        A SIPOC table names each step in its own "Process Step" column and lists steps in a
        single sequential column -- there is no ambiguity to resolve with an LLM classification
        pass, and the row order itself IS the process chain, so this connects consecutive steps
        directly instead of asking a model to infer sequence.
        """
        raw_steps = sipoc.get("steps") or []
        if not raw_steps:
            return None

        preferred_view_name = self._normalize_name(view_name or "Business Process View", fallback="Business Process View")
        max_steps = max(1, self.settings.max_action_steps)
        truncated = len(raw_steps) > max_steps

        elements: Dict[str, Dict[str, str]] = {}
        relationships: List[Dict[str, str]] = []
        ordered_keys: List[str] = []

        for raw_step in raw_steps[:max_steps]:
            name = self._normalize_name(raw_step.get("name", ""), fallback="")
            if not name:
                continue
            key = f"BusinessProcess::{self._norm_key(name)}"
            if key in elements:
                # Same step name repeated (e.g. a continuation header row slipped through) --
                # keep the chain moving without creating a duplicate node.
                if not ordered_keys or ordered_keys[-1] != key:
                    ordered_keys.append(key)
                continue

            doc_parts: List[str] = []
            step_id = raw_step.get("id", "")
            if step_id:
                doc_parts.append(f"ID: {step_id}")
            for label, field in (("Supplier", "supplier"), ("Input", "input"), ("Process", "process"), ("Output", "output"), ("Customer", "customer")):
                value = raw_step.get(field, "")
                if value:
                    doc_parts.append(f"{label}: {value}")
            documentation = self._normalize_name(" | ".join(doc_parts), fallback="", max_len=400)

            elements[key] = {"type": "BusinessProcess", "name": name}
            if documentation:
                elements[key]["documentation"] = documentation
            ordered_keys.append(key)

        if not ordered_keys:
            return None

        seen_pairs: set[tuple[str, str]] = set()
        for idx in range(1, len(ordered_keys)):
            source_key, target_key = ordered_keys[idx - 1], ordered_keys[idx]
            if source_key == target_key or (source_key, target_key) in seen_pairs:
                continue
            seen_pairs.add((source_key, target_key))
            relationships.append({"type": "TriggeringRelationship", "source_key": source_key, "target_key": target_key})

        # A SIPOC table is inherently one linear chain, not a 2D layout -- wrap it into readable
        # rows rather than a single very wide line.
        start_x = 100
        col_gap = 260
        row_gap = 220
        box_width, box_height = 220, 80
        columns_per_row = max(1, math.ceil(math.sqrt(max(1, len(ordered_keys)))))

        layout_positions: Dict[str, Dict[str, int]] = {}
        for index, key in enumerate(ordered_keys):
            col = index % columns_per_row
            row = index // columns_per_row
            layout_positions[key] = {
                "x": start_x + col * col_gap,
                "y": 110 + row * row_gap,
                "width": box_width,
                "height": box_height,
            }

        warnings = [
            f"Extracted directly from a SIPOC table's Process Step column: {len(ordered_keys)} step(s) "
            "chained in the table's own row order."
        ]
        if truncated:
            warnings.append(f"Process steps were truncated to the configured maximum of {max_steps}.")

        return {
            "view_name": preferred_view_name,
            "elements": elements,
            "relationships": relationships,
            "layout_positions": layout_positions,
            "steps_processed": len(ordered_keys),
            "steps_truncated": truncated,
            "warnings": warnings,
        }

    @staticmethod
    def _looks_like_sipoc_text(content_text: str) -> bool:
        normalized = content_text.lower()
        return "process step" in normalized or "prozessschritt" in normalized

    def _extract_business_process_plan_from_sipoc_llm(
        self,
        *,
        content_text: str,
        source_name: str,
        view_name: str | None = None,
    ) -> Dict[str, Any] | None:
        """Constrained LLM fallback for SIPOC documents whose detailed table isn't a PyMuPDF-
        recognizable ruled grid (extract_sipoc_steps returned None), but whose flattened text
        clearly contains a "Process Step" column heading. Deliberately narrow and rule-bound --
        ID + Process Step only, strict top-to-bottom sequential chaining, no semantic inference,
        every name evidence-checked against the source text -- rather than the open-ended
        extraction that risks hallucinating names or connections.
        """
        preferred_view_name = self._normalize_name(view_name or "Business Process View", fallback="Business Process View")
        user_prompt = (
            "Extract the as-is process steps from a SIPOC (Supplier-Input-Process-Output-Customer) "
            "document's DETAILED table only.\n\n"
            "The input may contain a SIPOC overview section and a detailed SIPOC table. Only use the "
            "detailed table, which lists one row per process step with columns similar to: ID, Process "
            "Step, Supplier, Input, Process, Output, Customer.\n\n"
            "Relevant columns: ID, Process Step.\n"
            "Columns that must NOT be used to name a process step: Supplier, Input, Process, Output, "
            "Customer.\n"
            "The 'Process Step' column contains the short step name. The 'Process' column contains a "
            "longer activity description and must never be used as the step name.\n\n"
            "Extraction rules:\n"
            "1. Extract each row of the detailed SIPOC table.\n"
            "2. Preserve the exact top-to-bottom row order, including across a page break.\n"
            "3. Copy the Process Step value verbatim -- do not rename, translate, shorten, or enrich it.\n"
            "4. Do not create process steps from the SIPOC overview section or from the Process column.\n"
            "5. Do not infer additional process steps that are not literally a row in the table.\n"
            "6. If an ID is missing for a row, use \"missing\". If a process step name is missing, skip "
            "that row.\n\n"
            "Connections: connect ONLY consecutive rows in table order (step 1 to 2, 2 to 3, ...). Never "
            "infer cross-connections, loops, or connections based on semantic meaning or on Supplier/"
            "Input/Process/Output/Customer values -- the caller builds the sequential chain from your row "
            "order, so just return the rows in order.\n\n"
            "Return strict JSON only with this schema:\n"
            "{\n"
            '  "process_steps": [{"order": 1, "id": "A1", "process_step": "string"}],\n'
            '  "validation_notes": ["string"]\n'
            "}\n"
            "If no detailed SIPOC table with a Process Step column is present at all, return "
            '{"process_steps": [], "validation_notes": ["no detailed SIPOC table found"]}.\n\n'
            f"Input:\n{content_text[: self.settings.max_upload_text_chars]}"
        )
        parsed = self._call_model_for_json(
            system_prompt=(
                "You are a strict data-extraction engine for SIPOC process tables. Extract only what is "
                "literally present in the Process Step column, in table order. Never invent, infer, "
                "translate, or reorder steps."
            ),
            user_prompt=user_prompt,
        )

        raw_steps = parsed.get("process_steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            return None

        max_steps = max(1, self.settings.max_action_steps)
        truncated = len(raw_steps) > max_steps
        source_text = content_text[: self.settings.max_upload_text_chars]

        elements: Dict[str, Dict[str, str]] = {}
        ordered_keys: List[str] = []
        discarded_low_evidence = 0
        for raw_step in raw_steps[:max_steps]:
            if not isinstance(raw_step, dict):
                continue
            name = self._normalize_name(raw_step.get("process_step", ""), fallback="")
            if not name:
                continue
            if not self._has_text_evidence(name, source_text):
                discarded_low_evidence += 1
                continue
            key = f"BusinessProcess::{self._norm_key(name)}"
            if key not in elements:
                elements[key] = {"type": "BusinessProcess", "name": name}
                step_id = raw_step.get("id")
                if step_id and step_id != "missing":
                    elements[key]["documentation"] = f"ID: {step_id}"
            if not ordered_keys or ordered_keys[-1] != key:
                ordered_keys.append(key)

        if not ordered_keys:
            return None

        relationships: List[Dict[str, str]] = []
        seen_pairs: set[tuple[str, str]] = set()
        for idx in range(1, len(ordered_keys)):
            source_key, target_key = ordered_keys[idx - 1], ordered_keys[idx]
            if source_key == target_key or (source_key, target_key) in seen_pairs:
                continue
            seen_pairs.add((source_key, target_key))
            relationships.append({"type": "TriggeringRelationship", "source_key": source_key, "target_key": target_key})

        start_x = 100
        col_gap = 260
        row_gap = 220
        box_width, box_height = 220, 80
        columns_per_row = max(1, math.ceil(math.sqrt(max(1, len(ordered_keys)))))
        layout_positions: Dict[str, Dict[str, int]] = {}
        for index, key in enumerate(ordered_keys):
            col = index % columns_per_row
            row = index // columns_per_row
            layout_positions[key] = {
                "x": start_x + col * col_gap,
                "y": 110 + row * row_gap,
                "width": box_width,
                "height": box_height,
            }

        warnings = [
            "No ruled SIPOC table could be parsed directly from the PDF's structure; used a constrained "
            f"extraction restricted to the Process Step column instead: {len(ordered_keys)} step(s) "
            "chained in document order."
        ]
        if discarded_low_evidence:
            warnings.append(f"Discarded {discarded_low_evidence} step candidate(s) not found verbatim in the source text.")
        validation_notes = parsed.get("validation_notes")
        if isinstance(validation_notes, list):
            warnings.extend(str(note) for note in validation_notes if note)
        if truncated:
            warnings.append(f"Process steps were truncated to the configured maximum of {max_steps}.")

        return {
            "view_name": preferred_view_name,
            "elements": elements,
            "relationships": relationships,
            "layout_positions": layout_positions,
            "steps_processed": len(ordered_keys),
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
                sipoc = pdf_table.extract_sipoc_steps(pdf_bytes)
            except Exception:  # noqa: BLE001
                sipoc = None
            if sipoc:
                sipoc_plan = self._extract_business_process_plan_from_sipoc(
                    sipoc=sipoc, source_name=source_name, view_name=view_name
                )
                if sipoc_plan:
                    return sipoc_plan

            if self._looks_like_sipoc_text(content_text):
                # The PDF's structure didn't give PyMuPDF a ruled grid to detect (e.g. a
                # borderless or non-standard table render), but the text unambiguously names a
                # "Process Step" column -- worth a narrow, rule-bound extraction pass rather than
                # falling straight to the diagram box/arrow heuristics, which mangle table rows.
                try:
                    sipoc_llm_plan = self._extract_business_process_plan_from_sipoc_llm(
                        content_text=content_text, source_name=source_name, view_name=view_name
                    )
                except Exception:  # noqa: BLE001
                    sipoc_llm_plan = None
                if sipoc_llm_plan:
                    return sipoc_llm_plan

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

    @staticmethod
    def _first_last_business_process_keys(elements: Dict[str, Dict[str, str]]) -> tuple[str | None, str | None]:
        process_keys = [key for key, item in elements.items() if item.get("type") == "BusinessProcess"]
        if not process_keys:
            return None, None
        return process_keys[0], process_keys[-1]

    def _merge_extraction_plans(
        self,
        *,
        plans: List[Dict[str, Any]],
        source_labels: List[str],
        view_name: str,
    ) -> Dict[str, Any]:
        """Merge one extraction plan per uploaded file, in the caller-specified order, into a
        single connected plan: each file keeps its own (already well-laid-out) internal structure,
        stacked top to bottom in order, with the previous file's last process chained via
        TriggeringRelationship to the next file's first process -- so multiple documents that
        together describe one end-to-end process actually read as one chain, not N disconnected
        islands. A single-file call degenerates to just that file's plan, tagged with its source."""
        if len(plans) == 1:
            solo = plans[0]
            tagged_elements = {
                key: {**item, "documentation": f"[{source_labels[0]}] {item.get('documentation', '')}".strip()}
                for key, item in solo["elements"].items()
            }
            return {**solo, "view_name": view_name or solo["view_name"], "elements": tagged_elements}

        elements: Dict[str, Dict[str, str]] = {}
        relationships: List[Dict[str, str]] = []
        layout_positions: Dict[str, Dict[str, int]] = {}
        seen_relationships: set[tuple[str, str, str]] = set()
        warnings: List[str] = []
        total_steps = 0
        any_truncated = False
        previous_last_key: str | None = None
        row_gap_between_files = 260
        y_offset = 0

        for plan, label in zip(plans, source_labels):
            for key, item in plan["elements"].items():
                doc = str(item.get("documentation", "")).strip()
                if key in elements:
                    existing_doc = str(elements[key].get("documentation", ""))
                    if label not in existing_doc:
                        elements[key]["documentation"] = f"{existing_doc} | Also in: {label}".strip(" |")
                    continue
                elements[key] = {**item, "documentation": f"[{label}] {doc}".strip()}

            for rel in plan["relationships"]:
                rel_tuple = (rel["type"], rel["source_key"], rel["target_key"])
                if rel_tuple in seen_relationships:
                    continue
                seen_relationships.add(rel_tuple)
                relationships.append(rel)

            file_positions = plan.get("layout_positions") or {}
            max_extent = y_offset
            for key, pos in file_positions.items():
                shifted = {**pos, "y": int(pos.get("y", 0)) + y_offset}
                layout_positions.setdefault(key, shifted)
                max_extent = max(max_extent, shifted["y"] + int(pos.get("height", 80)))
            if file_positions:
                y_offset = max_extent + row_gap_between_files

            total_steps += int(plan.get("steps_processed", 0))
            if plan.get("steps_truncated"):
                any_truncated = True
            for warning in plan.get("warnings", []):
                warnings.append(f"[{label}] {warning}")

            first_key, last_key = self._first_last_business_process_keys(plan["elements"])
            if previous_last_key and first_key:
                rel_tuple = ("TriggeringRelationship", previous_last_key, first_key)
                if rel_tuple not in seen_relationships:
                    seen_relationships.add(rel_tuple)
                    relationships.append(
                        {"type": "TriggeringRelationship", "source_key": previous_last_key, "target_key": first_key}
                    )
            if last_key:
                previous_last_key = last_key

        warnings.insert(
            0,
            f"Merged {len(plans)} files in this order: {', '.join(source_labels)}. Each file's last "
            "process was chained to the next file's first process -- review the relationships table "
            "and adjust if that doesn't match the real flow.",
        )

        return {
            "view_name": view_name or "Business Process View",
            "elements": elements,
            "relationships": relationships,
            "layout_positions": layout_positions,
            "steps_processed": total_steps,
            "steps_truncated": any_truncated,
            "warnings": warnings,
        }

    def plan_business_process_automation(
        self,
        *,
        uploads: List[Tuple[str, str, bytes | None]],
        view_name: str | None = None,
    ) -> Dict[str, Any]:
        """uploads is an ORDERED list of (content_text, source_name, pdf_bytes) -- one per file, in
        the sequence the user arranged them in (e.g. via the drag-drop reorder list in the wizard).
        A single upload behaves exactly as before; multiple uploads are extracted independently
        (each gets its best-available extraction method) and then chained together in order."""
        if not uploads:
            raise RuntimeError("No files were provided.")
        plans = [
            self._extract_business_process_plan(
                content_text=content_text, source_name=source_name, view_name=view_name, pdf_bytes=pdf_bytes
            )
            for content_text, source_name, pdf_bytes in uploads
        ]
        source_labels = [source_name for _content_text, source_name, _pdf_bytes in uploads]
        merged = self._merge_extraction_plans(
            plans=plans, source_labels=source_labels, view_name=view_name or plans[0]["view_name"]
        )
        combined_source_name = source_labels[0] if len(source_labels) == 1 else f"{len(source_labels)} files"
        return self._format_plan_response(
            action="business-process-upload", source_name=combined_source_name, extracted=merged
        )

    @_tracks_proposals
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

    @_tracks_proposals
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
    # Setup -> As-Is Capture -> To-Be Architecture -> Mapping & Gap Analysis -> Summary.
    # As-Is Capture reuses run_/plan_business_process_automation as-is (targeted at the As-Is
    # view name). The stages below cover the rest of the workflow.

    def assessment_setup(self, *, ist_view_name: str, soll_view_name: str) -> Dict[str, Any]:
        used_tools: List[str] = []
        ist_view_id = self._find_existing_view_id(view_name=ist_view_name, used_tools=used_tools)
        soll_view_id = self._find_existing_view_id(view_name=soll_view_name, used_tools=used_tools)
        business_elements = self._list_all(
            "search-elements", {"query": "", "layer": "Business", "exclude": ["documentation"]}, used_tools
        )

        notes: List[str] = []
        notes.append(
            f"Found existing As-Is view '{ist_view_name}' -- As-Is Capture will add to it."
            if ist_view_id
            else f"No existing '{ist_view_name}' view found -- it will be created during As-Is Capture."
        )
        notes.append(
            f"Found existing To-Be view '{soll_view_name}' -- To-Be Architecture will add to it."
            if soll_view_id
            else f"No existing '{soll_view_name}' view found -- it will be created during To-Be Architecture."
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

    def _check_soll_relevance_to_ist(
        self, *, ist_view_name: str, soll_names: List[str], used_tools: List[str]
    ) -> str | None:
        """Best-effort, non-blocking sanity check: does this newly-extracted To-Be content look
        like a plausible target-state counterpart for the As-Is processes already captured, or
        does it look like the wrong document got uploaded? Never raises -- a failed check just
        means no warning, not a blocked upload."""
        try:
            ist_view_id = self._find_existing_view_id(view_name=ist_view_name, used_tools=used_tools)
            if not ist_view_id:
                return None
            ist_processes = self._collect_view_business_processes(view_id=ist_view_id, used_tools=used_tools)
            ist_names = [p["name"] for p in ist_processes]
            if not ist_names or not soll_names:
                return None

            user_prompt = (
                "You are sanity-checking a target-state (To-Be) process upload against the as-is "
                "(As-Is) processes already captured for the same architecture assessment.\n"
                f"As-Is processes already captured:\n" + "\n".join(f"- {n}" for n in ist_names[:60]) + "\n\n"
                f"Newly uploaded To-Be process names:\n" + "\n".join(f"- {n}" for n in soll_names[:60]) + "\n\n"
                'Return strict JSON only: {"relevant": true|false, "reason": "one short sentence"}.\n'
                "relevant=true if the To-Be content plausibly represents a target-state evolution of "
                "(at least some of) the As-Is scope -- new, renamed, consolidated, or automated steps "
                "are all still relevant. relevant=false only if the content looks like it addresses a "
                "clearly different process or domain entirely (e.g. As-Is is an offer process and this "
                "document is about something unrelated like HR onboarding)."
            )
            parsed = self._call_model_for_json(
                system_prompt="You are a careful architecture assessment QA checker. Output strict JSON only.",
                user_prompt=user_prompt,
            )
            if parsed.get("relevant") is False:
                reason = str(parsed.get("reason", "")).strip()
                suffix = f" ({reason})" if reason else ""
                return (
                    f"This document's content doesn't look closely related to your As-Is processes{suffix} "
                    "-- double check this is the right target-state document before continuing."
                )
        except Exception:  # noqa: BLE001
            return None
        return None

    def plan_soll_architecture(
        self,
        *,
        uploads: List[Tuple[str, str, bytes | None]],
        view_name: str | None = None,
        ist_view_name: str | None = None,
    ) -> Dict[str, Any]:
        """A target-state upload (e.g. a To-Be SIPOC table or process diagram) describes the same
        kind of content as an As-Is upload -- process steps and their sequence -- so it reuses the
        exact same SIPOC-table / PDF-geometry / evidence-gated text extraction as As-Is Capture,
        just tagged as "target" instead of running the separate ApplicationComponent-oriented
        product-architecture extraction meant for requirement documents. uploads is an ORDERED list,
        same multi-file chaining behavior as plan_business_process_automation."""
        if not uploads:
            raise RuntimeError("No files were provided.")
        resolved_view_name = view_name or "To-Be Business Processes"
        plans = [
            self._extract_business_process_plan(
                content_text=content_text, source_name=source_name, view_name=resolved_view_name, pdf_bytes=pdf_bytes
            )
            for content_text, source_name, pdf_bytes in uploads
        ]
        source_labels = [source_name for _content_text, source_name, _pdf_bytes in uploads]
        merged = self._merge_extraction_plans(plans=plans, source_labels=source_labels, view_name=resolved_view_name)

        tagged_elements = {
            key: {**item, "properties": {**item.get("properties", {}), "status": "target"}}
            for key, item in merged["elements"].items()
        }
        merged = {**merged, "elements": tagged_elements}

        if ist_view_name:
            used_tools: List[str] = []
            soll_names = [item["name"] for item in tagged_elements.values() if item.get("type") == "BusinessProcess"]
            relevance_warning = self._check_soll_relevance_to_ist(
                ist_view_name=ist_view_name, soll_names=soll_names, used_tools=used_tools
            )
            if relevance_warning:
                merged = {**merged, "warnings": [relevance_warning, *merged.get("warnings", [])]}

        combined_source_name = source_labels[0] if len(source_labels) == 1 else f"{len(source_labels)} files"
        return self._format_plan_response(
            action="assessment-soll-upload", source_name=combined_source_name, extracted=merged
        )

    def propose_soll_architecture(
        self,
        *,
        ist_view_name: str,
        view_name: str | None = None,
    ) -> Dict[str, Any]:
        """Generate a To-Be Architecture proposal directly from the As-Is processes on the configured
        As-Is view, for when no separate target-requirements document exists yet to upload in step 3.
        Scoped to that specific view rather than a model-wide search -- see
        _collect_view_business_processes for why that distinction matters."""
        used_tools: List[str] = []
        ist_view_id = self._find_existing_view_id(view_name=ist_view_name, used_tools=used_tools)
        if not ist_view_id:
            raise RuntimeError(
                f"No As-Is view named '{ist_view_name}' found in the model. Run As-Is Capture first."
            )
        ist_processes = self._collect_view_business_processes(view_id=ist_view_id, used_tools=used_tools)
        ist_names = [p["name"] for p in ist_processes]

        if not ist_names:
            raise RuntimeError(
                f"No As-Is business processes found on view '{ist_view_name}'. Run As-Is Capture first."
            )

        preferred_view_name = self._normalize_name(view_name or "To-Be Business Processes", fallback="To-Be Business Processes")
        process_lines = "\n".join(f"- {name}" for name in ist_names)

        user_prompt = (
            "You are proposing a TO-BE business process architecture based on the following AS-IS "
            "business processes already in the model. Improve on them the way a target-state "
            "architecture typically would: automate manual-sounding steps, consolidate obviously redundant "
            "or duplicate steps, modernize outdated tooling references, and add at most a few clearly "
            "justified new capabilities (e.g. self-service, automation, AI-assisted variants). Do not invent "
            "an unrelated process landscape -- every proposed process must trace back to (replace, merge, or "
            "extend) something in the AS-IS list below.\n"
            "Return strict JSON only with this schema:\n"
            "{\n"
            '  "view_name": "string",\n'
            '  "elements": [{"name": "string", "type": "BusinessProcess", "description": "1 sentence: how '
            'this relates to the As-Is process(es) it is based on"}],\n'
            '  "relationships": [{"type": "TriggeringRelationship", "source": "element name", "target": '
            '"element name"}]\n'
            "}\n"
            "Rules:\n"
            "- Use only BusinessProcess for elements (this proposal is process-level).\n"
            "- Keep between 4 and 30 elements.\n"
            "- Connect the proposed processes in their intended sequential order via TriggeringRelationship.\n"
            "- No explanation text, only the JSON object.\n\n"
            f"Preferred view name: {preferred_view_name}\n\n"
            f"As-Is business processes already in the model:\n{process_lines}"
        )
        parsed = self._call_model_for_json(
            system_prompt="You are an enterprise architecture target-state design engine. Output strict JSON only.",
            user_prompt=user_prompt,
        )

        raw_elements = parsed.get("elements")
        raw_relationships = parsed.get("relationships")
        if not isinstance(raw_elements, list) or not raw_elements:
            raise RuntimeError("Could not generate a To-Be Architecture proposal from the current As-Is processes.")
        if not isinstance(raw_relationships, list):
            raw_relationships = []

        elements: Dict[str, Dict[str, Any]] = {}
        key_by_name: Dict[str, str] = {}
        for item in raw_elements:
            if not isinstance(item, dict):
                continue
            name = self._normalize_name(item.get("name", ""), fallback="")
            if not name:
                continue
            key = f"BusinessProcess::{self._norm_key(name)}"
            if key in elements:
                continue
            elements[key] = {
                "type": "BusinessProcess",
                "name": name,
                "documentation": self._normalize_name(item.get("description", ""), fallback="", max_len=400),
                "properties": {"status": "target"},
            }
            key_by_name.setdefault(self._norm_key(name), key)

        if not elements:
            raise RuntimeError("Could not generate a To-Be Architecture proposal from the current As-Is processes.")

        relationships: List[Dict[str, str]] = []
        for item in raw_relationships:
            if not isinstance(item, dict):
                continue
            source_key = key_by_name.get(self._norm_key(item.get("source", "")))
            target_key = key_by_name.get(self._norm_key(item.get("target", "")))
            if not source_key or not target_key or source_key == target_key:
                continue
            relationships.append({"type": "TriggeringRelationship", "source_key": source_key, "target_key": target_key})

        resolved_view_name = self._normalize_name(parsed.get("view_name", preferred_view_name), fallback=preferred_view_name)
        extracted = {
            "view_name": resolved_view_name,
            "elements": elements,
            "relationships": relationships,
            "layout_positions": self._grid_layout_positions(elements),
            "steps_processed": len(elements),
            "steps_truncated": False,
            "warnings": [
                f"AI-generated proposal based on {len(ist_names)} existing As-Is business process(es). Review "
                "carefully before applying -- this is a suggested target state, not an extraction from a "
                "source document."
            ],
        }
        return self._format_plan_response(
            action="assessment-soll-upload", source_name="AI-proposed from As-Is Capture", extracted=extracted
        )

    def run_mapping_gap_analysis(self, *, ist_view_name: str, soll_view_name: str) -> Dict[str, Any]:
        used_tools: List[str] = []
        ist_view_id = self._find_existing_view_id(view_name=ist_view_name, used_tools=used_tools)
        soll_view_id = self._find_existing_view_id(view_name=soll_view_name, used_tools=used_tools)

        if not ist_view_id:
            raise RuntimeError(
                f"No As-Is view named '{ist_view_name}' found in the model. Run As-Is Capture first."
            )
        if not soll_view_id:
            raise RuntimeError(
                f"No To-Be view named '{soll_view_name}' found in the model. Run To-Be Architecture first."
            )

        # Scoped to exactly what's on these two views -- not a model-wide search -- so a fresh view
        # name in a new assessment cycle actually gets a fresh mapping, instead of mixing in every
        # BusinessProcess element from every prior cycle (or unrelated model content).
        ist_processes = self._collect_view_business_processes(view_id=ist_view_id, used_tools=used_tools)
        soll_processes = self._collect_view_business_processes(view_id=soll_view_id, used_tools=used_tools)

        if not ist_processes:
            raise RuntimeError(
                f"No As-Is business processes found on view '{ist_view_name}'. Run As-Is Capture first."
            )
        if not soll_processes:
            raise RuntimeError(
                f"No To-Be business processes found on view '{soll_view_name}'. Run To-Be Architecture first."
            )

        # A full mapping+gap analysis over hundreds of processes in one LLM call gets slow enough to
        # risk gateway timeouts and degrades output quality. Cap and surface it as a warning rather
        # than silently truncating or hanging.
        max_mapping_items = max(1, self.settings.max_action_steps)
        ist_truncated = len(ist_processes) > max_mapping_items
        soll_truncated = len(soll_processes) > max_mapping_items
        ist_processes = ist_processes[:max_mapping_items]
        soll_processes = soll_processes[:max_mapping_items]

        ist_by_id = {p["id"]: p["name"] for p in ist_processes}
        soll_by_id = {p["id"]: p["name"] for p in soll_processes}
        ist_lines = "\n".join(f"{p['id']}: {p['name']}" for p in ist_processes)
        soll_lines = "\n".join(f"{p['id']}: {p['name']}" for p in soll_processes)

        user_prompt = (
            "You are comparing an AS-IS business process list to a TO-BE business process list "
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
            "- match_type 'full' = the AS-IS and TO-BE process describe essentially the same activity.\n"
            "- match_type 'partial' = related but meaningfully different scope or steps.\n"
            "- match_type 'legacy_no_soll' = an AS-IS process with no reasonable TO-BE equivalent (soll_id null).\n"
            "- match_type 'gap_new' = a TO-BE process with no AS-IS equivalent -- a new process not yet "
            "implemented (ist_id null).\n"
            "- Every listed AS-IS id and every listed TO-BE id must appear in exactly one mapping entry.\n"
            "- Only compare the process NAMES given; do not invent details not implied by the names.\n"
            "- gaps should summarize the most notable mapping outcomes (especially legacy_no_soll and gap_new "
            "entries, and any 'partial' entries with a meaningful difference), grouped by category and rated "
            "by business criticality. Not every mapping needs its own gap entry.\n"
            "- No explanation text, only the JSON object.\n\n"
            f"As-Is processes (from '{ist_view_name}'):\n{ist_lines}\n\n"
            f"To-Be processes (from '{soll_view_name}'):\n{soll_lines}"
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
            warnings.append(f"{unmapped_ist} As-Is process(es) were not covered by any mapping entry.")
        if unmapped_soll > 0:
            warnings.append(f"{unmapped_soll} To-Be process(es) were not covered by any mapping entry.")
        if ist_truncated:
            warnings.append(f"As-Is processes were truncated to the first {max_mapping_items} for this analysis.")
        if soll_truncated:
            warnings.append(f"To-Be processes were truncated to the first {max_mapping_items} for this analysis.")

        return {
            "ist_view_name": ist_view_name,
            "soll_view_name": soll_view_name,
            "mappings": mappings,
            "gaps": gaps,
            "warnings": warnings,
            "used_tools": used_tools,
        }

    @_tracks_proposals
    def apply_mapping_relationships(
        self,
        *,
        mappings: List[Dict[str, Any]],
        gaps: List[Dict[str, Any]] | None = None,
        view_name: str = "As-Is To-Be Mapping",
    ) -> Dict[str, Any]:
        """Create the As-Is<->To-Be traceability relationships AND a real Archi view visualizing them:
        an As-Is row and a To-Be row, matched pairs vertically aligned and connected, unmatched
        processes (legacy-no-To-Be / new-no-As-Is) shown standalone in their row so the gap is visible
        at a glance -- not just a relationship buried in the model. Also pulls in each included
        process's EXISTING TriggeringRelationship connections to other included processes (the
        internal As-Is/To-Be flow already captured during As-Is Capture / To-Be Architecture) so
        the mapping view shows the full picture, not just the new cross-mapping links; and creates
        one Gap element per identified gap, linked to its affected process(es) via "affects"."""
        included = [m for m in mappings if m.get("include", True)]
        matched = [m for m in included if m.get("ist_key") and m.get("soll_key")]
        ist_only = [m for m in included if m.get("ist_key") and not m.get("soll_key")]
        soll_only = [m for m in included if m.get("soll_key") and not m.get("ist_key")]

        if not matched and not ist_only and not soll_only:
            raise RuntimeError("No mappings were selected to apply.")

        preferred_view_name = self._normalize_name(view_name or "As-Is To-Be Mapping", fallback="As-Is To-Be Mapping")
        match_labels = {"full": "full match", "partial": "partial match"}

        elements_by_key: Dict[str, Dict[str, str]] = {}
        relationships: List[Dict[str, str]] = []
        layout_positions: Dict[str, Dict[str, int]] = {}
        # real Archi element id -> my internal key, so existing relationships between two included
        # processes (found by real id) can be looked up and re-added to this view.
        key_by_real_id: Dict[str, str] = {}
        # normalized process name -> my internal key, so gaps (which only carry names) can be
        # linked to the process(es) they affect.
        key_by_process_name: Dict[str, str] = {}

        start_x = 100
        col_gap = 260
        ist_y = 110
        soll_y = 420
        gap_y = 730
        box_w, box_h = 220, 90
        col_index = 0

        def register_process(internal_key: str, real_id: Any, name: str) -> None:
            if real_id:
                key_by_real_id[str(real_id)] = internal_key
            if name:
                key_by_process_name.setdefault(self._norm_key(name), internal_key)

        for mapping in matched:
            ist_key = f"ist::{mapping['ist_key']}"
            soll_key = f"soll::{mapping['soll_key']}"
            ist_name = str(mapping.get("ist_name") or "As-Is process")
            soll_name = str(mapping.get("soll_name") or "To-Be process")
            elements_by_key[ist_key] = {"type": "BusinessProcess", "name": ist_name}
            elements_by_key[soll_key] = {"type": "BusinessProcess", "name": soll_name}
            register_process(ist_key, mapping.get("ist_key"), ist_name)
            register_process(soll_key, mapping.get("soll_key"), soll_name)
            x = start_x + col_index * col_gap
            layout_positions[ist_key] = {"x": x, "y": ist_y, "width": box_w, "height": box_h}
            layout_positions[soll_key] = {"x": x, "y": soll_y, "width": box_w, "height": box_h}
            label = match_labels.get(str(mapping.get("match_type", "")), "mapped to")
            # RealizationRelationship is not a valid ArchiMate pairing between two BusinessProcess
            # elements (confirmed by the MCP server's own validator: BULK_VALIDATION_FAILED, valid
            # types for BusinessProcess->BusinessProcess are Composition/Aggregation/Serving/
            # Triggering/Flow/Specialization/Association) -- every prior "successful" apply was
            # silently rejected wholesale before _mcp_call was fixed to actually check for errors.
            # AssociationRelationship is the correct, always-valid ArchiMate type here; "realizes"
            # is still conveyed via the connection's name/label (source=To-Be, target=As-Is) since that
            # was the intended traceability semantics.
            relationships.append(
                {
                    "type": "AssociationRelationship",
                    "source_key": soll_key,
                    "target_key": ist_key,
                    "name": f"realizes ({label})",
                }
            )
            col_index += 1

        for mapping in ist_only:
            ist_key = f"ist::{mapping['ist_key']}"
            ist_name = str(mapping.get("ist_name") or "As-Is process")
            elements_by_key.setdefault(ist_key, {"type": "BusinessProcess", "name": ist_name})
            register_process(ist_key, mapping.get("ist_key"), ist_name)
            x = start_x + col_index * col_gap
            layout_positions[ist_key] = {"x": x, "y": ist_y, "width": box_w, "height": box_h}
            col_index += 1

        for mapping in soll_only:
            soll_key = f"soll::{mapping['soll_key']}"
            soll_name = str(mapping.get("soll_name") or "To-Be process")
            elements_by_key.setdefault(soll_key, {"type": "BusinessProcess", "name": soll_name})
            register_process(soll_key, mapping.get("soll_key"), soll_name)
            x = start_x + col_index * col_gap
            layout_positions[soll_key] = {"x": x, "y": soll_y, "width": box_w, "height": box_h}
            col_index += 1

        # Pull in each included process's existing TriggeringRelationship connections to other
        # included processes -- the internal As-Is/To-Be flow already captured earlier in the
        # wizard -- so the mapping view shows the full picture, not just the new cross-mapping
        # links. _execute_view_plan recognizes these as already-existing relationships (matched by
        # real id) and just adds a visual connection; it does not create duplicates.
        used_tools: List[str] = []
        existing_relationships = self._collect_existing_relationships(used_tools)
        trigger_type_key = self._norm_key("TriggeringRelationship")
        seen_trigger_pairs: set[tuple[str, str]] = set()
        for (rel_type_key, source_id, target_id), _rel_id in existing_relationships.items():
            if rel_type_key != trigger_type_key:
                continue
            source_key = key_by_real_id.get(source_id)
            target_key = key_by_real_id.get(target_id)
            if not source_key or not target_key or source_key == target_key:
                continue
            pair = (source_key, target_key)
            if pair in seen_trigger_pairs:
                continue
            seen_trigger_pairs.add(pair)
            relationships.append({"type": "TriggeringRelationship", "source_key": source_key, "target_key": target_key})

        # Gap elements: one per identified gap, linked to whichever included process(es) it
        # affects via an "affects" relationship.
        category_labels = {
            "missing_process": "Missing process",
            "redundancy": "Redundancy",
            "structural_difference": "Structural difference",
            "tooling_data_gap": "Tooling/data gap",
        }
        gap_name_counts: Dict[str, int] = {}
        gap_list = gaps or []
        for index, gap in enumerate(gap_list):
            category = str(gap.get("category", "")).strip()
            category_label = category_labels.get(category, category or "Gap")
            related_ist = str(gap.get("related_ist_name") or "").strip()
            related_soll = str(gap.get("related_soll_name") or "").strip()
            related = related_ist or related_soll
            base_name = f"{category_label}: {related}" if related else f"{category_label} gap"
            norm_base = self._norm_key(base_name)
            occurrence = gap_name_counts.get(norm_base, 0)
            gap_name_counts[norm_base] = occurrence + 1
            candidate_name = base_name if occurrence == 0 else f"{base_name} ({occurrence + 1})"
            gap_name = self._normalize_name(candidate_name, fallback=f"Gap {index + 1}")

            gap_key = f"gap::{index}"
            gap_properties = {
                key: value
                for key, value in (
                    ("criticality", meta_model.normalize_property_value("Gap", "criticality", gap.get("criticality"))[0]),
                    ("gapCategory", meta_model.normalize_property_value("Gap", "gapCategory", category)[0]),
                )
                if value
            }
            elements_by_key[gap_key] = {
                "type": "Gap",
                "name": gap_name,
                "documentation": self._normalize_name(
                    f"[{gap.get('criticality', 'medium')}] {gap.get('description', '')}", fallback="", max_len=400
                ),
                "properties": gap_properties,
            }
            layout_positions[gap_key] = {"x": start_x + index * col_gap, "y": gap_y, "width": box_w, "height": box_h}

            for related_name in (related_ist, related_soll):
                if not related_name:
                    continue
                target_key = key_by_process_name.get(self._norm_key(related_name))
                if not target_key:
                    continue
                relationships.append(
                    {
                        "type": "AssociationRelationship",
                        "source_key": gap_key,
                        "target_key": target_key,
                        "name": "affects",
                    }
                )

        execution = self._execute_view_plan(
            action_label="As-Is/To-Be mapping visualization",
            source_name="Mapping & Gap Analysis",
            view_name=preferred_view_name,
            elements_by_key=elements_by_key,
            relationships=relationships,
            layout_positions=layout_positions,
        )

        resolved_view_name = str(execution.get("view_name", preferred_view_name))
        summary = (
            f"Created mapping view '{resolved_view_name}': {len(matched)} matched pair(s) connected, "
            f"{len(ist_only)} As-Is-only (legacy, no To-Be equivalent), {len(soll_only)} To-Be-only (new, no As-Is "
            f"equivalent) shown standalone, {len(gap_list)} gap element(s) linked to affected processes. "
            f"{execution.get('created_relationships', 0)} new relationship(s) created."
            + self.approval_note(self._current_proposals())
        )
        return {
            "summary": summary,
            "view_name": resolved_view_name,
            "view_id": execution.get("view_id"),
            "created_relationships": int(execution.get("created_relationships", 0)),
            "used_tools": used_tools + list(execution.get("used_tools", [])),
        }

    # ---- Assessment step 5: Ratings & Roadmap -------------------------------------------------

    def _steering_scope(
        self, *, ist_view_names: List[str], soll_view_names: List[str], used_tools: List[str]
    ) -> Tuple[List[str], List[str], List[str]]:
        """Process ids on the assessment's As-Is and To-Be views (the step's process scope)."""
        warnings: List[str] = []
        scoped: Dict[str, List[str]] = {"ist": [], "soll": []}
        for side, names in (("ist", ist_view_names), ("soll", soll_view_names)):
            for name in dict.fromkeys(n.strip() for n in names if n and n.strip()):
                view_id = self._find_existing_view_id(view_name=name, used_tools=used_tools)
                if not view_id:
                    warnings.append(f"View '{name}' was not found in the model; its processes are not listed.")
                    continue
                scoped[side] += [p["id"] for p in self._collect_view_business_processes(view_id=view_id, used_tools=used_tools)]
        return scoped["ist"], scoped["soll"], warnings

    def steering_load(self, *, ist_view_names: List[str], soll_view_names: List[str]) -> Dict[str, Any]:
        used_tools: List[str] = []
        as_is, to_be, warnings = self._steering_scope(
            ist_view_names=ist_view_names, soll_view_names=soll_view_names, used_tools=used_tools
        )
        state = steering.current_state(dashboard.fetch_snapshot(self.mcp), as_is_process_ids=as_is, to_be_process_ids=to_be)
        state["warnings"] = warnings + state["warnings"]
        state["schema"] = meta_model.PROPERTIES
        return state

    def steering_propose(
        self, *, ist_view_names: List[str], soll_view_names: List[str], state: Dict[str, Any] | None = None
    ) -> Dict[str, Any]:
        """AI proposal merged into the (possibly already edited) tables; nothing is written."""
        if not state or not isinstance(state.get("capabilities"), list):
            state = self.steering_load(ist_view_names=ist_view_names, soll_view_names=soll_view_names)
        snapshot = dashboard.fetch_snapshot(self.mcp)
        process_keys = {r["key"] for r in state.get("processes") or []}
        to_be = {r["key"] for r in state.get("processes") or [] if r.get("status") == "target"}
        mappings = [
            (rel["sourceId"], rel["targetId"])
            for rel in snapshot.get("relationships") or []
            if rel.get("type") == "AssociationRelationship" and rel.get("sourceId") in to_be
            and rel.get("targetId") in process_keys and rel.get("targetId") not in to_be
        ]
        prompt = steering.proposal_prompt(state, mappings=mappings, today=date.today())
        proposal = self._call_model_for_json(
            system_prompt=(
                "You are an enterprise architecture assessment engine preparing capability-based planning data. "
                "Output strict JSON only."
            ),
            user_prompt=prompt,
        )
        merged = steering.merge_proposal(state, proposal)
        merged["schema"] = meta_model.PROPERTIES
        return merged

    @_tracks_proposals
    def steering_apply(self, *, state: Dict[str, Any]) -> Dict[str, Any]:
        """Write the reviewed tables to Archi: new elements and the relationships that reference them in
        one bulk-mutate call (named back-references), everything else in further calls."""
        used_tools: List[str] = []
        plan = steering.plan_changes(dashboard.fetch_snapshot(self.mcp), state)
        if plan["errors"]:
            shown = "; ".join(plan["errors"][:8])
            more = f" (+{len(plan['errors']) - 8} more)" if len(plan["errors"]) > 8 else ""
            raise RuntimeError(f"Nothing was written. Please fix: {shown}{more}")
        per_call = self._max_automation_ops()
        first = plan["create"] + plan["linked"]
        if len(first) > per_call:
            raise RuntimeError(
                f"Nothing was written: the new elements and their links need {len(first)} operations, more than the "
                f"{per_call} Archi accepts in one call. Untick some new rows and apply in two rounds."
            )
        batches = ([first] if first else []) + [
            plan["independent"][i : i + per_call] for i in range(0, len(plan["independent"]), per_call)
        ]
        proposals: List[str] = []
        for batch in batches:
            result = self._mcp_call(
                "bulk-mutate",
                {
                    "operations": batch,
                    "description": "Assessment: ratings & roadmap",
                    "intent": "Write the reviewed steering data of the assessment",
                },
                used_tools,
            )
            proposal = result.get("proposal") if isinstance(result, dict) else None
            if isinstance(proposal, dict) and proposal.get("proposalId"):
                proposals.append(str(proposal["proposalId"]))
        counts = plan["counts"]
        if not batches:
            summary = "No changes: the model already matches the tables."
        else:
            summary = (
                f"{counts['created']} element(s) created, {counts['updated']} updated, {counts['relationships']} "
                f"relationship(s) added, {counts['removedRelationships']} replaced link(s) removed."
            )
            if proposals:
                summary += f" Archi approval mode is on: approve proposal(s) {', '.join(proposals)} in Archi to apply them."
        return {"summary": summary, "counts": counts, "proposals": proposals, "used_tools": used_tools}

    def _steering_context(self) -> str:
        """Steering data from the model (via the dashboard metrics) for the executive summary."""
        try:
            data = dashboard.build_dashboard(self.mcp, date.today())
        except Exception:  # noqa: BLE001 -- the summary must not fail because the model can't be read
            return ""
        s, rc = data["strategy"]["kpis"], data["roadmapCoverage"]["kpis"]
        impl, m, a = data["implementation"], data["motivation"]["kpis"], data["application"]["kpis"]
        lines: List[str] = []
        if s["ratedCount"]:
            unplanned = rc["priorityGapsUnplanned"]
            lines.append(
                f"- Capabilities rated: {s['ratedCount']} of {s['capabilityCount']}; average maturity {s['avgMaturity']} "
                f"against a target of {s['avgTargetMaturity']}; {rc['priorityGaps']} priority gaps (2+ levels on high-importance "
                f"capabilities), {len(unplanned)} not planned in any plateau" + (f": {', '.join(unplanned[:6])}" if unplanned else "")
            )
        if impl["plateaus"]:
            lines.append("- Plateaus: " + "; ".join(
                f"{p['name']} (target {p['targetDate'] or 'n/a'}, {p['completed']} of {len(p['workPackages'])} work packages completed)"
                for p in impl["plateaus"]))
        k = impl["kpis"]
        if k["workPackageCount"]:
            lines.append(
                f"- Work packages: {k['completed']} completed, {k['in_progress']} in progress, {k['planned']} planned, "
                f"{k['overdue']} overdue; {k['gapsWithoutPlateau']} of {k['gapCount']} gaps not assigned to a plateau"
            )
        if m["trackedCount"]:
            lines.append(f"- Outcome KPIs: {m['onTrackOrAchieved']} of {m['trackedCount']} on track, {m['at_risk']} at risk, "
                         f"{m['off_track'] + m['missed']} off track")
        if a["applicationCount"]:
            lines.append(f"- Applications: {a['pastEndOfLife']} past end of life, {a['endOfLifeWithin24Months']} reach end of "
                         f"life within 24 months")
        return "\n".join(lines)

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
        high_gap_count = sum(1 for g in gaps if g.get("criticality") == "high")
        medium_gap_count = sum(1 for g in gaps if g.get("criticality") == "medium")
        low_gap_count = sum(1 for g in gaps if g.get("criticality") == "low")

        confidences = [
            self._as_float(m.get("confidence", 0.0), default=0.0)
            for m in mappings
            if m.get("match_type") in {"full", "partial"}
        ]
        average_similarity = (sum(confidences) / len(confidences) * 100.0) if confidences else 0.0
        total_mappings = max(1, len(mappings))
        maturity_score = ((full_matches + 0.5 * partial_matches) / total_mappings) * 100.0

        if maturity_score >= 75:
            readiness_label = "Advanced"
        elif maturity_score >= 45:
            readiness_label = "Progressing"
        else:
            readiness_label = "Early Stage"

        # Sort gaps highest-criticality-first so the LLM sees the most decision-relevant ones
        # first if the list has to be truncated, and so it can quote real descriptions instead of
        # inventing risk language.
        criticality_rank = {"high": 0, "medium": 1, "low": 2}
        sorted_gaps = sorted(gaps, key=lambda g: criticality_rank.get(str(g.get("criticality")), 3))
        gap_lines = "\n".join(
            f"- [{g.get('criticality', 'medium')}] {g.get('category', 'gap')}: {g.get('description', '')}"
            for g in sorted_gaps[:20]
        ) or "- (no gaps identified)"
        steering_context = self._steering_context()

        summary_prompt = (
            "You are preparing the executive summary slide for a steering committee (steerco) readout of an "
            "architecture assessment. The audience is senior management with limited time -- they need the "
            "verdict and the decision they're being asked to make, not a narrative of the analysis process.\n\n"
            "Ground every claim ONLY in the data below. Never invent a number, risk, or finding that isn't "
            "directly supported by it. top_risks entries must paraphrase or quote an actual gap from the list "
            "below, not a generic statement.\n\n"
            f"As-Is processes mapped: {len(ist_ids)}. To-Be processes mapped: {len(soll_ids)}.\n"
            f"Full matches: {full_matches}. Partial matches: {partial_matches}. "
            f"Legacy processes with no To-Be equivalent: {legacy_count}. "
            f"New To-Be processes with no As-Is equivalent: {gap_new_count}.\n"
            f"Total gaps: {gap_count} (high: {high_gap_count}, medium: {medium_gap_count}, low: {low_gap_count}).\n"
            f"Average mapping confidence/similarity: {average_similarity:.0f}%. "
            f"Overall maturity score: {maturity_score:.0f}% ({readiness_label}).\n\n"
            f"Gap list (highest criticality first):\n{gap_lines}\n\n"
            + (f"Steering data maintained in the information model (Ratings & Roadmap step):\n{steering_context}\n\n"
               if steering_context else "")
            + "Return strict JSON only with this schema:\n"
            "{\n"
            '  "headline": "one punchy sentence, the verdict a steerco member would remember -- state the '
            'maturity/readiness posture and the single biggest thing needing a decision",\n'
            '  "key_findings": ["3 to 4 short, specific, scannable bullet points -- each one fact, not a '
            'restatement of raw counts already shown elsewhere"],\n'
            '  "top_risks": ["2 to 3 bullets, each naming a REAL gap from the list above and why it matters '
            'to the business, ordered most severe first"],\n'
            '  "recommendation": "1-2 sentences: the single clearest recommended course of action given the '
            'findings -- what should leadership approve or prioritize",\n'
            '  "next_steps": ["2 to 3 short, concrete, near-term action items"],\n'
            '  "executive_summary": "4-6 sentence flowing paragraph version of the same verdict, for when a '
            'plain-text summary is needed instead of bullets"\n'
            "}\n"
            "Rules:\n"
            "- Plain text only inside every field -- no markdown, no bullet characters, no bold.\n"
            "- Be concrete and quantify where the data supports it (e.g. cite counts or the maturity score) "
            "rather than using vague language like \"several\" or \"significant\".\n"
            "- If there are zero high-criticality gaps, top_risks should say so explicitly rather than "
            "padding with lower-severity items dressed up as risks.\n"
            "- Where steering data is given, the recommendation and next_steps must build on it (for example "
            "priority gaps not yet planned in any plateau, or the next plateau and its open work packages).\n"
            "- No explanation text outside the JSON object."
        )
        summary_system_prompt = (
            "You write steerco-grade architecture assessment summaries: concise, decision-oriented, and "
            "strictly grounded in the data you're given. Output strict JSON only."
        )
        summary_model = self.settings.azure_openai_summary_model
        parsed: Dict[str, Any] | None = None
        if summary_model:
            try:
                parsed = self._call_model_for_json_via_responses_api(
                    model=summary_model, system_prompt=summary_system_prompt, user_prompt=summary_prompt
                )
            except Exception:  # noqa: BLE001
                # This deployment might not actually be a Responses-API-only model, or might be
                # temporarily unavailable -- either way, fall through to the normal Chat Completions
                # path (which has its own model-tier fallback chain) rather than failing the summary.
                parsed = None
        if parsed is None:
            parsed = self._call_model_for_json(
                system_prompt=summary_system_prompt,
                user_prompt=summary_prompt,
                model_override=summary_model or None,
            )

        def _str_list(raw: Any, *, max_items: int, max_len: int) -> List[str]:
            if not isinstance(raw, list):
                return []
            out = []
            for item in raw[:max_items]:
                text = self._normalize_name(item, fallback="", max_len=max_len)
                if text:
                    out.append(text)
            return out

        headline = self._normalize_name(parsed.get("headline", ""), fallback="", max_len=200)
        key_findings = _str_list(parsed.get("key_findings"), max_items=5, max_len=220)
        top_risks = _str_list(parsed.get("top_risks"), max_items=4, max_len=260)
        recommendation = self._normalize_name(parsed.get("recommendation", ""), fallback="", max_len=400)
        next_steps = _str_list(parsed.get("next_steps"), max_items=4, max_len=200)
        executive_summary = self._normalize_name(parsed.get("executive_summary", ""), fallback="", max_len=1200)

        if not top_risks and high_gap_count == 0:
            top_risks = ["No high-criticality gaps identified in this assessment."]

        return {
            "ist_process_count": len(ist_ids),
            "soll_process_count": len(soll_ids),
            "full_matches": full_matches,
            "partial_matches": partial_matches,
            "gap_count": gap_count,
            "critical_gap_count": high_gap_count,
            "medium_gap_count": medium_gap_count,
            "low_gap_count": low_gap_count,
            "average_similarity": round(average_similarity, 1),
            "maturity_score": round(maturity_score, 1),
            "readiness_label": readiness_label,
            "headline": headline,
            "key_findings": key_findings,
            "top_risks": top_risks,
            "recommendation": recommendation,
            "next_steps": next_steps,
            "executive_summary": executive_summary,
        }

    def _model_round(
        self,
        create_kwargs: Dict[str, Any],
        on_event: ChatEventSink | None,
        should_stop: Callable[[], bool] | None,
    ) -> Tuple[str, List[Dict[str, str]], str, int]:
        """One model call -> (text, tool calls, model used, retry attempts). With on_event the call is
        streamed and every text delta is reported as it arrives."""
        if on_event is None:
            response, model_used, attempts = self._call_model_with_retries(create_kwargs)
            message = response.choices[0].message
            calls = [
                {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments or ""}
                for tc in message.tool_calls or []
            ]
            return message.content or "", calls, model_used, attempts

        stream, model_used, attempts = self._call_model_with_retries({**create_kwargs, "stream": True})
        parts: List[str] = []
        calls_by_index: Dict[int, Dict[str, str]] = {}
        try:
            for chunk in stream:
                if should_stop and should_stop():
                    raise ChatStopped()
                if not chunk.choices:  # Azure sends content-filter metadata without choices
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    parts.append(delta.content)
                    on_event("delta", {"content": delta.content})
                for tc in delta.tool_calls or []:
                    slot = calls_by_index.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    slot["id"] = tc.id or slot["id"]
                    if tc.function and tc.function.name:
                        slot["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["arguments"] += tc.function.arguments
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        calls = [calls_by_index[i] for i in sorted(calls_by_index)]
        if calls and parts:
            # Text written before the model decided to call tools is not the answer.
            on_event("draft_reset", {})
        return "".join(parts), calls, model_used, attempts

    def _chat_internal(
        self,
        history: List[Dict[str, str]],
        message: str,
        system_prompt: str | None = None,
        include_trace: bool = False,
        on_event: ChatEventSink | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> Tuple[str, List[str], Dict[str, Any] | None]:
        """The tool-calling loop. on_event (optional) receives live progress -- ("round", ...),
        ("tool_start", ...), ("tool_end", ...), ("delta", ...), ("draft_reset", ...) -- and switches the
        model calls to streaming; should_stop (optional) is checked before every model call and tool
        call, so a stopped request never starts another change in Archi."""
        if not self.settings.azure_openai_api_key:
            raise RuntimeError("AZURE_OPENAI_API_KEY is not configured.")
        if not self.settings.azure_openai_base_url:
            raise RuntimeError("AZURE_OPENAI_BASE_URL is not configured.")

        def check_stop() -> None:
            if should_stop and should_stop():
                raise ChatStopped()

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
            check_stop()
            if on_event:
                on_event("round", {"round": round_index + 1})
            round_start = time.perf_counter()
            create_kwargs: Dict[str, Any] = dict(
                model=self.settings.azure_openai_model,
                messages=messages,
                tools=tools if tools else None,
                tool_choice="auto" if tools else None,
            )
            # Some newer models only allow default temperature and reject explicit values.
            create_kwargs["temperature"] = self.settings.model_temperature
            assistant_content, tool_calls, model_used, retry_attempts = self._model_round(create_kwargs, on_event, should_stop)
            used_models.add(model_used)

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
            if tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": assistant_content,
                        "tool_calls": [
                            {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                            for tc in tool_calls
                        ],
                    }
                )

                round_trace["finish_reason"] = "tool_calls"
                for tc in tool_calls:
                    check_stop()
                    tool_name = tc["name"]
                    round_trace["tool_calls"].append(tool_name)
                    try:
                        args = json.loads(tc["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}

                    if on_event:
                        on_event("tool_start", {"id": tc["id"], "name": tool_name})
                    tool_started = time.perf_counter()
                    proposal_id = None
                    try:
                        tool_result = self.mcp.call_tool(tool_name, args)
                        tool_error = None
                        proposal_id = self._record_proposal(self._unwrap_mcp_result(tool_result))
                    except Exception as exc:  # noqa: BLE001
                        tool_error = str(exc)
                        tool_result = {
                            "error": tool_error,
                            "tool_name": tool_name,
                            "arguments": args,
                        }
                    duration_ms = int((time.perf_counter() - tool_started) * 1000)
                    if on_event:
                        on_event("tool_end", {"id": tc["id"], "name": tool_name, "ok": tool_error is None,
                                              "duration_ms": duration_ms, "proposal": proposal_id})

                    used_tools.append(tool_name)
                    if tool_error:
                        round_trace["tool_errors"].append(tool_name)
                    if include_trace and trace is not None:
                        trace["notes"].append(
                            (
                                f"Round {round_index + 1} | tool={tool_name} | "
                                f"ok={tool_error is None} | duration_ms={duration_ms} | "
                                f"args={self._preview_json(args, limit=250)}"
                            )
                        )
                        if tool_error:
                            trace["notes"].append(f"Round {round_index + 1} | tool_error={tool_error}")

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
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
        if on_event:
            on_event("delta", {"content": answer})
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

    @_tracks_proposals
    def chat_turn(
        self,
        *,
        history: List[Dict[str, str]],
        message: str,
        system_prompt: str | None = None,
        on_event: ChatEventSink | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> Dict[str, Any]:
        """One assistant answer for the chat UI, including the approval proposals Archi created."""
        answer, used_tools, trace = self._chat_internal(
            history=history,
            message=message,
            system_prompt=system_prompt,
            include_trace=True,
            on_event=on_event,
            should_stop=should_stop,
        )
        return {"answer": answer, "used_tools": used_tools, "trace": trace or {}}

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
