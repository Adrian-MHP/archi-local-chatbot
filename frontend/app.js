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
  chatPending: null, // the answer being streamed: {conversationId, controller, text, steps, ...}
  lastUsedTools: new Set(),
  healthPollTimer: null,
  persistTimer: null,
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
    steering: { data: null, tab: "capabilities", loading: false, awaitingApproval: false },
  },
};

const ASSESSMENT_STEPS = ["setup", "ist", "soll", "mapping", "steering", "summary"];

const elements = {
  appGrid: document.getElementById("appGrid"),
  chatDrawer: document.getElementById("chatDrawer"),
  chatPanel: document.getElementById("chatPanel"),
  chatRail: document.getElementById("chatRail"),
  drawerCollapseBtn: document.getElementById("drawerCollapseBtn"),
  historyBtn: document.getElementById("historyBtn"),
  historyPanel: document.getElementById("historyPanel"),
  historySearch: document.getElementById("historySearch"),
  chatMenuBtn: document.getElementById("chatMenuBtn"),
  chatMenu: document.getElementById("chatMenu"),
  chatWindow: document.getElementById("chatWindow"),
  jumpLatestBtn: document.getElementById("jumpLatestBtn"),
  chatNotice: document.getElementById("chatNotice"),
  chatForm: document.getElementById("chatForm"),
  messageInput: document.getElementById("messageInput"),
  sendBtn: document.getElementById("sendBtn"),
  stopBtn: document.getElementById("stopBtn"),
  newChatBtn: document.getElementById("newChatBtn"),
  messageCounter: document.getElementById("messageCounter"),
  conversationList: document.getElementById("conversationList"),
  conversationTitle: document.getElementById("conversationTitle"),
  conversationMeta: document.getElementById("conversationMeta"),
  systemPromptInput: document.getElementById("systemPromptInput"),
  systemPromptResetBtn: document.getElementById("systemPromptResetBtn"),
  systemPromptStatus: document.getElementById("systemPromptStatus"),
  healthText: document.getElementById("healthText"),
  healthStatus: document.getElementById("healthStatus"),
  healthMcpStatus: document.getElementById("healthMcpStatus"),
  healthArchiModel: document.getElementById("healthArchiModel"),
  healthApproval: document.getElementById("healthApproval"),
  healthAzureModel: document.getElementById("healthAzureModel"),
  healthToolCount: document.getElementById("healthToolCount"),
  healthServer: document.getElementById("healthServer"),
  connectionBadge: document.getElementById("connectionBadge"),
  approvalBadge: document.getElementById("approvalBadge"),
  dashboardBtn: document.getElementById("dashboardBtn"),
  steeringDashboardBtn: document.getElementById("steeringDashboardBtn"),
  toolList: document.getElementById("toolList"),
  toolSearchInput: document.getElementById("toolSearchInput"),
  toolCountBadge: document.getElementById("toolCountBadge"),
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
  steeringLoadBtn: document.getElementById("steeringLoadBtn"),
  steeringProposeBtn: document.getElementById("steeringProposeBtn"),
  steeringApplyBtn: document.getElementById("steeringApplyBtn"),
  steeringStatus: document.getElementById("steeringStatus"),
  steeringTabs: document.getElementById("steeringTabs"),
  steeringTable: document.getElementById("steeringTable"),
  assessmentSteeringBackBtn: document.getElementById("assessmentSteeringBackBtn"),
  assessmentSteeringContinueBtn: document.getElementById("assessmentSteeringContinueBtn"),
  assessmentStartNewBtn: document.getElementById("assessmentStartNewBtn"),
  cycleNameInput: document.getElementById("cycleNameInput"),
  cycleNoteInput: document.getElementById("cycleNoteInput"),
  cycleSaveBtn: document.getElementById("cycleSaveBtn"),
  cycleStatus: document.getElementById("cycleStatus"),
  cycleLastBaseline: document.getElementById("cycleLastBaseline"),
  assessmentResetBtn: document.getElementById("assessmentResetBtn"),
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

function renderHealth() {
  const health = state.health || {};
  const mcpStatus = String(health.mcp_status || "unknown");
  const toolCount = Number.isFinite(health.mcp_tool_count) ? health.mcp_tool_count : "--";
  const approval = health.archi_approval_mode;
  const pending = Number.isFinite(health.archi_pending_approvals) ? health.archi_pending_approvals : 0;

  elements.healthStatus.textContent = health.status === "ok" ? "Running" : health.status === "error" ? "Not reachable" : "--";
  elements.healthMcpStatus.textContent = mcpStatus === "ok" ? "Connected"
    : mcpStatus === "error" ? `Not reachable${health.mcp_error ? ` (${health.mcp_error})` : ""}` : "Checking…";
  elements.healthMcpStatus.className = mcpStatus === "ok" ? "text-ok" : mcpStatus === "error" ? "text-error" : "";
  elements.healthArchiModel.textContent = health.archi_model || "--";
  elements.healthApproval.textContent = approval === true ? `On${pending ? ` · ${pending} pending` : ""}` : approval === false ? "Off" : "--";
  elements.healthAzureModel.textContent = health.azure_model || "--";
  elements.healthToolCount.textContent = String(toolCount);
  elements.healthServer.textContent = String(health.mcp_server_url || "--").replace(/^https?:\/\//, "");
  elements.healthText.textContent = JSON.stringify(health, null, 2);
  renderChatContext();

  // Approval mode as reported by Archi's MCP plugin -- when on, writes wait in Archi for approval.
  elements.approvalBadge.className = "badge";
  if (approval === true) {
    elements.approvalBadge.classList.add("status", "status-warn");
    elements.approvalBadge.textContent = pending ? `Approval mode on · ${pending} pending` : "Approval mode on";
    elements.approvalBadge.title = "Changes from this app wait in Archi under MCP Server > Pending approvals until you approve them.";
  } else if (approval === false) {
    elements.approvalBadge.textContent = "Approval mode off";
    elements.approvalBadge.title = "Changes from this app are applied in Archi immediately.";
  } else {
    elements.approvalBadge.textContent = "Approval: unknown";
    elements.approvalBadge.title = "The approval mode could not be read from Archi.";
  }
  elements.connectionBadge.className = "badge status badge-btn";
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
  steering: "assessmentStepSteering",
  summary: "assessmentStepSummary",
};

function setAssessmentStep(step) {
  if (!ASSESSMENT_STEPS.includes(step)) return;
  state.assessment.currentStep = step;
  setTimeout(persistAssessmentSession, 0);
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
  if (step === "steering") {
    if (!state.assessment.steering.data && !state.assessment.steering.loading) loadSteering();
    else renderSteering();
    if (state.assessment.steering.awaitingApproval) setAssessmentStatus(elements.steeringStatus, STEERING_AWAITING_APPROVAL, "warn");
  }
  if (step === "summary") refreshCycleCard();
}

/* ---------------- Assessment cycle: save a baseline ---------------- */

function localIsoDate() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

function formatDay(iso) {
  return iso ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "";
}

// Proposes a name and shows the latest baseline of the active model (the one this cycle is compared with).
async function refreshCycleCard() {
  let baselines = [];
  try {
    const res = await fetch("/api/baselines");
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    baselines = data.baselines || [];
    const last = baselines[baselines.length - 1];
    elements.cycleLastBaseline.textContent = last
      ? `Latest baseline of ${data.model.name}: "${last.name}" of ${formatDay(last.date)} (${baselines.length} in total).`
      : `${data.model.name} has no baseline yet: this will be the first.`;
  } catch (err) {
    elements.cycleLastBaseline.textContent = `The baselines could not be read: ${err.message || err}`;
  }
  if (!elements.cycleNameInput.value.trim()) {
    const month = new Date().toLocaleDateString("en-GB", { month: "short", year: "numeric" });
    elements.cycleNameInput.value = `${baselines.length ? "Re-assessment" : "Assessment"} ${month}`;
  }
}

async function saveCycleBaseline() {
  const name = elements.cycleNameInput.value.trim();
  if (!name) {
    setAssessmentStatus(elements.cycleStatus, "Give the baseline a name, e.g. 'Assessment Q4 2026'.", "error");
    return;
  }
  elements.cycleSaveBtn.disabled = true;
  setAssessmentStatus(elements.cycleStatus, "Reading the model and committing the baseline...", "pending");
  try {
    const res = await fetch("/api/baselines", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, note: elements.cycleNoteInput.value.trim(), date: localIsoDate() }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Request failed (${res.status})`);
    setAssessmentStatus(
      elements.cycleStatus,
      `Baseline "${data.name}" saved (${data.model.elementCount} elements, git commit ${data.commit}). The next re-assessment and the dashboard's Progress section measure against it.`,
      "ok",
    );
    const open = document.createElement("button");
    open.type = "button";
    open.className = "btn ghost small cycle-open";
    open.textContent = "Open Progress";
    open.addEventListener("click", openProgress);
    elements.cycleStatus.appendChild(open);
    elements.cycleNameInput.value = "";
    elements.cycleNoteInput.value = "";
    markAssessmentStepComplete("summary");
    refreshCycleCard();
  } catch (err) {
    setAssessmentStatus(elements.cycleStatus, `The baseline was not saved: ${err.message || err}`, "error");
  } finally {
    elements.cycleSaveBtn.disabled = false;
  }
}

function openProgress() {
  setActiveTab("dashboardTab");
  // The dashboard renders asynchronously; jump to the section once it exists.
  const started = Date.now();
  const jump = () => {
    const target = document.getElementById("dash-progress");
    if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    else if (Date.now() - started < 8000) setTimeout(jump, 200);
  };
  jump();
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

function renderAssessmentSetupResult() {
  const box = elements.assessmentSetupResult;
  box.innerHTML = "";
  box.classList.remove("hidden");
  state.assessment.pairs.forEach((pair, index) => {
    const group = document.createElement("div");
    group.className = "assessment-result-pair-group";
    const heading = document.createElement("strong");
    heading.textContent = `Pair ${index + 1}: ${pair.istViewName} / ${pair.sollViewName}`;
    group.appendChild(heading);
    for (const note of pair.setupNotes || []) {
      const p = document.createElement("p");
      p.textContent = note;
      group.appendChild(p);
    }
    box.appendChild(group);
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
    renderAssessmentSetupResult();
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

  const earlierFiles = side === "ist" ? pair.istFileNames : pair.sollFileNames;
  if (!files.length && Array.isArray(earlierFiles) && earlierFiles.length) {
    const earlier = document.createElement("p");
    earlier.className = "file-order-hint";
    earlier.textContent = `Used earlier in this session: ${earlierFiles.join(", ")}. Select the file(s) again only if you want to re-run the extraction.`;
    card.appendChild(earlier);
  }

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

/* ---------------- Assessment session (survives reloads within this browser tab) ---------------- */

// The wizard's progress lives in sessionStorage: it survives reloads and switching to the dashboard,
// and is scoped to this browser tab. Uploaded File objects cannot be stored -- their names are kept
// so the cards can say what was used; plans, mappings, ratings and the summary are kept completely.
const ASSESSMENT_SESSION_KEY = "archi-assessment-session-v1";
let lastAssessmentSessionSnapshot = "";

function assessmentSessionSnapshot() {
  const a = state.assessment;
  const names = (files, earlier) => (files && files.length ? files.map((f) => f.name) : earlier || []);
  return JSON.stringify({
    version: 1,
    activeTab: state.activeTab,
    currentStep: a.currentStep,
    completedSteps: [...a.completedSteps],
    pairs: a.pairs.map((p) => ({
      ...p,
      istFiles: [],
      sollFiles: [],
      istFileNames: names(p.istFiles, p.istFileNames),
      sollFileNames: names(p.sollFiles, p.sollFileNames),
    })),
    summaryResult: a.summaryResult,
    steering: { data: a.steering.data, tab: a.steering.tab, awaitingApproval: a.steering.awaitingApproval },
  });
}

function persistAssessmentSession() {
  try {
    const snapshot = assessmentSessionSnapshot();
    if (snapshot === lastAssessmentSessionSnapshot) return;
    sessionStorage.setItem(ASSESSMENT_SESSION_KEY, snapshot);
    lastAssessmentSessionSnapshot = snapshot;
  } catch (err) {
    console.warn("Unable to keep the assessment for this session:", err);
  }
}

function restoreAssessmentSession() {
  let saved = null;
  try {
    saved = JSON.parse(sessionStorage.getItem(ASSESSMENT_SESSION_KEY) || "null");
  } catch (err) {
    saved = null;
  }
  if (!saved || saved.version !== 1 || !Array.isArray(saved.pairs) || !saved.pairs.length) return false;
  state.assessment = {
    currentStep: ASSESSMENT_STEPS.includes(saved.currentStep) ? saved.currentStep : "setup",
    completedSteps: new Set((saved.completedSteps || []).filter((s) => ASSESSMENT_STEPS.includes(s))),
    pending: false,
    pairs: saved.pairs.map((p, index) => ({ ...createAssessmentPair(index), ...p, istFiles: [], sollFiles: [] })),
    summaryResult: saved.summaryResult || null,
    steering: {
      data: (saved.steering && saved.steering.data) || null,
      tab: (saved.steering && saved.steering.tab) || "capabilities",
      loading: false,
      awaitingApproval: Boolean(saved.steering && saved.steering.awaitingApproval),
    },
  };
  const savedTab = ["healthTab", "toolsTab"].includes(saved.activeTab) ? "settingsTab" : saved.activeTab; // before Settings
  if (["assessmentTab", "dashboardTab", "settingsTab"].includes(savedTab)) state.activeTab = savedTab;
  lastAssessmentSessionSnapshot = "";
  return true;
}

function applyRestoredAssessmentUi() {
  const a = state.assessment;
  document.querySelectorAll(".assessment-step-btn").forEach((btn) => {
    const step = btn.dataset.step;
    const previous = ASSESSMENT_STEPS[ASSESSMENT_STEPS.indexOf(step) - 1];
    btn.disabled = !(step === "setup" || step === a.currentStep || a.completedSteps.has(step) || (previous && a.completedSteps.has(previous)));
  });
  if (a.pairs.some((p) => p.setupChecked)) renderAssessmentSetupResult();
  elements.assessmentSetupContinueBtn.disabled = !a.completedSteps.has("setup");
  updateAssessmentCaptureContinueState("ist");
  updateAssessmentCaptureContinueState("soll");
  updateAssessmentMappingContinueState();
  if (a.summaryResult) {
    renderAssessmentSummary(a.summaryResult);
    renderSummaryPairBreakdown();
    elements.assessmentSummaryResult.classList.remove("hidden");
  }
  refreshAssessmentStepperClasses();
}

/* ---------------- Ratings & Roadmap (step 5) ---------------- */

// One editable table per element type. "prop" columns are rendered from the meta-model's property
// schema (sent by the backend with the data), so enum options and value ranges are never duplicated here.
const STEERING_TABLES = [
  {
    key: "capabilities", label: "Capabilities", singular: "capability", type: "Capability", addable: true,
    hint: "Current and target maturity drive the dashboard's heat map; the plateau says when the gap is closed.",
    columns: [
      { field: "include", kind: "include" },
      { field: "name", label: "Capability", kind: "name" },
      { field: "capabilityDomain", kind: "prop" },
      { field: "maturity", kind: "prop" },
      { field: "targetMaturity", kind: "prop" },
      { field: "strategicImportance", kind: "prop" },
      { field: "plateau", label: "Plateau", kind: "ref", table: "plateaus" },
      { field: "processes", label: "Realised by", kind: "processList" },
    ],
  },
  {
    key: "processes", label: "Process ratings", singular: "process", type: "BusinessProcess", addable: false,
    hint: "Processes on the assessment's As-Is and To-Be views. Ratings matter for the As-Is side.",
    columns: [
      { field: "include", kind: "include" },
      { field: "name", label: "Process", kind: "readonly" },
      { field: "status", kind: "prop" },
      { field: "processPhase", kind: "prop" },
      { field: "maturity", kind: "prop" },
      { field: "automationLevel", kind: "prop" },
      { field: "mediaBreaks", kind: "prop" },
      { field: "capabilities", label: "Realises", kind: "list" },
    ],
  },
  {
    key: "plateaus", label: "Plateaus", singular: "plateau", type: "Plateau", addable: true,
    hint: "Transition and target states of the roadmap.",
    columns: [
      { field: "include", kind: "include" },
      { field: "name", label: "Plateau", kind: "name" },
      { field: "targetDate", kind: "prop" },
    ],
  },
  {
    key: "workPackages", label: "Work packages", singular: "work package", type: "WorkPackage", addable: true,
    hint: "Each work package realises the plateau it delivers into.",
    columns: [
      { field: "include", kind: "include" },
      { field: "name", label: "Work package", kind: "name" },
      { field: "startDate", kind: "prop" },
      { field: "endDate", kind: "prop" },
      { field: "status", kind: "prop" },
      { field: "plateau", label: "Plateau", kind: "ref", table: "plateaus" },
      { field: "owner", kind: "prop" },
    ],
  },
  {
    key: "gaps", label: "Gaps", singular: "gap", type: "Gap", addable: false,
    hint: "Gaps from the Mapping & Gap Analysis: assign each to the plateau that closes it.",
    columns: [
      { field: "include", kind: "include" },
      { field: "name", label: "Gap", kind: "readonly" },
      { field: "processes", label: "Affected processes", kind: "list" },
      { field: "criticality", kind: "prop" },
      { field: "gapCategory", kind: "prop" },
      { field: "plateau", label: "Plateau", kind: "ref", table: "plateaus" },
    ],
  },
  {
    key: "goals", label: "Goals", singular: "goal", type: "Goal", addable: true,
    hint: "What the transformation is for.",
    columns: [
      { field: "include", kind: "include" },
      { field: "name", label: "Goal", kind: "name" },
      { field: "targetDate", kind: "prop" },
    ],
  },
  {
    key: "outcomes", label: "Outcomes & KPIs", singular: "outcome", type: "Outcome", addable: true,
    hint: "Measurable outcomes: baseline, current and target values with their dates.",
    columns: [
      { field: "include", kind: "include" },
      { field: "name", label: "Outcome", kind: "name" },
      { field: "kpi", kind: "prop" },
      { field: "unit", kind: "prop" },
      { field: "baseline", kind: "prop" },
      { field: "current", kind: "prop" },
      { field: "target", kind: "prop" },
      { field: "direction", kind: "prop" },
      { field: "baselineDate", kind: "prop" },
      { field: "targetDate", kind: "prop" },
      { field: "goal", label: "Goal", kind: "ref", table: "goals" },
      { field: "capability", label: "Realised by capability", kind: "ref", table: "capabilities" },
    ],
  },
  {
    key: "applications", label: "Applications", singular: "application", type: "ApplicationComponent", addable: false,
    hint: "Portfolio attributes of the application components in the model (maintained manually; the AI does not guess them).",
    columns: [
      { field: "include", kind: "include" },
      { field: "name", label: "Application", kind: "readonly" },
      { field: "lifecycle", kind: "prop" },
      { field: "endOfLife", kind: "prop" },
      { field: "timeClassification", kind: "prop" },
      { field: "functionalFit", kind: "prop" },
      { field: "technicalFit", kind: "prop" },
      { field: "businessCriticality", kind: "prop" },
      { field: "applicationCategory", kind: "prop" },
      { field: "vendor", kind: "prop" },
    ],
  },
];

function steeringViewNames() {
  const pairs = state.assessment.pairs || [];
  return {
    ist_view_names: pairs.map((p) => p.istViewName).filter(Boolean),
    soll_view_names: pairs.map((p) => p.mappingSollViewName || p.sollViewName).filter(Boolean),
  };
}

function steeringSchemaProp(type, field) {
  const schema = (state.assessment.steering.data && state.assessment.steering.data.schema) || {};
  return (schema[type] || []).find((p) => p.key === field) || null;
}

function steeringAiCounts(data) {
  let rows = 0;
  let fields = 0;
  for (const spec of STEERING_TABLES) {
    for (const row of data[spec.key] || []) {
      if (row.origin === "ai") rows += 1;
      else fields += (row.aiFields || []).length;
    }
  }
  return { rows, fields };
}

const STEERING_AWAITING_APPROVAL =
  'The last apply is waiting for approval in Archi (MCP Server > Pending approvals). Approve or reject it there, then click "Reload from model" before applying again.';

function setSteeringButtons() {
  const st = state.assessment.steering;
  elements.steeringLoadBtn.disabled = st.loading;
  elements.steeringProposeBtn.disabled = st.loading;
  // A second apply before the first one is approved would propose the new rows a second time.
  elements.steeringApplyBtn.disabled = st.loading || !st.data || st.awaitingApproval;
  elements.steeringApplyBtn.title = st.awaitingApproval ? STEERING_AWAITING_APPROVAL : "";
}

async function steeringRequest(path, body, pendingMessage) {
  const st = state.assessment.steering;
  st.loading = true;
  setSteeringButtons();
  setAssessmentStatus(elements.steeringStatus, pendingMessage, "pending");
  try {
    const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
    return data;
  } finally {
    st.loading = false;
    setSteeringButtons();
  }
}

function steeringWarnings(data) {
  const warnings = (data && data.warnings) || [];
  return warnings.length ? ` Notes: ${warnings.slice(0, 3).join(" ")}${warnings.length > 3 ? ` (+${warnings.length - 3} more)` : ""}` : "";
}

async function loadSteering() {
  try {
    const data = await steeringRequest("/api/assessment/steering/load", steeringViewNames(), "Reading the steering data from the active Archi model...");
    state.assessment.steering.data = data;
    state.assessment.steering.awaitingApproval = false;
    renderSteering();
    const summary = STEERING_TABLES.map((spec) => `${(data[spec.key] || []).length} ${spec.label.toLowerCase()}`).join(", ");
    setAssessmentStatus(elements.steeringStatus, `Loaded from the model: ${summary}.${steeringWarnings(data)}`, "ok");
  } catch (err) {
    setAssessmentStatus(elements.steeringStatus, `Loading failed: ${err.message || String(err)}`, "error");
  }
}

async function proposeSteering() {
  const st = state.assessment.steering;
  try {
    const data = await steeringRequest(
      "/api/assessment/steering/propose",
      { ...steeringViewNames(), state: st.data },
      "The AI is preparing proposals from the processes, mapping and gaps... this can take a minute.",
    );
    // Bring suggestions to the top once, right after the proposal (new rows first, then rows with
    // filled fields); later edits never reorder, so nothing jumps while the user reviews.
    const rank = (row) => (row.origin === "ai" ? 0 : (row.aiFields || []).length ? 1 : 2);
    for (const spec of STEERING_TABLES) {
      if (Array.isArray(data[spec.key])) data[spec.key] = [...data[spec.key]].sort((a, b) => rank(a) - rank(b));
    }
    st.data = data;
    renderSteering();
    const counts = steeringAiCounts(data);
    setAssessmentStatus(
      elements.steeringStatus,
      `AI proposal ready: ${counts.rows} new row(s) and ${counts.fields} filled field(s), highlighted in the tables. ` +
        `Review them, untick what you don't want, then apply.${steeringWarnings(data)}`,
      "ok",
    );
  } catch (err) {
    setAssessmentStatus(elements.steeringStatus, `AI proposal failed: ${err.message || String(err)}`, "error");
  }
}

async function applySteering() {
  const st = state.assessment.steering;
  if (!st.data) return;
  try {
    const result = await steeringRequest("/api/assessment/steering/apply", { state: st.data }, "Writing the steering data to Archi...");
    const pendingApproval = (result.proposals || []).length > 0;
    pushActionLogEntry({ action: "assessment-steering", viewName: "Ratings & Roadmap", summary: result.summary, tone: pendingApproval ? "warn" : "ok" });
    markAssessmentStepComplete("steering");
    if (pendingApproval) {
      st.awaitingApproval = true;
      setSteeringButtons();
      setAssessmentStatus(elements.steeringStatus, `${result.summary} Then click "Reload from model".`, "warn");
    } else {
      await loadSteering(); // fresh ids for the rows just created
      setAssessmentStatus(elements.steeringStatus, `${result.summary} The dashboard now shows the updated data.`, "ok");
    }
  } catch (err) {
    setAssessmentStatus(elements.steeringStatus, err.message || String(err), "error");
  }
}

function renderSteering() {
  renderSteeringTabs();
  renderSteeringTable();
  setSteeringButtons();
}

function renderSteeringTabs() {
  const st = state.assessment.steering;
  const container = elements.steeringTabs;
  container.replaceChildren();
  if (!st.data) return;
  for (const spec of STEERING_TABLES) {
    const rows = st.data[spec.key] || [];
    const suggestions = rows.filter((r) => r.origin === "ai" || (r.aiFields || []).length).length;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `steering-tab${st.tab === spec.key ? " active" : ""}`;
    btn.setAttribute("role", "tab");
    btn.setAttribute("aria-selected", String(st.tab === spec.key));
    btn.textContent = `${spec.label} (${rows.length})`;
    if (suggestions) {
      const badge = document.createElement("span");
      badge.className = "steering-ai-badge";
      badge.textContent = `${suggestions} AI`;
      btn.appendChild(badge);
    }
    btn.addEventListener("click", () => {
      st.tab = spec.key;
      renderSteering();
    });
    container.appendChild(btn);
  }
}

function renderSteeringTable() {
  const st = state.assessment.steering;
  const container = elements.steeringTable;
  container.replaceChildren();
  if (!st.data) return;
  const spec = STEERING_TABLES.find((t) => t.key === st.tab) || STEERING_TABLES[0];
  const rows = st.data[spec.key] || [];

  const hint = document.createElement("p");
  hint.className = "steering-hint";
  const baseline = st.data.baseline;
  hint.textContent = `${spec.hint} Highlighted cells and rows are AI suggestions; editing a cell confirms it.`
    + (baseline ? ` Re-assessment: under each field is its value in the baseline "${baseline.name}" of ${formatDay(baseline.date)}, marked where this cycle changes it.` : "");
  container.appendChild(hint);

  if (!rows.length) {
    const emptyState = document.createElement("p");
    emptyState.className = "steering-empty";
    emptyState.textContent = `No ${spec.label.toLowerCase()} in the model yet.${spec.addable ? " Add one below or let the AI propose." : ""}`;
    container.appendChild(emptyState);
  } else {
    const scroll = document.createElement("div");
    scroll.className = "table-scroll steering-scroll";
    const table = document.createElement("table");
    table.className = "preview-table steering-grid";
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    for (const col of spec.columns) {
      const th = document.createElement("th");
      th.textContent = col.kind === "include" ? "" : col.label || (steeringSchemaProp(spec.type, col.field) || {}).label || col.field;
      if (col.kind === "include") th.className = "col-check";
      headRow.appendChild(th);
    }
    thead.appendChild(headRow);
    const tbody = document.createElement("tbody");
    rows.forEach((row) => tbody.appendChild(renderSteeringRow(spec, row)));
    table.append(thead, tbody);
    scroll.appendChild(table);
    container.appendChild(scroll);
  }

  if (spec.addable) {
    const add = document.createElement("button");
    add.type = "button";
    add.className = "btn ghost small steering-add";
    add.textContent = `+ Add ${spec.singular}`;
    add.addEventListener("click", () => addSteeringRow(spec));
    container.appendChild(add);
  }
}

function renderSteeringRow(spec, row) {
  const tr = document.createElement("tr");
  if (row.origin === "ai") tr.classList.add("steering-row-ai");
  if (row.include === false) tr.classList.add("row-excluded");
  if (row.rationale) tr.title = `AI rationale: ${row.rationale}`;
  for (const col of spec.columns) tr.appendChild(renderSteeringCell(spec, row, col, tr));
  return tr;
}

function confirmSteeringField(row, field) {
  if (row.aiFields) row.aiFields = row.aiFields.filter((f) => f !== field);
}

function renderSteeringCell(spec, row, col, tr) {
  const td = document.createElement("td");
  if ((row.aiFields || []).includes(col.field)) {
    td.classList.add("steering-cell-ai");
    td.title = "AI suggestion";
  }
  const st = state.assessment.steering;
  switch (col.kind) {
    case "include": {
      td.className = "col-check";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = row.include !== false;
      box.title = row.id ? "Untick to leave this element unchanged" : "Untick to not create this element";
      box.addEventListener("change", () => {
        row.include = box.checked;
        tr.classList.toggle("row-excluded", !box.checked);
      });
      td.appendChild(box);
      break;
    }
    case "name": {
      const wrap = document.createElement("div");
      wrap.className = "steering-name";
      if (!row.id) {
        const badge = document.createElement("span");
        badge.className = `steering-origin steering-origin-${row.origin === "ai" ? "ai" : "new"}`;
        badge.textContent = row.origin === "ai" ? "AI" : "New";
        badge.title = row.origin === "ai" ? "Proposed by the AI -- created when you apply" : "Added by you -- created when you apply";
        wrap.appendChild(badge);
      }
      const input = document.createElement("input");
      input.className = "cell-name-input";
      input.value = row.name || "";
      input.addEventListener("change", () => {
        row.name = input.value;
      });
      wrap.appendChild(input);
      td.appendChild(wrap);
      break;
    }
    case "readonly":
      td.textContent = row[col.field] || "—";
      break;
    case "list":
      td.textContent = (row[col.field] || []).join(", ") || "—";
      td.className = `${td.className} steering-list`.trim();
      break;
    case "processList": {
      const names = new Map(Object.entries(st.data.processNames || {}));
      (st.data.processes || []).forEach((p) => names.set(p.key, p.name));
      td.textContent = (row[col.field] || []).map((k) => names.get(k) || "(unknown process)").join(", ") || "—";
      td.className = `${td.className} steering-list`.trim();
      break;
    }
    case "ref": {
      const select = document.createElement("select");
      select.className = "steering-input";
      select.appendChild(new Option("—", ""));
      for (const target of st.data[col.table] || []) {
        if (target.include === false) continue;
        select.appendChild(new Option(target.name || "(unnamed)", target.key));
      }
      select.value = row[col.field] || "";
      select.addEventListener("change", () => {
        row[col.field] = select.value || null;
        confirmSteeringField(row, col.field);
        td.classList.remove("steering-cell-ai");
      });
      td.appendChild(select);
      break;
    }
    default: {
      const input = steeringPropInput(spec.type, col.field, row[col.field]);
      const previous = steeringBaselineValue(row, col.field);
      const note = previous === null ? null : document.createElement("span");
      const markChange = () => note && note.classList.toggle("changed", String(row[col.field] ?? "") !== previous);
      input.addEventListener("change", () => {
        row[col.field] = input.value === "" ? null : input.value;
        confirmSteeringField(row, col.field);
        td.classList.remove("steering-cell-ai");
        markChange();
      });
      td.appendChild(input);
      if (note) {
        const baseline = st.data.baseline;
        note.className = "steering-baseline";
        note.textContent = `Baseline: ${steeringValueLabel(spec.type, col.field, previous)}`;
        note.title = `Value in the baseline "${baseline.name}" of ${formatDay(baseline.date)}; highlighted when this cycle changes it.`;
        markChange();
        td.appendChild(note);
      }
    }
  }
  return td;
}

// The value the latest baseline (previous assessment cycle) recorded for this element, if any.
function steeringBaselineValue(row, field) {
  const baseline = state.assessment.steering.data && state.assessment.steering.data.baseline;
  if (!baseline || !row.id) return null;
  const value = (baseline.values[row.id] || {})[field];
  return value === undefined || value === null || value === "" ? null : String(value);
}

function steeringValueLabel(type, field, value) {
  const def = steeringSchemaProp(type, field);
  if (def && def.type === "enum") return (def.values.find((v) => v.value === value) || {}).label || value;
  if (def && def.levels && def.levels[value]) return `${value} · ${def.levels[value]}`;
  return value;
}

function steeringPropInput(type, field, value) {
  const def = steeringSchemaProp(type, field);
  const current = value === null || value === undefined ? "" : String(value);
  if (def && (def.type === "enum" || (def.type === "integer" && def.levels))) {
    const select = document.createElement("select");
    select.className = "steering-input";
    select.appendChild(new Option("—", ""));
    const options = def.type === "enum"
      ? def.values.map((v) => [v.value, v.label])
      : Object.entries(def.levels).map(([level, label]) => [level, `${level} · ${label}`]);
    for (const [optionValue, label] of options) select.appendChild(new Option(label, optionValue));
    if (current && !options.some(([optionValue]) => optionValue === current)) {
      select.appendChild(new Option(`${current} (not in the schema)`, current)); // keep legacy values visible
    }
    select.value = current;
    select.title = def.description || "";
    return select;
  }
  const input = document.createElement("input");
  input.className = "steering-input";
  input.value = current;
  if (def && def.type === "date") input.type = "date";
  if (def && (def.type === "integer" || def.type === "number")) {
    input.type = "number";
    input.step = def.type === "integer" ? "1" : "any";
    if (def.min !== undefined) input.min = String(def.min);
    if (def.max !== undefined) input.max = String(def.max);
  }
  if (def) input.title = def.description || "";
  return input;
}

function addSteeringRow(spec) {
  const st = state.assessment.steering;
  const row = { key: `new:${spec.key}:${createId()}`, id: null, name: `New ${spec.singular}`, origin: "manual", include: true, aiFields: [], rationale: "" };
  for (const p of (st.data.schema || {})[spec.type] || []) row[p.key] = null;
  if (spec.key === "workPackages") row.status = "planned";
  if (spec.key === "capabilities") row.processes = [];
  st.data[spec.key].push(row);
  renderSteering();
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
    steering: { data: null, tab: "capabilities", loading: false, awaitingApproval: false },
  };
  renderSteering();
  elements.steeringStatus.classList.add("hidden");

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
  elements.assessmentMappingContinueBtn.addEventListener("click", () => setAssessmentStep("steering"));

  elements.steeringDashboardBtn.addEventListener("click", openDashboardTab);
  elements.steeringLoadBtn.addEventListener("click", loadSteering);
  elements.steeringProposeBtn.addEventListener("click", proposeSteering);
  elements.steeringApplyBtn.addEventListener("click", applySteering);
  elements.assessmentSteeringBackBtn.addEventListener("click", () => setAssessmentStep("mapping"));
  elements.assessmentSteeringContinueBtn.addEventListener("click", () => {
    // The step is optional: continuing without applying still unlocks the summary.
    markAssessmentStepComplete("steering");
    setAssessmentStep("summary");
  });

  elements.assessmentSummaryRunBtn.addEventListener("click", runAssessmentSummary);
  elements.assessmentSummaryBackBtn.addEventListener("click", () => setAssessmentStep("steering"));
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

  elements.dashboardBtn.addEventListener("click", openDashboardTab);

  // The wizard survives reloads, so starting over needs its own button in every step, not only on the summary.
  const confirmStartNewAssessment = () => {
    if (state.assessment.pending) return;
    if (!confirm("Start a new assessment? This clears the current wizard progress (nothing already created in Archi is deleted).")) {
      return;
    }
    startNewAssessment();
  };
  elements.assessmentStartNewBtn.addEventListener("click", confirmStartNewAssessment);
  elements.cycleSaveBtn.addEventListener("click", saveCycleBaseline);
  elements.assessmentResetBtn.addEventListener("click", confirmStartNewAssessment);
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

  // Steering properties: the information step 5 maintains and the dashboard reads.
  const properties = metaModel.properties || {};
  if (!Object.keys(properties).length) return;
  const propHead = document.createElement("h3");
  propHead.className = "meta-model-relationships-head";
  propHead.textContent = "Steering properties (maintained in step 5, read by the dashboard)";
  container.appendChild(propHead);
  const propScroll = document.createElement("div");
  propScroll.className = "table-scroll meta-model-table-scroll";
  const propTable = document.createElement("table");
  propTable.className = "preview-table";
  const propHeadRow = document.createElement("tr");
  for (const label of ["Element type", "Property", "Values", "Meaning"]) {
    const th = document.createElement("th");
    th.textContent = label;
    propHeadRow.appendChild(th);
  }
  const propThead = document.createElement("thead");
  propThead.appendChild(propHeadRow);
  propTable.appendChild(propThead);
  const propBody = document.createElement("tbody");
  for (const [elementType, props] of Object.entries(properties)) {
    for (const prop of props) {
      const tr = document.createElement("tr");
      let values = prop.type;
      if (prop.type === "enum") values = prop.values.map((v) => v.label).join(" · ");
      else if (prop.levels) values = Object.entries(prop.levels).map(([level, label]) => `${level} ${label}`).join(" · ");
      else if (prop.type === "date") values = "date (YYYY-MM-DD)";
      const cells = [elementType, `${prop.label} (${prop.key})${prop.core ? " *" : ""}`, values, prop.description];
      cells.forEach((text, index) => {
        const td = document.createElement("td");
        td.textContent = text;
        if (index === 3) td.className = "meta-model-note";
        tr.appendChild(td);
      });
      propBody.appendChild(tr);
    }
  }
  propTable.appendChild(propBody);
  propScroll.appendChild(propTable);
  container.appendChild(propScroll);
  const footnote = document.createElement("p");
  footnote.className = "meta-model-note";
  footnote.textContent = "* needed by a dashboard metric (counts towards data completeness).";
  container.appendChild(footnote);
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
  renderChat();
  renderHealth();
  renderToolList();
  renderActionLog();
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

/* ---------------- Tabs + chat drawer ---------------- */

function setActiveTab(tabId) {
  state.activeTab = tabId;
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tabId);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("hidden", panel.id !== tabId);
  });
  // The dashboard lives in this page: opening its tab re-reads the model; the assessment keeps its state.
  if (tabId === "dashboardTab" && window.TransformationDashboard) window.TransformationDashboard.show();
  persistAssessmentSession();
}

function showSettingsSection(sectionId) {
  document.querySelectorAll(".settings-nav-btn").forEach((btn) => {
    const active = btn.dataset.section === sectionId;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".settings-section").forEach((section) => {
    section.classList.toggle("hidden", section.id !== sectionId);
  });
}

function openConnectionSettings() {
  setActiveTab("settingsTab");
  showSettingsSection("settingsConnection");
  loadHealth();
}

function openDashboardTab() {
  setActiveTab("dashboardTab");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function attachEventHandlers() {
  elements.connectionBadge.addEventListener("click", openConnectionSettings);
  elements.refreshHealthInlineBtn.addEventListener("click", loadHealth);
  elements.refreshToolsBtn.addEventListener("click", loadTools);
  elements.toolSearchInput.addEventListener("input", () => {
    renderToolList();
  });

  document.querySelectorAll(".tab-btn").forEach((button) => {
    button.addEventListener("click", () => {
      setActiveTab(button.dataset.tab);
    });
  });
  document.querySelectorAll(".settings-nav-btn").forEach((button) => {
    button.addEventListener("click", () => showSettingsSection(button.dataset.section));
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
}

function init() {
  validateRequiredElements();
  attachEventHandlers();
  attachAssessmentEventHandlers();
  initChat();
  const restoredAssessment = restoreAssessmentSession();
  if (!state.assessment.pairs.length) {
    state.assessment.pairs = [createAssessmentPair(0)];
  }
  renderAssessmentPairRows();
  renderAssessmentCaptureList("ist");
  renderAssessmentCaptureList("soll");
  renderAssessmentPairingReview();
  renderAssessmentMappingPairList();
  // ?step=steering opens Ratings & Roadmap directly -- for re-assessments, where maturity and the
  // roadmap are maintained again without repeating steps 1-4.
  if (restoredAssessment) applyRestoredAssessmentUi();
  const directStep = new URLSearchParams(window.location.search).get("step");
  if (directStep === "steering") {
    const btn = document.querySelector('.assessment-step-btn[data-step="steering"]');
    if (btn) btn.disabled = false;
    setAssessmentStep("steering");
    // The link asks for the step, so it wins over the tab restored from the session. Drop the
    // parameter afterwards: later reloads restore wherever the user went next.
    state.activeTab = "assessmentTab";
    history.replaceState(null, "", window.location.pathname);
  } else {
    setAssessmentStep(restoredAssessment ? state.assessment.currentStep : "setup");
  }
  // Bookmarks of the former stand-alone dashboard page open the dashboard tab.
  if (window.location.pathname === "/dashboard.html") {
    state.activeTab = "dashboardTab";
    history.replaceState(null, "", "/");
  }
  setActiveTab(state.activeTab);
  // Autosave: cheap (skipped when nothing changed) and robust against any code path that edits the
  // wizard state; pagehide covers reloads and closing the tab.
  setInterval(persistAssessmentSession, 2000);
  window.addEventListener("pagehide", persistAssessmentSession);
  renderAll();
  loadHealth();
  loadTools();
  startHealthPolling();
}

// chat.js is loaded after this file; DOMContentLoaded fires once every script has run.
document.addEventListener("DOMContentLoaded", init);
