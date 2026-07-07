const state = {
  history: [],
};

const chatWindow = document.getElementById("chatWindow");
const chatForm = document.getElementById("chatForm");
const messageInput = document.getElementById("messageInput");
const clearBtn = document.getElementById("clearBtn");
const healthText = document.getElementById("healthText");

function normalizeAssistantText(raw) {
  if (!raw) return "";

  let text = String(raw).replace(/\r\n/g, "\n");

  // Remove markdown heading markers.
  text = text.replace(/^\s{0,3}#{1,6}\s*/gm, "");

  // Remove unordered list markers.
  text = text.replace(/^\s*[-*+]\s+/gm, "");

  // Remove ordered list markers like "1. ".
  text = text.replace(/^\s*\d+\.\s+/gm, "");

  // Remove markdown emphasis markers while keeping content.
  text = text.replace(/\*\*(.*?)\*\*/g, "$1");
  text = text.replace(/\*(.*?)\*/g, "$1");

  // Normalize excessive whitespace.
  text = text.replace(/\n{3,}/g, "\n\n").trim();
  return text;
}

function appendFormattedText(container, text) {
  const normalized = normalizeAssistantText(text);
  if (!normalized) {
    return;
  }

  const blocks = normalized
    .split(/\n\s*\n/g)
    .map((b) => b.trim())
    .filter(Boolean);

  for (const block of blocks) {
    const p = document.createElement("p");
    p.className = "msg-paragraph";
    p.textContent = block.replace(/\n+/g, " ");
    container.appendChild(p);
  }
}

function renderMessage(role, content, tools = []) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  appendFormattedText(div, content);

  if (tools.length) {
    const toolInfo = document.createElement("span");
    toolInfo.className = "tools";
    toolInfo.textContent = `Tools used: ${tools.join(", ")}`;
    div.appendChild(toolInfo);
  }

  chatWindow.appendChild(div);
  chatWindow.scrollTop = chatWindow.scrollHeight;
}

async function loadHealth() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    healthText.textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    healthText.textContent = `Health check failed: ${err.message}`;
  }
}

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const content = messageInput.value.trim();
  if (!content) {
    return;
  }

  renderMessage("user", content);
  state.history.push({ role: "user", content });
  messageInput.value = "";

  const sendBtn = document.getElementById("sendBtn");
  sendBtn.disabled = true;

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: content,
        history: state.history.slice(0, -1),
      }),
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "Request failed");
    }

    renderMessage("assistant", data.answer || "", data.used_tools || []);
    state.history.push({ role: "assistant", content: data.answer || "" });
    await loadHealth();
  } catch (err) {
    renderMessage("assistant", `Error: ${err.message}`);
  } finally {
    sendBtn.disabled = false;
    messageInput.focus();
  }
});

clearBtn.addEventListener("click", () => {
  state.history = [];
  chatWindow.innerHTML = "";
  messageInput.focus();
});

loadHealth();
