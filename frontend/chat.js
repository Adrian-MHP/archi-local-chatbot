/* ---------------- Assistant chat ----------------
   Conversations live in this browser (localStorage). Answers stream from /api/chat/stream: every MCP
   tool call shows up as a live step, the answer text appears as the model writes it. Markdown is
   rendered to DOM nodes (never innerHTML), so model output cannot inject markup. Loaded after
   app.js, which owns `state`, `elements` and calls initChat(). */

const CHAT_HISTORY_TURNS = 30; // earlier turns are not sent to the model
const CHAT_COUNTER_FROM = 0.8; // show the character counter from 80% of the limit

const CHAT_SUGGESTIONS = [
  "Summarise the active model: layers, element counts and the main views.",
  "Which capabilities have the largest gap between current and target maturity?",
  "Which applications reach end of life within 24 months, and which processes do they support?",
  "Check the model against the governance meta-model and list any violations.",
];

// MCP tools that only read the model; every other tool can change it.
const READ_ONLY_TOOL = /^(get-|list-|find-|detect-|assess-|search-elements$|search-relationships$|export-view$)/;
const TOOL_LABELS = {
  "get-model-info": "Read the model overview",
  "search-elements": "Searched elements",
  "search-relationships": "Searched relationships",
  "get-element": "Read an element",
  "get-relationships": "Followed relationships",
  "get-views": "Listed the views",
  "get-view-contents": "Read a view",
  "get-folder-tree": "Read the folder tree",
  "find-concept-usage": "Looked up where an element is used",
  "list-pending-approvals": "Checked pending approvals",
  "bulk-mutate": "Changed the model",
  "create-element": "Created an element",
  "update-element": "Updated an element",
  "delete-element": "Deleted an element",
  "create-relationship": "Created a relationship",
  "update-relationship": "Updated a relationship",
  "delete-relationship": "Deleted a relationship",
  "create-view": "Created a view",
  "add-to-view": "Added elements to a view",
};

function toolLabel(name) {
  if (TOOL_LABELS[name]) return TOOL_LABELS[name];
  const words = String(name || "tool").replace(/-/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function isWriteTool(name) {
  return !READ_ONLY_TOOL.test(String(name || ""));
}

/* ---------------- Icons (inline SVG, 24px grid, stroked) ---------------- */

const SVG_NS = "http://www.w3.org/2000/svg";
const ICON_PATHS = {
  plus: ["M12 5v14", "M5 12h14"],
  send: ["M12 19V5", "m5 12 7-7 7 7"],
  copy: ["M9 9h10a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V10a1 1 0 0 1 1-1Z", "M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"],
  check: ["M20 6 9 17l-5-5"],
  retry: ["M3 12a9 9 0 0 1 15.4-6.4L21 8", "M21 3v5h-5", "M21 12a9 9 0 0 1-15.4 6.4L3 16", "M3 21v-5h5"],
  edit: ["M12 20h9", "M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"],
  history: ["M3 12a9 9 0 1 0 2.6-6.4L3 8", "M3 3v5h5", "M12 7v5l3 2"],
  chevronRight: ["m9 18 6-6-6-6"],
  chevronLeft: ["m15 18-6-6 6-6"],
  arrowDown: ["M12 5v14", "m19 12-7 7-7-7"],
  trash: ["M3 6h18", "M8 6V4h8v2", "m6 6 1 14h10l1-14"],
  sparkle: ["M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8Z", "M19 15l.7 1.8 1.8.7-1.8.7L19 20l-.7-1.8-1.8-.7 1.8-.7Z"],
  eye: ["M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z", "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z"],
  alert: ["M12 9v4", "M12 17h.01", "M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"],
  download: ["M12 3v12", "m7 10 5 5 5-5", "M5 21h14"],
  search: ["M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14Z", "m21 21-4.3-4.3"],
  x: ["M18 6 6 18", "m6 6 12 12"],
  clock: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z", "M12 7v5l3 2"],
};

function icon(name, size = 16) {
  const svg = document.createElementNS(SVG_NS, "svg");
  for (const [key, value] of Object.entries({
    viewBox: "0 0 24 24", width: size, height: size, fill: "none", stroke: "currentColor",
    "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", class: "icon",
  })) svg.setAttribute(key, String(value));
  const add = (tag, attrs) => {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    svg.appendChild(node);
  };
  if (name === "stop") add("rect", { x: 6, y: 6, width: 12, height: 12, rx: 2, fill: "currentColor", stroke: "none" });
  else if (name === "more") [5, 12, 19].forEach((cx) => add("circle", { cx, cy: 12, r: 1.6, fill: "currentColor", stroke: "none" }));
  else (ICON_PATHS[name] || []).forEach((d) => add("path", { d }));
  return svg;
}

function chatNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function iconButton(iconName, label, onClick, className = "icon-btn") {
  const button = chatNode("button", className);
  button.type = "button";
  button.title = label;
  button.setAttribute("aria-label", label);
  button.appendChild(icon(iconName));
  if (onClick) button.addEventListener("click", onClick);
  return button;
}

/* ---------------- Markdown (safe subset: GitHub-flavoured, rendered to DOM nodes) ---------------- */

const MD_FENCE = /^\s*(```|~~~)\s*([\w#+.-]*)\s*$/;
const MD_HEADING = /^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/;
const MD_HR = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const MD_QUOTE = /^\s{0,3}>\s?(.*)$/;
const MD_LIST_ITEM = /^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$/;
const MD_TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
// code | **bold** | __bold__ | *italic* | _italic_ (not inside words such as snake_case) | [label](url) | bare URL
const MD_INLINE =
  /`([^`\n]+)`|\*\*(\S(?:[\s\S]*?\S)?)\*\*|__(\S(?:[\s\S]*?\S)?)__|\*([^\s*](?:[^*\n]*[^\s*])?)\*|(?<![\w])_([^_\s](?:[^_\n]*[^_\s])?)_(?![\w])|\[([^\]\n]+)\]\(((?:[^()\s]|\([^()\s]*\))+)\)|(https?:\/\/[^\s<>()\]]*[^\s<>()\].,;:!?'"])/g;

function renderMarkdown(source) {
  const fragment = document.createDocumentFragment();
  const lines = String(source || "").replace(/\r\n?/g, "\n").split("\n");
  mdBlocks(lines, 0, lines.length, fragment);
  return fragment;
}

function mdIndent(text) {
  let width = 0;
  for (const ch of text) {
    if (ch === " ") width += 1;
    else if (ch === "\t") width += 4;
    else break;
  }
  return width;
}

function mdIsTableStart(lines, i) {
  return lines[i].includes("|") && i + 1 < lines.length && MD_TABLE_SEP.test(lines[i + 1]);
}

function mdStartsBlock(lines, i) {
  const line = lines[i];
  return MD_FENCE.test(line) || MD_HEADING.test(line) || MD_HR.test(line) || MD_QUOTE.test(line)
    || MD_LIST_ITEM.test(line) || mdIsTableStart(lines, i);
}

function mdBlocks(lines, start, end, parent) {
  let i = start;
  while (i < end) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }
    let m = MD_FENCE.exec(line);
    if (m) {
      const body = [];
      i += 1;
      while (i < end && !lines[i].trim().startsWith(m[1])) body.push(lines[i++]);
      i += 1; // closing fence (missing while the answer is still streaming)
      parent.appendChild(mdCodeBlock(body.join("\n"), m[2]));
      continue;
    }
    m = MD_HEADING.exec(line);
    if (m) {
      // Chat headings stay small: # -> h3 ... ### -> h5.
      const heading = chatNode("h" + Math.min(6, m[1].length + 2));
      mdInline(m[2], heading);
      parent.appendChild(heading);
      i += 1;
      continue;
    }
    if (mdIsTableStart(lines, i)) {
      i = mdTable(lines, i, end, parent);
      continue;
    }
    if (MD_HR.test(line)) {
      parent.appendChild(chatNode("hr"));
      i += 1;
      continue;
    }
    if (MD_QUOTE.test(line)) {
      const inner = [];
      while (i < end && MD_QUOTE.test(lines[i])) inner.push(MD_QUOTE.exec(lines[i++])[1]);
      const quote = chatNode("blockquote");
      mdBlocks(inner, 0, inner.length, quote);
      parent.appendChild(quote);
      continue;
    }
    if (MD_LIST_ITEM.test(line)) {
      i = mdList(lines, i, end, parent);
      continue;
    }
    const paragraph = [line.trim()];
    i += 1;
    while (i < end && lines[i].trim() && !mdStartsBlock(lines, i)) paragraph.push(lines[i++].trim());
    const p = chatNode("p");
    mdInlineLines(paragraph, p);
    parent.appendChild(p);
  }
}

function mdList(lines, start, end, parent) {
  const first = MD_LIST_ITEM.exec(lines[start]);
  const baseIndent = mdIndent(first[1]);
  const ordered = /\d/.test(first[2]);
  const list = chatNode(ordered ? "ol" : "ul");
  if (ordered && parseInt(first[2], 10) !== 1) list.start = parseInt(first[2], 10);
  let i = start;
  while (i < end) {
    const item = MD_LIST_ITEM.exec(lines[i]);
    if (!item || mdIndent(item[1]) !== baseIndent || /\d/.test(item[2]) !== ordered) break;
    const li = chatNode("li");
    const text = [item[3]];
    const nested = [];
    i += 1;
    while (i < end) {
      const line = lines[i];
      if (!line.trim()) {
        // A blank line continues the item only when the list goes on below it.
        let next = i + 1;
        while (next < end && !lines[next].trim()) next += 1;
        const nextItem = next < end ? MD_LIST_ITEM.exec(lines[next]) : null;
        if (next < end && (mdIndent(lines[next]) > baseIndent || (nextItem && mdIndent(nextItem[1]) === baseIndent))) {
          i = next;
          continue;
        }
        break;
      }
      const child = MD_LIST_ITEM.exec(line);
      if (child && mdIndent(child[1]) > baseIndent) {
        const holder = document.createDocumentFragment();
        i = mdList(lines, i, end, holder);
        nested.push(holder);
        continue;
      }
      if (child) break; // the next item of this list or of an outer one
      if (mdIndent(line) > baseIndent) {
        text.push(line.trim());
        i += 1;
        continue;
      }
      break;
    }
    mdInlineLines(text, li);
    nested.forEach((node) => li.appendChild(node));
    list.appendChild(li);
  }
  parent.appendChild(list);
  return i;
}

function mdSplitRow(line) {
  let row = line.trim();
  if (row.startsWith("|")) row = row.slice(1);
  if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
  return row.split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, "|"));
}

function mdTable(lines, start, end, parent) {
  const head = mdSplitRow(lines[start]);
  const aligns = mdSplitRow(lines[start + 1]).map((cell) =>
    cell.startsWith(":") && cell.endsWith(":") ? "center" : cell.endsWith(":") ? "right" : "");
  const wrap = chatNode("div", "md-table-wrap");
  const table = chatNode("table");
  const headRow = chatNode("tr");
  head.forEach((cell, c) => {
    const th = chatNode("th");
    if (aligns[c]) th.style.textAlign = aligns[c];
    mdInline(cell, th);
    headRow.appendChild(th);
  });
  table.appendChild(chatNode("thead")).appendChild(headRow);
  const body = table.appendChild(chatNode("tbody"));
  let i = start + 2;
  while (i < end && lines[i].trim() && lines[i].includes("|")) {
    const cells = mdSplitRow(lines[i++]);
    const tr = chatNode("tr");
    head.forEach((_, c) => {
      const td = chatNode("td");
      if (aligns[c]) td.style.textAlign = aligns[c];
      mdInline(cells[c] || "", td);
      tr.appendChild(td);
    });
    body.appendChild(tr);
  }
  wrap.appendChild(table);
  parent.appendChild(wrap);
  return i;
}

function mdCodeBlock(text, language) {
  const block = chatNode("div", "md-code");
  const head = chatNode("div", "md-code-head");
  head.appendChild(chatNode("span", "", language || "code"));
  const copy = chatNode("button", "md-copy");
  copy.type = "button";
  copy.append(icon("copy", 14), chatNode("span", "", "Copy"));
  copy.addEventListener("click", () => copyToClipboard(text, copy));
  head.appendChild(copy);
  const pre = chatNode("pre");
  pre.appendChild(chatNode("code", "", text));
  block.append(head, pre);
  return block;
}

function mdInlineLines(lines, parent) {
  lines.forEach((line, index) => {
    if (index) parent.appendChild(document.createElement("br"));
    mdInline(line, parent);
  });
}

function mdInline(text, parent) {
  const pattern = new RegExp(MD_INLINE.source, "g"); // own instance: mdInline recurses
  let last = 0;
  let m;
  while ((m = pattern.exec(text))) {
    if (m.index > last) parent.appendChild(document.createTextNode(text.slice(last, m.index)));
    if (m[1] !== undefined) {
      parent.appendChild(chatNode("code", "", m[1]));
    } else if (m[2] !== undefined || m[3] !== undefined) {
      mdInline(m[2] !== undefined ? m[2] : m[3], parent.appendChild(chatNode("strong")));
    } else if (m[4] !== undefined || m[5] !== undefined) {
      mdInline(m[4] !== undefined ? m[4] : m[5], parent.appendChild(chatNode("em")));
    } else if (m[6] !== undefined) {
      mdLink(m[7], m[6], parent);
    } else {
      mdLink(m[8], null, parent);
    }
    last = pattern.lastIndex;
  }
  if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
}

function mdLink(href, label, parent) {
  if (!/^(https?:|mailto:)/i.test(href)) {
    mdInline(label || href, parent);
    return;
  }
  const link = chatNode("a");
  link.href = href;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  if (label) mdInline(label, link);
  else link.textContent = href;
  parent.appendChild(link);
}

/* ---------------- Conversations (localStorage) ---------------- */

function createConversation(title = "New conversation", history = []) {
  const now = new Date().toISOString();
  return { id: createId(), title, createdAt: now, updatedAt: now, history, draft: "" };
}

function sanitizeHistory(rawHistory) {
  if (!Array.isArray(rawHistory)) return [];
  const out = [];
  for (const turn of rawHistory) {
    if (!turn || (turn.role !== "user" && turn.role !== "assistant")) continue;
    const toolsRaw = turn.tools || turn.used_tools || [];
    const clean = {
      role: turn.role,
      content: String(turn.content || "").slice(0, MAX_MESSAGE_LENGTH),
      tools: Array.isArray(toolsRaw) ? toolsRaw.map((t) => String(t)).slice(0, 40) : [],
      timestamp: isNaN(Date.parse(turn.timestamp || "")) ? new Date().toISOString() : turn.timestamp,
    };
    if (turn.status === "error" || turn.status === "stopped") clean.status = turn.status;
    if (Array.isArray(turn.steps) && turn.steps.length) {
      clean.steps = turn.steps.slice(0, 60).map((step) => ({
        name: String((step && step.name) || "").slice(0, 80),
        ok: !step || step.ok !== false,
        ms: step && Number.isFinite(step.ms) ? step.ms : null,
        proposal: step && step.proposal ? String(step.proposal).slice(0, 80) : null,
      }));
    }
    if (Array.isArray(turn.proposals) && turn.proposals.length) clean.proposals = turn.proposals.map(String).slice(0, 20);
    if (Number.isFinite(turn.durationMs)) clean.durationMs = turn.durationMs;
    out.push(clean);
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
        const createdAt = isNaN(Date.parse(conv.createdAt || "")) ? new Date().toISOString() : conv.createdAt;
        return {
          id: String(conv.id || createId()),
          title: String(conv.title || "New conversation").slice(0, 80),
          createdAt,
          updatedAt: isNaN(Date.parse(conv.updatedAt || "")) ? createdAt : conv.updatedAt,
          history: sanitizeHistory(conv.history),
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
    // keep the default
  }
}

function persistState() {
  state.conversations = state.conversations.slice(0, MAX_CONVERSATIONS);
  const conversations = state.conversations.map((conv) => ({
    id: conv.id,
    title: String(conv.title || "New conversation").slice(0, 80),
    createdAt: conv.createdAt,
    updatedAt: conv.updatedAt,
    history: sanitizeHistory(conv.history).slice(-400),
    draft: String(conv.draft || "").slice(0, MAX_MESSAGE_LENGTH),
  }));
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ conversations, activeConversationId: state.activeConversationId }));
  } catch (err) {
    console.warn("Unable to save the conversations:", err);
  }
}

function schedulePersist() {
  clearTimeout(state.persistTimer);
  state.persistTimer = setTimeout(() => {
    state.persistTimer = null;
    persistState();
  }, 250);
}

function formatClock(isoTimestamp) {
  const d = new Date(isoTimestamp || "");
  return isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatRelativeTime(isoTimestamp) {
  const then = Date.parse(isoTimestamp || "");
  if (isNaN(then)) return "just now";
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  const days = Math.floor(seconds / 86400);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

function formatDuration(ms) {
  if (!Number.isFinite(ms)) return "";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
}

function deriveConversationTitle(text) {
  const normalized = String(text || "").replace(/\s+/g, " ").trim();
  if (!normalized) return "New conversation";
  return normalized.length > 52 ? `${normalized.slice(0, 52)}…` : normalized;
}

function getActiveConversation() {
  const active = state.conversations.find((conv) => conv.id === state.activeConversationId);
  if (active) return active;
  if (!state.conversations.length) state.conversations = [createConversation()];
  state.activeConversationId = state.conversations[0].id;
  return state.conversations[0];
}

function setActiveConversationDraft(value) {
  getActiveConversation().draft = String(value || "").slice(0, MAX_MESSAGE_LENGTH);
}

function syncComposerFromActiveConversation() {
  elements.messageInput.value = String(getActiveConversation().draft || "");
  autoResizeTextarea(elements.messageInput);
  updateComposerState();
}

function updateLastUsedTools() {
  const lastAssistant = [...getActiveConversation().history]
    .reverse()
    .find((turn) => turn.role === "assistant" && Array.isArray(turn.tools) && turn.tools.length);
  state.lastUsedTools = new Set(lastAssistant ? lastAssistant.tools : []);
}

function appendTurn(conversation, role, content, tools = [], extra = {}) {
  const turn = {
    role,
    content: String(content || ""),
    tools: Array.isArray(tools) ? tools.map((t) => String(t)) : [],
    timestamp: new Date().toISOString(),
    ...extra,
  };
  conversation.history.push(turn);
  conversation.history = conversation.history.slice(-400);
  conversation.updatedAt = turn.timestamp;
  if (role === "user" && conversation.title === "New conversation") conversation.title = deriveConversationTitle(content);
}

// The turns sent to the model: answered exchanges only -- error notices and stopped partial answers
// are for the reader, not context for the next answer.
function buildApiHistory(conversation) {
  return conversation.history
    .slice(0, -1)
    .filter((turn) => !turn.status && turn.content.trim())
    .slice(-CHAT_HISTORY_TURNS)
    .map((turn) => ({ role: turn.role, content: turn.content }));
}

function currentSystemPrompt() {
  return elements.systemPromptInput.value.trim().slice(0, MAX_SYSTEM_PROMPT_LENGTH);
}

/* ---------------- Sending and streaming ---------------- */

async function sendMessage(rawMessage) {
  const raw = String(rawMessage || "");
  const content = raw.trim();
  if (!content || state.chatPending || raw.length > MAX_MESSAGE_LENGTH) return;
  const conversation = getActiveConversation();
  conversation.draft = "";
  appendTurn(conversation, "user", content);
  elements.messageInput.value = "";
  autoResizeTextarea(elements.messageInput);
  persistState();
  await requestAnswer(conversation);
}

async function streamChat(body, signal, onEvent) {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => ({}));
    const detail = typeof data.detail === "string" ? data.detail : "";
    throw new Error(detail || `The assistant is not reachable (HTTP ${response.status}).`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        let event = "message";
        let data = "";
        for (const line of block.split("\n")) {
          if (line.startsWith("event: ")) event = line.slice(7);
          else if (line.startsWith("data: ")) data += line.slice(6);
        }
        if (data) onEvent(event, JSON.parse(data)); // blocks without data are keep-alives
      }
    }
  } finally {
    reader.cancel().catch(() => {});
  }
}

async function requestAnswer(conversation) {
  const lastTurn = conversation.history[conversation.history.length - 1];
  if (!lastTurn || lastTurn.role !== "user" || state.chatPending) return;
  const pending = {
    conversationId: conversation.id,
    controller: new AbortController(),
    text: "",
    steps: [],
    startedAt: Date.now(),
    frame: 0,
    view: null,
  };
  state.chatPending = pending;
  renderChat();

  let finished = null;
  try {
    await streamChat(
      { message: lastTurn.content, history: buildApiHistory(conversation), system_prompt: currentSystemPrompt() || undefined },
      pending.controller.signal,
      (event, data) => {
        if (event === "tool_start") {
          pending.steps.push({ id: data.id, name: data.name, ok: null, ms: null, proposal: null });
        } else if (event === "tool_end") {
          const step = pending.steps.find((s) => s.id === data.id && s.ok === null);
          if (step) Object.assign(step, { ok: Boolean(data.ok), ms: data.duration_ms, proposal: data.proposal || null });
        } else if (event === "delta") {
          pending.text += data.content || "";
        } else if (event === "draft_reset") {
          pending.text = "";
        } else if (event === "done") {
          finished = data;
        } else if (event === "error") {
          throw new Error(data.detail || "The assistant could not answer.");
        }
        schedulePendingRender();
      },
    );
    if (!finished) throw new Error("The connection closed before the answer was complete.");
    const proposals = Array.isArray(finished.proposals) ? finished.proposals : [];
    appendTurn(conversation, "assistant", finished.answer || pending.text, finished.used_tools || [], {
      steps: finishedSteps(pending),
      proposals,
      durationMs: Date.now() - pending.startedAt,
    });
    if (proposals.length) loadHealth(); // pending-approval count in the header
  } catch (err) {
    const stopped = err && err.name === "AbortError";
    appendTurn(conversation, "assistant", stopped ? pending.text : String((err && err.message) || err), [], {
      status: stopped ? "stopped" : "error",
      steps: finishedSteps(pending),
      durationMs: Date.now() - pending.startedAt,
    });
  } finally {
    cancelAnimationFrame(pending.frame);
    state.chatPending = null;
    persistState();
    updateLastUsedTools();
    renderToolList();
    renderChat();
    if (state.activeConversationId === conversation.id && !state.chatCollapsed) elements.messageInput.focus();
  }
}

function finishedSteps(pending) {
  return pending.steps.map(({ name, ok, ms, proposal }) => ({ name, ok: ok !== false, ms, proposal }));
}

function stopAnswer() {
  if (state.chatPending) state.chatPending.controller.abort();
}

function regenerateAnswer() {
  if (state.chatPending) return;
  const conversation = getActiveConversation();
  while (conversation.history.length && conversation.history[conversation.history.length - 1].role === "assistant") {
    conversation.history.pop();
  }
  requestAnswer(conversation);
}

function editUserMessage(index) {
  if (state.chatPending) return;
  const conversation = getActiveConversation();
  const turn = conversation.history[index];
  if (!turn || turn.role !== "user") return;
  conversation.history = conversation.history.slice(0, index);
  elements.messageInput.value = turn.content;
  setActiveConversationDraft(turn.content);
  persistState();
  renderChat();
  autoResizeTextarea(elements.messageInput);
  elements.messageInput.focus();
  elements.messageInput.setSelectionRange(turn.content.length, turn.content.length);
}

async function copyToClipboard(text, button) {
  let copied = false;
  try {
    await navigator.clipboard.writeText(text);
    copied = true;
  } catch (err) {
    const temp = document.createElement("textarea");
    temp.value = text;
    document.body.appendChild(temp);
    temp.select();
    copied = document.execCommand("copy");
    temp.remove();
  }
  if (!copied || !button) return;
  const label = button.querySelector("span");
  const iconNode = button.querySelector("svg");
  const previousLabel = label ? label.textContent : null;
  if (iconNode) iconNode.replaceWith(icon("check", label ? 14 : 16));
  if (label) label.textContent = "Copied";
  button.classList.add("copied");
  setTimeout(() => {
    const current = button.querySelector("svg");
    if (current) current.replaceWith(icon("copy", label ? 14 : 16));
    if (label) label.textContent = previousLabel;
    button.classList.remove("copied");
  }, 1400);
}

/* ---------------- Rendering ---------------- */

function renderChat() {
  renderConversationHeader();
  renderConversationList();
  renderChatWindow();
  updateComposerState();
}

function renderConversationHeader() {
  const active = getActiveConversation();
  if (!elements.conversationTitle.querySelector("input")) {
    elements.conversationTitle.textContent = active.title || "New conversation";
  }
  renderChatContext();
}

// The active Archi model next to the title, and a notice when Archi is not reachable.
function renderChatContext() {
  const health = state.health || {};
  const connected = health.mcp_status === "ok";
  const meta = elements.conversationMeta;
  meta.replaceChildren(chatNode("span", `dot${connected ? "" : " off"}`));
  meta.appendChild(document.createTextNode(
    connected ? (health.archi_model ? `Archi model: ${health.archi_model}` : "Connected to Archi")
      : health.mcp_status ? "Archi not connected" : "Checking the connection…",
  ));
  const showNotice = health.mcp_status === "error";
  elements.chatNotice.classList.toggle("hidden", !showNotice);
  if (showNotice) {
    elements.chatNotice.replaceChildren(
      icon("alert", 16),
      chatNode("span", "", "Archi's MCP server is not reachable, so the assistant cannot read the model. Start it in Archi with MCP Server › Start MCP Server."),
    );
  }
}

function renderConversationList() {
  const list = elements.conversationList;
  list.replaceChildren();
  const query = elements.historySearch.value.trim().toLowerCase();
  const sorted = [...state.conversations]
    .filter((conv) => !query || conv.title.toLowerCase().includes(query)
      || conv.history.some((turn) => turn.content.toLowerCase().includes(query)))
    .sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt));
  if (!sorted.length) {
    list.appendChild(chatNode("p", "conversation-empty", query ? "No conversation matches your search." : "No conversations yet."));
    return;
  }
  const startOfToday = new Date().setHours(0, 0, 0, 0);
  const groupOf = (conv) => {
    const updated = Date.parse(conv.updatedAt);
    if (updated >= startOfToday) return "Today";
    if (updated >= startOfToday - 6 * 86400000) return "Previous 7 days";
    return "Older";
  };
  let currentGroup = "";
  for (const conv of sorted) {
    const group = groupOf(conv);
    if (group !== currentGroup) {
      list.appendChild(chatNode("p", "conversation-group", group));
      currentGroup = group;
    }
    const item = chatNode("div", `conversation-item${conv.id === state.activeConversationId ? " active" : ""}`);
    const main = chatNode("button", "conversation-main");
    main.type = "button";
    main.dataset.conversationId = conv.id;
    const answering = state.chatPending && state.chatPending.conversationId === conv.id;
    main.append(
      chatNode("p", "conversation-title", conv.title || "New conversation"),
      chatNode("p", "conversation-meta", answering ? "Answering…"
        : `${conv.history.length} message${conv.history.length === 1 ? "" : "s"} · ${formatRelativeTime(conv.updatedAt)}`),
    );
    main.addEventListener("click", () => {
      switchActiveConversation(conv.id);
      setHistoryOpen(false);
    });
    item.append(main, iconButton("trash", "Delete conversation", () => deleteConversation(conv.id), "icon-btn conversation-delete"));
    list.appendChild(item);
  }
}

function chatIsNearBottom() {
  const win = elements.chatWindow;
  return win.scrollHeight - win.scrollTop - win.clientHeight < 80;
}

function scrollChatToBottom() {
  elements.chatWindow.scrollTop = elements.chatWindow.scrollHeight;
  elements.jumpLatestBtn.classList.add("hidden");
}

function renderChatWindow() {
  const active = getActiveConversation();
  const win = elements.chatWindow;
  win.replaceChildren();
  const pendingHere = state.chatPending && state.chatPending.conversationId === active.id;
  if (!active.history.length && !pendingHere) {
    win.appendChild(chatWelcome());
    elements.jumpLatestBtn.classList.add("hidden");
    return;
  }
  const lastUserIndex = active.history.map((t) => t.role).lastIndexOf("user");
  active.history.forEach((turn, index) => {
    win.appendChild(turn.role === "user"
      ? userMessage(turn, index, index === lastUserIndex && !state.chatPending)
      : assistantMessage(turn, index === active.history.length - 1 && !state.chatPending));
  });
  const last = active.history[active.history.length - 1];
  if (last && last.role === "user" && !pendingHere) {
    // e.g. the page was reloaded while the answer was being written
    const row = chatNode("div", "chat-unanswered");
    row.append(icon("clock", 14), chatNode("span", "", "No answer yet."));
    const retry = chatNode("button", "link-btn", "Get an answer");
    retry.type = "button";
    retry.disabled = Boolean(state.chatPending);
    retry.addEventListener("click", () => requestAnswer(active));
    row.appendChild(retry);
    win.appendChild(row);
  }
  if (pendingHere) {
    state.chatPending.view = pendingMessage();
    win.appendChild(state.chatPending.view.root);
    updatePendingMessage();
  }
  scrollChatToBottom();
}

function chatWelcome() {
  const box = chatNode("div", "chat-welcome");
  const badge = chatNode("div", "chat-welcome-icon");
  badge.appendChild(icon("sparkle", 20));
  box.append(
    badge,
    chatNode("h3", "", "Ask about your architecture"),
    chatNode("p", "", "The assistant reads the active Archi model through MCP and follows the governance meta-model. With Archi's approval mode on, any change it makes waits for your approval in Archi."),
  );
  const suggestions = chatNode("div", "chat-suggestions");
  for (const text of CHAT_SUGGESTIONS) {
    const button = chatNode("button", "chat-suggestion", text);
    button.type = "button";
    button.addEventListener("click", () => sendMessage(text));
    suggestions.appendChild(button);
  }
  box.appendChild(suggestions);
  return box;
}

function userMessage(turn, index, editable) {
  const article = chatNode("article", "message user");
  const wrap = chatNode("div", "message-user-wrap");
  wrap.appendChild(chatNode("div", "message-bubble", turn.content));
  const actions = chatNode("div", "message-actions");
  actions.appendChild(chatNode("span", "message-time", formatClock(turn.timestamp)));
  const copy = iconButton("copy", "Copy");
  copy.addEventListener("click", () => copyToClipboard(turn.content, copy));
  actions.appendChild(copy);
  if (editable) actions.appendChild(iconButton("edit", "Edit and resend", () => editUserMessage(index)));
  wrap.appendChild(actions);
  article.appendChild(wrap);
  return article;
}

function assistantAvatar() {
  const avatar = chatNode("div", "message-avatar");
  avatar.appendChild(icon("sparkle", 15));
  return avatar;
}

function assistantMessage(turn, isLast) {
  const article = chatNode("article", `message assistant${turn.status ? ` ${turn.status}` : ""}${isLast ? " last" : ""}`);
  const main = chatNode("div", "message-main");
  if (turn.steps && turn.steps.length) main.appendChild(stepsSummary(turn.steps, turn.durationMs));
  if (turn.status === "error") {
    const error = chatNode("div", "message-error");
    error.append(icon("alert", 16), chatNode("div", "", ""));
    error.lastChild.append(chatNode("strong", "", "The assistant could not answer. "), document.createTextNode(turn.content));
    main.appendChild(error);
  } else {
    if (turn.content.trim()) {
      const body = chatNode("div", "message-body markdown");
      body.appendChild(renderMarkdown(turn.content));
      main.appendChild(body);
    }
    if (turn.status === "stopped") main.appendChild(chatNode("p", "message-note", "Stopped before the answer was complete."));
  }
  if (turn.proposals && turn.proposals.length) main.appendChild(approvalNotice(turn.proposals));

  const actions = chatNode("div", "message-actions");
  actions.appendChild(chatNode("span", "message-time", formatClock(turn.timestamp)));
  if (turn.status !== "error" && turn.content.trim()) {
    const copy = iconButton("copy", "Copy answer");
    copy.addEventListener("click", () => copyToClipboard(turn.content, copy));
    actions.appendChild(copy);
  }
  if (isLast) actions.appendChild(iconButton("retry", turn.status ? "Try again" : "Regenerate answer", regenerateAnswer));
  main.appendChild(actions);
  article.append(assistantAvatar(), main);
  return article;
}

function approvalNotice(proposals) {
  const notice = chatNode("div", "message-approval");
  notice.appendChild(icon("alert", 16));
  const text = chatNode("div");
  text.append(
    chatNode("strong", "", "Waiting for your approval in Archi. "),
    document.createTextNode(`Archi's approval mode is on, so the change is queued as proposal ${proposals.join(", ")}. `
      + "Approve or reject it in Archi under MCP Server › Pending approvals."),
  );
  notice.appendChild(text);
  return notice;
}

function stepRow(step) {
  const status = step.ok === null ? "running" : step.ok ? "ok" : "failed";
  const row = chatNode("li", `step ${status}${isWriteTool(step.name) ? " write" : ""}`);
  const badge = chatNode("span", "step-status");
  if (status === "running") badge.appendChild(chatNode("span", "spinner"));
  else badge.appendChild(icon(status === "ok" ? "check" : "x", 13));
  const name = chatNode("span", "step-name", toolLabel(step.name));
  name.title = step.name;
  row.append(badge, name);
  if (step.proposal) row.appendChild(chatNode("span", "step-tag", "awaiting approval"));
  else if (isWriteTool(step.name)) row.appendChild(chatNode("span", "step-tag write", "writes"));
  if (Number.isFinite(step.ms)) row.appendChild(chatNode("span", "step-ms", formatDuration(step.ms)));
  return row;
}

function stepsSummary(steps, durationMs) {
  const details = chatNode("details", "message-steps");
  const summary = chatNode("summary");
  const failed = steps.filter((s) => !s.ok).length;
  const writes = steps.filter((s) => isWriteTool(s.name)).length;
  summary.append(
    icon("chevronRight", 14),
    chatNode("span", "", `${steps.length} step${steps.length === 1 ? "" : "s"} in Archi`
      + (writes ? ` · ${writes} change${writes === 1 ? "" : "s"}` : "")
      + (failed ? ` · ${failed} failed` : "")
      + (Number.isFinite(durationMs) ? ` · ${formatDuration(durationMs)}` : "")),
  );
  const list = chatNode("ol", "step-list");
  steps.forEach((step) => list.appendChild(stepRow(step)));
  details.append(summary, list);
  return details;
}

function pendingMessage() {
  const root = chatNode("article", "message assistant pending");
  const main = chatNode("div", "message-main");
  const steps = chatNode("ol", "step-list live");
  const status = chatNode("div", "thinking");
  const body = chatNode("div", "message-body markdown");
  main.append(steps, status, body);
  root.append(assistantAvatar(), main);
  return { root, steps, status, body };
}

function schedulePendingRender() {
  const pending = state.chatPending;
  if (!pending || pending.frame) return;
  pending.frame = requestAnimationFrame(() => {
    pending.frame = 0;
    updatePendingMessage();
  });
}

function updatePendingMessage() {
  const pending = state.chatPending;
  if (!pending || !pending.view || !pending.view.root.isConnected) return;
  const stick = chatIsNearBottom();
  const { steps, status, body } = pending.view;
  steps.replaceChildren(...pending.steps.map(stepRow));
  steps.classList.toggle("hidden", !pending.steps.length);
  const running = pending.steps.find((s) => s.ok === null);
  status.classList.toggle("hidden", Boolean(pending.text));
  status.replaceChildren(chatNode("span", "spinner"), chatNode("span", "", running ? `${toolLabel(running.name)}…`
    : pending.steps.length ? "Writing the answer…" : "Thinking…"));
  body.replaceChildren(renderMarkdown(pending.text));
  if (pending.text) appendCaret(body);
  if (stick) scrollChatToBottom();
  else elements.jumpLatestBtn.classList.remove("hidden");
}

function appendCaret(container) {
  let node = container.lastElementChild;
  while (node && node.lastElementChild && ["UL", "OL", "LI", "BLOCKQUOTE"].includes(node.tagName)) node = node.lastElementChild;
  (node && !["PRE", "TABLE", "DIV", "HR"].includes(node.tagName) ? node : container).appendChild(chatNode("span", "caret"));
}

function updateComposerState() {
  const value = elements.messageInput.value || "";
  const overLimit = value.length > MAX_MESSAGE_LENGTH;
  const pending = state.chatPending;
  const pendingHere = pending && pending.conversationId === state.activeConversationId;
  elements.sendBtn.classList.toggle("hidden", Boolean(pendingHere));
  elements.stopBtn.classList.toggle("hidden", !pendingHere);
  elements.sendBtn.disabled = Boolean(pending) || !value.trim() || overLimit;
  elements.sendBtn.title = pending && !pendingHere ? "Wait until the answer in the other conversation is complete" : "Send (Enter)";
  const showCounter = value.length >= MAX_MESSAGE_LENGTH * CHAT_COUNTER_FROM;
  elements.messageCounter.textContent = showCounter ? `${value.length.toLocaleString()} / ${MAX_MESSAGE_LENGTH.toLocaleString()}` : "";
  elements.messageCounter.classList.toggle("over-limit", overLimit);
}

function autoResizeTextarea(textarea) {
  textarea.style.height = "auto";
  if (!textarea.offsetParent) return; // hidden (assistant collapsed): measure when it is shown
  textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
}

/* ---------------- Conversation management ---------------- */

function abortIfAnswering(conversationId) {
  if (state.chatPending && state.chatPending.conversationId === conversationId) state.chatPending.controller.abort();
}

function switchActiveConversation(conversationId) {
  if (!conversationId || state.activeConversationId === conversationId) return;
  if (!state.conversations.some((conv) => conv.id === conversationId)) return;
  setActiveConversationDraft(elements.messageInput.value);
  state.activeConversationId = conversationId;
  persistState();
  updateLastUsedTools();
  renderToolList();
  renderChat();
  syncComposerFromActiveConversation();
  elements.messageInput.focus();
}

function createNewConversation() {
  setActiveConversationDraft(elements.messageInput.value);
  const empty = state.conversations.find((conv) => !conv.history.length);
  const conversation = empty || createConversation();
  if (!empty) state.conversations.unshift(conversation);
  state.activeConversationId = conversation.id;
  persistState();
  setHistoryOpen(false);
  renderChat();
  syncComposerFromActiveConversation();
  elements.messageInput.focus();
}

function deleteConversation(conversationId) {
  const conversation = state.conversations.find((conv) => conv.id === conversationId);
  if (!conversation) return;
  if (conversation.history.length && !confirm(`Delete "${conversation.title}"? This cannot be undone.`)) return;
  abortIfAnswering(conversationId);
  state.conversations = state.conversations.filter((conv) => conv.id !== conversationId);
  if (!state.conversations.length) state.conversations = [createConversation()];
  if (state.activeConversationId === conversationId) state.activeConversationId = state.conversations[0].id;
  persistState();
  renderChat();
  syncComposerFromActiveConversation();
}

function clearActiveConversation() {
  const active = getActiveConversation();
  if (!active.history.length || !confirm("Clear all messages of this conversation?")) return;
  abortIfAnswering(active.id);
  active.history = [];
  active.title = "New conversation";
  active.updatedAt = new Date().toISOString();
  persistState();
  renderChat();
}

function startRenameConversation() {
  const active = getActiveConversation();
  const heading = elements.conversationTitle;
  const input = chatNode("input", "chat-title-input");
  input.type = "text";
  input.value = active.title || "";
  input.maxLength = 80;
  input.setAttribute("aria-label", "Conversation title");
  heading.replaceChildren(input);
  input.focus();
  input.select();
  let done = false;
  const finish = (save) => {
    if (done) return;
    done = true;
    const clean = input.value.replace(/\s+/g, " ").trim();
    if (save && clean) {
      active.title = clean.slice(0, 80);
      persistState();
    }
    heading.replaceChildren(document.createTextNode(active.title || "New conversation"));
    renderConversationList();
  };
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") finish(true);
    if (event.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
}

function exportActiveConversation() {
  const active = getActiveConversation();
  const lines = [`# ${active.title}`, "", `_Exported ${new Date().toLocaleString()} from Archi Local Chatbot_`, ""];
  for (const turn of active.history) {
    const when = new Date(turn.timestamp).toLocaleString();
    lines.push(`### ${turn.role === "user" ? "You" : "Assistant"} · ${when}`, "");
    if (turn.status === "error") lines.push(`> The assistant could not answer: ${turn.content}`);
    else lines.push(turn.content || "_(no text)_");
    if (turn.status === "stopped") lines.push("", "_Stopped before the answer was complete._");
    if (turn.steps && turn.steps.length) lines.push("", `_Steps in Archi: ${turn.steps.map((s) => s.name).join(", ")}_`);
    if (turn.proposals && turn.proposals.length) lines.push("", `_Waiting for approval in Archi: ${turn.proposals.join(", ")}_`);
    lines.push("");
  }
  const blob = new Blob([lines.join("\n")], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${active.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "conversation"}.md`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function setHistoryOpen(open) {
  elements.historyPanel.classList.toggle("hidden", !open);
  elements.chatPanel.classList.toggle("history-open", open);
  elements.historyBtn.setAttribute("aria-expanded", String(open));
  if (open) {
    elements.historySearch.value = "";
    renderConversationList();
    elements.historySearch.focus();
  }
}

function setChatMenuOpen(open, viaKeyboard = false) {
  elements.chatMenu.classList.toggle("hidden", !open);
  elements.chatMenuBtn.setAttribute("aria-expanded", String(open));
  if (open && viaKeyboard) elements.chatMenu.querySelector("button").focus();
}

function setChatCollapsed(collapsed) {
  state.chatCollapsed = collapsed;
  elements.appGrid.classList.toggle("chat-collapsed", collapsed);
  elements.chatRail.setAttribute("aria-expanded", String(!collapsed));
  elements.drawerCollapseBtn.setAttribute("aria-expanded", String(!collapsed));
  try {
    localStorage.setItem(CHAT_COLLAPSED_KEY, collapsed ? "1" : "0");
  } catch (err) {
    // ignore storage errors
  }
}

function openAssistant() {
  setChatCollapsed(false);
  fitChatDrawer();
  autoResizeTextarea(elements.messageInput);
  elements.messageInput.focus();
}

// The drawer is sticky: below the top bar at first, at the top of the window once scrolled. Its
// height follows, so the composer is always on screen (CSS alone can only fit one of the two).
function fitChatDrawer() {
  const drawer = elements.chatDrawer;
  if (getComputedStyle(drawer).position !== "sticky") {
    drawer.style.removeProperty("height");
    return;
  }
  const top = Math.max(16, drawer.getBoundingClientRect().top);
  drawer.style.height = `${Math.max(320, window.innerHeight - top - 16)}px`;
}

/* ---------------- Setup ---------------- */

function renderSystemPromptStatus() {
  const custom = Boolean(currentSystemPrompt());
  elements.systemPromptStatus.textContent = custom
    ? "Custom instruction active in this browser."
    : "Using the default instruction of the server.";
  elements.systemPromptResetBtn.disabled = !custom;
}

function attachChatEventHandlers() {
  elements.chatForm.addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage(elements.messageInput.value);
  });
  elements.messageInput.addEventListener("input", () => {
    setActiveConversationDraft(elements.messageInput.value);
    schedulePersist();
    autoResizeTextarea(elements.messageInput);
    updateComposerState();
  });
  elements.messageInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      elements.chatForm.requestSubmit();
    }
  });
  elements.stopBtn.addEventListener("click", stopAnswer);
  elements.newChatBtn.addEventListener("click", createNewConversation);
  elements.historyBtn.addEventListener("click", () => setHistoryOpen(elements.historyPanel.classList.contains("hidden")));
  elements.historySearch.addEventListener("input", renderConversationList);
  elements.chatRail.addEventListener("click", openAssistant);
  elements.drawerCollapseBtn.addEventListener("click", () => setChatCollapsed(true));
  elements.jumpLatestBtn.addEventListener("click", scrollChatToBottom);
  elements.chatWindow.addEventListener("scroll", () => {
    if (chatIsNearBottom()) elements.jumpLatestBtn.classList.add("hidden");
  }, { passive: true });

  elements.chatMenuBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    setChatMenuOpen(elements.chatMenu.classList.contains("hidden"), event.detail === 0); // detail 0: Enter/Space
  });
  elements.chatMenu.addEventListener("click", (event) => {
    const item = event.target.closest("button[data-action]");
    if (!item) return;
    setChatMenuOpen(false);
    ({
      rename: startRenameConversation,
      export: exportActiveConversation,
      clear: clearActiveConversation,
      delete: () => deleteConversation(state.activeConversationId),
    })[item.dataset.action]();
  });
  document.addEventListener("click", (event) => {
    if (!elements.chatMenu.classList.contains("hidden") && !elements.chatMenu.contains(event.target)) setChatMenuOpen(false);
  });
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      openAssistant();
    } else if (event.key === "Escape") {
      if (!elements.chatMenu.classList.contains("hidden")) setChatMenuOpen(false);
      else if (!elements.historyPanel.classList.contains("hidden")) setHistoryOpen(false);
    }
  });

  elements.systemPromptInput.addEventListener("input", () => {
    try {
      localStorage.setItem(SYSTEM_PROMPT_KEY, elements.systemPromptInput.value.slice(0, MAX_SYSTEM_PROMPT_LENGTH));
    } catch (err) {
      console.warn("Unable to store the assistant instruction:", err);
    }
    renderSystemPromptStatus();
  });
  elements.systemPromptResetBtn.addEventListener("click", () => {
    elements.systemPromptInput.value = "";
    try {
      localStorage.removeItem(SYSTEM_PROMPT_KEY);
    } catch (err) {
      // ignore storage errors
    }
    renderSystemPromptStatus();
  });

  window.addEventListener("beforeunload", () => {
    setActiveConversationDraft(elements.messageInput.value);
    persistState();
  });
}

function initChat() {
  loadPersistedState();
  try {
    elements.systemPromptInput.value = (localStorage.getItem(SYSTEM_PROMPT_KEY) || "").slice(0, MAX_SYSTEM_PROMPT_LENGTH);
  } catch (err) {
    console.warn("Unable to load the assistant instruction:", err);
  }
  renderSystemPromptStatus();
  // Static buttons get their icons here, so the markup stays readable.
  for (const [button, name] of [
    [elements.historyBtn, "history"], [elements.newChatBtn, "plus"], [elements.chatMenuBtn, "more"],
    [elements.drawerCollapseBtn, "chevronRight"], [elements.sendBtn, "send"], [elements.stopBtn, "stop"],
  ]) button.prepend(icon(name, button === elements.sendBtn || button === elements.stopBtn ? 18 : 17));
  elements.chatRail.prepend(icon("sparkle", 18));
  elements.jumpLatestBtn.prepend(icon("arrowDown", 14));
  elements.historySearch.parentElement.prepend(icon("search", 15));
  const menuIcons = { rename: "edit", export: "download", clear: "x", delete: "trash" };
  elements.chatMenu.querySelectorAll("button[data-action]").forEach((item) => item.prepend(icon(menuIcons[item.dataset.action], 15)));

  attachChatEventHandlers();
  let fitFrame = 0;
  const scheduleFit = () => {
    cancelAnimationFrame(fitFrame);
    fitFrame = requestAnimationFrame(fitChatDrawer);
  };
  window.addEventListener("scroll", scheduleFit, { passive: true });
  window.addEventListener("resize", scheduleFit);
  setChatCollapsed(state.chatCollapsed);
  fitChatDrawer();
  updateLastUsedTools();
  renderChat();
  syncComposerFromActiveConversation();
}
