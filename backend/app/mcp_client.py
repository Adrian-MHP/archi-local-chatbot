from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, Dict, List

import httpx


class McpProtocolError(RuntimeError):
    pass


@dataclass
class McpTool:
    name: str
    description: str
    input_schema: Dict[str, Any]


class McpClient:
    def __init__(self, server_url: str, bearer_token: str = "", timeout_seconds: float = 60.0):
        self.server_url = server_url
        self.bearer_token = bearer_token
        self.timeout_seconds = timeout_seconds

        self._id_lock = threading.Lock()
        self._next_id = 1
        self._session_id: str | None = None
        self._initialized = False

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        return headers

    def _next_request_id(self) -> int:
        with self._id_lock:
            current = self._next_id
            self._next_id += 1
            return current

    @staticmethod
    def _extract_sse_data_chunks(raw_text: str) -> List[str]:
        chunks: List[str] = []
        current: List[str] = []
        for line in raw_text.splitlines():
            if line.startswith("data:"):
                current.append(line[5:].lstrip())
                continue
            if line.strip() == "":
                if current:
                    chunks.append("\n".join(current))
                    current = []
                continue
        if current:
            chunks.append("\n".join(current))
        return chunks

    def _parse_jsonrpc_payload(self, response: httpx.Response) -> Dict[str, Any]:
        content_type = (response.headers.get("content-type") or "").lower()
        text = response.text.strip()

        # Some MCP servers stream responses using SSE even for non-streaming RPC calls.
        if "text/event-stream" in content_type or text.startswith("id:") or "\nevent:" in text:
            chunks = self._extract_sse_data_chunks(text)
            for chunk in reversed(chunks):
                candidate = chunk.strip()
                if not candidate:
                    continue
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    continue
            raise McpProtocolError(
                f"Could not parse SSE JSON payload from MCP server. Raw response: {text[:2500]}"
            )

        try:
            parsed = response.json()
        except json.JSONDecodeError as exc:
            raise McpProtocolError(
                f"Invalid JSON payload from MCP server. Raw response: {text[:2500]}"
            ) from exc
        if not isinstance(parsed, dict):
            raise McpProtocolError(f"Unexpected MCP payload type: {type(parsed).__name__}")
        return parsed

    def _request(
        self,
        payload: Dict[str, Any],
        expect_response: bool = True,
        raise_for_status: bool = True,
    ) -> Dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(self.server_url, headers=self._build_headers(), json=payload)

        if response.headers.get("mcp-session-id"):
            self._session_id = response.headers.get("mcp-session-id")

        if raise_for_status and response.status_code >= 400:
            body_preview = response.text[:2500]
            try:
                parsed = response.json()
                if isinstance(parsed, dict) and parsed.get("message"):
                    body_preview = f"{parsed.get('message')} | raw={body_preview}"
            except Exception:
                pass
            raise McpProtocolError(
                f"HTTP {response.status_code} from MCP server at {self.server_url}: {body_preview}"
            )

        if not expect_response:
            return {}

        data = self._parse_jsonrpc_payload(response)
        if "error" in data:
            err = data["error"]
            raise McpProtocolError(f"MCP error {err.get('code')}: {err.get('message')}")
        if "result" not in data:
            raise McpProtocolError(f"Invalid MCP response: {json.dumps(data, ensure_ascii=False)}")
        return data["result"]

    def initialize(self) -> None:
        if self._initialized:
            return

        init_payload = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "archi-local-chatbot", "version": "0.1.0"},
            },
        }
        init_result = self._request(init_payload)
        if isinstance(init_result, dict):
            sid_from_body = (
                init_result.get("sessionId")
                or init_result.get("session_id")
                or init_result.get("mcpSessionId")
            )
            if sid_from_body and not self._session_id:
                self._session_id = str(sid_from_body)

        # Some MCP server implementations reject initialized notifications over this transport.
        # Keep this non-blocking so tool usage still works after a successful initialize handshake.
        initialized_notification = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        try:
            self._request(initialized_notification, expect_response=False, raise_for_status=False)
        except Exception:
            pass
        self._initialized = True

    def list_tools(self) -> List[McpTool]:
        self.initialize()
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "tools/list",
        }
        result = self._request(payload)
        tools = result.get("tools", []) if isinstance(result, dict) else []

        parsed: List[McpTool] = []
        for t in tools:
            parsed.append(
                McpTool(
                    name=str(t.get("name", "")),
                    description=str(t.get("description", "")),
                    input_schema=t.get("inputSchema") or {"type": "object", "properties": {}},
                )
            )
        return parsed

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        self.initialize()
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }
        return self._request(payload)
