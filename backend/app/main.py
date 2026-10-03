from __future__ import annotations

from datetime import date, datetime, timezone
import io
import json
import logging
import os
from queue import Empty, Queue
import re
from pathlib import Path
from threading import Event, Thread
from typing import Any, Dict
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import httpx
from pydantic import ValidationError

from .azure_agent import ChatService, ChatStopped
from .config import get_settings
from . import baselines, coarchi, dashboard, meta_model
from .schemas import (
    ActionResponse,
    ApplyPlanRequest,
    AssessmentSetupResponse,
    AssessmentSummaryRequest,
    AssessmentSummaryResponse,
    AutomationPlanResponse,
    BaselineCreateRequest,
    BaselineImportRequest,
    ChatRequest,
    ChatResponse,
    ChatStreamRequest,
    ChatTraceResponse,
    ConversationArchive,
    ConversationExportRequest,
    ConversationExportResponse,
    ConversationImportRequest,
    ConversationImportResponse,
    MappingApplyRequest,
    MappingApplyResponse,
    MappingGapRequest,
    MappingGapResponse,
    SollProposalRequest,
    SteeringApplyRequest,
    SteeringRequest,
)

load_dotenv()

settings = get_settings()
chat_service = ChatService(settings)

logger = logging.getLogger("uvicorn.error")

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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _history_from_payload(payload: ChatRequest) -> list[dict[str, str]]:
    return [{"role": item.role, "content": item.content} for item in payload.history]


def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _normalize_conversation(conversation: ConversationArchive) -> tuple[ConversationArchive, list[str]]:
    warnings: list[str] = []
    now = _now_iso()

    if not conversation.created_at:
        conversation.created_at = now
        warnings.append("created_at was missing and has been set automatically.")
    if not conversation.updated_at:
        conversation.updated_at = now
        warnings.append("updated_at was missing and has been set automatically.")
    if conversation.updated_at and conversation.created_at and conversation.updated_at < conversation.created_at:
        conversation.updated_at = conversation.created_at
        warnings.append("updated_at was earlier than created_at and has been corrected.")
    return conversation, warnings


def _normalize_extracted_text(text: str) -> str:
    cleaned = (text or "").replace("\x00", " ")
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _extract_text_from_pdf(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"PDF support is unavailable: {exc}") from exc

    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Unable to read PDF: {exc}") from exc

    text_parts: list[str] = []
    max_pages = 50
    for page_index, page in enumerate(reader.pages):
        if page_index >= max_pages:
            break
        page_text = page.extract_text() or ""
        if page_text.strip():
            text_parts.append(page_text)
    combined = "\n\n".join(text_parts)
    if not combined.strip():
        raise HTTPException(
            status_code=422,
            detail="No readable text found in PDF. If this is a scanned PDF, OCR is required first.",
        )
    return combined


def _extract_text_from_xlsx(content: bytes) -> str:
    try:
        from openpyxl import load_workbook
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Excel support is unavailable: {exc}") from exc

    try:
        workbook = load_workbook(filename=io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Unable to read Excel file: {exc}") from exc

    lines: list[str] = []
    max_sheets = 8
    max_rows_per_sheet = 500
    max_cells_per_row = 30

    try:
        for sheet_index, sheet in enumerate(workbook.worksheets):
            if sheet_index >= max_sheets:
                break
            lines.append(f"Sheet: {sheet.title}")
            for row_index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                if row_index > max_rows_per_sheet:
                    break
                values: list[str] = []
                for value in row[:max_cells_per_row]:
                    if value is None:
                        continue
                    string_value = str(value).strip()
                    if string_value:
                        values.append(string_value)
                if values:
                    lines.append(" | ".join(values))
    finally:
        workbook.close()

    combined = "\n".join(lines)
    if not combined.strip():
        raise HTTPException(status_code=422, detail="No readable rows found in Excel file.")
    return combined


async def _extract_text_from_upload(file: UploadFile) -> tuple[str, str, bytes | None]:
    filename = (file.filename or "upload").strip() or "upload"
    lower_name = filename.lower()
    suffix = f".{lower_name.rsplit('.', 1)[-1]}" if "." in lower_name else ""

    content = await file.read()
    size = len(content)
    if size <= 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if size > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Uploaded file exceeds size limit of {settings.max_upload_bytes} bytes.",
        )

    if suffix in {".txt", ".md", ".csv", ".json"}:
        try:
            raw_text = content.decode("utf-8")
        except UnicodeDecodeError:
            raw_text = content.decode("latin-1", errors="ignore")
    elif suffix == ".pdf":
        raw_text = _extract_text_from_pdf(content)
    elif suffix in {".xlsx", ".xlsm"}:
        raw_text = _extract_text_from_xlsx(content)
    elif suffix == ".xls":
        raise HTTPException(
            status_code=415,
            detail="Legacy .xls is not supported. Please upload .xlsx or export to PDF/text.",
        )
    else:
        raise HTTPException(
            status_code=415,
            detail="Unsupported file type. Use PDF, XLSX, XLSM, TXT, MD, CSV, or JSON.",
        )

    text = _normalize_extracted_text(raw_text)
    if not text:
        raise HTTPException(status_code=422, detail="No processable text could be extracted from uploaded file.")
    if len(text) > settings.max_upload_text_chars:
        text = text[: settings.max_upload_text_chars]
    pdf_bytes = content if suffix == ".pdf" else None
    return text, filename, pdf_bytes


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

    # Archi's approval mode, read from the plugin (not assumed), so the UI can show the real state.
    approval_mode = None
    pending_approvals = None
    archi_model = None
    if mcp_status == "ok":
        try:
            approvals = dashboard._payload(chat_service.mcp, "list-pending-approvals", {}).get("result") or {}
            approval_mode = bool(approvals.get("approvalMode"))
            pending_approvals = int(approvals.get("pendingCount") or 0)
        except Exception:  # noqa: BLE001
            approval_mode = None
        # Archi serves the model opened last; show which one, so nobody works on the wrong model.
        try:
            archi_model = (dashboard._payload(chat_service.mcp, "get-model-info", {}).get("result") or {}).get("name") or None
        except Exception:  # noqa: BLE001
            archi_model = None

    return {
        "status": "ok",
        "azure_model": settings.azure_openai_model,
        "mcp_server_url": settings.mcp_server_url,
        "mcp_status": mcp_status,
        "mcp_error": mcp_error,
        "mcp_tool_count": tool_count,
        "archi_model": archi_model,
        "archi_approval_mode": approval_mode,
        "archi_pending_approvals": pending_approvals,
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


@app.get("/api/dashboard")
def get_dashboard(as_of: str | None = Query(default=None, alias="asOf")) -> Dict[str, Any]:
    """Transformation dashboard metrics, computed live from the active Archi model via MCP."""
    try:
        today = date.fromisoformat(as_of) if as_of else date.today()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="asOf must be a date in YYYY-MM-DD format.") from exc
    try:
        return dashboard.build_dashboard(chat_service.mcp, today)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not read the Archi model via MCP: {exc}") from exc


# ---------------------------------------------------------------------------------------------
# Assessment baselines: the model per assessment cycle, versioned in git (baselines.py), and
# earlier model versions from the model's coArchi repository (coarchi.py)
# ---------------------------------------------------------------------------------------------

def _baseline_http_error(exc: baselines.BaselineError) -> HTTPException:
    if isinstance(exc, baselines.BaselineNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, baselines.BaselineConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, baselines.BaselineStorageError):
        return HTTPException(status_code=500, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


def _live_snapshot() -> Dict[str, Any]:
    try:
        return baselines.normalize_snapshot(dashboard.fetch_snapshot(chat_service.mcp))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not read the Archi model via MCP: {exc}") from exc


def _active_model_name() -> str:
    try:
        name = (dashboard._payload(chat_service.mcp, "get-model-info", {}).get("result") or {}).get("name")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not read the Archi model via MCP: {exc}") from exc
    if not name:
        raise HTTPException(status_code=422, detail="The active Archi model has no name, so it has no baselines.")
    return str(name)


def _coarchi_info(model_name: str) -> Dict[str, Any]:
    try:
        repo = coarchi.repository_for(Path(settings.coarchi_dir), model_name)
    except Exception as exc:  # noqa: BLE001 -- coArchi is optional
        return {"available": False, "reason": str(exc)}
    if repo is None:
        return {"available": False,
                "reason": f"No coArchi repository of '{model_name}' in {settings.coarchi_display_path}."}
    return {"available": True, "repository": repo["folder"], "head": repo["head"][:7],
            "path": f"{settings.coarchi_display_path.rstrip('/')}/{repo['folder']}"}


def _parse_day(value: str | None, *, field: str = "asOf") -> date:
    if not value:
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field} must be a date in YYYY-MM-DD format.") from exc


@app.get("/api/baselines")
def list_baselines() -> Dict[str, Any]:
    model_name = _active_model_name()
    store = chat_service.baselines
    return {"model": {"name": model_name}, "baselines": store.list(model_name), "store": store.status(),
            "coarchi": _coarchi_info(model_name)}


@app.post("/api/baselines")
def create_baseline(payload: BaselineCreateRequest) -> Dict[str, Any]:
    """Freeze the live model as the baseline of an assessment cycle (one git commit + tag)."""
    day = _parse_day(payload.date, field="date")
    if abs((day - date.today()).days) > 1:  # the client's local date, within a day of the server's
        raise HTTPException(status_code=422, detail="A baseline is dated the day the model is frozen, i.e. today.")
    snapshot = _live_snapshot()
    try:
        return chat_service.baselines.create(
            snapshot, name=payload.name, note=payload.note, day=day,
            source={"type": "mcp", "modelVersion": snapshot["model"]["modelVersion"]},
        )
    except baselines.BaselineError as exc:
        raise _baseline_http_error(exc) from exc


@app.delete("/api/baselines/{baseline_id}")
def delete_baseline(baseline_id: str) -> Dict[str, Any]:
    try:
        return chat_service.baselines.delete(baseline_id, _active_model_name())
    except baselines.BaselineError as exc:
        raise _baseline_http_error(exc) from exc


@app.get("/api/baselines/progress")
def baseline_progress(
    compare_from: str | None = Query(default=None, alias="from"),
    compare_to: str | None = Query(default=None, alias="to"),
    as_of: str | None = Query(default=None, alias="asOf"),
) -> Dict[str, Any]:
    """Every baseline of the active model plus the live model: trend per metric, maturity per
    capability and cycle, outcome KPI measurements, and the comparison of two points."""
    today = _parse_day(as_of)
    snapshot = _live_snapshot()
    model_name = snapshot["model"]["name"]
    store = chat_service.baselines
    metas = store.list(model_name)
    points, warnings = [], []
    for meta in metas:
        try:
            points.append({"id": meta["id"], "label": meta["name"], "date": meta["date"], "moment": baselines.moment(meta), "kind": "baseline",
                           "source": meta.get("source"), "note": meta.get("note") or "",
                           "snapshot": store.snapshot(meta), "dashboard": store.evaluated(meta)})
        except baselines.BaselineError as exc:
            warnings.append(str(exc))
    points.append({"id": baselines.CURRENT, "label": "Now", "date": today.isoformat(), "moment": f"{today.isoformat()}T23:59:59+00:00", "kind": "current",
                   "source": {"type": "mcp"}, "note": "", "snapshot": snapshot,
                   "dashboard": baselines.evaluate(snapshot, today)})
    points.sort(key=lambda p: (p["date"], p["kind"] == "current", p["moment"]))
    report = baselines.build_progress(points, compare_from, compare_to)
    return {"model": {"name": model_name}, "asOf": today.isoformat(), "baselines": metas, "store": store.status(),
            "coarchi": _coarchi_info(model_name), "warnings": warnings, **report}


@app.get("/api/coarchi/commits")
def coarchi_commits() -> Dict[str, Any]:
    model_name = _active_model_name()
    repo = coarchi.repository_for(Path(settings.coarchi_dir), model_name)
    if repo is None:
        raise HTTPException(status_code=404, detail=_coarchi_info(model_name).get("reason"))
    try:
        commits = coarchi.commits(repo["path"])
    except coarchi.CoArchiError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    imported = {(m.get("source") or {}).get("commit"): m["name"] for m in chat_service.baselines.list(model_name)}
    for commit in commits:
        commit["baseline"] = imported.get(commit["commit"])
    return {"repository": repo["folder"], "commits": commits}


@app.post("/api/baselines/import")
def import_baseline(payload: BaselineImportRequest) -> Dict[str, Any]:
    """A commit of the model's coArchi repository as a baseline, dated with the commit date."""
    model_name = _active_model_name()
    repo = coarchi.repository_for(Path(settings.coarchi_dir), model_name)
    if repo is None:
        raise HTTPException(status_code=404, detail=_coarchi_info(model_name).get("reason"))
    try:
        commit = next((c for c in coarchi.commits(repo["path"], limit=1000) if c["commit"].startswith(payload.commit)), None)
        if commit is None:
            raise HTTPException(status_code=404, detail=f"Commit {payload.commit} is not in the history of {repo['folder']}.")
        snapshot = coarchi.snapshot_at(repo["path"], commit["commit"])
    except coarchi.CoArchiError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    snapshot["model"]["name"] = model_name  # matched by name; keep the spelling of the live model
    name = payload.name or (commit["tags"][0] if commit["tags"] else f"coArchi {commit['date']}: {commit['message']}"[:80])
    try:
        return chat_service.baselines.create(
            snapshot, name=name, note=payload.note, day=date.fromisoformat(commit["date"]),
            source={"type": "coarchi", "repository": repo["folder"], "commit": commit["commit"],
                    "commitDate": commit["committedAt"], "author": commit["author"], "message": commit["message"]},
        )
    except baselines.BaselineError as exc:
        raise _baseline_http_error(exc) from exc


@app.get("/api/meta-model")
def get_meta_model() -> Dict[str, Any]:
    return meta_model.as_dict()


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
    if settings.mcp_host_header:
        headers["Host"] = settings.mcp_host_header

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
    history = _history_from_payload(payload)
    try:
        result = chat_service.chat_turn(history=history, message=payload.message, system_prompt=payload.system_prompt)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ChatResponse(answer=result["answer"], used_tools=result["used_tools"], proposals=result.get("proposals", []))


@app.post("/api/chat/trace", response_model=ChatTraceResponse)
def chat_trace(payload: ChatRequest) -> ChatTraceResponse:
    history = _history_from_payload(payload)
    try:
        answer, used_tools, trace = chat_service.chat_with_trace(
            history=history,
            message=payload.message,
            system_prompt=payload.system_prompt,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ChatTraceResponse(answer=answer, used_tools=used_tools, trace=trace)


@app.post("/api/chat/stream")
def chat_stream(payload: ChatStreamRequest) -> StreamingResponse:
    """Server-sent events while the assistant works: start, round, tool_start / tool_end (each MCP call,
    with the approval proposal it created), delta (answer text as the model writes it), draft_reset,
    done {answer, used_tools, proposals[, trace]}, error, close. When the client disconnects (Stop),
    no further model or tool call is made."""
    history = _history_from_payload(payload)
    events: Queue[tuple[str, Any]] = Queue()
    stopped = Event()

    def worker() -> None:
        try:
            result = chat_service.chat_turn(
                history=history,
                message=payload.message,
                system_prompt=payload.system_prompt,
                on_event=lambda name, data: events.put((name, data)),
                should_stop=stopped.is_set,
            )
            done = {"answer": result["answer"], "used_tools": result["used_tools"], "proposals": result.get("proposals", [])}
            if payload.include_trace:
                done["trace"] = result.get("trace", {})
            events.put(("done", done))
        except ChatStopped:
            logger.info("Chat request stopped by the client; no further model or tool call was made.")
        except Exception as exc:  # noqa: BLE001
            events.put(("error", {"detail": str(exc)}))
        finally:
            events.put(("end", None))

    def event_stream():
        Thread(target=worker, daemon=True).start()
        stream_id = str(uuid4())
        try:
            yield _sse("start", {"stream_id": stream_id, "started_at": _now_iso()})
            while True:
                try:
                    kind, data = events.get(timeout=1.0)
                except Empty:
                    yield ": keep-alive\n\n"
                    continue
                if kind == "end":
                    break
                yield _sse(kind, data)
            yield _sse("close", {"stream_id": stream_id, "completed_at": _now_iso()})
        finally:
            stopped.set()  # also reached when the client goes away mid-answer

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/actions/business-process-upload", response_model=ActionResponse)
async def business_process_upload_action(
    file: UploadFile = File(...),
    view_name: str | None = Form(default=None),
) -> ActionResponse:
    text, source_name, pdf_bytes = await _extract_text_from_upload(file)
    try:
        execution = chat_service.run_business_process_automation(
            content_text=text,
            source_name=source_name,
            view_name=view_name,
            pdf_bytes=pdf_bytes,
        )
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Business process automation failed: {exc}") from exc

    steps_processed = int(execution.get("steps_processed", 0))
    steps_truncated = bool(execution.get("steps_truncated", False))
    status = "partial" if steps_truncated else "ok"
    summary = (
        f"Processed {steps_processed} process steps from '{source_name}'. "
        f"View '{execution.get('view_name', view_name or 'Business Process View')}' was updated in one automation run."
        + chat_service.approval_note(execution.get("proposals"))
    )
    if steps_truncated:
        summary += f" Steps were truncated to the configured maximum of {settings.max_action_steps}."

    return ActionResponse(
        action="business-process-upload",
        status=status,
        summary=summary,
        view_name=str(execution.get("view_name", view_name or "Business Process View")),
        view_id=execution.get("view_id"),
        steps_processed=steps_processed,
        steps_truncated=steps_truncated,
        created_elements=int(execution.get("created_elements", 0)),
        created_relationships=int(execution.get("created_relationships", 0)),
        added_to_view=int(execution.get("added_to_view", 0)),
        added_connections=int(execution.get("added_connections", 0)),
        used_tools=list(execution.get("used_tools", [])),
    )


@app.post("/api/actions/requirements-upload", response_model=ActionResponse)
async def requirements_upload_action(
    file: UploadFile = File(...),
    view_name: str | None = Form(default=None),
) -> ActionResponse:
    text, source_name, _pdf_bytes = await _extract_text_from_upload(file)
    try:
        execution = chat_service.run_requirements_automation(
            content_text=text,
            source_name=source_name,
            view_name=view_name,
        )
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Requirements automation failed: {exc}") from exc

    steps_processed = int(execution.get("steps_processed", 0))
    steps_truncated = bool(execution.get("steps_truncated", False))
    status = "partial" if steps_truncated else "ok"
    summary = (
        f"Generated product architecture from '{source_name}' and updated view "
        f"'{execution.get('view_name', view_name or 'Product Architecture')}' in one automation run."
        + chat_service.approval_note(execution.get("proposals"))
    )
    if steps_truncated:
        summary += " Input was truncated to fit the configured action limits."

    return ActionResponse(
        action="requirements-upload",
        status=status,
        summary=summary,
        view_name=str(execution.get("view_name", view_name or "Product Architecture")),
        view_id=execution.get("view_id"),
        steps_processed=steps_processed,
        steps_truncated=steps_truncated,
        created_elements=int(execution.get("created_elements", 0)),
        created_relationships=int(execution.get("created_relationships", 0)),
        added_to_view=int(execution.get("added_to_view", 0)),
        added_connections=int(execution.get("added_connections", 0)),
        used_tools=list(execution.get("used_tools", [])),
    )


@app.post("/api/actions/business-process-upload/preview", response_model=AutomationPlanResponse)
async def business_process_upload_preview(
    files: list[UploadFile] = File(...),
    view_name: str | None = Form(default=None),
) -> AutomationPlanResponse:
    uploads = [await _extract_text_from_upload(file) for file in files]
    try:
        plan = chat_service.plan_business_process_automation(
            uploads=uploads,
            view_name=view_name,
        )
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Business process preview failed: {exc}") from exc
    return AutomationPlanResponse(**plan)


@app.post("/api/actions/requirements-upload/preview", response_model=AutomationPlanResponse)
async def requirements_upload_preview(
    file: UploadFile = File(...),
    view_name: str | None = Form(default=None),
) -> AutomationPlanResponse:
    text, source_name, _pdf_bytes = await _extract_text_from_upload(file)
    try:
        plan = chat_service.plan_requirements_automation(
            content_text=text,
            source_name=source_name,
            view_name=view_name,
        )
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Requirements preview failed: {exc}") from exc
    return AutomationPlanResponse(**plan)


@app.post("/api/actions/apply", response_model=ActionResponse)
def apply_plan(payload: ApplyPlanRequest) -> ActionResponse:
    plan_dict = payload.plan.model_dump()
    try:
        execution = chat_service.apply_plan(plan_dict)
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to apply plan: {exc}") from exc

    created = int(execution.get("created_elements", 0))
    reused = int(execution.get("reused_elements", 0))
    relationships = int(execution.get("created_relationships", 0))
    view = execution.get("view_name", payload.plan.view_name)
    if not (created or relationships or execution.get("added_to_view") or execution.get("added_connections")):
        summary = f"Nothing new to write: all {reused} element(s) and their relationships already exist in view '{view}'."
    else:
        summary = (
            f"Applied plan to view '{view}': {created} new element(s)"
            + (f", {reused} existing element(s) reused" if reused else "")
            + f", {relationships} new relationship(s)."
        )
    summary += chat_service.approval_note(execution.get("proposals"))

    return ActionResponse(
        action=payload.plan.action,
        status="ok",
        summary=summary,
        view_name=str(execution.get("view_name", payload.plan.view_name)),
        view_id=execution.get("view_id"),
        steps_processed=int(execution.get("steps_processed", 0)),
        steps_truncated=False,
        created_elements=int(execution.get("created_elements", 0)),
        created_relationships=int(execution.get("created_relationships", 0)),
        added_to_view=int(execution.get("added_to_view", 0)),
        added_connections=int(execution.get("added_connections", 0)),
        used_tools=list(execution.get("used_tools", [])),
    )


@app.get("/api/assessment/setup", response_model=AssessmentSetupResponse)
def assessment_setup(
    ist_view_name: str = "As-Is Business Processes",
    soll_view_name: str = "To-Be Business Processes",
) -> AssessmentSetupResponse:
    try:
        result = chat_service.assessment_setup(ist_view_name=ist_view_name, soll_view_name=soll_view_name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Assessment setup failed: {exc}") from exc
    return AssessmentSetupResponse(**result)


@app.post("/api/assessment/soll-architecture/preview", response_model=AutomationPlanResponse)
async def assessment_soll_architecture_preview(
    files: list[UploadFile] = File(...),
    view_name: str | None = Form(default=None),
    ist_view_name: str | None = Form(default=None),
) -> AutomationPlanResponse:
    uploads = [await _extract_text_from_upload(file) for file in files]
    try:
        plan = chat_service.plan_soll_architecture(
            uploads=uploads,
            view_name=view_name,
            ist_view_name=ist_view_name,
        )
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"To-Be Architecture preview failed: {exc}") from exc
    return AutomationPlanResponse(**plan)


@app.post("/api/assessment/soll-architecture/propose", response_model=AutomationPlanResponse)
def assessment_soll_architecture_propose(payload: SollProposalRequest) -> AutomationPlanResponse:
    try:
        plan = chat_service.propose_soll_architecture(
            ist_view_name=payload.ist_view_name,
            view_name=payload.view_name,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"To-Be Architecture proposal failed: {exc}") from exc
    return AutomationPlanResponse(**plan)


@app.post("/api/assessment/mapping/preview", response_model=MappingGapResponse)
def assessment_mapping_preview(payload: MappingGapRequest) -> MappingGapResponse:
    try:
        result = chat_service.run_mapping_gap_analysis(
            ist_view_name=payload.ist_view_name,
            soll_view_name=payload.soll_view_name,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Mapping & gap analysis failed: {exc}") from exc
    return MappingGapResponse(**result)


@app.post("/api/assessment/mapping/apply", response_model=MappingApplyResponse)
def assessment_mapping_apply(payload: MappingApplyRequest) -> MappingApplyResponse:
    mapping_dicts = [m.model_dump() for m in payload.mappings]
    gap_dicts = [g.model_dump() for g in payload.gaps]
    try:
        result = chat_service.apply_mapping_relationships(
            mappings=mapping_dicts, gaps=gap_dicts, view_name=payload.view_name
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Applying mappings failed: {exc}") from exc
    return MappingApplyResponse(**result)


@app.post("/api/assessment/steering/load")
def assessment_steering_load(payload: SteeringRequest) -> Dict[str, Any]:
    try:
        return chat_service.steering_load(ist_view_names=payload.ist_view_names, soll_view_names=payload.soll_view_names)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Loading ratings & roadmap failed: {exc}") from exc


@app.post("/api/assessment/steering/propose")
def assessment_steering_propose(payload: SteeringRequest) -> Dict[str, Any]:
    try:
        return chat_service.steering_propose(
            ist_view_names=payload.ist_view_names, soll_view_names=payload.soll_view_names, state=payload.state
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"AI proposal failed: {exc}") from exc


@app.post("/api/assessment/steering/apply")
def assessment_steering_apply(payload: SteeringApplyRequest) -> Dict[str, Any]:
    try:
        return chat_service.steering_apply(state=payload.state)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Applying ratings & roadmap failed: {exc}") from exc


@app.post("/api/assessment/summary", response_model=AssessmentSummaryResponse)
def assessment_summary(payload: AssessmentSummaryRequest) -> AssessmentSummaryResponse:
    mapping_dicts = [m.model_dump() for m in payload.mappings]
    gap_dicts = [g.model_dump() for g in payload.gaps]
    try:
        result = chat_service.generate_assessment_summary(mappings=mapping_dicts, gaps=gap_dicts)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Summary generation failed: {exc}") from exc
    return AssessmentSummaryResponse(**result)


@app.post("/api/conversations/export", response_model=ConversationExportResponse)
def export_conversation(payload: ConversationExportRequest) -> ConversationExportResponse:
    conversation = payload.conversation.model_copy(deep=True)
    conversation, _warnings = _normalize_conversation(conversation)
    now = _now_iso()

    return ConversationExportResponse(
        exported_at=now,
        message_count=len(conversation.history),
        conversation=conversation,
    )


@app.post("/api/conversations/import", response_model=ConversationImportResponse)
def import_conversation(payload: ConversationImportRequest) -> ConversationImportResponse:
    warnings: list[str] = []
    raw_payload = payload.payload

    conversation_payload = raw_payload.get("conversation")
    if isinstance(conversation_payload, dict):
        raw_conversation = conversation_payload
        schema_version = raw_payload.get("schema_version")
        if schema_version and schema_version != "archi-chat-conversation/v1":
            warnings.append(f"Unexpected schema_version '{schema_version}'. Imported with best-effort parsing.")
    else:
        raw_conversation = raw_payload
        warnings.append("Imported payload did not contain an export envelope. Parsed as raw conversation object.")

    try:
        conversation = ConversationArchive.model_validate(raw_conversation)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid conversation payload: {exc}") from exc

    conversation, normalize_warnings = _normalize_conversation(conversation)
    warnings.extend(normalize_warnings)
    return ConversationImportResponse(
        message_count=len(conversation.history),
        warnings=warnings,
        conversation=conversation,
    )
