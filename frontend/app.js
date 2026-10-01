const STORAGE_KEY = "archi-local-chatbot-ui-v3";
const SYSTEM_PROMPT_KEY = "archi-local-chatbot-system-prompt-v1";
const CHAT_COLLAPSED_KEY = "archi-local-chatbot-chat-collapsed-v1";
const MAX_CONVERSATIONS = 30;
const MAX_MESSAGE_LENGTH = 20000;
const MAX_SYSTEM_PROMPT_LENGTH = 20000;
const MAX_ACTION_LOG_ENTRIES = 20;
const HEALTH_POLL_INTERVAL_MS = 30000;

const state = {
  conversations: [],
  activeConversationId: null,
  health: null,
  tools: [],
  pendingController: null,
  abortResponseMessage: "Request stopped by user.",
  lastUsedTools: new Set(),
  healthPollTimer: null,
  persistTimer: null,
  pendingAction: null,
  actionLog: [],
  activeTab: "assessmentTab",
  chatCollapsed: true,
  metaModel: null,
  assessment: {
    currentStep: "setup",
    completedSteps: new Set(),
    pending: false,
    pairs: [],
    summaryResult: null,
  },
};

const ASSESSMENT_STEPS = ["setup", "ist", "soll", "mapping", "summary"];

const elements = {
  appGrid: document.getElementById("appGrid"),
  chatDrawer: document.getElementById("chatDrawer"),
  chatToggleBtn: document.getElementById("chatToggleBtn"),
  drawerCollapseBtn: document.getElementById("drawerCollapseBtn"),
  chatWindow: document.getElementById("chatWindow"),
  chatForm: document.getElementById("chatForm"),
  messageInput: document.getElementById("messageInput"),
  systemPromptInput: document.getElementById("systemPromptInput"),
  sendBtn: document.getElementById("sendBtn"),
  retryBtn: document.getElementById("retryBtn"),
  stopBtn: document.getElementById("stopBtn"),
  clearBtn: document.getElementById("clearBtn"),
  renameConversationBtn: document.getElementById("renameConversationBtn"),
  exportBtn: document.getElementById("exportBtn"),
  newChatBtn: document.getElementById("newChatBtn"),
  messageCounter: document.getElementById("messageCounter"),
  conversationList: document.getElementById("conversationList"),
  conversationTitle: document.getElementById("conversationTitle"),
  conversationMeta: document.getElementById("conversationMeta"),
  healthText: document.getElementById("healthText"),
  healthStatus: document.getElementById("healthStatus"),
  healthMcpStatus: document.getElementById("healthMcpStatus"),
  healthToolCount: document.getElementById("healthToolCount"),
  healthServer: document.getElementById("healthServer"),
  connectionBadge: document.getElementById("connectionBadge"),
  modelBadge: document.getElementById("modelBadge"),
  toolList: document.getElementById("toolList"),
  toolSearchInput: document.getElementById("toolSearchInput"),
  toolCountBadge: document.getElementById("toolCountBadge"),
  refreshHealthBtn: document.getElementById("refreshHealthBtn"),
  refreshHealthInlineBtn: document.getElementById("refreshHealthInlineBtn"),
  refreshToolsBtn: document.getElementById("refreshToolsBtn"),
  actionLog: document.getElementById("actionLog"),
  metaModelBtn: document.getElementById("metaModelBtn"),
  metaModelModal: document.getElementById("metaModelModal"),
  metaModelCloseBtn: document.getElementById("metaModelCloseBtn"),
  metaModelBody: document.getElementById("metaModelBody"),
  assessmentPairRows: document.getElementById("assessmentPairRows"),
  assessmentPairAddBtn: document.getElementById("assessmentPairAddBtn"),
  assessmentSetupBtn: document.getElementById("assessmentSetupBtn"),
  assessmentSetupResult: document.getElementById("assessmentSetupResult"),
  assessmentSetupContinueBtn: document.getElementById("assessmentSetupContinueBtn"),
  assessmentIstPreviewAllBtn: document.getElementById("assessmentIstPreviewAllBtn"),
  assessmentIstApplyAllBtn: document.getElementById("assessmentIstApplyAllBtn"),
  assessmentIstPairList: document.getElementById("assessmentIstPairList"),
  assessmentIstBackBtn: document.getElementById("assessmentIstBackBtn"),
  assessmentIstContinueBtn: document.getElementById("assessmentIstContinueBtn"),
  assessmentSollPreviewAllBtn: document.getElementById("assessmentSollPreviewAllBtn"),
  assessmentSollProposeAllBtn: document.getElementById("assessmentSollProposeAllBtn"),
  assessmentSollApplyAllBtn: document.getElementById("assessmentSollApplyAllBtn"),
  assessmentSollPairList: document.getElementById("assessmentSollPairList"),
  assessmentSollBackBtn: document.getElementById("assessmentSollBackBtn"),
  assessmentSollContinueBtn: document.getElementById("assessmentSollContinueBtn"),
  assessmentPairingReview: document.getElementById("assessmentPairingReview"),
  assessmentMappingRunBtn: document.getElementById("assessmentMappingRunBtn"),
  assessmentMappingStatus: document.getElementById("assessmentMappingStatus"),
  assessmentMappingApplyAllBtn: document.getElementById("assessmentMappingApplyAllBtn"),
  assessmentMappingPairList: document.getElementById("assessmentMappingPairList"),
  assessmentMappingBackBtn: document.getElementById("assessmentMappingBackBtn"),
  assessmentMappingContinueBtn: document.getElementById("assessmentMappingContinueBtn"),
  assessmentSummaryRunBtn: document.getElementById("assessmentSummaryRunBtn"),
  assessmentSummaryStatus: document.getElementById("assessmentSummaryStatus"),
  assessmentSummaryResult: document.getElementById("assessmentSummaryResult"),
  assessmentSummaryCopyBtn: document.getElementById("assessmentSummaryCopyBtn"),
  assessmentSummaryDownloadBtn: document.getElementById("assessmentSummaryDownloadBtn"),
  summaryHeadline: document.getElementById("summaryHeadline"),
  summaryReadinessBadge: document.getElementById("summaryReadinessBadge"),
  summaryStatRow: document.getElementById("summaryStatRow"),
  summaryPairBreakdown: document.getElementById("summaryPairBreakdown"),
  summaryFindingsList: document.getElementById("summaryFindingsList"),
  summaryRisksList: document.getElementById("summaryRisksList"),
  summaryRecommendationText: document.getElementById("summaryRecommendationText"),
  summaryNextStepsList: document.getElementById("summaryNextStepsList"),
  summaryGeneratedAt: document.getElementById("summaryGeneratedAt"),
  assessmentSummaryBackBtn: document.getElementById("assessmentSummaryBackBtn"),
  assessmentStartNewBtn: document.getElementById("assessmentStartNewBtn"),
};

function validateRequiredElements() {
  const missing = Object.entries(elements)
    .filter((entry) => !entry[1])
    .map((entry) => entry[0]);
  if (missing.length) {
    throw new Error(`Missing required DOM elements: ${missing.join(", ")}`);
  }
}

function createId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function createConversation(title = "New conversation", history = []) {
  const now = new Date().toISOString();
  return {
    id: createId(),
    title,
    createdAt: now,
    updatedAt: now,
    history,
    draft: "",
  };
}

function sanitizeHistory(rawHistory) {
  if (!Array.isArray(rawHistory)) return [];
  const out = [];
  for (const turn of rawHistory) {
    if (!turn || (turn.role !== "user" && turn.role !== "assistant")) continue;
    const content = String(turn.content || "");
    const toolsRaw = turn.tools || turn.used_tools || [];
    const tools = Array.isArray(toolsRaw) ? toolsRaw.map((t) => String(t)).slice(0, 30) : [];
    out.push({
      role: turn.role,
      content: content.slice(0, 20000),
      tools,
      timestamp: isNaN(Date.parse(turn.timestamp || "")) ? new Date().toISOString() : turn.timestamp,
    });
  }
  return out;
}

function loadPersistedState() {
  let parsed = null;
  try {
    parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
  } catch (err) {
    parsed = null;
  }

  if (parsed && Array.isArray(parsed.conversations)) {
    state.conversations = parsed.conversations
      .map((conv) => {
        if (!conv || typeof conv !== "object") return null;
        const history = sanitizeHistory(conv.history);
        const createdAt = isNaN(Date.parse(conv.createdAt || "")) ? new Date().toISOString() : conv.createdAt;
        const updatedAt = isNaN(Date.parse(conv.updatedAt || "")) ? createdAt : conv.updatedAt;
        return {
          id: String(conv.id || createId()),
          title: String(conv.title || "New conversation").slice(0, 80),
          createdAt,
          updatedAt,
          history,
          draft: String(conv.draft || "").slice(0, MAX_MESSAGE_LENGTH),
        };
      })
      .filter(Boolean);
    state.activeConversationId = String(parsed.activeConversationId || "");
  }

  if (!state.conversations.length) {
    const first = createConversation();
    state.conversations = [first];
    state.activeConversationId = first.id;
  }

  if (!state.conversations.some((c) => c.id === state.activeConversationId)) {
    state.activeConversationId = state.conversations[0].id;
  }

  try {
    const stored = localStorage.getItem(CHAT_COLLAPSED_KEY);
    if (stored !== null) state.chatCollapsed = stored === "1";
  } catch (err) {
    // leave the default (collapsed) in place
  }
}

function persistState() {
  state.conversations = state.conversations.slice(0, MAX_CONVERSATIONS);
  for (const conv of state.conversations) {
    conv.title = String(conv.title || "New conversation").slice(0, 80);
    conv.history = sanitizeHistory(conv.history).slice(-400);
    conv.draft = String(conv.draft || "").slice(0, MAX_MESSAGE_LENGTH);
  }

  const serializableConversations = state.conversations.map((conv) => ({
    id: conv.id,
    title: conv.title,
    createdAt: conv.createdAt,
    updatedAt: conv.updatedAt,
    history: conv.history,
    draft: conv.draft,
  }));
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        conversations: serializableConversations,
        activeConversationId: state.activeConversationId,
      })
    );
  } catch (err) {
    console.warn("Unable to persist UI state:", err);
  }
}

function schedulePersist() {
  if (state.persistTimer) {
    clearTimeout(state.persistTimer);
  }
  state.persistTimer = setTimeout(() => {
    state.persistTimer = null;
    persistState();
  }, 250);
}

function formatClock(isoTimestamp) {
  if (!isoTimestamp) return "--:--";
  const d = new Date(isoTimestamp);
  if (isNaN(d.getTime())) return "--:--";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatRelativeTime(isoTimestamp) {
  const then = Date.parse(isoTimestamp || "");
  if (isNaN(then)) return "just now";
  const deltaSeconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (deltaSeconds < 60) return `${deltaSeconds}s ago`;
  const deltaMinutes = Math.floor(deltaSeconds / 60);
  if (deltaMinutes < 60) return `${deltaMinutes}m ago`;
  const deltaHours = Math.floor(deltaMinutes / 60);
  if (deltaHours < 24) return `${deltaHours}h ago`;
  const deltaDays = Math.floor(deltaHours / 24);
  return `${deltaDays}d ago`;
}

function deriveConversationTitle(text) {
  const normalized = String(text || "")
    .replace(/\s+/g, " ")
    .trim();
  if (!normalized) return "New conversation";
  return normalized.slice(0, 52) + (normalized.length > 52 ? "..." : "");
}

function getActiveConversation() {
  const active = state.conversations.find((conv) => conv.id === state.activeConversationId);
  if (active) return active;
  const fallback = state.conversations[0];
  if (fallback) {
    state.activeConversationId = fallback.id;
    return fallback;
  }
  const created = createConversation();
  state.conversations = [created];
  state.activeConversationId = created.id;
  return created;
}

function setActiveConversationDraft(value) {
  const active = getActiveConversation();
  active.draft = String(value || "").slice(0, MAX_MESSAGE_LENGTH);
}

function syncComposerFromActiveConversation() {
  const active = getActiveConversation();
  elements.messageInput.value = String(active.draft || "");
  autoResizeTextarea(elements.messageInput);
  updateMessageCounter();
  updateSendButtonState();
}

function normalizeAssistantText(raw) {
  if (!raw) return "";
  let text = String(raw).replace(/\r\n/g, "\n");
  text = text.replace(/^\s{0,3}#{1,6}\s*/gm, "");
  text = text.replace(/^\s*[-*+]\s+/gm, "");
  text = text.replace(/^\s*\d+\.\s+/gm, "");
  text = text.replace(/\*\*(.*?)\*\*/g, "$1");
  text = text.replace(/\*(.*?)\*/g, "$1");
  text = text.replace(/\n{3,}/g, "\n\n").trim();
  return text;
}

function splitParagraphs(rawText) {
  const normalized = normalizeAssistantText(rawText);
  if (!normalized) return [];
  return normalized
    .split(/\n\s*\n/g)
    .map((block) => block.trim())
    .filter(Boolean);
}

function updateLastUsedTools() {
  const active = getActiveConversation();
  const lastAssistant = [...active.history]
    .reverse()
    .find((turn) => turn.role === "assistant" && Array.isArray(turn.tools) && turn.tools.length);
  state.lastUsedTools = new Set(lastAssistant ? lastAssistant.tools : []);
}

function renderConversationList() {
  const list = elements.conversationList;
  list.innerHTML = "";

  const sorted = [...state.conversations].sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt));
  if (!sorted.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No saved conversations";
    list.appendChild(empty);
    return;
  }

  for (const conv of sorted) {
    const item = document.createElement("div");
    item.className = "conversation-item";

    const mainButton = document.createElement("button");
    mainButton.className = `conversation-main${conv.id === state.activeConversationId ? " active" : ""}`;
    mainButton.type = "button";
    mainButton.setAttribute("data-conversation-id", conv.id);

    const title = document.createElement("p");
    title.className = "conversation-title";
    title.textContent = conv.title || "New conversation";
    mainButton.appendChild(title);

    const meta = document.createElement("p");
    meta.className = "conversation-meta";
    meta.textContent = `${conv.history.length} msgs · ${formatRelativeTime(conv.updatedAt)}`;
    mainButton.appendChild(meta);

    mainButton.addEventListener("click", () => {
      switchActiveConversation(conv.id);
    });

    const deleteButton = document.createElement("button");
    deleteButton.className = "conversation-delete";
    deleteButton.type = "button";
    deleteButton.textContent = "Del";
    deleteButton.title = "Delete conversation";
    deleteButton.addEventListener("click", () => {
      deleteConversation(conv.id);
    });

    item.appendChild(mainButton);
    item.appendChild(deleteButton);
    list.appendChild(item);
  }
}

function renderConversationHeader() {
  const active = getActiveConversation();
  elements.conversationTitle.textContent = active.title || "Assistant";
  elements.conversationMeta.textContent = `${active.history.length} messages`;
}

function updateRetryButtonState() {
  const active = getActiveConversation();
  const hasUserTurn = active.history.some((turn) => turn.role === "user" && turn.content);
  elements.retryBtn.disabled = Boolean(state.pendingController || state.pendingAction || !hasUserTurn);
}

function createMessageElement(turn, index) {
  const wrapper = document.createElement("article");
  wrapper.className = `message ${turn.role}`;

  const meta = document.createElement("div");
  meta.className = "message-meta";

  const role = document.createElement("span");
  role.className = "message-role";
  role.textContent = turn.role === "user" ? "You" : "Assistant";
  meta.appendChild(role);

  const rightMeta = document.createElement("div");
  rightMeta.className = "message-time";
  rightMeta.textContent = formatClock(turn.timestamp);
  meta.appendChild(rightMeta);

  if (turn.role === "assistant") {
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "message-copy";
    copy.textContent = "Copy";
    copy.dataset.turnIndex = String(index);
    meta.appendChild(copy);
  }

  wrapper.appendChild(meta);

  const paragraphs = splitParagraphs(turn.content);
  if (!paragraphs.length) {
    const emptyParagraph = document.createElement("p");
    emptyParagraph.className = "msg-paragraph";
    emptyParagraph.textContent = "";
    wrapper.appendChild(emptyParagraph);
  } else {
    for (const paragraphText of paragraphs) {
      const paragraph = document.createElement("p");
      paragraph.className = "msg-paragraph";
      paragraph.textContent = paragraphText;
      wrapper.appendChild(paragraph);
    }
  }

  if (Array.isArray(turn.tools) && turn.tools.length) {
    const toolList = document.createElement("div");
    toolList.className = "tool-chip-list";
    for (const tool of turn.tools) {
      const chip = document.createElement("span");
      chip.className = "tool-chip";
      chip.textContent = tool;
      toolList.appendChild(chip);
    }
    wrapper.appendChild(toolList);
  }

  return wrapper;
}

function renderChatWindow() {
  const active = getActiveConversation();
  const container = elements.chatWindow;
  container.innerHTML = "";

  if (!active.history.length) {
    const empty = document.createElement("div");
    empty.className = "empty-chat";
    empty.innerHTML = "<h3>Ask about your model</h3><p>Use quick prompts or ask free-form questions.</p>";
    container.appendChild(empty);
    return;
  }

  active.history.forEach((turn, index) => {
    container.appendChild(createMessageElement(turn, index));
  });
  container.scrollTop = container.scrollHeight;
}

function renderHealth() {
  const health = state.health || {};
  const status = String(health.status || "unknown");
  const mcpStatus = String(health.mcp_status || "unknown");
  const model = String(health.azure_model || "--");
  const toolCount = Number.isFinite(health.mcp_tool_count) ? health.mcp_tool_count : "--";
  const serverUrl = String(health.mcp_server_url || "--");

  elements.healthStatus.textContent = status;
  elements.healthMcpStatus.textContent = mcpStatus;
  elements.healthToolCount.textContent = String(toolCount);
  elements.healthServer.textContent = serverUrl.replace(/^https?:\/\//, "");
  elements.healthText.textContent = JSON.stringify(health, null, 2);

  elements.modelBadge.textContent = `Model: ${model}`;
  elements.connectionBadge.className = "badge status";
  if (mcpStatus === "ok") {
    elements.connectionBadge.classList.add("status-ok");
    elements.connectionBadge.textContent = "MCP Connected";
  } else if (mcpStatus === "error") {
    elements.connectionBadge.classList.add("status-error");
    elements.connectionBadge.textContent = "MCP Error";
  } else {
    elements.connectionBadge.classList.add("status-pending");
    elements.connectionBadge.textContent = "Checking MCP";
  }
}

function renderToolList(errorMessage = "") {
  const filter = elements.toolSearchInput.value.trim().toLowerCase();
  const allTools = Array.isArray(state.tools) ? state.tools : [];
  const filtered = allTools
    .filter((tool) => {
      if (!filter) return true;
      const haystack = `${tool.name || ""} ${tool.description || ""}`.toLowerCase();
      return haystack.includes(filter);
    })
    .sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")));

  elements.toolCountBadge.textContent = `${filtered.length}/${allTools.length} tools`;
  elements.toolList.innerHTML = "";

  if (errorMessage) {
    const errorState = document.createElement("div");
    errorState.className = "empty-state";
    errorState.textContent = errorMessage;
    elements.toolList.appendChild(errorState);
    return;
  }

  if (!filtered.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = allTools.length ? "No tools match your search" : "No tools loaded yet";
    elements.toolList.appendChild(empty);
    return;
  }

  for (const tool of filtered) {
    const card = document.createElement("article");
    card.className = `tool-card${state.lastUsedTools.has(tool.name) ? " used" : ""}`;

    const top = document.createElement("div");
    top.className = "tool-top";

    const name = document.createElement("p");
    name.className = "tool-name";
    name.textContent = tool.name || "Unnamed tool";
    top.appendChild(name);

    if (state.lastUsedTools.has(tool.name)) {
      const usedBadge = document.createElement("span");
      usedBadge.className = "badge";
      usedBadge.textContent = "Used";
      top.appendChild(usedBadge);
    }
    card.appendChild(top);

    const desc = document.createElement("p");
    desc.className = "tool-description";
    desc.textContent = tool.description || "No description available";
    card.appendChild(desc);

    const schema = document.createElement("pre");
    schema.className = "tool-schema";
    const schemaString = JSON.stringify(tool.input_schema || {}, null, 2);
    schema.textContent = schemaString.length > 1400 ? `${schemaString.slice(0, 1400)}\n...` : schemaString;
    card.appendChild(schema);

    elements.toolList.appendChild(card);
  }
}

function typeColor(type) {
  const t = String(type || "");
  if (t.startsWith("Business")) {
    return { fill: "#fdf3d2", stroke: "#c9a227", text: "#6b4e00" };
  }
  if (t.startsWith("Application") || t === "DataObject") {
    return { fill: "#dff7fb", stroke: "#2f8fb8", text: "#0b4f66" };
  }
  if (t.startsWith("Technology") || ["Node", "Device", "SystemSoftware", "TechnologyService"].includes(t)) {
    return { fill: "#e3f7e3", stroke: "#4c9c4c", text: "#245c24" };
  }
  return { fill: "#f1f1f6", stroke: "#8f8fa3", text: "#3a3a4a" };
}

function truncateLabel(text, maxLen) {
  const t = String(text || "");
  return t.length > maxLen ? `${t.slice(0, maxLen - 1)}…` : t;
}

function pushActionLogEntry(entry) {
  state.actionLog.unshift({ ...entry, timestamp: new Date().toISOString() });
  state.actionLog = state.actionLog.slice(0, MAX_ACTION_LOG_ENTRIES);
  renderActionLog();
}

function renderActionLog() {
  const container = elements.actionLog;
  container.innerHTML = "";
  if (!state.actionLog.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No automation runs yet this session.";
    container.appendChild(empty);
    return;
  }
  for (const entry of state.actionLog) {
    const item = document.createElement("article");
    item.className = `action-log-item action-log-${entry.tone || "neutral"}`;

    const head = document.createElement("div");
    head.className = "action-log-head";
    const title = document.createElement("strong");
    title.textContent = entry.viewName || entry.action || "Automation run";
    head.appendChild(title);
    const time = document.createElement("span");
    time.textContent = formatClock(entry.timestamp);
    head.appendChild(time);
    item.appendChild(head);

    const summary = document.createElement("p");
    summary.textContent = entry.summary || "";
    item.appendChild(summary);

    container.appendChild(item);
  }
}

/* ---------------- Architecture Assessment wizard ---------------- */

function setAssessmentStatus(el, message, tone = "neutral") {
  el.textContent = String(message || "");
  el.className = "action-status";
  if (tone) el.classList.add(`action-${tone}`);
  el.classList.toggle("hidden", !message);
}

function refreshAssessmentStepperClasses() {
  document.querySelectorAll(".assessment-step-btn").forEach((btn) => {
    const btnStep = btn.dataset.step;
    btn.classList.toggle("active", btnStep === state.assessment.currentStep);
    btn.classList.toggle("completed", state.assessment.completedSteps.has(btnStep));
  });
}

const ASSESSMENT_PANEL_ID_BY_STEP = {
  setup: "assessmentStepSetup",
  ist: "assessmentStepIst",
  soll: "assessmentStepSoll",
  mapping: "assessmentStepMapping",
  summary: "assessmentStepSummary",
};

function setAssessmentStep(step) {
  if (!ASSESSMENT_STEPS.includes(step)) return;
  state.assessment.currentStep = step;
  Object.entries(ASSESSMENT_PANEL_ID_BY_STEP).forEach(([key, id]) => {
    const panel = document.getElementById(id);
    if (panel) panel.classList.toggle("hidden", key !== step);
  });
  refreshAssessmentStepperClasses();
  // Re-render per-pair lists on entry so a view name edited in Setup (after this step was already
  // visited once) is reflected immediately, since view names double as the identifiers sent to the
  // backend -- a stale card header could otherwise mislead the user about which source it's for.
  if (step === "ist") renderAssessmentCaptureList("ist");
  if (step === "soll") renderAssessmentCaptureList("soll");
  if (step === "mapping") {
    renderAssessmentPairingReview();
    renderAssessmentMappingPairList();
  }
}

function markAssessmentStepComplete(step) {
  state.assessment.completedSteps.add(step);
  const idx = ASSESSMENT_STEPS.indexOf(step);
  const nextStep = ASSESSMENT_STEPS[idx + 1];
  if (nextStep) {
    const nextBtn = document.querySelector(`.assessment-step-btn[data-step="${nextStep}"]`);
    if (nextBtn) nextBtn.disabled = false;
  }
  refreshAssessmentStepperClasses();
}

/* ---------------- Multi-source pair model (Setup step 1) ---------------- */

function createAssessmentPair(index, suffix = "") {
  const n = index + 1;
  const istViewName = `As-Is Business Process Source ${n}${suffix}`;
  const sollViewName = `To-Be Business Process Source ${n}${suffix}`;
  return {
    id: createId(),
    istViewName,
    sollViewName,
    setupNotes: [],
    setupChecked: false,
    istFiles: [],
    istPlan: null,
    istApplied: false,
    istStatus: { message: "", tone: "neutral" },
    sollFiles: [],
    sollPlan: null,
    sollApplied: false,
    sollStatus: { message: "", tone: "neutral" },
    // mappingSollViewName is the human-in-the-loop-corrected pairing for step 4; it tracks
    // sollViewName automatically (so renaming a pair's To-Be field in Setup flows through) until
    // the user explicitly re-pairs it via the step-4 dropdown, at which point it's "manually set"
    // and Setup edits no longer silently overwrite that deliberate correction.
    mappingSollViewName: sollViewName,
    mappingSollViewNameManuallySet: false,
    mappingViewName: `${istViewName} to ${sollViewName} Mapping`,
    mappingViewNameManuallySet: false,
    mappingResult: null,
    mappingApplied: false,
    mappingStatus: { message: "", tone: "neutral" },
  };
}

// Keeps the step-4 derived fields (the suggested To-Be pairing and mapping view name) following
// istViewName/sollViewName as the user edits them in Setup -- but only for whichever of the two
// fields the user hasn't explicitly overridden themselves (dropdown re-pairing, or hand-editing
// the mapping view name). Without this, renaming a pair in Setup left mappingSollViewName frozen
// at its original "To-Be Business Process Source N" placeholder, which the step-4 dropdown would
// then silently mis-render as if it still matched (see the two bugs this fixes).
function syncAssessmentPairDerivedNames(pair) {
  if (!pair.mappingSollViewNameManuallySet) {
    pair.mappingSollViewName = pair.sollViewName;
  }
  if (!pair.mappingViewNameManuallySet) {
    pair.mappingViewName = `${pair.istViewName} to ${pair.mappingSollViewName} Mapping`;
  }
}

function getAssessmentPair(pairId) {
  return state.assessment.pairs.find((p) => p.id === pairId) || null;
}

function addAssessmentPair() {
  state.assessment.pairs.push(createAssessmentPair(state.assessment.pairs.length));
  renderAssessmentPairRows();
}

function removeAssessmentPair(pairId) {
  if (state.assessment.pairs.length <= 1) return;
  state.assessment.pairs = state.assessment.pairs.filter((p) => p.id !== pairId);
  renderAssessmentPairRows();
}

function findDuplicateAssessmentViewNames() {
  const seen = new Set();
  const duplicates = [];
  const check = (name) => {
    const key = String(name || "").trim().toLowerCase();
    if (!key) return;
    if (seen.has(key)) duplicates.push(name);
    else seen.add(key);
  };
  for (const pair of state.assessment.pairs) {
    check(pair.istViewName);
    check(pair.sollViewName);
  }
  return duplicates;
}

function renderAssessmentPairRows() {
  const container = elements.assessmentPairRows;
  container.innerHTML = "";
  state.assessment.pairs.forEach((pair, index) => {
    const row = document.createElement("div");
    row.className = "assessment-pair-row";

    const badge = document.createElement("span");
    badge.className = "file-order-badge";
    badge.textContent = String(index + 1);
    row.appendChild(badge);

    const fieldRow = document.createElement("div");
    fieldRow.className = "assessment-field-row";

    const istLabel = document.createElement("label");
    istLabel.textContent = "As-Is Input / View";
    const istInput = document.createElement("input");
    istInput.type = "text";
    istInput.maxLength = 120;
    istInput.value = pair.istViewName;
    istInput.addEventListener("input", () => {
      pair.istViewName = istInput.value;
      syncAssessmentPairDerivedNames(pair);
    });
    istInput.addEventListener("blur", () => {
      // Trim only once typing is done (not on every keystroke, or a trailing space would vanish
      // before a multi-word name could be finished). A stray leading/trailing space here doesn't
      // survive into Archi -- it trims view names on creation -- so leaving it in our own stored
      // name causes later lookups against that same view to fail even though it genuinely exists.
      const trimmed = istInput.value.trim();
      if (trimmed !== istInput.value) {
        istInput.value = trimmed;
        pair.istViewName = trimmed;
        syncAssessmentPairDerivedNames(pair);
      }
    });
    istLabel.appendChild(istInput);
    fieldRow.appendChild(istLabel);

    const sollLabel = document.createElement("label");
    sollLabel.textContent = "To-Be Input / View";
    const sollInput = document.createElement("input");
    sollInput.type = "text";
    sollInput.maxLength = 120;
    sollInput.value = pair.sollViewName;
    sollInput.addEventListener("input", () => {
      pair.sollViewName = sollInput.value;
      syncAssessmentPairDerivedNames(pair);
    });
    sollInput.addEventListener("blur", () => {
      const trimmed = sollInput.value.trim();
      if (trimmed !== sollInput.value) {
        sollInput.value = trimmed;
        pair.sollViewName = trimmed;
        syncAssessmentPairDerivedNames(pair);
      }
    });
    sollLabel.appendChild(sollInput);
    fieldRow.appendChild(sollLabel);

    row.appendChild(fieldRow);

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "file-order-remove";
    removeBtn.textContent = "×";
    removeBtn.title = "Remove this source pair";
    removeBtn.disabled = state.assessment.pairs.length <= 1;
    removeBtn.addEventListener("click", () => removeAssessmentPair(pair.id));
    row.appendChild(removeBtn);

    container.appendChild(row);
  });
}

async function runAssessmentSetup() {
  const box = elements.assessmentSetupResult;
  const duplicates = findDuplicateAssessmentViewNames();
  if (duplicates.length) {
    box.innerHTML = "";
    box.classList.remove("hidden");
    const p = document.createElement("p");
    p.textContent = `Error: view names must be unique across pairs. Duplicate: ${duplicates.join(", ")}`;
    box.appendChild(p);
    return;
  }
  if (state.assessment.pending) return;
  state.assessment.pending = true;
  elements.assessmentSetupBtn.disabled = true;
  box.classList.add("hidden");
  try {
    for (const pair of state.assessment.pairs) {
      const params = new URLSearchParams({ ist_view_name: pair.istViewName, soll_view_name: pair.sollViewName });
      const res = await fetch(`/api/assessment/setup?${params.toString()}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Setup check failed for '${pair.istViewName}' (${res.status})`);
      pair.setupNotes = data.notes || [];
      pair.setupChecked = true;
    }
    box.innerHTML = "";
    box.classList.remove("hidden");
    state.assessment.pairs.forEach((pair, index) => {
      const group = document.createElement("div");
      group.className = "assessment-result-pair-group";
      const heading = document.createElement("strong");
      heading.textContent = `Pair ${index + 1}: ${pair.istViewName} / ${pair.sollViewName}`;
      group.appendChild(heading);
      for (const note of pair.setupNotes) {
        const p = document.createElement("p");
        p.textContent = note;
        group.appendChild(p);
      }
      box.appendChild(group);
    });
    elements.assessmentSetupContinueBtn.disabled = false;
    markAssessmentStepComplete("setup");
  } catch (err) {
    box.innerHTML = "";
    box.classList.remove("hidden");
    const p = document.createElement("p");
    p.textContent = `Error: ${err.message || String(err)}`;
    box.appendChild(p);
  } finally {
    state.assessment.pending = false;
    elements.assessmentSetupBtn.disabled = false;
  }
}

function updateAssessmentCounts(plan, countsEl) {
  const included = plan.elements.filter((el) => el.include).length;
  countsEl.textContent =
    `${included}/${plan.elements.length} elements selected · ${plan.relationships.length} relationship(s) ` +
    `will be created · ${plan.steps_processed} steps processed`;
}

function renderAssessmentElementsTable(plan, tbodyEl, countsEl) {
  tbodyEl.innerHTML = "";
  if (!plan || !plan.elements.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 3;
    td.className = "empty-state";
    td.textContent = "No elements extracted.";
    tr.appendChild(td);
    tbodyEl.appendChild(tr);
    if (countsEl) countsEl.textContent = "";
    return;
  }
  plan.elements.forEach((el) => {
    const tr = document.createElement("tr");
    if (!el.include) tr.classList.add("row-excluded");

    const checkTd = document.createElement("td");
    checkTd.className = "col-check";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = el.include;
    checkbox.addEventListener("change", () => {
      el.include = checkbox.checked;
      tr.classList.toggle("row-excluded", !el.include);
      if (countsEl) updateAssessmentCounts(plan, countsEl);
    });
    checkTd.appendChild(checkbox);
    tr.appendChild(checkTd);

    const typeTd = document.createElement("td");
    typeTd.className = "cell-type";
    typeTd.textContent = el.type;
    tr.appendChild(typeTd);

    const nameTd = document.createElement("td");
    const nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.value = el.name;
    nameInput.maxLength = 120;
    nameInput.className = "cell-name-input";
    nameInput.addEventListener("input", () => {
      el.name = nameInput.value;
    });
    nameTd.appendChild(nameInput);
    tr.appendChild(nameTd);

    tbodyEl.appendChild(tr);
  });
  if (countsEl) updateAssessmentCounts(plan, countsEl);
}

/* ---------------- Multi-file drag-drop ordering (As-Is / To-Be uploads) ---------------- */

function uploadErrorMessage(res, data) {
  if (data && data.detail) return data.detail;
  if (res.status === 413) {
    return "These files are too large combined. Try uploading fewer files at once, or split large documents.";
  }
  return `Preview failed (${res.status})`;
}

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function renderFileOrderList(listEl, files, { onRemove, onReorder }) {
  listEl.innerHTML = "";
  let dragIndex = null;

  files.forEach((file, index) => {
    const li = document.createElement("li");
    li.className = "file-order-item";
    li.draggable = true;

    const handle = document.createElement("span");
    handle.className = "file-order-handle";
    handle.textContent = "⣿";
    handle.setAttribute("aria-hidden", "true");
    li.appendChild(handle);

    const badge = document.createElement("span");
    badge.className = "file-order-badge";
    badge.textContent = String(index + 1);
    li.appendChild(badge);

    const info = document.createElement("span");
    info.className = "file-order-info";
    const name = document.createElement("span");
    name.className = "file-order-name";
    name.textContent = file.name;
    name.title = file.name;
    const size = document.createElement("span");
    size.className = "file-order-size";
    size.textContent = formatFileSize(file.size);
    info.appendChild(name);
    info.appendChild(size);
    li.appendChild(info);

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "file-order-remove";
    removeBtn.textContent = "×";
    removeBtn.title = "Remove this file";
    removeBtn.addEventListener("click", () => onRemove(index));
    li.appendChild(removeBtn);

    li.addEventListener("dragstart", (event) => {
      dragIndex = index;
      li.classList.add("dragging");
      if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
    });
    li.addEventListener("dragend", () => {
      li.classList.remove("dragging");
      listEl.querySelectorAll(".file-order-item").forEach((item) => item.classList.remove("drag-over"));
      dragIndex = null;
    });
    li.addEventListener("dragover", (event) => {
      event.preventDefault();
      if (dragIndex === null || dragIndex === index) return;
      li.classList.add("drag-over");
    });
    li.addEventListener("dragleave", () => {
      li.classList.remove("drag-over");
    });
    li.addEventListener("drop", (event) => {
      event.preventDefault();
      li.classList.remove("drag-over");
      if (dragIndex === null || dragIndex === index) return;
      onReorder(dragIndex, index);
    });

    listEl.appendChild(li);
  });
}

function renderPlanWarningsBox(boxEl, warnings) {
  boxEl.innerHTML = "";
  if (!warnings || !warnings.length) {
    boxEl.classList.add("hidden");
    return;
  }
  boxEl.classList.remove("hidden");
  for (const message of warnings) {
    const p = document.createElement("p");
    p.textContent = message;
    boxEl.appendChild(p);
  }
}

/* ---------------- Steps 2 & 3: per-pair, side-parameterized capture ---------------- */

function getPairFiles(pair, side) {
  return side === "ist" ? pair.istFiles : pair.sollFiles;
}
function setPairFiles(pair, side, files) {
  if (side === "ist") pair.istFiles = files;
  else pair.sollFiles = files;
}
function getPairPlan(pair, side) {
  return side === "ist" ? pair.istPlan : pair.sollPlan;
}
function setPairPlan(pair, side, plan) {
  if (side === "ist") pair.istPlan = plan;
  else pair.sollPlan = plan;
}
function getPairApplied(pair, side) {
  return side === "ist" ? pair.istApplied : pair.sollApplied;
}
function setPairApplied(pair, side, val) {
  if (side === "ist") pair.istApplied = val;
  else pair.sollApplied = val;
}
function getPairStatus(pair, side) {
  return side === "ist" ? pair.istStatus : pair.sollStatus;
}
function setPairStatus(pair, side, message, tone) {
  const status = { message, tone };
  if (side === "ist") pair.istStatus = status;
  else pair.sollStatus = status;
}
function getPairViewName(pair, side) {
  return side === "ist" ? pair.istViewName : pair.sollViewName;
}

function buildAssessmentCaptureCard(pair, side) {
  const card = document.createElement("div");
  card.className = "assessment-pair-capture-card";

  const head = document.createElement("div");
  head.className = "assessment-pair-card-head";
  const title = document.createElement("h4");
  title.textContent = getPairViewName(pair, side);
  head.appendChild(title);
  const statusBadge = document.createElement("span");
  statusBadge.className = "assessment-pair-card-status";
  statusBadge.textContent = getPairApplied(pair, side) ? "Created in Archi" : "Not yet created";
  statusBadge.dataset.state = getPairApplied(pair, side) ? "done" : "pending";
  head.appendChild(statusBadge);
  card.appendChild(head);

  const form = document.createElement("form");
  form.className = "action-form";
  const fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.accept = ".pdf,.xlsx,.xlsm,.csv,.txt,.md,.json";
  fileInput.multiple = true;
  const submitBtn = document.createElement("button");
  submitBtn.type = "submit";
  submitBtn.className = "btn primary";
  submitBtn.textContent = "Preview extraction";
  form.appendChild(fileInput);
  form.appendChild(submitBtn);
  card.appendChild(form);

  const files = getPairFiles(pair, side);
  const orderHint = document.createElement("p");
  orderHint.className = "file-order-hint";
  orderHint.textContent =
    "Multiple files selected. Drag to arrange them in the order their processes connect -- the last step of each file will be chained to the first step of the next.";
  orderHint.classList.toggle("hidden", files.length < 2);
  card.appendChild(orderHint);

  const fileListEl = document.createElement("ul");
  fileListEl.className = "file-order-list";
  fileListEl.classList.toggle("hidden", files.length === 0);
  card.appendChild(fileListEl);
  renderFileOrderList(fileListEl, files, {
    onRemove: (index) => {
      setPairFiles(pair, side, getPairFiles(pair, side).filter((_f, i) => i !== index));
      renderAssessmentCaptureList(side);
    },
    onReorder: (fromIndex, toIndex) => {
      const updated = [...getPairFiles(pair, side)];
      const [moved] = updated.splice(fromIndex, 1);
      updated.splice(toIndex, 0, moved);
      setPairFiles(pair, side, updated);
      renderAssessmentCaptureList(side);
    },
  });

  const statusEl = document.createElement("div");
  statusEl.className = "action-status";
  const status = getPairStatus(pair, side);
  if (status && status.message) {
    statusEl.textContent = status.message;
    statusEl.classList.add(`action-${status.tone || "neutral"}`);
  } else {
    statusEl.classList.add("hidden");
  }
  card.appendChild(statusEl);

  const plan = getPairPlan(pair, side);
  const previewEl = document.createElement("div");
  previewEl.className = "assessment-preview";
  previewEl.classList.toggle("hidden", !plan);
  const countsEl = document.createElement("div");
  countsEl.className = "preview-counts";
  previewEl.appendChild(countsEl);
  const warningsEl = document.createElement("div");
  warningsEl.className = "preview-warnings hidden";
  previewEl.appendChild(warningsEl);
  const tableScroll = document.createElement("div");
  tableScroll.className = "table-scroll";
  const table = document.createElement("table");
  table.className = "preview-table";
  table.innerHTML = `<thead><tr><th class="col-check"></th><th>Type</th><th>Name</th></tr></thead><tbody></tbody>`;
  tableScroll.appendChild(table);
  previewEl.appendChild(tableScroll);
  const actionsEl = document.createElement("div");
  actionsEl.className = "preview-actions";
  const discardBtn = document.createElement("button");
  discardBtn.type = "button";
  discardBtn.className = "btn ghost";
  discardBtn.textContent = "Discard";
  const applyBtn = document.createElement("button");
  applyBtn.type = "button";
  applyBtn.className = "btn primary";
  applyBtn.textContent = "Create in Archi";
  actionsEl.appendChild(discardBtn);
  actionsEl.appendChild(applyBtn);
  previewEl.appendChild(actionsEl);
  card.appendChild(previewEl);

  if (plan) {
    renderAssessmentElementsTable(plan, table.querySelector("tbody"), countsEl);
    renderPlanWarningsBox(warningsEl, plan.warnings);
  }

  if (side === "soll") {
    const divider = document.createElement("div");
    divider.className = "assessment-or-divider";
    divider.textContent = "or";
    card.appendChild(divider);
    const proposeBtn = document.createElement("button");
    proposeBtn.type = "button";
    proposeBtn.className = "btn primary";
    proposeBtn.textContent = "Propose To-Be Architecture (AI)";
    proposeBtn.addEventListener("click", () => proposeAssessmentPairSollArchitecture(pair.id));
    card.appendChild(proposeBtn);
  }

  fileInput.addEventListener("change", () => {
    setPairFiles(pair, side, [...getPairFiles(pair, side), ...Array.from(fileInput.files || [])]);
    fileInput.value = "";
    renderAssessmentCaptureList(side);
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await loadAssessmentPairPreview(pair.id, side);
  });
  discardBtn.addEventListener("click", () => discardAssessmentPairPlan(pair.id, side));
  applyBtn.addEventListener("click", () => applyAssessmentPairPlan(pair.id, side));

  return card;
}

function renderAssessmentCaptureList(side) {
  const container = side === "ist" ? elements.assessmentIstPairList : elements.assessmentSollPairList;
  container.innerHTML = "";
  for (const pair of state.assessment.pairs) {
    container.appendChild(buildAssessmentCaptureCard(pair, side));
  }
}

async function loadAssessmentPairPreview(pairId, side) {
  const pair = getAssessmentPair(pairId);
  if (!pair) return;
  const files = getPairFiles(pair, side);
  if (!files.length) {
    setPairStatus(pair, side, "Select at least one file first.", "error");
    renderAssessmentCaptureList(side);
    return;
  }
  if (state.assessment.pending) return;
  state.assessment.pending = true;
  const label = files.length === 1 ? `'${files[0].name}'` : `${files.length} files`;
  setPairStatus(pair, side, `Extracting preview from ${label}...`, "pending");
  renderAssessmentCaptureList(side);
  try {
    const formData = new FormData();
    for (const file of files) formData.append("files", file);
    formData.append("view_name", getPairViewName(pair, side));
    let endpoint = "/api/actions/business-process-upload/preview";
    if (side === "soll") {
      endpoint = "/api/assessment/soll-architecture/preview";
      formData.append("ist_view_name", pair.istViewName);
    }
    const res = await fetch(endpoint, { method: "POST", body: formData });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(uploadErrorMessage(res, data));
    setPairPlan(pair, side, data);
    setPairStatus(
      pair,
      side,
      `Preview ready: ${data.elements.length} elements, ${data.relationships.length} relationships. Review, then click "Create in Archi".`,
      "ok"
    );
    setPairFiles(pair, side, []);
  } catch (err) {
    setPairStatus(pair, side, `Error: ${err.message || String(err)}`, "error");
  } finally {
    state.assessment.pending = false;
    renderAssessmentCaptureList(side);
  }
}

async function proposeAssessmentPairSollArchitecture(pairId) {
  const pair = getAssessmentPair(pairId);
  if (!pair || state.assessment.pending) return;
  state.assessment.pending = true;
  setPairStatus(pair, "soll", "Generating a To-Be Architecture proposal from the As-Is processes...", "pending");
  renderAssessmentCaptureList("soll");
  try {
    const res = await fetch("/api/assessment/soll-architecture/propose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ist_view_name: pair.istViewName, view_name: pair.sollViewName }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Proposal failed (${res.status})`);
    setPairPlan(pair, "soll", data);
    setPairStatus(
      pair,
      "soll",
      `Proposal ready: ${data.elements.length} processes, ${data.relationships.length} relationships (tagged status=target). Review carefully, then click "Create in Archi".`,
      "ok"
    );
  } catch (err) {
    setPairStatus(pair, "soll", `Error: ${err.message || String(err)}`, "error");
  } finally {
    state.assessment.pending = false;
    renderAssessmentCaptureList("soll");
  }
}

async function proposeAllAssessmentPairSollArchitecture() {
  if (state.assessment.pending) return;
  for (const pair of state.assessment.pairs) {
    if (!getPairApplied(pair, "soll")) {
      await proposeAssessmentPairSollArchitecture(pair.id);
    }
  }
}

async function applyAssessmentPairPlan(pairId, side) {
  const pair = getAssessmentPair(pairId);
  if (!pair) return;
  const plan = getPairPlan(pair, side);
  if (!plan || state.assessment.pending) return;
  if (!plan.elements.some((el) => el.include)) {
    setPairStatus(pair, side, "Select at least one element before applying.", "error");
    renderAssessmentCaptureList(side);
    return;
  }
  state.assessment.pending = true;
  setPairStatus(pair, side, `Creating '${plan.view_name}' in Archi...`, "pending");
  renderAssessmentCaptureList(side);
  try {
    const res = await fetch("/api/actions/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Apply failed (${res.status})`);
    setPairStatus(pair, side, data.summary || "Applied to Archi.", "ok");
    pushActionLogEntry({
      action: side === "ist" ? "business-process-upload" : "assessment-soll-upload",
      viewName: data.view_name || plan.view_name,
      summary: data.summary || "Applied to Archi.",
      tone: "ok",
    });
    setPairApplied(pair, side, true);
    setPairPlan(pair, side, null);
    updateAssessmentCaptureContinueState(side);
    await loadHealth();
    await loadTools();
  } catch (err) {
    setPairStatus(pair, side, `Error: ${err.message || String(err)}`, "error");
  } finally {
    state.assessment.pending = false;
    renderAssessmentCaptureList(side);
  }
}

async function previewAllAssessmentPairPlans(side) {
  if (state.assessment.pending) return;
  for (const pair of state.assessment.pairs) {
    // loadAssessmentPairPreview clears a pair's files on success, so a pair with none queued
    // either has nothing to preview yet or was already just previewed -- either way, skip it.
    if (getPairFiles(pair, side).length) {
      await loadAssessmentPairPreview(pair.id, side);
    }
  }
}

async function applyAllAssessmentPairPlans(side) {
  if (state.assessment.pending) return;
  for (const pair of state.assessment.pairs) {
    const plan = getPairPlan(pair, side);
    if (plan && !getPairApplied(pair, side)) {
      await applyAssessmentPairPlan(pair.id, side);
    }
  }
}

function discardAssessmentPairPlan(pairId, side) {
  const pair = getAssessmentPair(pairId);
  if (!pair) return;
  setPairPlan(pair, side, null);
  setPairStatus(pair, side, "Preview discarded. Nothing was written to Archi.", "neutral");
  renderAssessmentCaptureList(side);
}

function updateAssessmentCaptureContinueState(side) {
  const allApplied = state.assessment.pairs.every((p) => getPairApplied(p, side));
  if (side === "ist") {
    elements.assessmentIstContinueBtn.disabled = !allApplied;
    if (allApplied) markAssessmentStepComplete("ist");
  } else {
    elements.assessmentSollContinueBtn.disabled = !allApplied;
    if (allApplied) markAssessmentStepComplete("soll");
  }
}

function freshAssessmentViewNameStamp() {
  return new Date().toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function startNewAssessment() {
  // Reusing a prior cycle's view name would mix this new cycle's mapping analysis with the old
  // one's data -- mapping is scoped to whatever's actually on the configured As-Is/To-Be views, so
  // a genuinely fresh, non-colliding name is what makes "start new assessment" actually mean it.
  const stamp = freshAssessmentViewNameStamp();
  state.assessment = {
    currentStep: "setup",
    completedSteps: new Set(),
    pending: false,
    pairs: [createAssessmentPair(0, ` (${stamp})`)],
    summaryResult: null,
  };

  elements.assessmentSetupResult.classList.add("hidden");
  elements.assessmentSetupContinueBtn.disabled = true;
  elements.assessmentIstContinueBtn.disabled = true;
  elements.assessmentSollContinueBtn.disabled = true;
  elements.assessmentMappingStatus.classList.add("hidden");
  elements.assessmentMappingContinueBtn.disabled = true;
  elements.assessmentSummaryStatus.classList.add("hidden");
  elements.assessmentSummaryResult.classList.add("hidden");
  elements.summaryPairBreakdown.classList.add("hidden");

  document.querySelectorAll(".assessment-step-btn").forEach((btn) => {
    btn.disabled = btn.dataset.step !== "setup";
  });

  renderAssessmentPairRows();
  renderAssessmentCaptureList("ist");
  renderAssessmentCaptureList("soll");
  renderAssessmentPairingReview();
  renderAssessmentMappingPairList();
  setAssessmentStep("setup");
}

const ASSESSMENT_MATCH_TYPE_LABELS = {
  full: "Full match",
  partial: "Partial match",
  legacy_no_soll: "Legacy (no To-Be)",
  gap_new: "New (no As-Is)",
};

const ASSESSMENT_CRITICALITY_LABELS = { high: "High", medium: "Medium", low: "Low" };

const ASSESSMENT_CATEGORY_LABELS = {
  missing_process: "Missing process",
  redundancy: "Redundancy",
  structural_difference: "Structural difference",
  tooling_data_gap: "Tooling / data gap",
};

function renderAssessmentMappingTable(data, tbody) {
  tbody.innerHTML = "";
  if (!data.mappings.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 5;
    td.className = "empty-state";
    td.textContent = "No mappings.";
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }
  data.mappings.forEach((mapping) => {
    const tr = document.createElement("tr");
    const hasEndpoint = Boolean(mapping.ist_key || mapping.soll_key);
    if (!mapping.include || !hasEndpoint) tr.classList.add("row-excluded");

    const checkTd = document.createElement("td");
    checkTd.className = "col-check";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = mapping.include;
    checkbox.disabled = !hasEndpoint;
    checkbox.title = hasEndpoint
      ? "Matched pairs are connected in the mapping view; As-Is-only/To-Be-only rows are shown standalone."
      : "";
    checkbox.addEventListener("change", () => {
      mapping.include = checkbox.checked;
      tr.classList.toggle("row-excluded", !mapping.include || !hasEndpoint);
    });
    checkTd.appendChild(checkbox);
    tr.appendChild(checkTd);

    const istTd = document.createElement("td");
    istTd.textContent = mapping.ist_name || "—";
    tr.appendChild(istTd);

    const matchTd = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = `cell-match-type match-${mapping.match_type}`;
    badge.textContent = ASSESSMENT_MATCH_TYPE_LABELS[mapping.match_type] || mapping.match_type;
    matchTd.appendChild(badge);
    tr.appendChild(matchTd);

    const sollTd = document.createElement("td");
    sollTd.textContent = mapping.soll_name || "—";
    tr.appendChild(sollTd);

    const rationaleTd = document.createElement("td");
    rationaleTd.className = "meta-model-note";
    rationaleTd.textContent = mapping.rationale || "";
    tr.appendChild(rationaleTd);

    tbody.appendChild(tr);
  });
}

function renderAssessmentGapTable(data, tbody) {
  tbody.innerHTML = "";
  if (!data.gaps.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 3;
    td.className = "empty-state";
    td.textContent = "No gaps identified.";
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }
  data.gaps.forEach((gap) => {
    const tr = document.createElement("tr");

    const categoryTd = document.createElement("td");
    categoryTd.textContent = ASSESSMENT_CATEGORY_LABELS[gap.category] || gap.category;
    tr.appendChild(categoryTd);

    const criticalityTd = document.createElement("td");
    const span = document.createElement("span");
    span.className = `criticality-${gap.criticality}`;
    span.textContent = ASSESSMENT_CRITICALITY_LABELS[gap.criticality] || gap.criticality;
    criticalityTd.appendChild(span);
    tr.appendChild(criticalityTd);

    const descTd = document.createElement("td");
    descTd.textContent = gap.description;
    tr.appendChild(descTd);

    tbody.appendChild(tr);
  });
}

function renderAssessmentPairingReview() {
  const container = elements.assessmentPairingReview;
  container.innerHTML = "";
  const heading = document.createElement("strong");
  heading.textContent = "Confirm As-Is → To-Be pairing";
  container.appendChild(heading);
  state.assessment.pairs.forEach((pair) => {
    const row = document.createElement("div");
    row.className = "assessment-pairing-review-row";

    const istLabel = document.createElement("span");
    istLabel.className = "pairing-ist-label";
    istLabel.textContent = pair.istViewName;
    row.appendChild(istLabel);

    const arrow = document.createElement("span");
    arrow.className = "pairing-arrow";
    arrow.textContent = "→";
    row.appendChild(arrow);

    const select = document.createElement("select");
    let matchedCurrentPairing = false;
    state.assessment.pairs.forEach((candidate) => {
      const option = document.createElement("option");
      option.value = candidate.sollViewName;
      option.textContent = candidate.sollViewName;
      if (candidate.sollViewName === pair.mappingSollViewName) {
        option.selected = true;
        matchedCurrentPairing = true;
      }
      select.appendChild(option);
    });
    if (!matchedCurrentPairing && pair.mappingSollViewName) {
      // The stored pairing doesn't match any current pair's To-Be name (e.g. it was renamed
      // after this was set). Show the actual stored value instead of letting the browser
      // silently default to the first option -- that default looking plausible is exactly what
      // hid this bug the first two times.
      const orphan = document.createElement("option");
      orphan.value = pair.mappingSollViewName;
      orphan.textContent = `${pair.mappingSollViewName} (not a current pair -- please re-select)`;
      orphan.selected = true;
      select.insertBefore(orphan, select.firstChild);
    }
    select.addEventListener("change", () => {
      pair.mappingSollViewName = select.value;
      pair.mappingSollViewNameManuallySet = true;
      syncAssessmentPairDerivedNames(pair);
    });
    row.appendChild(select);

    container.appendChild(row);
  });
}

async function runAssessmentMappingForPair(pairId) {
  const pair = getAssessmentPair(pairId);
  if (!pair) return;
  pair.mappingStatus = { message: "Running mapping & gap analysis...", tone: "pending" };
  renderAssessmentMappingPairList();
  try {
    const res = await fetch("/api/assessment/mapping/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ist_view_name: pair.istViewName, soll_view_name: pair.mappingSollViewName }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Mapping analysis failed (${res.status})`);
    pair.mappingResult = data;
    const warningText = data.warnings && data.warnings.length ? ` (${data.warnings.join(" ")})` : "";
    pair.mappingStatus = {
      message: `Found ${data.mappings.length} mapping(s) and ${data.gaps.length} gap(s).${warningText}`,
      tone: "ok",
    };
  } catch (err) {
    pair.mappingStatus = { message: `Error: ${err.message || String(err)}`, tone: "error" };
  }
  renderAssessmentMappingPairList();
}

async function runAssessmentMappingAllPairs() {
  if (state.assessment.pending) return;
  state.assessment.pending = true;
  elements.assessmentMappingRunBtn.disabled = true;
  const pairs = state.assessment.pairs;
  let failures = 0;
  for (let i = 0; i < pairs.length; i++) {
    setAssessmentStatus(
      elements.assessmentMappingStatus,
      `Running pair ${i + 1} of ${pairs.length}: '${pairs[i].istViewName}' -> '${pairs[i].mappingSollViewName}'...`,
      "pending"
    );
    await runAssessmentMappingForPair(pairs[i].id);
    if (pairs[i].mappingStatus.tone === "error") failures += 1;
  }
  const okCount = pairs.length - failures;
  setAssessmentStatus(
    elements.assessmentMappingStatus,
    failures
      ? `${okCount} of ${pairs.length} pair(s) completed; ${failures} failed. See each pair's card for details.`
      : `Mapping & gap analysis complete for all ${pairs.length} pair(s).`,
    failures ? "error" : "ok"
  );
  state.assessment.pending = false;
  elements.assessmentMappingRunBtn.disabled = false;
}

function buildAssessmentMappingCard(pair) {
  const card = document.createElement("div");
  card.className = "assessment-pair-mapping-card";

  const head = document.createElement("div");
  head.className = "assessment-pair-card-head";
  const title = document.createElement("h4");
  title.textContent = `${pair.istViewName} ↔ ${pair.mappingSollViewName}`;
  head.appendChild(title);
  const statusBadge = document.createElement("span");
  statusBadge.className = "assessment-pair-card-status";
  statusBadge.textContent = pair.mappingApplied ? "Applied" : pair.mappingResult ? "Ready to apply" : "Not yet run";
  statusBadge.dataset.state = pair.mappingApplied ? "done" : "pending";
  head.appendChild(statusBadge);
  card.appendChild(head);

  const statusEl = document.createElement("div");
  statusEl.className = "action-status";
  if (pair.mappingStatus && pair.mappingStatus.message) {
    statusEl.textContent = pair.mappingStatus.message;
    statusEl.classList.add(`action-${pair.mappingStatus.tone || "neutral"}`);
  } else {
    statusEl.classList.add("hidden");
  }
  card.appendChild(statusEl);

  if (pair.mappingResult) {
    const mapHeading = document.createElement("h4");
    mapHeading.textContent = "Mappings";
    card.appendChild(mapHeading);
    const mapScroll = document.createElement("div");
    mapScroll.className = "table-scroll";
    const mapTable = document.createElement("table");
    mapTable.className = "preview-table";
    mapTable.innerHTML =
      "<thead><tr><th class=\"col-check\"></th><th>As-Is process</th><th>Match</th><th>To-Be process</th><th>Rationale</th></tr></thead><tbody></tbody>";
    mapScroll.appendChild(mapTable);
    card.appendChild(mapScroll);

    const gapHeading = document.createElement("h4");
    gapHeading.textContent = "Gap report";
    card.appendChild(gapHeading);
    const gapScroll = document.createElement("div");
    gapScroll.className = "table-scroll";
    const gapTable = document.createElement("table");
    gapTable.className = "preview-table";
    gapTable.innerHTML = "<thead><tr><th>Category</th><th>Criticality</th><th>Description</th></tr></thead><tbody></tbody>";
    gapScroll.appendChild(gapTable);
    card.appendChild(gapScroll);

    renderAssessmentMappingTable(pair.mappingResult, mapTable.querySelector("tbody"));
    renderAssessmentGapTable(pair.mappingResult, gapTable.querySelector("tbody"));

    const viewNameLabel = document.createElement("label");
    viewNameLabel.className = "assessment-mapping-view-name-label";
    viewNameLabel.textContent = "Mapping view name";
    const viewNameInput = document.createElement("input");
    viewNameInput.type = "text";
    viewNameInput.maxLength = 120;
    viewNameInput.value = pair.mappingViewName;
    viewNameInput.addEventListener("input", () => {
      pair.mappingViewName = viewNameInput.value;
      pair.mappingViewNameManuallySet = true;
    });
    viewNameLabel.appendChild(viewNameInput);
    card.appendChild(viewNameLabel);

    const actions = document.createElement("div");
    actions.className = "preview-actions";
    const applyBtn = document.createElement("button");
    applyBtn.type = "button";
    applyBtn.className = "btn primary";
    applyBtn.textContent = "Apply mapping to Archi";
    applyBtn.addEventListener("click", () => applyAssessmentMappingsForPair(pair.id));
    actions.appendChild(applyBtn);
    card.appendChild(actions);
  }

  return card;
}

function renderAssessmentMappingPairList() {
  const container = elements.assessmentMappingPairList;
  container.innerHTML = "";
  for (const pair of state.assessment.pairs) {
    container.appendChild(buildAssessmentMappingCard(pair));
  }
}

async function applyAssessmentMappingsForPair(pairId) {
  const pair = getAssessmentPair(pairId);
  if (!pair || !pair.mappingResult || state.assessment.pending) return;
  const included = pair.mappingResult.mappings.filter((m) => m.include && (m.ist_key || m.soll_key));
  if (!included.length) {
    pair.mappingStatus = { message: "Select at least one mapping to apply.", tone: "error" };
    renderAssessmentMappingPairList();
    return;
  }
  state.assessment.pending = true;
  pair.mappingStatus = { message: `Building the mapping view for '${pair.istViewName}' in Archi...`, tone: "pending" };
  renderAssessmentMappingPairList();
  try {
    const viewName = pair.mappingViewName || `${pair.istViewName} to ${pair.mappingSollViewName} Mapping`;
    const res = await fetch("/api/assessment/mapping/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mappings: pair.mappingResult.mappings, gaps: pair.mappingResult.gaps || [], view_name: viewName }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Applying mappings failed (${res.status})`);
    pair.mappingStatus = { message: data.summary || "Mappings applied.", tone: "ok" };
    pushActionLogEntry({
      action: "assessment-mapping",
      viewName: data.view_name || viewName,
      summary: data.summary || "Mappings applied.",
      tone: "ok",
    });
    pair.mappingApplied = true;
    updateAssessmentMappingContinueState();
  } catch (err) {
    pair.mappingStatus = { message: `Error: ${err.message || String(err)}`, tone: "error" };
  } finally {
    state.assessment.pending = false;
    renderAssessmentMappingPairList();
  }
}

async function applyAllAssessmentMappings() {
  if (state.assessment.pending) return;
  for (const pair of state.assessment.pairs) {
    if (pair.mappingResult && !pair.mappingApplied) {
      await applyAssessmentMappingsForPair(pair.id);
    }
  }
}

function updateAssessmentMappingContinueState() {
  const allApplied = state.assessment.pairs.every((p) => p.mappingApplied);
  elements.assessmentMappingContinueBtn.disabled = !allApplied;
  if (allApplied) markAssessmentStepComplete("mapping");
}

function buildSummaryPlainText(data) {
  const lines = [];
  lines.push("ARCHITECTURE ASSESSMENT — EXECUTIVE SUMMARY");
  if (data.readiness_label) lines.push(`Readiness: ${data.readiness_label} (maturity ${data.maturity_score}%)`);
  lines.push("");
  if (data.headline) {
    lines.push(data.headline);
    lines.push("");
  }
  lines.push(
    `As-Is processes: ${data.ist_process_count}  ·  To-Be processes: ${data.soll_process_count}  ·  ` +
      `Full matches: ${data.full_matches}  ·  Partial matches: ${data.partial_matches}  ·  ` +
      `Gaps: ${data.gap_count} (high: ${data.critical_gap_count}, medium: ${data.medium_gap_count}, low: ${data.low_gap_count})`
  );
  lines.push("");
  if (data.key_findings && data.key_findings.length) {
    lines.push("KEY FINDINGS");
    data.key_findings.forEach((f) => lines.push(`- ${f}`));
    lines.push("");
  }
  if (data.top_risks && data.top_risks.length) {
    lines.push("TOP RISKS");
    data.top_risks.forEach((r) => lines.push(`- ${r}`));
    lines.push("");
  }
  if (data.recommendation) {
    lines.push("RECOMMENDATION");
    lines.push(data.recommendation);
    lines.push("");
  }
  if (data.next_steps && data.next_steps.length) {
    lines.push("NEXT STEPS");
    data.next_steps.forEach((s, i) => lines.push(`${i + 1}. ${s}`));
    lines.push("");
  }
  if (data.executive_summary) {
    lines.push("SUMMARY");
    lines.push(data.executive_summary);
  }
  return lines.join("\n").trim();
}

function readinessLevelSlug(label) {
  const key = String(label || "").toLowerCase();
  if (key === "advanced") return "advanced";
  if (key === "early stage") return "early-stage";
  return "progressing";
}

function renderAssessmentSummary(data) {
  state.assessment.summaryResult = data;

  elements.summaryHeadline.textContent = data.headline || "Assessment summary";
  elements.summaryReadinessBadge.textContent = `${data.readiness_label || "—"} · ${data.maturity_score}% maturity`;
  elements.summaryReadinessBadge.dataset.level = readinessLevelSlug(data.readiness_label);

  const statRow = elements.summaryStatRow;
  statRow.innerHTML = "";
  const tiles = [
    ["As-Is processes", data.ist_process_count, false],
    ["To-Be processes", data.soll_process_count, false],
    ["Full matches", data.full_matches, false],
    ["Partial matches", data.partial_matches, false],
    ["Total gaps", data.gap_count, false],
    ["High-criticality gaps", data.critical_gap_count, data.critical_gap_count > 0],
  ];
  for (const [label, value, warn] of tiles) {
    const tile = document.createElement("div");
    tile.className = warn ? "summary-stat-tile summary-stat-warn" : "summary-stat-tile";
    const strong = document.createElement("strong");
    strong.textContent = String(value);
    const span = document.createElement("span");
    span.textContent = label;
    tile.appendChild(strong);
    tile.appendChild(span);
    statRow.appendChild(tile);
  }

  const fillList = (listEl, items, emptyText) => {
    listEl.innerHTML = "";
    const values = items && items.length ? items : [emptyText];
    for (const text of values) {
      const li = document.createElement("li");
      li.textContent = text;
      listEl.appendChild(li);
    }
  };
  fillList(elements.summaryFindingsList, data.key_findings, "No specific findings generated.");
  fillList(elements.summaryRisksList, data.top_risks, "No risks identified.");
  fillList(elements.summaryNextStepsList, data.next_steps, "No next steps generated.");

  elements.summaryRecommendationText.textContent = data.recommendation || "No recommendation generated.";
  elements.summaryGeneratedAt.textContent = `Generated ${new Date().toLocaleString()}`;
}

function collectAssessmentSummaryInputs() {
  const mappings = [];
  const gaps = [];
  state.assessment.pairs.forEach((pair, index) => {
    if (!pair.mappingResult) return;
    const label = `Pair ${index + 1}`;
    for (const m of pair.mappingResult.mappings || []) {
      mappings.push({ ...m, key: `${pair.id}::${m.key}` });
    }
    for (const g of pair.mappingResult.gaps || []) {
      const prefixed = `[${label}] ${g.description || ""}`.slice(0, 400);
      gaps.push({ ...g, description: prefixed });
    }
  });
  return { mappings, gaps };
}

function renderSummaryPairBreakdown() {
  const container = elements.summaryPairBreakdown;
  container.innerHTML = "";
  const pairsWithResults = state.assessment.pairs.filter((p) => p.mappingResult);
  if (!pairsWithResults.length) {
    container.classList.add("hidden");
    return;
  }
  container.classList.remove("hidden");
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  thead.innerHTML = "<tr><th>Pair</th><th>As-Is</th><th>To-Be</th><th>Full</th><th>Partial</th><th>Gaps (H/M/L)</th></tr>";
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  pairsWithResults.forEach((pair, index) => {
    const result = pair.mappingResult;
    const full = result.mappings.filter((m) => m.match_type === "full").length;
    const partial = result.mappings.filter((m) => m.match_type === "partial").length;
    const high = result.gaps.filter((g) => g.criticality === "high").length;
    const medium = result.gaps.filter((g) => g.criticality === "medium").length;
    const low = result.gaps.filter((g) => g.criticality === "low").length;
    const tr = document.createElement("tr");
    const cells = [String(index + 1), pair.istViewName, pair.mappingSollViewName, String(full), String(partial), `${high}/${medium}/${low}`];
    for (const text of cells) {
      const td = document.createElement("td");
      td.textContent = text;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  container.appendChild(table);
}

async function runAssessmentSummary() {
  const hasAnyResult = state.assessment.pairs.some((p) => p.mappingResult);
  if (!hasAnyResult) {
    setAssessmentStatus(elements.assessmentSummaryStatus, "Run mapping & gap analysis for at least one pair first.", "error");
    return;
  }
  if (state.assessment.pending) return;
  state.assessment.pending = true;
  elements.assessmentSummaryRunBtn.disabled = true;
  setAssessmentStatus(
    elements.assessmentSummaryStatus,
    "Generating summary... the strongest configured model can take a minute or two for this step.",
    "pending"
  );
  try {
    const { mappings, gaps } = collectAssessmentSummaryInputs();
    const res = await fetch("/api/assessment/summary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mappings, gaps }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Summary generation failed (${res.status})`);
    renderAssessmentSummary(data);
    renderSummaryPairBreakdown();
    elements.assessmentSummaryResult.classList.remove("hidden");
    setAssessmentStatus(elements.assessmentSummaryStatus, "Summary generated.", "ok");
    markAssessmentStepComplete("summary");
  } catch (err) {
    setAssessmentStatus(elements.assessmentSummaryStatus, `Error: ${err.message || String(err)}`, "error");
  } finally {
    state.assessment.pending = false;
    elements.assessmentSummaryRunBtn.disabled = false;
  }
}

function attachAssessmentEventHandlers() {
  document.querySelectorAll(".assessment-step-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.disabled) return;
      setAssessmentStep(btn.dataset.step);
    });
  });

  elements.assessmentPairAddBtn.addEventListener("click", addAssessmentPair);
  elements.assessmentSetupBtn.addEventListener("click", runAssessmentSetup);
  elements.assessmentSetupContinueBtn.addEventListener("click", () => setAssessmentStep("ist"));

  elements.assessmentIstPreviewAllBtn.addEventListener("click", () => previewAllAssessmentPairPlans("ist"));
  elements.assessmentIstApplyAllBtn.addEventListener("click", () => applyAllAssessmentPairPlans("ist"));
  elements.assessmentIstBackBtn.addEventListener("click", () => setAssessmentStep("setup"));
  elements.assessmentIstContinueBtn.addEventListener("click", () => setAssessmentStep("soll"));

  elements.assessmentSollPreviewAllBtn.addEventListener("click", () => previewAllAssessmentPairPlans("soll"));
  elements.assessmentSollProposeAllBtn.addEventListener("click", proposeAllAssessmentPairSollArchitecture);
  elements.assessmentSollApplyAllBtn.addEventListener("click", () => applyAllAssessmentPairPlans("soll"));
  elements.assessmentSollBackBtn.addEventListener("click", () => setAssessmentStep("ist"));
  elements.assessmentSollContinueBtn.addEventListener("click", () => setAssessmentStep("mapping"));

  elements.assessmentMappingRunBtn.addEventListener("click", runAssessmentMappingAllPairs);
  elements.assessmentMappingApplyAllBtn.addEventListener("click", applyAllAssessmentMappings);
  elements.assessmentMappingBackBtn.addEventListener("click", () => setAssessmentStep("soll"));
  elements.assessmentMappingContinueBtn.addEventListener("click", () => setAssessmentStep("summary"));

  elements.assessmentSummaryRunBtn.addEventListener("click", runAssessmentSummary);
  elements.assessmentSummaryBackBtn.addEventListener("click", () => setAssessmentStep("mapping"));
  elements.assessmentSummaryCopyBtn.addEventListener("click", async () => {
    if (!state.assessment.summaryResult) return;
    try {
      await navigator.clipboard.writeText(buildSummaryPlainText(state.assessment.summaryResult));
      const original = elements.assessmentSummaryCopyBtn.textContent;
      elements.assessmentSummaryCopyBtn.textContent = "Copied";
      setTimeout(() => {
        elements.assessmentSummaryCopyBtn.textContent = original;
      }, 1200);
    } catch (err) {
      // clipboard unavailable; ignore
    }
  });

  elements.assessmentSummaryDownloadBtn.addEventListener("click", () => {
    if (!state.assessment.summaryResult) return;
    window.print();
  });

  elements.assessmentStartNewBtn.addEventListener("click", () => {
    if (state.assessment.pending) return;
    if (!confirm("Start a new assessment? This clears the current wizard progress (nothing already created in Archi is deleted).")) {
      return;
    }
    startNewAssessment();
  });
}

/* ---------------- Meta model viewer ---------------- */

async function loadMetaModel() {
  if (state.metaModel) return state.metaModel;
  const res = await fetch("/api/meta-model");
  if (!res.ok) {
    throw new Error(`Meta-model request failed (${res.status})`);
  }
  state.metaModel = await res.json();
  return state.metaModel;
}

function renderMetaModelBody(metaModel) {
  const container = elements.metaModelBody;
  container.innerHTML = "";

  const layersSection = document.createElement("div");
  layersSection.className = "meta-model-layers";
  for (const layer of metaModel.layers || []) {
    const card = document.createElement("article");
    card.className = "meta-model-layer-card";

    const title = document.createElement("h3");
    title.textContent = layer.label;
    card.appendChild(title);

    const chipList = document.createElement("div");
    chipList.className = "meta-model-chip-list";
    for (const elementType of layer.elements || []) {
      const palette = typeColor(elementType);
      const chip = document.createElement("span");
      chip.className = "meta-model-chip";
      chip.style.background = palette.fill;
      chip.style.borderColor = palette.stroke;
      chip.style.color = palette.text;
      chip.textContent = elementType;
      chipList.appendChild(chip);
    }
    card.appendChild(chipList);
    layersSection.appendChild(card);
  }
  container.appendChild(layersSection);

  const relHead = document.createElement("h3");
  relHead.className = "meta-model-relationships-head";
  relHead.textContent = "Allowed relationships";
  container.appendChild(relHead);

  const tableScroll = document.createElement("div");
  tableScroll.className = "table-scroll meta-model-table-scroll";
  const table = document.createElement("table");
  table.className = "preview-table";
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const label of ["Source", "Relationship", "Target", "Note"]) {
    const th = document.createElement("th");
    th.textContent = label;
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (const rel of metaModel.relationships || []) {
    const tr = document.createElement("tr");

    const sourceTd = document.createElement("td");
    sourceTd.textContent = rel.source;
    tr.appendChild(sourceTd);

    const relTd = document.createElement("td");
    relTd.textContent = `${rel.label} (${rel.type})`;
    tr.appendChild(relTd);

    const targetTd = document.createElement("td");
    targetTd.textContent = rel.target;
    tr.appendChild(targetTd);

    const noteTd = document.createElement("td");
    noteTd.className = "meta-model-note";
    noteTd.textContent = rel.note || "";
    tr.appendChild(noteTd);

    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  tableScroll.appendChild(table);
  container.appendChild(tableScroll);
}

async function openMetaModelModal() {
  elements.metaModelModal.classList.remove("hidden");
  document.body.classList.add("modal-open");

  if (state.metaModel) {
    renderMetaModelBody(state.metaModel);
    return;
  }

  elements.metaModelBody.innerHTML = "";
  const loading = document.createElement("div");
  loading.className = "empty-state";
  loading.textContent = "Loading meta-model...";
  elements.metaModelBody.appendChild(loading);

  try {
    const metaModel = await loadMetaModel();
    renderMetaModelBody(metaModel);
  } catch (err) {
    elements.metaModelBody.innerHTML = "";
    const errorState = document.createElement("div");
    errorState.className = "empty-state";
    errorState.textContent = `Unable to load meta-model: ${err.message || String(err)}`;
    elements.metaModelBody.appendChild(errorState);
  }
}

function closeMetaModelModal() {
  elements.metaModelModal.classList.add("hidden");
  document.body.classList.remove("modal-open");
}

/* ---------------- Chat / general UI ---------------- */

function renderAll() {
  updateLastUsedTools();
  renderConversationList();
  renderConversationHeader();
  renderChatWindow();
  renderHealth();
  renderToolList();
  renderActionLog();
  updateMessageCounter();
  updateSendButtonState();
  updateRetryButtonState();
}

function abortPendingRequest(message = "Request stopped by user.") {
  if (!state.pendingController) {
    return false;
  }
  state.abortResponseMessage = message;
  state.pendingController.abort();
  return true;
}

function switchActiveConversation(conversationId) {
  if (!conversationId || state.activeConversationId === conversationId) {
    return;
  }
  if (!state.conversations.some((conv) => conv.id === conversationId)) {
    return;
  }
  setActiveConversationDraft(elements.messageInput.value);
  if (state.pendingController) {
    abortPendingRequest("");
  }
  state.activeConversationId = conversationId;
  persistState();
  renderConversationList();
  renderConversationHeader();
  renderChatWindow();
  updateLastUsedTools();
  renderToolList();
  syncComposerFromActiveConversation();
  elements.messageInput.focus();
}

function createNewConversation() {
  setActiveConversationDraft(elements.messageInput.value);
  if (state.pendingController) {
    abortPendingRequest("");
  }
  const conversation = createConversation();
  state.conversations.unshift(conversation);
  state.conversations = state.conversations.slice(0, MAX_CONVERSATIONS);
  state.activeConversationId = conversation.id;
  persistState();
  renderConversationList();
  renderConversationHeader();
  renderChatWindow();
  syncComposerFromActiveConversation();
  elements.messageInput.focus();
}

function deleteConversation(conversationId) {
  if (!conversationId) {
    return;
  }
  if (state.pendingController && state.activeConversationId === conversationId) {
    abortPendingRequest("");
  }
  if (state.conversations.length <= 1) {
    const active = getActiveConversation();
    active.history = [];
    active.title = "New conversation";
    active.updatedAt = new Date().toISOString();
    active.draft = "";
    persistState();
    renderConversationList();
    renderConversationHeader();
    renderChatWindow();
    syncComposerFromActiveConversation();
    return;
  }

  state.conversations = state.conversations.filter((conv) => conv.id !== conversationId);
  if (state.activeConversationId === conversationId) {
    state.activeConversationId = state.conversations[0]?.id || null;
  }
  persistState();
  renderConversationList();
  renderConversationHeader();
  renderChatWindow();
  syncComposerFromActiveConversation();
}

function clearActiveConversation() {
  if (state.pendingController) {
    abortPendingRequest("");
  }
  const active = getActiveConversation();
  active.history = [];
  active.title = "New conversation";
  active.updatedAt = new Date().toISOString();
  active.draft = "";
  persistState();
  renderConversationList();
  renderConversationHeader();
  renderChatWindow();
  syncComposerFromActiveConversation();
  elements.messageInput.focus();
}

function exportActiveConversation() {
  const active = getActiveConversation();
  const payload = {
    exported_at: new Date().toISOString(),
    conversation: active,
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  const safeTitle = active.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "conversation";
  anchor.href = url;
  anchor.download = `${safeTitle}-${Date.now()}.json`;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

function renameActiveConversation() {
  const active = getActiveConversation();
  const nextTitle = window.prompt("Conversation title", active.title || "New conversation");
  if (nextTitle === null) return;
  const clean = nextTitle.replace(/\s+/g, " ").trim();
  if (!clean) return;
  active.title = clean.slice(0, 80);
  active.updatedAt = new Date().toISOString();
  persistState();
  renderConversationList();
  renderConversationHeader();
}

function retryLastUserPrompt() {
  if (state.pendingController) {
    return;
  }
  const active = getActiveConversation();
  const lastUserTurn = [...active.history].reverse().find((turn) => turn.role === "user" && turn.content);
  if (!lastUserTurn) {
    return;
  }
  sendMessage(lastUserTurn.content);
}

function autoResizeTextarea(textarea) {
  textarea.style.height = "auto";
  const nextHeight = Math.min(textarea.scrollHeight, 160);
  textarea.style.height = `${nextHeight}px`;
}

function updateMessageCounter() {
  const rawLength = elements.messageInput.value.length;
  elements.messageCounter.textContent = `${rawLength}/${MAX_MESSAGE_LENGTH}`;
  elements.messageCounter.classList.toggle("over-limit", rawLength > MAX_MESSAGE_LENGTH);
}

function updateSendButtonState() {
  const rawValue = elements.messageInput.value || "";
  const hasText = rawValue.trim().length > 0;
  const overLimit = rawValue.length > MAX_MESSAGE_LENGTH;
  elements.sendBtn.disabled = Boolean(state.pendingController || state.pendingAction || !hasText || overLimit);
}

function setSending(isSending) {
  elements.sendBtn.textContent = isSending ? "Sending..." : "Send";
  updateSendButtonState();
  elements.stopBtn.disabled = !isSending;
  updateRetryButtonState();

  const existing = document.getElementById("typingIndicator");
  if (isSending && !existing) {
    const typing = document.createElement("div");
    typing.id = "typingIndicator";
    typing.className = "typing-indicator";
    typing.textContent = "Assistant is analyzing your model...";
    elements.chatWindow.appendChild(typing);
    elements.chatWindow.scrollTop = elements.chatWindow.scrollHeight;
  }
  if (!isSending && existing) {
    existing.remove();
  }
}

function appendTurn(conversation, role, content, tools = []) {
  const turn = {
    role,
    content: String(content || ""),
    tools: Array.isArray(tools) ? tools.map((t) => String(t)) : [],
    timestamp: new Date().toISOString(),
  };
  conversation.history.push(turn);
  conversation.history = conversation.history.slice(-400);
  conversation.updatedAt = turn.timestamp;
  if (role === "user" && (conversation.title === "New conversation" || conversation.history.length <= 2)) {
    conversation.title = deriveConversationTitle(content);
  }
}

function buildApiHistory(conversation) {
  return conversation.history
    .slice(0, -1)
    .filter((turn) => turn.role === "user" || turn.role === "assistant")
    .map((turn) => ({ role: turn.role, content: turn.content }));
}

async function sendMessage(rawMessage) {
  const raw = String(rawMessage || "");
  const content = raw.trim();
  if (!content || state.pendingController) return;
  if (raw.length > MAX_MESSAGE_LENGTH) {
    updateMessageCounter();
    updateSendButtonState();
    return;
  }

  const active = getActiveConversation();
  active.draft = "";
  appendTurn(active, "user", content);
  persistState();
  renderConversationList();
  renderConversationHeader();
  renderChatWindow();

  elements.messageInput.value = "";
  autoResizeTextarea(elements.messageInput);

  const controller = new AbortController();
  state.pendingController = controller;
  setSending(true);

  const systemPrompt = elements.systemPromptInput.value.trim().slice(0, MAX_SYSTEM_PROMPT_LENGTH);
  if (systemPrompt) {
    localStorage.setItem(SYSTEM_PROMPT_KEY, systemPrompt);
  } else {
    localStorage.removeItem(SYSTEM_PROMPT_KEY);
  }

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({
        message: content,
        history: buildApiHistory(active),
        system_prompt: systemPrompt || undefined,
      }),
    });

    let data = {};
    try {
      data = await response.json();
    } catch (err) {
      data = {};
    }

    if (!response.ok) {
      throw new Error(data.detail || `Request failed with status ${response.status}`);
    }

    appendTurn(active, "assistant", data.answer || "", data.used_tools || []);
    persistState();
    updateLastUsedTools();
    renderConversationList();
    renderConversationHeader();
    renderChatWindow();
    await loadHealth();
    if (!state.tools.length) {
      await loadTools();
    } else {
      renderToolList();
    }
  } catch (err) {
    if (err && err.name === "AbortError") {
      if (state.abortResponseMessage) {
        appendTurn(active, "assistant", state.abortResponseMessage);
      }
    } else {
      appendTurn(active, "assistant", `Error: ${err.message || String(err)}`);
    }
    persistState();
    renderConversationList();
    renderConversationHeader();
    renderChatWindow();
  } finally {
    state.abortResponseMessage = "Request stopped by user.";
    state.pendingController = null;
    setSending(false);
    updateSendButtonState();
    elements.messageInput.focus();
  }
}

async function loadHealth() {
  try {
    const res = await fetch("/api/health");
    if (!res.ok) {
      throw new Error(`Health request failed (${res.status})`);
    }
    state.health = await res.json();
  } catch (err) {
    state.health = {
      status: "error",
      mcp_status: "error",
      mcp_error: err.message || String(err),
      mcp_tool_count: 0,
    };
  }
  renderHealth();
}

async function loadTools() {
  try {
    const res = await fetch("/api/tools");
    if (!res.ok) {
      let payload = {};
      try {
        payload = await res.json();
      } catch (err) {
        payload = {};
      }
      throw new Error(payload.detail || `Tool request failed (${res.status})`);
    }
    const data = await res.json();
    state.tools = Array.isArray(data.tools) ? data.tools : [];
    renderToolList();
  } catch (err) {
    state.tools = [];
    renderToolList(`Unable to load tools: ${err.message || String(err)}`);
  }
}

function startHealthPolling() {
  if (state.healthPollTimer) {
    clearInterval(state.healthPollTimer);
  }
  state.healthPollTimer = setInterval(() => {
    if (document.visibilityState === "visible") {
      loadHealth();
    }
  }, HEALTH_POLL_INTERVAL_MS);
}

async function copyMessage(turnIndex, button) {
  const active = getActiveConversation();
  const turn = active.history[turnIndex];
  if (!turn) return;

  let copied = false;
  try {
    await navigator.clipboard.writeText(turn.content || "");
    copied = true;
  } catch (err) {
    const temp = document.createElement("textarea");
    temp.value = turn.content || "";
    document.body.appendChild(temp);
    temp.select();
    copied = document.execCommand("copy");
    document.body.removeChild(temp);
  }

  if (!copied) return;
  const original = button.textContent;
  button.textContent = "Copied";
  setTimeout(() => {
    button.textContent = original;
  }, 1200);
}

/* ---------------- Tabs + chat drawer ---------------- */

function setActiveTab(tabId) {
  state.activeTab = tabId;
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tabId);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("hidden", panel.id !== tabId);
  });
}

function setChatCollapsed(collapsed) {
  state.chatCollapsed = collapsed;
  elements.appGrid.classList.toggle("chat-collapsed", collapsed);
  elements.chatToggleBtn.textContent = collapsed ? "Show assistant" : "Hide assistant";
  elements.chatToggleBtn.setAttribute("aria-expanded", String(!collapsed));
  elements.drawerCollapseBtn.textContent = collapsed ? "«" : "»";
  elements.drawerCollapseBtn.title = collapsed ? "Expand assistant" : "Collapse assistant";
  try {
    localStorage.setItem(CHAT_COLLAPSED_KEY, collapsed ? "1" : "0");
  } catch (err) {
    // ignore storage errors
  }
}

function attachEventHandlers() {
  elements.chatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await sendMessage(elements.messageInput.value);
  });

  elements.messageInput.addEventListener("input", () => {
    setActiveConversationDraft(elements.messageInput.value);
    schedulePersist();
    autoResizeTextarea(elements.messageInput);
    updateMessageCounter();
    updateSendButtonState();
  });

  elements.messageInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      elements.chatForm.requestSubmit();
    }
  });

  elements.systemPromptInput.addEventListener("input", () => {
    if (elements.systemPromptInput.value.length > MAX_SYSTEM_PROMPT_LENGTH) {
      elements.systemPromptInput.value = elements.systemPromptInput.value.slice(0, MAX_SYSTEM_PROMPT_LENGTH);
    }
    try {
      localStorage.setItem(SYSTEM_PROMPT_KEY, elements.systemPromptInput.value);
    } catch (err) {
      console.warn("Unable to store system prompt:", err);
    }
  });

  elements.renameConversationBtn.addEventListener("click", renameActiveConversation);
  elements.clearBtn.addEventListener("click", clearActiveConversation);
  elements.exportBtn.addEventListener("click", exportActiveConversation);
  elements.retryBtn.addEventListener("click", retryLastUserPrompt);
  elements.newChatBtn.addEventListener("click", createNewConversation);

  elements.stopBtn.addEventListener("click", () => {
    abortPendingRequest("Request stopped by user.");
  });

  elements.refreshHealthBtn.addEventListener("click", loadHealth);
  elements.refreshHealthInlineBtn.addEventListener("click", loadHealth);
  elements.refreshToolsBtn.addEventListener("click", loadTools);

  elements.toolSearchInput.addEventListener("input", () => {
    renderToolList();
  });

  elements.chatWindow.addEventListener("click", async (event) => {
    if (!(event.target instanceof Element)) return;
    const button = event.target.closest(".message-copy");
    if (!button) return;
    const turnIndex = Number(button.dataset.turnIndex);
    if (!Number.isInteger(turnIndex)) return;
    await copyMessage(turnIndex, button);
  });

  document.querySelectorAll(".tab-btn").forEach((button) => {
    button.addEventListener("click", () => {
      setActiveTab(button.dataset.tab);
    });
  });

  elements.chatToggleBtn.addEventListener("click", () => {
    setChatCollapsed(!state.chatCollapsed);
  });
  elements.drawerCollapseBtn.addEventListener("click", () => {
    setChatCollapsed(!state.chatCollapsed);
  });

  elements.metaModelBtn.addEventListener("click", openMetaModelModal);
  elements.metaModelCloseBtn.addEventListener("click", closeMetaModelModal);
  elements.metaModelModal.addEventListener("click", (event) => {
    if (event.target === elements.metaModelModal) {
      closeMetaModelModal();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !elements.metaModelModal.classList.contains("hidden")) {
      closeMetaModelModal();
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      loadHealth();
    }
  });

  window.addEventListener("beforeunload", () => {
    setActiveConversationDraft(elements.messageInput.value);
    persistState();
  });
}

function init() {
  validateRequiredElements();
  loadPersistedState();

  try {
    const prompt = localStorage.getItem(SYSTEM_PROMPT_KEY);
    if (prompt) {
      elements.systemPromptInput.value = prompt.slice(0, MAX_SYSTEM_PROMPT_LENGTH);
    }
  } catch (err) {
    console.warn("Unable to load saved system prompt:", err);
  }

  attachEventHandlers();
  attachAssessmentEventHandlers();
  if (!state.assessment.pairs.length) {
    state.assessment.pairs = [createAssessmentPair(0)];
  }
  renderAssessmentPairRows();
  renderAssessmentCaptureList("ist");
  renderAssessmentCaptureList("soll");
  renderAssessmentPairingReview();
  renderAssessmentMappingPairList();
  setAssessmentStep("setup");
  setActiveTab(state.activeTab);
  setChatCollapsed(state.chatCollapsed);
  renderAll();
  syncComposerFromActiveConversation();
  updateMessageCounter();
  updateSendButtonState();
  loadHealth();
  loadTools();
  startHealthPolling();
  elements.messageInput.focus();
}

init();
