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
  currentPlan: null,
  previewFileLabel: "",
  actionLog: [],
  activeTab: "workspaceTab",
  chatCollapsed: false,
};

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
  businessProcessForm: document.getElementById("businessProcessForm"),
  businessProcessFile: document.getElementById("businessProcessFile"),
  businessProcessViewName: document.getElementById("businessProcessViewName"),
  businessProcessBtn: document.getElementById("businessProcessBtn"),
  requirementsForm: document.getElementById("requirementsForm"),
  requirementsFile: document.getElementById("requirementsFile"),
  requirementsViewName: document.getElementById("requirementsViewName"),
  requirementsBtn: document.getElementById("requirementsBtn"),
  actionStatus: document.getElementById("actionStatus"),
  previewPanel: document.getElementById("previewPanel"),
  previewEmptyState: document.getElementById("previewEmptyState"),
  previewContent: document.getElementById("previewContent"),
  previewViewName: document.getElementById("previewViewName"),
  previewSourceBadge: document.getElementById("previewSourceBadge"),
  previewCounts: document.getElementById("previewCounts"),
  previewWarnings: document.getElementById("previewWarnings"),
  previewDiagram: document.getElementById("previewDiagram"),
  elementsTable: document.getElementById("elementsTable"),
  relationshipsTable: document.getElementById("relationshipsTable"),
  discardPreviewBtn: document.getElementById("discardPreviewBtn"),
  applyPreviewBtn: document.getElementById("applyPreviewBtn"),
  actionLog: document.getElementById("actionLog"),
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

/* ---------------- Upload preview / apply workflow ---------------- */

const PREVIEW_CONFIG = {
  "business-process-upload": {
    endpoint: "/api/actions/business-process-upload/preview",
    fileInput: () => elements.businessProcessFile,
    viewInput: () => elements.businessProcessViewName,
    label: "Business process",
  },
  "requirements-upload": {
    endpoint: "/api/actions/requirements-upload/preview",
    fileInput: () => elements.requirementsFile,
    viewInput: () => elements.requirementsViewName,
    label: "Requirements",
  },
};

function buildKeyIndex(plan) {
  return new Map((plan.elements || []).map((el) => [el.key, el]));
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

function rectBoundaryPoint(cx, cy, halfW, halfH, dx, dy) {
  if (!dx && !dy) return { x: cx, y: cy };
  const candidates = [];
  if (dx) candidates.push(halfW / Math.abs(dx));
  if (dy) candidates.push(halfH / Math.abs(dy));
  const t = Math.min(...candidates);
  return { x: cx + dx * t, y: cy + dy * t };
}

function buildRelationshipLine(svgNS, sourcePos, targetPos, offsetX, offsetY) {
  const cx1 = sourcePos.x + sourcePos.width / 2 + offsetX;
  const cy1 = sourcePos.y + sourcePos.height / 2 + offsetY;
  const cx2 = targetPos.x + targetPos.width / 2 + offsetX;
  const cy2 = targetPos.y + targetPos.height / 2 + offsetY;
  const dx = cx2 - cx1;
  const dy = cy2 - cy1;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len;
  const uy = dy / len;
  const p1 = rectBoundaryPoint(cx1, cy1, sourcePos.width / 2, sourcePos.height / 2, ux, uy);
  const p2 = rectBoundaryPoint(cx2, cy2, targetPos.width / 2, targetPos.height / 2, -ux, -uy);
  const line = document.createElementNS(svgNS, "line");
  line.setAttribute("x1", String(p1.x));
  line.setAttribute("y1", String(p1.y));
  line.setAttribute("x2", String(p2.x));
  line.setAttribute("y2", String(p2.y));
  line.setAttribute("class", "diagram-edge");
  line.setAttribute("marker-end", "url(#archiArrowHead)");
  return line;
}

function buildElementNode(svgNS, el, pos, offsetX, offsetY) {
  const palette = typeColor(el.type);
  const group = document.createElementNS(svgNS, "g");

  const rect = document.createElementNS(svgNS, "rect");
  rect.setAttribute("x", String(pos.x + offsetX));
  rect.setAttribute("y", String(pos.y + offsetY));
  rect.setAttribute("width", String(pos.width));
  rect.setAttribute("height", String(pos.height));
  rect.setAttribute("rx", "8");
  rect.setAttribute("fill", palette.fill);
  rect.setAttribute("stroke", palette.stroke);
  rect.setAttribute("stroke-width", "1.3");
  group.appendChild(rect);

  const typeLabel = document.createElementNS(svgNS, "text");
  typeLabel.setAttribute("x", String(pos.x + offsetX + 8));
  typeLabel.setAttribute("y", String(pos.y + offsetY + 17));
  typeLabel.setAttribute("class", "diagram-type-label");
  typeLabel.setAttribute("fill", palette.stroke);
  typeLabel.textContent = truncateLabel(el.type, 26);
  group.appendChild(typeLabel);

  const nameLabel = document.createElementNS(svgNS, "text");
  nameLabel.setAttribute("x", String(pos.x + offsetX + 8));
  nameLabel.setAttribute("y", String(pos.y + offsetY + 40));
  nameLabel.setAttribute("class", "diagram-name-label");
  nameLabel.setAttribute("fill", palette.text);
  nameLabel.textContent = truncateLabel(el.name, 22);
  group.appendChild(nameLabel);

  const titleEl = document.createElementNS(svgNS, "title");
  titleEl.textContent = `${el.type}: ${el.name}`;
  group.appendChild(titleEl);

  return group;
}

function renderPreviewDiagram() {
  const container = elements.previewDiagram;
  container.innerHTML = "";
  const plan = state.currentPlan;
  if (!plan) return;

  const includedElements = plan.elements.filter((el) => el.include);
  const includedKeys = new Set(includedElements.map((el) => el.key));
  const includedRelationships = plan.relationships.filter(
    (rel) => rel.include && includedKeys.has(rel.source_key) && includedKeys.has(rel.target_key)
  );

  if (!includedElements.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No elements selected.";
    container.appendChild(empty);
    return;
  }

  const positions = {};
  includedElements.forEach((el, index) => {
    const pos = plan.layout_positions ? plan.layout_positions[el.key] : null;
    positions[el.key] = pos || {
      x: 100 + (index % 4) * 260,
      y: 100 + Math.floor(index / 4) * 140,
      width: 200,
      height: 80,
    };
  });

  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const pos of Object.values(positions)) {
    minX = Math.min(minX, pos.x);
    minY = Math.min(minY, pos.y);
    maxX = Math.max(maxX, pos.x + pos.width);
    maxY = Math.max(maxY, pos.y + pos.height);
  }
  const pad = 40;
  const viewW = maxX - minX + pad * 2;
  const viewH = maxY - minY + pad * 2;
  const offsetX = -minX + pad;
  const offsetY = -minY + pad;

  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", `0 0 ${viewW} ${viewH}`);
  svg.setAttribute("preserveAspectRatio", "xMinYMin meet");
  svg.classList.add("diagram-svg");

  const defs = document.createElementNS(svgNS, "defs");
  const marker = document.createElementNS(svgNS, "marker");
  marker.setAttribute("id", "archiArrowHead");
  marker.setAttribute("markerWidth", "8");
  marker.setAttribute("markerHeight", "8");
  marker.setAttribute("refX", "7");
  marker.setAttribute("refY", "4");
  marker.setAttribute("orient", "auto");
  const arrowPath = document.createElementNS(svgNS, "path");
  arrowPath.setAttribute("d", "M0,0 L8,4 L0,8 Z");
  arrowPath.setAttribute("fill", "#8497b5");
  marker.appendChild(arrowPath);
  defs.appendChild(marker);
  svg.appendChild(defs);

  for (const rel of includedRelationships) {
    const sourcePos = positions[rel.source_key];
    const targetPos = positions[rel.target_key];
    if (!sourcePos || !targetPos) continue;
    svg.appendChild(buildRelationshipLine(svgNS, sourcePos, targetPos, offsetX, offsetY));
  }

  for (const el of includedElements) {
    const pos = positions[el.key];
    if (!pos) continue;
    svg.appendChild(buildElementNode(svgNS, el, pos, offsetX, offsetY));
  }

  container.appendChild(svg);
}

function renderElementsTable() {
  const tbody = elements.elementsTable.querySelector("tbody");
  tbody.innerHTML = "";
  const plan = state.currentPlan;
  if (!plan) return;

  if (!plan.elements.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 3;
    td.className = "empty-state";
    td.textContent = "No elements extracted.";
    tr.appendChild(td);
    tbody.appendChild(tr);
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
      renderRelationshipsTable();
      renderPreviewDiagram();
      renderPreviewCounts();
      updateActionButtonsState();
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
      renderRelationshipsTable();
      renderPreviewDiagram();
    });
    nameTd.appendChild(nameInput);
    tr.appendChild(nameTd);

    tbody.appendChild(tr);
  });
}

function renderRelationshipsTable() {
  const tbody = elements.relationshipsTable.querySelector("tbody");
  tbody.innerHTML = "";
  const plan = state.currentPlan;
  if (!plan) return;

  if (!plan.relationships.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 3;
    td.className = "empty-state";
    td.textContent = "No relationships extracted.";
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  const keyIndex = buildKeyIndex(plan);

  plan.relationships.forEach((rel) => {
    const sourceEl = keyIndex.get(rel.source_key);
    const targetEl = keyIndex.get(rel.target_key);
    const endpointsIncluded = Boolean(sourceEl && sourceEl.include && targetEl && targetEl.include);

    const tr = document.createElement("tr");
    if (!rel.include || !endpointsIncluded) tr.classList.add("row-excluded");

    const checkTd = document.createElement("td");
    checkTd.className = "col-check";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = rel.include;
    checkbox.disabled = !endpointsIncluded;
    checkbox.title = endpointsIncluded ? "" : "Both endpoints must be included first";
    checkbox.addEventListener("change", () => {
      rel.include = checkbox.checked;
      tr.classList.toggle("row-excluded", !rel.include || !endpointsIncluded);
      renderPreviewDiagram();
      renderPreviewCounts();
      updateActionButtonsState();
    });
    checkTd.appendChild(checkbox);
    tr.appendChild(checkTd);

    const typeTd = document.createElement("td");
    typeTd.className = "cell-type";
    typeTd.textContent = rel.type;
    tr.appendChild(typeTd);

    const endpointsTd = document.createElement("td");
    const sourceName = sourceEl ? sourceEl.name : rel.source_key;
    const targetName = targetEl ? targetEl.name : rel.target_key;
    endpointsTd.textContent = `${sourceName} → ${targetName}`;
    tr.appendChild(endpointsTd);

    tbody.appendChild(tr);
  });
}

function renderPreviewCounts() {
  const plan = state.currentPlan;
  if (!plan) return;
  const keyIndex = buildKeyIndex(plan);
  const includedElementsCount = plan.elements.filter((el) => el.include).length;
  const includedRelationshipsCount = plan.relationships.filter((rel) => {
    const sourceEl = keyIndex.get(rel.source_key);
    const targetEl = keyIndex.get(rel.target_key);
    return rel.include && sourceEl && sourceEl.include && targetEl && targetEl.include;
  }).length;
  elements.previewCounts.textContent =
    `${includedElementsCount}/${plan.elements.length} elements selected · ` +
    `${includedRelationshipsCount}/${plan.relationships.length} relationships selected · ` +
    `${plan.steps_processed} steps processed`;
}

function renderPreviewWarnings() {
  const plan = state.currentPlan;
  const box = elements.previewWarnings;
  box.innerHTML = "";
  const messages = [];
  if (plan) {
    if (Array.isArray(plan.warnings)) messages.push(...plan.warnings);
    if (plan.steps_truncated) messages.push("Input was truncated to fit configured automation limits.");
  }
  if (!messages.length) {
    box.classList.add("hidden");
    return;
  }
  box.classList.remove("hidden");
  for (const message of messages) {
    const p = document.createElement("p");
    p.textContent = message;
    box.appendChild(p);
  }
}

function renderPreview() {
  const plan = state.currentPlan;
  if (!plan) {
    elements.previewEmptyState.classList.remove("hidden");
    elements.previewContent.classList.add("hidden");
    updateActionButtonsState();
    return;
  }
  elements.previewEmptyState.classList.add("hidden");
  elements.previewContent.classList.remove("hidden");
  elements.previewViewName.value = plan.view_name;
  elements.previewSourceBadge.textContent = plan.source_name || state.previewFileLabel || "upload";
  renderElementsTable();
  renderRelationshipsTable();
  renderPreviewDiagram();
  renderPreviewCounts();
  renderPreviewWarnings();
  updateActionButtonsState();
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

async function loadPreview(actionKey) {
  if (state.pendingController || state.pendingAction) return;
  const config = PREVIEW_CONFIG[actionKey];
  if (!config) return;

  const fileInput = config.fileInput();
  const viewInput = config.viewInput();
  const file = fileInput.files && fileInput.files[0];
  if (!file) {
    setActionStatus("Select a file first.", "error");
    return;
  }
  const viewName = String(viewInput.value || "").trim().slice(0, 120);

  state.pendingAction = config.endpoint;
  updateActionButtonsState();
  setActionStatus(`Extracting preview from '${file.name}'...`, "pending");

  try {
    const formData = new FormData();
    formData.append("file", file);
    if (viewName) formData.append("view_name", viewName);

    const response = await fetch(config.endpoint, { method: "POST", body: formData });
    let data = {};
    try {
      data = await response.json();
    } catch (err) {
      data = {};
    }
    if (!response.ok) {
      throw new Error(data.detail || `Preview failed with status ${response.status}`);
    }

    state.currentPlan = data;
    state.previewFileLabel = file.name;
    renderPreview();
    setActionStatus(
      `Preview ready from '${file.name}': ${data.elements.length} elements, ${data.relationships.length} relationships. ` +
        "Review below, then click \"Create in Archi\" to apply.",
      "ok"
    );
    fileInput.value = "";
  } catch (err) {
    setActionStatus(`Error: ${err.message || String(err)}`, "error");
  } finally {
    state.pendingAction = null;
    updateActionButtonsState();
  }
}

function discardPreview() {
  if (!state.currentPlan) return;
  state.currentPlan = null;
  state.previewFileLabel = "";
  renderPreview();
  setActionStatus("Preview discarded. Nothing was written to Archi.", "neutral");
}

async function applyPreview() {
  const plan = state.currentPlan;
  if (!plan || state.pendingController || state.pendingAction) return;
  if (!plan.elements.some((el) => el.include)) {
    setActionStatus("Select at least one element before applying.", "error");
    return;
  }

  state.pendingAction = "/api/actions/apply";
  updateActionButtonsState();
  setActionStatus(`Creating '${plan.view_name}' in Archi...`, "pending");

  try {
    const response = await fetch("/api/actions/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan }),
    });
    let data = {};
    try {
      data = await response.json();
    } catch (err) {
      data = {};
    }
    if (!response.ok) {
      throw new Error(data.detail || `Apply failed with status ${response.status}`);
    }

    const summary = data.summary || "Applied to Archi.";
    setActionStatus(summary, "ok");
    pushActionLogEntry({
      action: plan.action,
      viewName: data.view_name || plan.view_name,
      summary,
      tone: "ok",
    });

    const active = getActiveConversation();
    appendTurn(active, "assistant", summary, data.used_tools || []);
    persistState();
    renderConversationList();
    renderConversationHeader();
    renderChatWindow();
    updateLastUsedTools();

    state.currentPlan = null;
    state.previewFileLabel = "";
    renderPreview();
    await loadHealth();
    await loadTools();
  } catch (err) {
    const message = `Error: ${err.message || String(err)}`;
    setActionStatus(message, "error");
    pushActionLogEntry({ action: plan.action, viewName: plan.view_name, summary: message, tone: "error" });
  } finally {
    state.pendingAction = null;
    updateActionButtonsState();
  }
}

/* ---------------- Chat / general UI ---------------- */

function renderAll() {
  updateLastUsedTools();
  renderConversationList();
  renderConversationHeader();
  renderChatWindow();
  renderHealth();
  renderToolList();
  renderPreview();
  renderActionLog();
  updateMessageCounter();
  updateSendButtonState();
  updateRetryButtonState();
  updateActionButtonsState();
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

function updateActionButtonsState() {
  const isBusy = Boolean(state.pendingController || state.pendingAction);
  elements.businessProcessBtn.disabled = isBusy;
  elements.requirementsBtn.disabled = isBusy;
  const hasIncludedElement = Boolean(state.currentPlan && state.currentPlan.elements.some((el) => el.include));
  elements.applyPreviewBtn.disabled = isBusy || !hasIncludedElement;
  elements.discardPreviewBtn.disabled = isBusy || !state.currentPlan;
}

function setSending(isSending) {
  elements.sendBtn.textContent = isSending ? "Sending..." : "Send";
  updateSendButtonState();
  updateActionButtonsState();
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

function setActionStatus(message, tone = "neutral") {
  const box = elements.actionStatus;
  box.textContent = String(message || "");
  box.className = "action-status";
  if (tone) {
    box.classList.add(`action-${tone}`);
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
    updateActionButtonsState();
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

  elements.businessProcessForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await loadPreview("business-process-upload");
  });

  elements.requirementsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await loadPreview("requirements-upload");
  });

  elements.previewViewName.addEventListener("input", () => {
    if (state.currentPlan) {
      state.currentPlan.view_name = elements.previewViewName.value;
    }
  });

  elements.discardPreviewBtn.addEventListener("click", discardPreview);
  elements.applyPreviewBtn.addEventListener("click", applyPreview);

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
