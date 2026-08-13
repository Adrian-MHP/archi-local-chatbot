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
  chatCollapsed: false,
  metaModel: null,
  assessment: {
    currentStep: "setup",
    completedSteps: new Set(),
    pending: false,
    istPlan: null,
    sollPlan: null,
    mappingResult: null,
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
  assessmentIstViewName: document.getElementById("assessmentIstViewName"),
  assessmentSollViewName: document.getElementById("assessmentSollViewName"),
  assessmentSetupBtn: document.getElementById("assessmentSetupBtn"),
  assessmentSetupResult: document.getElementById("assessmentSetupResult"),
  assessmentSetupContinueBtn: document.getElementById("assessmentSetupContinueBtn"),
  assessmentIstForm: document.getElementById("assessmentIstForm"),
  assessmentIstFile: document.getElementById("assessmentIstFile"),
  assessmentIstBtn: document.getElementById("assessmentIstBtn"),
  assessmentIstStatus: document.getElementById("assessmentIstStatus"),
  assessmentIstPreview: document.getElementById("assessmentIstPreview"),
  assessmentIstCounts: document.getElementById("assessmentIstCounts"),
  assessmentIstTable: document.getElementById("assessmentIstTable"),
  assessmentIstDiscardBtn: document.getElementById("assessmentIstDiscardBtn"),
  assessmentIstApplyBtn: document.getElementById("assessmentIstApplyBtn"),
  assessmentIstBackBtn: document.getElementById("assessmentIstBackBtn"),
  assessmentIstContinueBtn: document.getElementById("assessmentIstContinueBtn"),
  assessmentSollForm: document.getElementById("assessmentSollForm"),
  assessmentSollFile: document.getElementById("assessmentSollFile"),
  assessmentSollBtn: document.getElementById("assessmentSollBtn"),
  assessmentSollProposeBtn: document.getElementById("assessmentSollProposeBtn"),
  assessmentSollStatus: document.getElementById("assessmentSollStatus"),
  assessmentSollPreview: document.getElementById("assessmentSollPreview"),
  assessmentSollCounts: document.getElementById("assessmentSollCounts"),
  assessmentSollTable: document.getElementById("assessmentSollTable"),
  assessmentSollDiscardBtn: document.getElementById("assessmentSollDiscardBtn"),
  assessmentSollApplyBtn: document.getElementById("assessmentSollApplyBtn"),
  assessmentSollBackBtn: document.getElementById("assessmentSollBackBtn"),
  assessmentSollContinueBtn: document.getElementById("assessmentSollContinueBtn"),
  assessmentMappingRunBtn: document.getElementById("assessmentMappingRunBtn"),
  assessmentMappingStatus: document.getElementById("assessmentMappingStatus"),
  assessmentMappingResult: document.getElementById("assessmentMappingResult"),
  assessmentMappingTable: document.getElementById("assessmentMappingTable"),
  assessmentGapTable: document.getElementById("assessmentGapTable"),
  assessmentMappingApplyBtn: document.getElementById("assessmentMappingApplyBtn"),
  assessmentMappingViewName: document.getElementById("assessmentMappingViewName"),
  assessmentMappingBackBtn: document.getElementById("assessmentMappingBackBtn"),
  assessmentMappingContinueBtn: document.getElementById("assessmentMappingContinueBtn"),
  assessmentSummaryRunBtn: document.getElementById("assessmentSummaryRunBtn"),
  assessmentSummaryStatus: document.getElementById("assessmentSummaryStatus"),
  assessmentSummaryResult: document.getElementById("assessmentSummaryResult"),
  assessmentSummaryStats: document.getElementById("assessmentSummaryStats"),
  assessmentSummaryText: document.getElementById("assessmentSummaryText"),
  assessmentSummaryCopyBtn: document.getElementById("assessmentSummaryCopyBtn"),
  assessmentSummaryBackBtn: document.getElementById("assessmentSummaryBackBtn"),
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
    state.chatCollapsed = localStorage.getItem(CHAT_COLLAPSED_KEY) === "1";
  } catch (err) {
    state.chatCollapsed = false;
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

async function runAssessmentSetup() {
  const istViewName = elements.assessmentIstViewName.value.trim() || "Ist-Business-Prozesse";
  const sollViewName = elements.assessmentSollViewName.value.trim() || "Soll-Architektur";
  elements.assessmentSetupBtn.disabled = true;
  elements.assessmentSetupResult.classList.add("hidden");
  try {
    const params = new URLSearchParams({ ist_view_name: istViewName, soll_view_name: sollViewName });
    const res = await fetch(`/api/assessment/setup?${params.toString()}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Setup check failed (${res.status})`);

    const box = elements.assessmentSetupResult;
    box.innerHTML = "";
    box.classList.remove("hidden");
    for (const note of data.notes || []) {
      const p = document.createElement("p");
      p.textContent = note;
      box.appendChild(p);
    }
    elements.assessmentSetupContinueBtn.disabled = false;
    markAssessmentStepComplete("setup");
  } catch (err) {
    const box = elements.assessmentSetupResult;
    box.innerHTML = "";
    box.classList.remove("hidden");
    const p = document.createElement("p");
    p.textContent = `Error: ${err.message || String(err)}`;
    box.appendChild(p);
  } finally {
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

async function loadAssessmentIstPreview() {
  const file = elements.assessmentIstFile.files && elements.assessmentIstFile.files[0];
  if (!file) {
    setAssessmentStatus(elements.assessmentIstStatus, "Select a file first.", "error");
    return;
  }
  if (state.assessment.pending) return;
  state.assessment.pending = true;
  elements.assessmentIstBtn.disabled = true;
  setAssessmentStatus(elements.assessmentIstStatus, `Extracting preview from '${file.name}'...`, "pending");
  try {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("view_name", elements.assessmentIstViewName.value.trim() || "Ist-Business-Prozesse");
    const res = await fetch("/api/actions/business-process-upload/preview", { method: "POST", body: formData });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Preview failed (${res.status})`);
    state.assessment.istPlan = data;
    elements.assessmentIstPreview.classList.remove("hidden");
    renderAssessmentElementsTable(data, elements.assessmentIstTable.querySelector("tbody"), elements.assessmentIstCounts);
    setAssessmentStatus(
      elements.assessmentIstStatus,
      `Preview ready: ${data.elements.length} elements, ${data.relationships.length} relationships. Review, then click "Create in Archi".`,
      "ok"
    );
    elements.assessmentIstFile.value = "";
  } catch (err) {
    setAssessmentStatus(elements.assessmentIstStatus, `Error: ${err.message || String(err)}`, "error");
  } finally {
    state.assessment.pending = false;
    elements.assessmentIstBtn.disabled = false;
  }
}

async function applyAssessmentIstPlan() {
  const plan = state.assessment.istPlan;
  if (!plan || state.assessment.pending) return;
  if (!plan.elements.some((el) => el.include)) {
    setAssessmentStatus(elements.assessmentIstStatus, "Select at least one element before applying.", "error");
    return;
  }
  state.assessment.pending = true;
  elements.assessmentIstApplyBtn.disabled = true;
  setAssessmentStatus(elements.assessmentIstStatus, `Creating '${plan.view_name}' in Archi...`, "pending");
  try {
    const res = await fetch("/api/actions/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Apply failed (${res.status})`);
    setAssessmentStatus(elements.assessmentIstStatus, data.summary || "Applied to Archi.", "ok");
    pushActionLogEntry({
      action: "business-process-upload",
      viewName: data.view_name || plan.view_name,
      summary: data.summary || "Applied to Archi.",
      tone: "ok",
    });
    markAssessmentStepComplete("ist");
    elements.assessmentIstContinueBtn.disabled = false;
    state.assessment.istPlan = null;
    elements.assessmentIstPreview.classList.add("hidden");
    await loadHealth();
    await loadTools();
  } catch (err) {
    setAssessmentStatus(elements.assessmentIstStatus, `Error: ${err.message || String(err)}`, "error");
  } finally {
    state.assessment.pending = false;
    elements.assessmentIstApplyBtn.disabled = false;
  }
}

function discardAssessmentIstPlan() {
  state.assessment.istPlan = null;
  elements.assessmentIstPreview.classList.add("hidden");
  setAssessmentStatus(elements.assessmentIstStatus, "Preview discarded. Nothing was written to Archi.", "neutral");
}

async function loadAssessmentSollPreview() {
  const file = elements.assessmentSollFile.files && elements.assessmentSollFile.files[0];
  if (!file) {
    setAssessmentStatus(elements.assessmentSollStatus, "Select a file first.", "error");
    return;
  }
  if (state.assessment.pending) return;
  state.assessment.pending = true;
  elements.assessmentSollBtn.disabled = true;
  setAssessmentStatus(elements.assessmentSollStatus, `Extracting preview from '${file.name}'...`, "pending");
  try {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("view_name", elements.assessmentSollViewName.value.trim() || "Soll-Architektur");
    const res = await fetch("/api/assessment/soll-architecture/preview", { method: "POST", body: formData });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Preview failed (${res.status})`);
    state.assessment.sollPlan = data;
    elements.assessmentSollPreview.classList.remove("hidden");
    renderAssessmentElementsTable(data, elements.assessmentSollTable.querySelector("tbody"), elements.assessmentSollCounts);
    setAssessmentStatus(
      elements.assessmentSollStatus,
      `Preview ready: ${data.elements.length} elements, ${data.relationships.length} relationships (tagged status=target). Review, then click "Create in Archi".`,
      "ok"
    );
    elements.assessmentSollFile.value = "";
  } catch (err) {
    setAssessmentStatus(elements.assessmentSollStatus, `Error: ${err.message || String(err)}`, "error");
  } finally {
    state.assessment.pending = false;
    elements.assessmentSollBtn.disabled = false;
  }
}

async function proposeAssessmentSollArchitecture() {
  if (state.assessment.pending) return;
  state.assessment.pending = true;
  elements.assessmentSollProposeBtn.disabled = true;
  setAssessmentStatus(elements.assessmentSollStatus, "Generating a Soll-Architektur proposal from the Ist processes...", "pending");
  try {
    const istViewName = elements.assessmentIstViewName.value.trim() || "Ist-Business-Prozesse";
    const viewName = elements.assessmentSollViewName.value.trim() || "Soll-Architektur";
    const res = await fetch("/api/assessment/soll-architecture/propose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ist_view_name: istViewName, view_name: viewName }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Proposal failed (${res.status})`);
    state.assessment.sollPlan = data;
    elements.assessmentSollPreview.classList.remove("hidden");
    renderAssessmentElementsTable(data, elements.assessmentSollTable.querySelector("tbody"), elements.assessmentSollCounts);
    setAssessmentStatus(
      elements.assessmentSollStatus,
      `Proposal ready: ${data.elements.length} processes, ${data.relationships.length} relationships (tagged status=target). Review carefully, then click "Create in Archi".`,
      "ok"
    );
  } catch (err) {
    setAssessmentStatus(elements.assessmentSollStatus, `Error: ${err.message || String(err)}`, "error");
  } finally {
    state.assessment.pending = false;
    elements.assessmentSollProposeBtn.disabled = false;
  }
}

async function applyAssessmentSollPlan() {
  const plan = state.assessment.sollPlan;
  if (!plan || state.assessment.pending) return;
  if (!plan.elements.some((el) => el.include)) {
    setAssessmentStatus(elements.assessmentSollStatus, "Select at least one element before applying.", "error");
    return;
  }
  state.assessment.pending = true;
  elements.assessmentSollApplyBtn.disabled = true;
  setAssessmentStatus(elements.assessmentSollStatus, `Creating '${plan.view_name}' in Archi...`, "pending");
  try {
    const res = await fetch("/api/actions/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Apply failed (${res.status})`);
    setAssessmentStatus(elements.assessmentSollStatus, data.summary || "Applied to Archi.", "ok");
    pushActionLogEntry({
      action: "assessment-soll-upload",
      viewName: data.view_name || plan.view_name,
      summary: data.summary || "Applied to Archi.",
      tone: "ok",
    });
    markAssessmentStepComplete("soll");
    elements.assessmentSollContinueBtn.disabled = false;
    state.assessment.sollPlan = null;
    elements.assessmentSollPreview.classList.add("hidden");
    await loadHealth();
    await loadTools();
  } catch (err) {
    setAssessmentStatus(elements.assessmentSollStatus, `Error: ${err.message || String(err)}`, "error");
  } finally {
    state.assessment.pending = false;
    elements.assessmentSollApplyBtn.disabled = false;
  }
}

function discardAssessmentSollPlan() {
  state.assessment.sollPlan = null;
  elements.assessmentSollPreview.classList.add("hidden");
  setAssessmentStatus(elements.assessmentSollStatus, "Preview discarded. Nothing was written to Archi.", "neutral");
}

const ASSESSMENT_MATCH_TYPE_LABELS = {
  full: "Full match",
  partial: "Partial match",
  legacy_no_soll: "Legacy (no Soll)",
  gap_new: "New (no Ist)",
};

const ASSESSMENT_CRITICALITY_LABELS = { high: "High", medium: "Medium", low: "Low" };

const ASSESSMENT_CATEGORY_LABELS = {
  missing_process: "Missing process",
  redundancy: "Redundancy",
  structural_difference: "Structural difference",
  tooling_data_gap: "Tooling / data gap",
};

function renderAssessmentMappingTable(data) {
  const tbody = elements.assessmentMappingTable.querySelector("tbody");
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
      ? "Matched pairs are connected in the mapping view; Ist-only/Soll-only rows are shown standalone."
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

function renderAssessmentGapTable(data) {
  const tbody = elements.assessmentGapTable.querySelector("tbody");
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

async function runAssessmentMapping() {
  if (state.assessment.pending) return;
  state.assessment.pending = true;
  elements.assessmentMappingRunBtn.disabled = true;
  setAssessmentStatus(elements.assessmentMappingStatus, "Running mapping & gap analysis...", "pending");
  try {
    const istViewName = elements.assessmentIstViewName.value.trim() || "Ist-Business-Prozesse";
    const sollViewName = elements.assessmentSollViewName.value.trim() || "Soll-Architektur";
    const res = await fetch("/api/assessment/mapping/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ist_view_name: istViewName, soll_view_name: sollViewName }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Mapping analysis failed (${res.status})`);
    state.assessment.mappingResult = data;
    elements.assessmentMappingResult.classList.remove("hidden");
    renderAssessmentMappingTable(data);
    renderAssessmentGapTable(data);
    const warningText = data.warnings && data.warnings.length ? ` (${data.warnings.join(" ")})` : "";
    setAssessmentStatus(
      elements.assessmentMappingStatus,
      `Found ${data.mappings.length} mapping(s) and ${data.gaps.length} gap(s).${warningText}`,
      "ok"
    );
  } catch (err) {
    setAssessmentStatus(elements.assessmentMappingStatus, `Error: ${err.message || String(err)}`, "error");
  } finally {
    state.assessment.pending = false;
    elements.assessmentMappingRunBtn.disabled = false;
  }
}

async function applyAssessmentMappings() {
  const result = state.assessment.mappingResult;
  if (!result || state.assessment.pending) return;
  const included = result.mappings.filter((m) => m.include && (m.ist_key || m.soll_key));
  if (!included.length) {
    setAssessmentStatus(elements.assessmentMappingStatus, "Select at least one mapping to apply.", "error");
    return;
  }
  state.assessment.pending = true;
  elements.assessmentMappingApplyBtn.disabled = true;
  setAssessmentStatus(elements.assessmentMappingStatus, "Building the mapping view in Archi...", "pending");
  try {
    const viewName = elements.assessmentMappingViewName.value.trim() || "Ist-Soll Mapping";
    const res = await fetch("/api/assessment/mapping/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mappings: result.mappings, view_name: viewName }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Applying mappings failed (${res.status})`);
    setAssessmentStatus(elements.assessmentMappingStatus, data.summary || "Mappings applied.", "ok");
    pushActionLogEntry({
      action: "assessment-mapping",
      viewName: data.view_name || viewName,
      summary: data.summary || "Mappings applied.",
      tone: "ok",
    });
    markAssessmentStepComplete("mapping");
    elements.assessmentMappingContinueBtn.disabled = false;
  } catch (err) {
    setAssessmentStatus(elements.assessmentMappingStatus, `Error: ${err.message || String(err)}`, "error");
  } finally {
    state.assessment.pending = false;
    elements.assessmentMappingApplyBtn.disabled = false;
  }
}

function renderAssessmentSummary(data) {
  const stats = elements.assessmentSummaryStats;
  stats.innerHTML = "";
  const rows = [
    ["Ist processes mapped", data.ist_process_count],
    ["Soll processes mapped", data.soll_process_count],
    ["Full matches", data.full_matches],
    ["Partial matches", data.partial_matches],
    ["Gaps identified", data.gap_count],
    ["Critical gaps", data.critical_gap_count],
    ["Average similarity", `${data.average_similarity}%`],
    ["Maturity score", `${data.maturity_score}%`],
  ];
  for (const [label, value] of rows) {
    const div = document.createElement("div");
    const span = document.createElement("span");
    span.textContent = label;
    const strong = document.createElement("strong");
    strong.textContent = String(value);
    div.appendChild(span);
    div.appendChild(strong);
    stats.appendChild(div);
  }
  elements.assessmentSummaryText.textContent = data.executive_summary || "";
}

async function runAssessmentSummary() {
  const result = state.assessment.mappingResult;
  if (!result) {
    setAssessmentStatus(elements.assessmentSummaryStatus, "Run mapping & gap analysis first.", "error");
    return;
  }
  if (state.assessment.pending) return;
  state.assessment.pending = true;
  elements.assessmentSummaryRunBtn.disabled = true;
  setAssessmentStatus(elements.assessmentSummaryStatus, "Generating summary...", "pending");
  try {
    const res = await fetch("/api/assessment/summary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mappings: result.mappings, gaps: result.gaps }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Summary generation failed (${res.status})`);
    renderAssessmentSummary(data);
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

  elements.assessmentSetupBtn.addEventListener("click", runAssessmentSetup);
  elements.assessmentSetupContinueBtn.addEventListener("click", () => setAssessmentStep("ist"));

  elements.assessmentIstForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await loadAssessmentIstPreview();
  });
  elements.assessmentIstApplyBtn.addEventListener("click", applyAssessmentIstPlan);
  elements.assessmentIstDiscardBtn.addEventListener("click", discardAssessmentIstPlan);
  elements.assessmentIstBackBtn.addEventListener("click", () => setAssessmentStep("setup"));
  elements.assessmentIstContinueBtn.addEventListener("click", () => setAssessmentStep("soll"));

  elements.assessmentSollForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await loadAssessmentSollPreview();
  });
  elements.assessmentSollApplyBtn.addEventListener("click", applyAssessmentSollPlan);
  elements.assessmentSollProposeBtn.addEventListener("click", proposeAssessmentSollArchitecture);
  elements.assessmentSollDiscardBtn.addEventListener("click", discardAssessmentSollPlan);
  elements.assessmentSollBackBtn.addEventListener("click", () => setAssessmentStep("ist"));
  elements.assessmentSollContinueBtn.addEventListener("click", () => setAssessmentStep("mapping"));

  elements.assessmentMappingRunBtn.addEventListener("click", runAssessmentMapping);
  elements.assessmentMappingApplyBtn.addEventListener("click", applyAssessmentMappings);
  elements.assessmentMappingBackBtn.addEventListener("click", () => setAssessmentStep("soll"));
  elements.assessmentMappingContinueBtn.addEventListener("click", () => setAssessmentStep("summary"));

  elements.assessmentSummaryRunBtn.addEventListener("click", runAssessmentSummary);
  elements.assessmentSummaryBackBtn.addEventListener("click", () => setAssessmentStep("mapping"));
  elements.assessmentSummaryCopyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(elements.assessmentSummaryText.textContent || "");
      const original = elements.assessmentSummaryCopyBtn.textContent;
      elements.assessmentSummaryCopyBtn.textContent = "Copied";
      setTimeout(() => {
        elements.assessmentSummaryCopyBtn.textContent = original;
      }, 1200);
    } catch (err) {
      // clipboard unavailable; ignore
    }
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

  const quickPrompts = document.querySelectorAll(".quick-prompt");
  quickPrompts.forEach((button) => {
    button.addEventListener("click", () => {
      const prompt = button.dataset.prompt || "";
      elements.messageInput.value = prompt;
      setActiveConversationDraft(prompt);
      schedulePersist();
      autoResizeTextarea(elements.messageInput);
      updateMessageCounter();
      updateSendButtonState();
      elements.messageInput.focus();
    });
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
