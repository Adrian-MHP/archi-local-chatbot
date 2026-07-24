from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field


Role = Literal["user", "assistant"]


class ChatTurn(BaseModel):
    role: Role
    content: str = Field(default="", max_length=20000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    history: List[ChatTurn] = Field(default_factory=list)
    system_prompt: str | None = Field(default=None, max_length=20000)


class ChatStreamRequest(ChatRequest):
    stream_chunk_chars: int = Field(default=180, ge=20, le=2000)
    include_trace: bool = Field(default=True)


class ChatResponse(BaseModel):
    answer: str
    used_tools: List[str]


class ChatTraceRound(BaseModel):
    round: int
    model: str
    retry_attempts: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    assistant_content_chars: int = Field(default=0, ge=0)
    tool_calls: List[str] = Field(default_factory=list)
    tool_errors: List[str] = Field(default_factory=list)
    finish_reason: Literal["tool_calls", "final", "limit"]


class ChatTrace(BaseModel):
    started_at: str
    completed_at: str
    duration_ms: int = Field(default=0, ge=0)
    fallback_used: bool = Field(default=False)
    rounds: List[ChatTraceRound] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class ChatTraceResponse(ChatResponse):
    trace: ChatTrace


class ActionResponse(BaseModel):
    action: Literal["business-process-upload", "requirements-upload"]
    status: Literal["ok", "partial"]
    summary: str
    view_name: str
    view_id: str | None = None
    steps_processed: int = Field(default=0, ge=0)
    steps_truncated: bool = False
    created_elements: int = Field(default=0, ge=0)
    created_relationships: int = Field(default=0, ge=0)
    added_to_view: int = Field(default=0, ge=0)
    added_connections: int = Field(default=0, ge=0)
    used_tools: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class LayoutPosition(BaseModel):
    x: int = 0
    y: int = 0
    width: int = Field(default=180, gt=0)
    height: int = Field(default=70, gt=0)


class PlanElement(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    type: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    documentation: str = Field(default="", max_length=400)
    include: bool = True


class PlanRelationship(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    type: str = Field(min_length=1, max_length=80)
    source_key: str = Field(min_length=1, max_length=200)
    target_key: str = Field(min_length=1, max_length=200)
    name: str | None = Field(default=None, max_length=120)
    accessType: str | None = Field(default=None, max_length=16)
    include: bool = True


class AutomationPlan(BaseModel):
    action: Literal["business-process-upload", "requirements-upload"]
    source_name: str = Field(default="", max_length=200)
    view_name: str = Field(min_length=1, max_length=120)
    elements: List[PlanElement] = Field(default_factory=list)
    relationships: List[PlanRelationship] = Field(default_factory=list)
    layout_positions: Dict[str, LayoutPosition] = Field(default_factory=dict)
    steps_processed: int = Field(default=0, ge=0)
    steps_truncated: bool = False
    warnings: List[str] = Field(default_factory=list)


class AutomationPlanResponse(AutomationPlan):
    pass


class ApplyPlanRequest(BaseModel):
    plan: AutomationPlan


class ConversationTurn(BaseModel):
    role: Role
    content: str = Field(default="", max_length=20000)
    tools: List[str] = Field(default_factory=list)
    timestamp: str | None = Field(default=None, max_length=100)


class ConversationArchive(BaseModel):
    id: str | None = Field(default=None, max_length=120)
    title: str = Field(default="Conversation", min_length=1, max_length=200)
    created_at: str | None = Field(default=None, max_length=100)
    updated_at: str | None = Field(default=None, max_length=100)
    history: List[ConversationTurn] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ConversationExportRequest(BaseModel):
    conversation: ConversationArchive


class ConversationExportResponse(BaseModel):
    schema_version: str = "archi-chat-conversation/v1"
    exported_at: str
    message_count: int = Field(default=0, ge=0)
    conversation: ConversationArchive


class ConversationImportRequest(BaseModel):
    payload: Dict[str, Any]


class ConversationImportResponse(BaseModel):
    message_count: int = Field(default=0, ge=0)
    warnings: List[str] = Field(default_factory=list)
    conversation: ConversationArchive
