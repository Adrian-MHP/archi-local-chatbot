"""Minimal stdlib MCP client for seeding scripts that run on the host against Archi's MCP server.

Kept separate from backend/app/mcp_client.py on purpose: the seed scripts run outside Docker
(no httpx, no Host-header workaround needed on loopback) and must not depend on the backend image.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, List


class McpError(RuntimeError):
    pass


class SeedMcpClient:
    def __init__(self, url: str = "http://127.0.0.1:18090/mcp", bearer_token: str = "", timeout: float = 300.0):
        self.url = url
        self.bearer_token = bearer_token
        self.timeout = timeout
        self._session_id: str | None = None
        self._next_id = 1
        self._initialize()

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any] | None:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        request = urllib.request.Request(self.url, json.dumps(payload).encode("utf-8"), headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            session_id = response.headers.get("mcp-session-id")
            if session_id:
                self._session_id = session_id
            body = response.read().decode("utf-8")
            content_type = (response.headers.get("content-type") or "").lower()
        if not body.strip():
            return None
        if "text/event-stream" in content_type:
            data_lines = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
            body = data_lines[-1] if data_lines else "{}"
        message = json.loads(body)
        if "error" in message:
            raise McpError(json.dumps(message["error"]))
        return message.get("result")

    def _request(self, method: str, params: Dict[str, Any] | None = None) -> Any:
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        self._next_id += 1
        if params is not None:
            payload["params"] = params
        return self._post(payload)

    def _initialize(self) -> None:
        self._request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "archi-local-chatbot-seed", "version": "1.0.0"},
            },
        )
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, tool: str, arguments: Dict[str, Any] | None = None) -> Any:
        """Call a tool and return its unwrapped "result" payload; raise on tool-level errors."""
        data = self.call_raw(tool, arguments)
        return data.get("result", data) if isinstance(data, dict) else data

    def call_raw(self, tool: str, arguments: Dict[str, Any] | None = None) -> Any:
        """Call a tool and return the whole parsed payload (result, nextSteps, _meta)."""
        result = self._request("tools/call", {"name": tool, "arguments": arguments or {}})
        text = ""
        for block in (result or {}).get("content", []):
            if block.get("type") == "text":
                text = block.get("text") or ""
        data = json.loads(text) if text.strip().startswith(("{", "[")) else {"text": text}
        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            raise McpError(f"{tool}: {json.dumps(data['error'])[:2000]}")
        if (result or {}).get("isError"):
            raise McpError(f"{tool}: {text[:2000]}")
        return data

    def bulk(self, operations: List[Dict[str, Any]], description: str) -> List[Dict[str, Any]]:
        """Run bulk-mutate in chunks of at most 150 operations. Operations must not back-reference
        across chunk boundaries -- callers that need back-references keep them within one chunk."""
        results: List[Dict[str, Any]] = []
        for start in range(0, len(operations), 150):
            chunk = operations[start : start + 150]
            result = self.call("bulk-mutate", {"operations": chunk, "description": description})
            if isinstance(result, dict) and result.get("proposal"):
                raise McpError(
                    "Archi queued the change for approval instead of applying it. Switch off "
                    "MCP Server > Approval Mode in Archi and run the script again."
                )
            if isinstance(result, dict) and result.get("allSucceeded") is False:
                raise McpError(f"bulk-mutate did not fully succeed: {json.dumps(result)[:2000]}")
            results.extend(result.get("operations", []))
        return results

    def search_all(self, tool: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Page through search-elements / search-relationships."""
        items: List[Dict[str, Any]] = []
        args = {**arguments, "limit": 500}
        while True:
            data = self.call_raw(tool, args)
            page = data.get("result") or []
            items.extend(page if isinstance(page, list) else [])
            cursor = (data.get("_meta") or {}).get("cursor")
            if not cursor:
                return items
            args = {"cursor": cursor, "limit": 500}

    def approval_mode_on(self) -> bool:
        return bool(self.call("list-pending-approvals").get("approvalMode"))
