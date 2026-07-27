from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    azure_openai_api_key: str
    azure_openai_base_url: str
    azure_openai_model: str
    azure_openai_fallback_model: str
    azure_openai_extraction_model: str
    mcp_server_url: str
    mcp_bearer_token: str
    app_host: str
    app_port: int
    request_timeout_seconds: float
    model_temperature: float
    max_model_retries: int
    model_retry_backoff_seconds: float
    max_tool_roundtrips: int
    max_upload_bytes: int
    max_upload_text_chars: int
    max_action_steps: int
    default_system_prompt: str


def _normalize_azure_base_url(raw: str) -> str:
    url = (raw or "").strip()
    if not url:
        return url
    if "openai.azure.com" in url and "/openai/v1" not in url:
        return url.rstrip("/") + "/openai/v1"
    return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        azure_openai_api_key=os.getenv("AZURE_OPENAI_API_KEY", "").strip(),
        azure_openai_base_url=_normalize_azure_base_url(os.getenv("AZURE_OPENAI_BASE_URL", "")),
        azure_openai_model=os.getenv("AZURE_OPENAI_MODEL", "gpt-5.4").strip(),
        azure_openai_fallback_model=os.getenv("AZURE_OPENAI_FALLBACK_MODEL", "").strip(),
        azure_openai_extraction_model=os.getenv("AZURE_OPENAI_EXTRACTION_MODEL", "").strip(),
        mcp_server_url=os.getenv("MCP_SERVER_URL", "http://127.0.0.1:18090/mcp").strip(),
        mcp_bearer_token=os.getenv("MCP_BEARER_TOKEN", "").strip(),
        app_host=os.getenv("APP_HOST", "0.0.0.0").strip(),
        app_port=int(os.getenv("APP_PORT", "8000")),
        request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "60")),
        model_temperature=float(os.getenv("MODEL_TEMPERATURE", "0.2")),
        max_model_retries=int(os.getenv("MAX_MODEL_RETRIES", "3")),
        model_retry_backoff_seconds=float(os.getenv("MODEL_RETRY_BACKOFF_SECONDS", "1.5")),
        max_tool_roundtrips=int(os.getenv("MAX_TOOL_ROUNDTRIPS", "8")),
        max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", str(8 * 1024 * 1024))),
        max_upload_text_chars=int(os.getenv("MAX_UPLOAD_TEXT_CHARS", "80000")),
        max_action_steps=int(os.getenv("MAX_ACTION_STEPS", "60")),
        default_system_prompt=os.getenv(
            "SYSTEM_PROMPT",
            (
                "You are an ArchiMate assistant. Use MCP tools for all model facts. "
                "Do not invent elements, relationships, IDs, or view memberships. "
                "If data is missing from tool results, state that explicitly."
            ),
        ).strip(),
    )
