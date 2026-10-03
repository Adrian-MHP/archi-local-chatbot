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
    include_trace: bool = Field(default=False)


class ChatResponse(BaseModel):
    answer: str
    used_tools: List[str]
    # Approval proposals Archi created for changes made in this answer (approval mode on).
    proposals: List[str] = Field(default_factory=list)


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
    action: Literal["business-process-upload", "requirements-upload", "assessment-soll-upload"]
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
    properties: Dict[str, str] = Field(default_factory=dict)
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
    action: Literal["business-process-upload", "requirements-upload", "assessment-soll-upload"]
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


class AssessmentSetupResponse(BaseModel):
    ist_view_name: str
    soll_view_name: str
    ist_view_exists: bool
    soll_view_exists: bool
    existing_business_element_count: int = Field(default=0, ge=0)
    notes: List[str] = Field(default_factory=list)


class MappingEntry(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    ist_key: str | None = Field(default=None, max_length=200)
    ist_name: str | None = Field(default=None, max_length=200)
    soll_key: str | None = Field(default=None, max_length=200)
    soll_name: str | None = Field(default=None, max_length=200)
    match_type: Literal["full", "partial", "gap_new", "legacy_no_soll"]
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=400)
    include: bool = True


class GapEntry(BaseModel):
    category: Literal["missing_process", "redundancy", "structural_difference", "tooling_data_gap"]
    criticality: Literal["high", "medium", "low"]
    description: str = Field(default="", max_length=400)
    related_ist_name: str | None = Field(default=None, max_length=200)
    related_soll_name: str | None = Field(default=None, max_length=200)


class SollProposalRequest(BaseModel):
    ist_view_name: str = Field(default="As-Is Business Processes", max_length=120)
    view_name: str | None = Field(default=None, max_length=120)


class MappingGapRequest(BaseModel):
    ist_view_name: str = Field(default="As-Is Business Processes", max_length=120)
    soll_view_name: str = Field(default="To-Be Business Processes", max_length=120)


class MappingGapResponse(BaseModel):
    ist_view_name: str
    soll_view_name: str
    mappings: List[MappingEntry] = Field(default_factory=list)
    gaps: List[GapEntry] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class MappingApplyRequest(BaseModel):
    mappings: List[MappingEntry] = Field(default_factory=list)
    gaps: List[GapEntry] = Field(default_factory=list)
    view_name: str = Field(default="As-Is To-Be Mapping", max_length=120)


class MappingApplyResponse(BaseModel):
    summary: str
    view_name: str | None = None
    view_id: str | None = None
    created_relationships: int = Field(default=0, ge=0)
    used_tools: List[str] = Field(default_factory=list)


class SteeringRequest(BaseModel):
    ist_view_names: List[str] = Field(default_factory=list)
    soll_view_names: List[str] = Field(default_factory=list)
    state: Dict[str, Any] | None = None


class SteeringApplyRequest(BaseModel):
    state: Dict[str, Any]


class AssessmentSummaryRequest(BaseModel):
    mappings: List[MappingEntry] = Field(default_factory=list)
    gaps: List[GapEntry] = Field(default_factory=list)


class AssessmentSummaryResponse(BaseModel):
    ist_process_count: int = Field(default=0, ge=0)
    soll_process_count: int = Field(default=0, ge=0)
    full_matches: int = Field(default=0, ge=0)
    partial_matches: int = Field(default=0, ge=0)
    gap_count: int = Field(default=0, ge=0)
    critical_gap_count: int = Field(default=0, ge=0)
    medium_gap_count: int = Field(default=0, ge=0)
    low_gap_count: int = Field(default=0, ge=0)
    average_similarity: float = Field(default=0.0, ge=0.0, le=100.0)
    maturity_score: float = Field(default=0.0, ge=0.0, le=100.0)
    readiness_label: str = ""
    headline: str = ""
    key_findings: List[str] = Field(default_factory=list)
    top_risks: List[str] = Field(default_factory=list)
    recommendation: str = ""
    next_steps: List[str] = Field(default_factory=list)
    executive_summary: str = ""


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


class BaselineCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=1000)
    # The client's local date; the baseline is dated the day the model is frozen.
    date: str | None = Field(default=None, max_length=10)


class BaselineImportRequest(BaseModel):
    commit: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    name: str | None = Field(default=None, max_length=80)
    note: str = Field(default="", max_length=1000)
