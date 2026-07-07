from __future__ import annotations

import os
from typing import Any, Dict

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx

from .azure_agent import ChatService
from .config import get_settings
from .schemas import ChatRequest, ChatResponse

load_dotenv()

settings = get_settings()
chat_service = ChatService(settings)

app = FastAPI(title="Archi Local Chatbot API", version="0.1.0")

allowed_origins = os.getenv("ALLOWED_ORIGINS", "*")
origins = [o.strip() for o in allowed_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> Dict[str, Any]:
    mcp_status = "ok"
    mcp_error = None
    try:
        tool_count = len(chat_service.mcp.list_tools())
    except Exception as exc:  # noqa: BLE001
        mcp_status = "error"
        mcp_error = str(exc)
        tool_count = 0

    return {
        "status": "ok",
        "azure_model": settings.azure_openai_model,
        "mcp_server_url": settings.mcp_server_url,
        "mcp_status": mcp_status,
        "mcp_error": mcp_error,
        "mcp_tool_count": tool_count,
    }


@app.get("/api/tools")
def tools() -> Dict[str, Any]:
    try:
        mcp_tools = chat_service.mcp.list_tools()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Failed to list MCP tools: {exc}") from exc

    return {
        "count": len(mcp_tools),
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in mcp_tools
        ],
    }


@app.get("/api/debug/mcp")
def debug_mcp() -> Dict[str, Any]:
    """Low-level MCP handshake diagnostics to troubleshoot 400 responses."""
    url = settings.mcp_server_url
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if settings.mcp_bearer_token:
        headers["Authorization"] = f"Bearer {settings.mcp_bearer_token}"

    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "archi-local-chatbot-debug", "version": "0.1.0"},
        },
    }

    out: Dict[str, Any] = {
        "server_url": url,
        "has_bearer_token": bool(settings.mcp_bearer_token),
        "token_length": len(settings.mcp_bearer_token or ""),
    }

    with httpx.Client(timeout=settings.request_timeout_seconds) as client:
        init_resp = client.post(url, headers=headers, json=init_payload)
        sid = init_resp.headers.get("mcp-session-id")
        out["initialize"] = {
            "status_code": init_resp.status_code,
            "session_header": sid,
            "body": init_resp.text[:2500],
        }

        list_headers = dict(headers)
        if sid:
            list_headers["mcp-session-id"] = sid
        tools_payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        tools_resp = client.post(url, headers=list_headers, json=tools_payload)
        out["tools_list"] = {
            "status_code": tools_resp.status_code,
            "session_sent": bool(sid),
            "body": tools_resp.text[:2500],
        }

    return out


@app.post("/api/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    history = [{"role": item.role, "content": item.content} for item in payload.history]
    try:
        answer, used_tools = chat_service.chat(
            history=history,
            message=payload.message,
            system_prompt=payload.system_prompt,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ChatResponse(answer=answer, used_tools=used_tools)
