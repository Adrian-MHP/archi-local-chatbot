from __future__ import annotations

import json
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
        messages: List[Dict[str, Any]] = [
            {
                "role": "system",
                "content": (system_prompt or self.settings.default_system_prompt) + " " + ui_formatting_rules,
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

    def _call_model_with_retries(self, create_kwargs: Dict[str, Any]):
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
                    return self._call_model_once(kwargs_for_model)
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

    def chat(self, history: List[Dict[str, str]], message: str, system_prompt: str | None = None) -> Tuple[str, List[str]]:
        if not self.settings.azure_openai_api_key:
            raise RuntimeError("AZURE_OPENAI_API_KEY is not configured.")
        if not self.settings.azure_openai_base_url:
            raise RuntimeError("AZURE_OPENAI_BASE_URL is not configured.")

        mcp_tools = self.mcp.list_tools()
        tools = [self._tool_to_openai_format(t) for t in mcp_tools]

        messages = self._build_messages(history=history, message=message, system_prompt=system_prompt)
        used_tools: List[str] = []

        for _ in range(self.settings.max_tool_roundtrips):
            create_kwargs: Dict[str, Any] = dict(
                model=self.settings.azure_openai_model,
                messages=messages,
                tools=tools if tools else None,
                tool_choice="auto" if tools else None,
            )
            # Some newer models only allow default temperature and reject explicit values.
            create_kwargs["temperature"] = self.settings.model_temperature
            response = self._call_model_with_retries(create_kwargs)

            assistant = response.choices[0].message
            if assistant.tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": assistant.content or "",
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

                for tc in assistant.tool_calls:
                    tool_name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}

                    try:
                        tool_result = self.mcp.call_tool(tool_name, args)
                    except Exception as exc:  # noqa: BLE001
                        tool_result = {
                            "error": str(exc),
                            "tool_name": tool_name,
                            "arguments": args,
                        }

                    used_tools.append(tool_name)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(tool_result, ensure_ascii=False),
                        }
                    )
                continue

            return (assistant.content or "", used_tools)

        return (
            "I hit the tool round-trip limit before producing a final answer. Please refine the question.",
            used_tools,
        )
