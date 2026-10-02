"use strict";

// Transformation Dashboard. All figures come from GET /api/dashboard, which reads the active Archi
// model live via MCP; nothing here is stored or entered by hand. Charts are plain SVG/HTML built
// with DOM APIs (model names are untrusted text -> textContent only, never innerHTML).

const SVG_NS = "http://www.w3.org/2000/svg";

const ui = {
  modelLine: document.getElementById("modelLine"),
  asOf: document.getElementById("asOfInput"),
  refresh: document.getElementById("refreshBtn"),
  main: document.getElementById("dashboard"),
  kpiRow: document.getElementById("kpiRow"),
  sections: document.getElementById("sections"),
  error: document.getElementById("errorBox"),
  tooltip: document.getElementById("tooltip"),
};

const state = {
  data: null,
  cards: [],
  loading: false,
  resizeTimer: null,
  heatMode: new URLSearchParams(window.location.search).get("heat") === "gap" ? "gap" : "maturity",
};

// ---------------------------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------------------------

function appendChildren(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "text") node.textContent = value;
    else if (key === "class") node.className = value;
    else if (key === "style") Object.assign(node.style, value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  appendChildren(node, children);
  return node;
}

function s(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "text") node.textContent = value;
    else node.setAttribute(key, String(value));
  }
  appendChildren(node, children);
  return node;
}

function svgRoot(width, height, label) {
  return s("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": label });
}

function truncate(text, maxPx, fontPx = 12) {
  const value = String(text ?? "");
  const maxChars = Math.max(4, Math.floor(maxPx / (fontPx * 0.56)));
  return value.length > maxChars ? `${value.slice(0, maxChars - 1)}…` : value;
}

function plural(n, word, pluralWord = `${word}s`) {
  return `${fmt.int(n)} ${Math.round(n) === 1 ? word : pluralWord}`;
}

function clamp(value, lo, hi) {
  return Math.min(hi, Math.max(lo, value));
}

// Bar with a 4px rounded data-end and a square baseline end (direction: +1 grows right, -1 left).
function barPath(x0, y, length, height, direction = 1) {
  const w = Math.abs(length);
  if (w <= 0) return "";
  const r = Math.min(4, w, height / 2);
  if (direction >= 0) {
    return `M${x0},${y} h${w - r} a${r},${r} 0 0 1 ${r},${r} v${height - 2 * r} a${r},${r} 0 0 1 ${-r},${r} h${-(w - r)} Z`;
  }
  return `M${x0},${y} h${-(w - r)} a${r},${r} 0 0 0 ${-r},${r} v${height - 2 * r} a${r},${r} 0 0 0 ${r},${r} h${w - r} Z`;
}

// ---------------------------------------------------------------------------------------------
// Formatting & vocabularies
// ---------------------------------------------------------------------------------------------

const NUM = new Intl.NumberFormat("en-GB");

const fmt = {
  int: (v) => (v === null || v === undefined ? "–" : NUM.format(Math.round(v))),
  dec: (v, digits = 1) => (v === null || v === undefined ? "–" : Number(v).toFixed(digits)),
  pct: (v) => (v === null || v === undefined ? "–" : `${Math.round(v * 100)}%`),
  eur(v) {
    if (v === null || v === undefined) return "–";
    const a = Math.abs(v);
    if (a >= 1e6) return `€${(v / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M`;
    if (a >= 1e3) return `€${Math.round(v / 1e3)}K`;
    return `€${Math.round(v)}`;
  },
  month: (iso) => (iso ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { month: "short", year: "numeric" }) : "–"),
  day: (iso) =>
    iso ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "–",
  months(m) {
    if (m === null || m === undefined) return "–";
    const a = Math.abs(m);
    const text = a < 1 ? "<1 month" : `${Math.round(a)} month${Math.round(a) === 1 ? "" : "s"}`;
    return m < 0 ? `${text} ago` : `in ${text}`;
  },
  num(v, unit) {
    if (v === null || v === undefined) return "–";
    const text = Number.isInteger(v) ? NUM.format(v) : NUM.format(Math.round(v * 10) / 10);
    if (!unit || ["count", "number", "#", "n"].includes(String(unit).toLowerCase())) return text;
    return unit === "%" ? `${text}%` : `${text} ${unit}`;
  },
};

const STATUS_ICON = { good: "✓", warning: "▲", serious: "◆", critical: "✕", neutral: "○" };
const RISK = {
  ok: ["good", "No risk flags"],
  warning: ["warning", "Watch"],
  serious: ["serious", "High risk"],
  critical: ["critical", "Critical risk"],
};
const SUPPORT = {
  out: ["critical", "Out of vendor support"],
  "expiring-12": ["serious", "Support ends within 12 months"],
  "expiring-24": ["warning", "Support ends within 24 months"],
  supported: ["good", "Supported"],
  unknown: ["neutral", "No support date recorded"],
};
const OUTCOME = {
  achieved: ["good", "Achieved"],
  "on-track": ["good", "On track"],
  "at-risk": ["warning", "At risk"],
  "off-track": ["critical", "Off track"],
  missed: ["critical", "Target date missed"],
  tracking: ["neutral", "No baseline date"],
  unknown: ["neutral", "No KPI data"],
};
const RAG = { green: ["good", "Green"], amber: ["warning", "Amber"], red: ["critical", "Red"] };
const MATURITY_LABELS = { 1: "Initial", 2: "Managed", 3: "Defined", 4: "Quantitatively managed", 5: "Optimizing" };
const IMPORTANCE_LABELS = { high: "High", medium: "Medium", low: "Low" };
const LIFECYCLE_LABELS = { plan: "Plan", "phase-in": "Phase in", active: "Active", "phase-out": "Phase out", "end-of-life": "End of life" };
const TIME_LABELS = { invest: "Invest", tolerate: "Tolerate", migrate: "Migrate", eliminate: "Eliminate" };
const CRITICALITY_LABELS = {
  "mission-critical": "Mission critical",
  "business-critical": "Business critical",
  "business-operational": "Business operational",
  administrative: "Administrative service",
};
const AUTOMATION_LABELS = { manual: "Manual", partial: "Partially automated", automated: "Automated" };
const FUNCTIONAL_FIT = { 1: "Unreasonable", 2: "Insufficient", 3: "Appropriate", 4: "Perfect" };
const TECHNICAL_FIT = { 1: "Inappropriate", 2: "Unreasonable", 3: "Adequate", 4: "Fully appropriate" };

function chip(status, label, title) {
  return h(
    "span",
    { class: `chip st-${status}`, title },
    h("span", { class: "chip-icon", "aria-hidden": "true", text: STATUS_ICON[status] }),
    label,
  );
}

function statusChip(map, key) {
  const [status, label] = map[key] || ["neutral", key || "Unknown"];
  return chip(status, label);
}

// ---------------------------------------------------------------------------------------------
// Tooltip (enhances, never gates: every value is also in the chart labels or its table view)
// ---------------------------------------------------------------------------------------------

const tipContent = new WeakMap();

function tip(node, content) {
  tipContent.set(node, content);
  if (!node.hasAttribute("tabindex")) node.setAttribute("tabindex", "0");
  node.addEventListener("pointerenter", showTip);
  node.addEventListener("pointermove", moveTip);
  node.addEventListener("pointerleave", hideTip);
  node.addEventListener("focus", showTip);
  node.addEventListener("blur", hideTip);
  return node;
}

function showTip(event) {
  const content = tipContent.get(event.currentTarget);
  if (!content) return;
  const box = ui.tooltip;
  box.replaceChildren();
  if (content.title) box.append(h("div", { class: "tt-title", text: content.title }));
  for (const [value, label] of content.rows || []) {
    if (value === null || value === undefined || value === "–") continue;
    box.append(h("div", { class: "tt-row" }, h("strong", { text: value }), h("span", { text: label })));
  }
  for (const note of [].concat(content.notes || [])) {
    if (note) box.append(h("div", { class: "tt-note", text: note }));
  }
  box.hidden = false;
  if (event.type === "focus") {
    const rect = event.currentTarget.getBoundingClientRect();
    placeTip(rect.left + rect.width / 2, rect.bottom);
  } else {
    placeTip(event.clientX, event.clientY);
  }
}

function moveTip(event) {
  if (!ui.tooltip.hidden) placeTip(event.clientX, event.clientY);
}

function placeTip(x, y) {
  const box = ui.tooltip;
  const pad = 14;
  let left = x + pad;
  let top = y + pad;
  if (left + box.offsetWidth > window.innerWidth - 8) left = x - box.offsetWidth - pad;
  if (top + box.offsetHeight > window.innerHeight - 8) top = y - box.offsetHeight - pad;
  box.style.left = `${Math.max(8, left)}px`;
  box.style.top = `${Math.max(8, top)}px`;
}

function hideTip() {
  ui.tooltip.hidden = true;
}

// ---------------------------------------------------------------------------------------------
// Layout building blocks: section, chart card (with table-view twin), legend, table, empty state
// ---------------------------------------------------------------------------------------------

function section(id, title, intro, cards) {
  return h(
    "section",
    { id, class: "dash-section", "aria-labelledby": `${id}Title` },
    h("div", { class: "section-head" }, h("h2", { id: `${id}Title`, text: title }), intro ? h("p", { text: intro }) : null),
    h("div", { class: "card-grid" }, cards.filter(Boolean)),
  );
}

function card({ title, subtitle, chart, table, legend, note, span }) {
  const body = h("div", { class: "chart-body" });
  const tableWrap = table ? h("div", { class: "data-table-wrap", hidden: true }) : null;
  let toggle = null;
  if (table) {
    toggle = h("button", { class: "dash-btn small", type: "button", "aria-pressed": "false", text: "Table" });
    toggle.addEventListener("click", () => {
      const showTable = toggle.getAttribute("aria-pressed") !== "true";
      toggle.setAttribute("aria-pressed", String(showTable));
      toggle.textContent = showTable ? "Chart" : "Table";
      body.hidden = showTable;
      tableWrap.hidden = !showTable;
      if (showTable && !tableWrap.firstChild) tableWrap.append(table());
    });
  }
  const figure = h(
    "figure",
    { class: `dash-card chart-card${span ? " span-all" : ""}` },
    h("div", { class: "chart-head" }, h("div", {}, h("h3", { text: title }), subtitle ? h("p", { text: subtitle }) : null), toggle),
    legend || null,
    body,
    tableWrap,
    note ? h("p", { class: "chart-note", text: note }) : null,
  );
  const render = () => {
    const width = Math.max(260, Math.floor(body.clientWidth || figure.clientWidth - 32 || 600));
    body.replaceChildren(chart(width));
  };
  state.cards.push(render);
  return figure;
}

function legend(items) {
  return h(
    "ul",
    { class: "legend" },
    items.map(([kind, color, label]) =>
      h("li", {}, h("span", { class: `swatch${kind === "dot" ? " dot" : ""}`, style: { background: color } }), label),
    ),
  );
}

function statusLegend(entries) {
  return h("ul", { class: "legend" }, entries.map(([status, label]) => h("li", {}, chip(status, label))));
}

function dataTable(columns, rows) {
  return h(
    "table",
    { class: "data-table" },
    h("thead", {}, h("tr", {}, columns.map((c) => h("th", { scope: "col", class: c.num ? "num" : null, text: c.label })))),
    h(
      "tbody",
      {},
      rows.map((row) =>
        h(
          "tr",
          {},
          columns.map((c) => {
            const value = c.value(row);
            return h("td", { class: c.num ? "num" : null }, value instanceof Node ? value : value ?? "–");
          }),
        ),
      ),
    ),
  );
}

function empty(message) {
  return h("div", { class: "chart-empty", text: message });
}

function inlineBar(value, max, label, alt = false) {
  const width = max > 0 && value ? Math.max(2, Math.round((value / max) * 110)) : 0;
  return h(
    "div",
    { class: "inline-bar" },
    width ? h("span", { class: `bar${alt ? " alt" : ""}`, style: { width: `${width}px` } }) : null,
    h("span", { text: label }),
  );
}

// ---------------------------------------------------------------------------------------------
// Generic charts
// ---------------------------------------------------------------------------------------------

function hbars(rows, width, { label = "Bar chart", color = "var(--series)", labelWidth, valueWidth = 120 } = {}) {
  if (!rows.length) return empty("No data in the model yet.");
  const labelW = labelWidth ?? Math.min(210, Math.round(width * 0.36));
  const rowH = 30;
  const barH = 14;
  const plotW = Math.max(60, width - labelW - valueWidth);
  const max = Math.max(...rows.map((r) => r.value || 0)) || 1;
  const height = rows.length * rowH + 2;
  const root = svgRoot(width, height, label);
  root.append(s("line", { class: "ax-line", x1: labelW, x2: labelW, y1: 0, y2: height }));
  rows.forEach((row, i) => {
    const y = i * rowH + (rowH - barH) / 2;
    const barW = row.value ? Math.max(2, (row.value / max) * plotW) : 0;
    const labelNode = s("text", { class: "row-text", x: labelW - 10, y: y + barH - 3, "text-anchor": "end" });
    if (row.status) labelNode.append(s("tspan", { style: `fill:var(--${row.status})`, text: `${STATUS_ICON[row.status]} ` }));
    labelNode.append(truncate(row.label, labelW - 24));
    root.append(labelNode);
    const g = s("g", { class: "mark" });
    if (barW) g.append(s("path", { d: barPath(labelW, y, barW, barH), style: `fill:${row.color || color}` }));
    g.append(s("rect", { class: "hit", x: labelW, y: i * rowH, width: plotW + valueWidth, height: rowH }));
    root.append(g);
    root.append(s("text", { class: "val-text", x: labelW + barW + 6, y: y + barH - 3, text: row.display ?? fmt.int(row.value) }));
    if (row.tip) tip(g, row.tip);
  });
  return root;
}

function stackedBars(rows, keys, width, { label, colorFor, inkFor, labelFor }) {
  if (!rows.length) return empty("No data in the model yet.");
  const labelW = Math.min(210, Math.round(width * 0.36));
  const rowH = 32;
  const barH = 18;
  const plotW = Math.max(80, width - labelW - 50);
  const max = Math.max(...rows.map((r) => keys.reduce((sum, k) => sum + (r.values[k] || 0), 0))) || 1;
  const root = svgRoot(width, rows.length * rowH + 2, label);
  root.append(s("line", { class: "ax-line", x1: labelW, x2: labelW, y1: 0, y2: rows.length * rowH + 2 }));
  rows.forEach((row, i) => {
    const y = i * rowH + (rowH - barH) / 2;
    root.append(s("text", { class: "row-text", x: labelW - 10, y: y + barH - 4, "text-anchor": "end", text: truncate(row.label, labelW - 12) }));
    const present = keys.filter((k) => row.values[k]);
    let x = labelW;
    present.forEach((key, j) => {
      const isLast = j === present.length - 1;
      const full = (row.values[key] / max) * plotW;
      const w = Math.max(1, full - (isLast ? 0 : 2)); // 2px surface gap between segments
      const g = s("g", { class: "mark" });
      g.append(
        isLast
          ? s("path", { d: barPath(x, y, w, barH), style: `fill:${colorFor(key)}` })
          : s("rect", { x, y, width: w, height: barH, style: `fill:${colorFor(key)}` }),
      );
      if (w >= 22) {
        g.append(s("text", { x: x + w / 2, y: y + barH - 5, "text-anchor": "middle", class: "val-text", style: `fill:${inkFor(key)}`, text: row.values[key] }));
      }
      tip(g, { title: row.label, rows: [[fmt.int(row.values[key]), labelFor(key)]] });
      root.append(g);
      x += full;
    });
    const total = keys.reduce((sum, k) => sum + (row.values[k] || 0), 0);
    root.append(s("text", { class: "val-text", x: x + 6, y: y + barH - 4, text: fmt.int(total) }));
  });
  return root;
}

function timeScale(startIso, endIso, x0, x1) {
  const t0 = new Date(`${startIso}T00:00:00`).getTime();
  const t1 = new Date(`${endIso}T00:00:00`).getTime();
  const fn = (iso) => x0 + ((new Date(`${iso}T00:00:00`).getTime() - t0) / (t1 - t0 || 1)) * (x1 - x0);
  fn.years = [];
  for (let y = new Date(t0).getFullYear(); y <= new Date(t1).getFullYear() + 1; y++) {
    const iso = `${y}-01-01`;
    const t = new Date(`${iso}T00:00:00`).getTime();
    if (t >= t0 && t <= t1) fn.years.push({ year: y, iso });
  }
  return fn;
}

function shiftMonths(iso, months) {
  // Local-date arithmetic: toISOString() would convert to UTC and shift the day east of Greenwich.
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(y, m - 1 + months, d);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function yearAxis(root, x, top, bottom) {
  for (const { year, iso } of x.years) {
    root.append(s("line", { class: "grid-line", x1: x(iso), x2: x(iso), y1: top, y2: bottom }));
    root.append(s("text", { class: "ax-text", x: x(iso) + 4, y: top - 6, text: year }));
  }
}

function todayMarker(root, x, todayIso, top, bottom) {
  root.append(s("line", { class: "today-line", x1: x(todayIso), x2: x(todayIso), y1: top - 14, y2: bottom }));
  root.append(s("text", { class: "today-text", x: x(todayIso) + 4, y: top - 18, text: "Today" }));
}

// ---------------------------------------------------------------------------------------------
// KPI row
// ---------------------------------------------------------------------------------------------

function kpiValue(k) {
  if (k.value === null || k.value === undefined) return { main: "–", suffix: "no data" };
  const show = (v) => (k.format === "percent" ? fmt.pct(v) : k.format === "decimal" ? fmt.dec(v) : fmt.int(v));
  let suffix = null;
  if (k.of !== undefined && k.of !== null) suffix = `of ${fmt.int(k.of)}`;
  else if (k.target !== undefined && k.target !== null) suffix = `${k.targetLabel || "target"} ${show(k.target)}`;
  return { main: show(k.value), suffix };
}

function renderKpis(headline) {
  ui.kpiRow.replaceChildren(
    ...headline.map((k) => {
      const value = kpiValue(k);
      return h(
        "a",
        { class: "dash-card kpi-tile", href: `#${k.id}` },
        h("span", { class: "kpi-layer", text: k.layer }),
        h("span", { class: "kpi-label", text: k.label }),
        h("span", { class: "kpi-value" }, value.main, value.suffix ? h("small", { text: value.suffix }) : null),
        h("span", { class: "kpi-detail", text: k.detail }),
      );
    }),
  );
}

// ---------------------------------------------------------------------------------------------
// Strategy: capability maturity heat map & value stream
// ---------------------------------------------------------------------------------------------

function maturityLevel(value) {
  return value === null || value === undefined ? 0 : clamp(Math.round(value), 1, 5);
}

function maturityLegend() {
  return h(
    "div",
    { class: "scale-legend", "aria-label": "Maturity scale" },
    [1, 2, 3, 4, 5].map((level) => h("span", { class: `m${level}`, text: `${level} ${MATURITY_LABELS[level]}` })),
    h("span", { class: "m0", text: "Not rated" }),
  );
}

function capabilityTip(c) {
  return {
    title: c.name,
    rows: [
      [c.maturity !== null ? `${fmt.dec(c.maturity, 0)} · ${MATURITY_LABELS[maturityLevel(c.maturity)]}` : null, "current maturity"],
      [c.targetMaturity !== null ? `${fmt.dec(c.targetMaturity, 0)} · ${MATURITY_LABELS[maturityLevel(c.targetMaturity)]}` : null, "target maturity"],
      [c.gap !== null ? fmt.dec(c.gap, 0) : null, "maturity gap"],
      [IMPORTANCE_LABELS[c.importance] || null, "strategic importance"],
      [c.priorityScore !== null ? fmt.dec(c.priorityScore, 0) : null, "priority score (gap × importance)"],
      [c.investmentEUR ? fmt.eur(c.investmentEUR) : null, "allocated work-package budget"],
    ],
    notes: [
      c.applications.length
        ? `Applications: ${c.applications.map((a) => `${a.name}${a.risk !== "ok" ? ` (${RISK[a.risk][1].toLowerCase()})` : ""}`).join(", ")}`
        : "No supporting application in the model",
      c.workPackages.length ? `Work packages: ${c.workPackages.join(", ")}` : "No work package addresses this capability",
    ],
  };
}

const GAP_BAND = {
  "at-target": ["good", "At target"],
  "one-level": ["warning", "1 level to go"],
  "two-plus": ["critical", "2+ levels to go"],
};

function gapLegend() {
  return statusLegend([
    ["good", "At target maturity"],
    ["warning", "One level below target"],
    ["critical", "Two or more levels below target"],
    ["neutral", "No target set"],
  ]);
}

function heatMapCard(groups) {
  const wrap = h("div", {});
  const buttons = {};
  const draw = () => {
    const mode = state.heatMode;
    for (const [key, button] of Object.entries(buttons)) button.setAttribute("aria-pressed", String(key === mode));
    wrap.replaceChildren(controls, mode === "maturity" ? maturityLegend() : gapLegend(), heatMap(groups, mode));
  };
  const controls = h(
    "div",
    { class: "legend", role: "group", "aria-label": "Colour tiles by" },
    h("span", { text: "Colour by" }),
    ["maturity", "gap"].map((key) => {
      buttons[key] = h("button", {
        class: "dash-btn small",
        type: "button",
        "aria-pressed": "false",
        text: key === "maturity" ? "Current maturity" : "Gap to target (TOGAF)",
        onclick: () => {
          state.heatMode = key;
          draw();
        },
      });
      return buttons[key];
    }),
  );
  draw();
  return wrap;
}

function heatMap(groups, mode = "maturity") {
  if (!groups.length) return empty("No capabilities in the model.");
  return h(
    "div",
    { class: "heat-grid" },
    groups.map((g) =>
      h(
        "div",
        { class: "heat-group" },
        h(
          "div",
          { class: "heat-group-head" },
          h("span", { text: g.name }),
          h("span", { text: g.maturity !== null ? `${fmt.dec(g.maturity)} → ${fmt.dec(g.targetMaturity)}` : "not rated" }),
        ),
        g.capabilities.map((c) => {
          const level = maturityLevel(c.maturity);
          const band = GAP_BAND[c.gapBand] || ["neutral", "No target"];
          const tile = h(
            "div",
            {
              class: mode === "gap" ? `heat-tile gap-tile band-${band[0]}` : `heat-tile m${level}`,
              "aria-label": `${c.name}: maturity ${c.maturity ?? "not rated"}, target ${c.targetMaturity ?? "none"}`,
            },
            h("span", { class: "tile-name", text: c.name }),
            h(
              "span",
              { class: "tile-meta" },
              h("span", {
                text:
                  c.maturity === null
                    ? "Not rated"
                    : `${fmt.dec(c.maturity, 0)} → ${c.targetMaturity !== null ? fmt.dec(c.targetMaturity, 0) : "?"}`,
              }),
              mode === "gap" ? chip(band[0], band[1]) : null,
              c.isPriorityGap ? chip("warning", "Priority gap") : null,
              c.appRisk === "critical" || c.appRisk === "serious" ? chip(RISK[c.appRisk][0], "App risk") : null,
            ),
          );
          return tip(tile, capabilityTip(c));
        }),
      ),
    ),
  );
}

function valueStreamChart(stages, width) {
  if (!stages.length) return empty("No value stream stages with a stageOrder property in the model.");
  const labelW = Math.min(190, Math.round(width * 0.34));
  const right = 60;
  const top = 24;
  const rowH = 34;
  const plotW = Math.max(120, width - labelW - right);
  const x = (v) => labelW + ((v - 1) / 4) * plotW;
  const height = top + stages.length * rowH + 6;
  const root = svgRoot(width, height, "Current versus target maturity per value stream stage");
  for (let v = 1; v <= 5; v++) {
    root.append(s("line", { class: "grid-line", x1: x(v), x2: x(v), y1: top - 4, y2: height - 2 }));
    root.append(s("text", { class: "ax-text", x: x(v), y: 12, "text-anchor": "middle", text: v }));
  }
  stages.forEach((stage, i) => {
    const cy = top + i * rowH + rowH / 2;
    root.append(s("text", { class: "row-text", x: 0, y: cy + 4, text: truncate(stage.name, labelW - 10) }));
    if (stage.maturity === null) {
      root.append(s("text", { class: "val-text", x: labelW, y: cy + 4, text: "not rated" }));
      return;
    }
    const target = stage.targetMaturity ?? stage.maturity;
    const g = s("g", { class: "mark" });
    g.append(s("line", { x1: x(stage.maturity), x2: x(target), y1: cy, y2: cy, style: "stroke:var(--seq-3);stroke-width:2;stroke-linecap:round" }));
    g.append(s("circle", { cx: x(stage.maturity), cy, r: 5, style: "fill:var(--seq-2);stroke:var(--surface);stroke-width:2" }));
    g.append(s("circle", { cx: x(target), cy, r: 5, style: "fill:var(--seq-5);stroke:var(--surface);stroke-width:2" }));
    g.append(s("rect", { class: "hit", x: x(stage.maturity) - 12, y: cy - 12, width: Math.abs(x(target) - x(stage.maturity)) + 24, height: 24 }));
    root.append(g);
    root.append(s("text", { class: "val-text strong", x: labelW + plotW + 12, y: cy + 4, text: `+${fmt.dec(target - stage.maturity)}` }));
    tip(g, {
      title: stage.name,
      rows: [
        [fmt.dec(stage.maturity), "average current maturity"],
        [fmt.dec(stage.targetMaturity), "average target maturity"],
      ],
      notes: [`Capabilities: ${stage.capabilities.join(", ")}`],
    });
  });
  return root;
}

function strategySection(d) {
  const st = d.strategy;
  const leaves = st.groups.flatMap((g) => g.capabilities);
  const k = st.kpis;
  const heat = card({
    title: "Capability maturity heat map",
    subtitle:
      "Each tile is a leaf capability; the tile text shows current → target maturity. Colour by current maturity, or by the gap to target " +
      "(TOGAF Business Capabilities guide: at target, one level away, two or more levels away). " +
      "“Priority gap” = 2+ levels on a high-importance capability; “App risk” = an application realising it is past or near end of life or runs on unsupported technology.",
    chart: () => heatMapCard(st.groups),
    table: () =>
      dataTable(
        [
          { label: "Capability", value: (c) => c.name },
          { label: "Group", value: (c) => c.group },
          { label: "Current", num: true, value: (c) => fmt.dec(c.maturity, 0) },
          { label: "Target", num: true, value: (c) => fmt.dec(c.targetMaturity, 0) },
          { label: "Gap", num: true, value: (c) => fmt.dec(c.gap, 0) },
          { label: "Importance", value: (c) => IMPORTANCE_LABELS[c.importance] || "–" },
          { label: "Priority score", num: true, value: (c) => fmt.dec(c.priorityScore, 0) },
          { label: "Applications", value: (c) => c.applications.map((a) => a.name).join(", ") || "–" },
          { label: "App risk", value: (c) => statusChip(RISK, c.appRisk) },
        ],
        leaves,
      ),
    span: true,
  });
  const valueStream = card({
    title: "Maturity along the value stream",
    subtitle: "Average current and target maturity of the capabilities that serve each stage; the number on the right is the uplift still to deliver.",
    legend: legend([
      ["dot", "var(--seq-2)", "Current"],
      ["dot", "var(--seq-5)", "Target"],
    ]),
    chart: (w) => valueStreamChart(st.valueStream, w),
    table: () =>
      dataTable(
        [
          { label: "Stage", value: (r) => r.name },
          { label: "Current", num: true, value: (r) => fmt.dec(r.maturity) },
          { label: "Target", num: true, value: (r) => fmt.dec(r.targetMaturity) },
          { label: "Capabilities", value: (r) => r.capabilities.join(", ") },
        ],
        st.valueStream,
      ),
  });
  const coverage = card({
    title: "Application support of capabilities",
    subtitle: "Share of leaf capabilities realised by at least one application, directly or through a process it serves.",
    chart: () =>
      h(
        "div",
        { class: "meter-list" },
        meterRow({
          title: `${fmt.pct(k.coverageShare)} of capabilities supported`,
          sub: `${k.capabilityCount - k.unsupportedCapabilities.length} of ${k.capabilityCount} leaf capabilities`,
          value: k.coverageShare,
        }),
        k.unsupportedCapabilities.length
          ? h("div", {}, h("p", { class: "chart-note", text: "Without any supporting application:" }), h("ul", { class: "list-plain" }, k.unsupportedCapabilities.map((n) => h("li", { text: n }))))
          : h("p", { class: "chart-note", text: "Every capability has at least one supporting application." }),
      ),
  });
  return section(
    "strategy",
    "Strategy · Capability-based planning",
    `${k.ratedCount} of ${k.capabilityCount} leaf capabilities rated · average maturity ${fmt.dec(k.avgMaturity)} against a target of ${fmt.dec(k.avgTargetMaturity)} · ${d.crossLayer.kpis.priorityGaps} priority gaps.`,
    [heat, valueStream, coverage],
  );
}

// ---------------------------------------------------------------------------------------------
// Cross-layer: priority gaps versus investment
// ---------------------------------------------------------------------------------------------

function gapsSection(d) {
  const x = d.crossLayer;
  const rows = x.gapCoverage;
  const maxInvest = Math.max(0, ...rows.map((r) => r.investmentEUR || 0));
  const columns = [
    { label: "Capability", value: (r) => h("div", {}, h("div", { text: r.name }), h("div", { class: "meter-sub", text: r.group })) },
    { label: "Maturity gap", value: (r) => inlineBar(r.gap, 4, `${fmt.dec(r.maturity, 0)} → ${fmt.dec(r.targetMaturity, 0)}`) },
    { label: "Importance", value: (r) => IMPORTANCE_LABELS[r.importance] || "–" },
    { label: "Priority", num: true, value: (r) => fmt.dec(r.priorityScore, 0) },
    { label: "Allocated budget", value: (r) => inlineBar(r.investmentEUR, maxInvest, fmt.eur(r.investmentEUR), true) },
    {
      label: "Work packages",
      value: (r) => (r.workPackages.length ? r.workPackages.join(", ") : r.isPriorityGap ? chip("critical", "Unaddressed") : chip("neutral", "None")),
    },
    { label: "App risk", value: (r) => statusChip(RISK, r.appRisk) },
  ];
  let showAll = false;
  const limit = 12;
  const gapCard = card({
    title: "Priority gaps versus investment",
    subtitle:
      "Capabilities with a maturity gap, ordered by priority score (gap × strategic importance: high 3, medium 2, low 1). " +
      "Budget is the work-package budget associated with the capability, split evenly when a package changes several capabilities.",
    chart: () => {
      if (!rows.length) return empty("No capability has both a maturity and a target maturity yet.");
      const wrap = h("div", { class: "data-table-wrap" });
      const draw = () => {
        const visible = showAll ? rows : rows.slice(0, limit);
        wrap.replaceChildren(dataTable(columns, visible));
        if (rows.length > limit) {
          wrap.append(
            h("button", {
              class: "dash-btn small",
              type: "button",
              text: showAll ? "Show top 12" : `Show all ${rows.length}`,
              onclick: () => {
                showAll = !showAll;
                draw();
              },
            }),
          );
        }
      };
      draw();
      return wrap;
    },
    span: true,
  });
  const k = x.kpis;
  return section(
    "gaps",
    "Cross-layer · Are we investing where the gaps are?",
    `${k.priorityGapsAddressed} of ${k.priorityGaps} priority gaps have a work package` +
      (k.priorityGapsUnaddressed.length ? ` · unaddressed: ${k.priorityGapsUnaddressed.join(", ")}` : "") +
      (k.investedCapabilitiesWithoutGap.length ? ` · investment without a gap: ${k.investedCapabilitiesWithoutGap.join(", ")}` : ""),
    [gapCard],
  );
}

// ---------------------------------------------------------------------------------------------
// Business: media breaks and automation along the value stream
// ---------------------------------------------------------------------------------------------

function businessSection(d) {
  const b = d.business;
  const k = b.kpis;
  const stages = b.stages;
  const processes = stages.flatMap((st) => st.processes.map((p) => ({ ...p, stage: st.name })));
  const breaks = card({
    title: "Media breaks along the value stream",
    subtitle: "Sum of the mediaBreaks property of the processes in each stage: tool or data handovers without integration in the digital thread.",
    chart: (w) =>
      hbars(
        stages.map((st) => ({
          label: st.name,
          value: st.mediaBreaks,
          display: `${plural(st.mediaBreaks, "break")} · ${plural(st.processes.length, "process", "processes")}`,
          tip: {
            title: st.name,
            rows: [
              [fmt.int(st.mediaBreaks), "media breaks"],
              [fmt.dec(st.avgMaturity), "average process maturity"],
            ],
            notes: st.processes.map((p) => `${p.name}: ${fmt.int(p.mediaBreaks)}`),
          },
        })),
        w,
        { label: "Media breaks per value stream stage", valueWidth: 170 },
      ),
    table: () =>
      dataTable(
        [
          { label: "Stage", value: (r) => r.name },
          { label: "Processes", num: true, value: (r) => r.processes.length },
          { label: "Media breaks", num: true, value: (r) => fmt.int(r.mediaBreaks) },
          { label: "Avg. maturity", num: true, value: (r) => fmt.dec(r.avgMaturity) },
        ],
        stages,
      ),
  });
  const automationKeys = ["manual", "partial", "automated"];
  const autoColor = { manual: "var(--auto-1)", partial: "var(--auto-2)", automated: "var(--auto-3)" };
  const autoInk = { manual: "var(--seq-ink-1)", partial: "var(--seq-ink-3)", automated: "var(--seq-ink-5)" };
  const automation = card({
    title: "Automation level per stage",
    subtitle: "Number of processes by automationLevel; darker means more automated.",
    legend: legend(automationKeys.map((key) => ["rect", autoColor[key], AUTOMATION_LABELS[key]])),
    chart: (w) =>
      stackedBars(
        stages.map((st) => ({ label: st.name, values: st.automation })),
        automationKeys,
        w,
        {
          label: "Processes by automation level per stage",
          colorFor: (key) => autoColor[key],
          inkFor: (key) => autoInk[key],
          labelFor: (key) => AUTOMATION_LABELS[key],
        },
      ),
    table: () =>
      dataTable(
        [{ label: "Stage", value: (r) => r.name }, ...automationKeys.map((key) => ({ label: AUTOMATION_LABELS[key], num: true, value: (r) => r.automation[key] }))],
        stages,
      ),
  });
  const processTable = card({
    title: "Process scorecard",
    subtitle: "Rated business processes, most media breaks first.",
    chart: () =>
      processes.length
        ? h(
            "div",
            { class: "data-table-wrap" },
            dataTable(
              [
                { label: "Process", value: (p) => p.name },
                { label: "Stage", value: (p) => p.stage },
                { label: "Maturity", num: true, value: (p) => fmt.dec(p.maturity, 0) },
                { label: "Automation", value: (p) => AUTOMATION_LABELS[p.automation] || "–" },
                { label: "Media breaks", num: true, value: (p) => fmt.int(p.mediaBreaks) },
                { label: "Applications", value: (p) => p.applications.join(", ") || chip("warning", "None") },
              ],
              [...processes].sort((a, b2) => (b2.mediaBreaks || 0) - (a.mediaBreaks || 0)),
            ),
          )
        : empty("No business process carries maturity, automationLevel or mediaBreaks properties yet."),
    span: true,
  });
  return section(
    "business",
    "Business · Process performance",
    `${k.ratedCount} of ${k.processCount} processes rated · ${fmt.int(k.mediaBreaks)} media breaks · average maturity ${fmt.dec(k.avgMaturity)}` +
      (k.manualShare !== null ? ` · ${fmt.pct(k.manualShare)} manual` : "") +
      (k.unsupportedProcesses.length ? ` · without application support: ${k.unsupportedProcesses.join(", ")}` : ""),
    [breaks, automation, processTable],
  );
}

// ---------------------------------------------------------------------------------------------
// Application: lifecycle, end of life, TIME portfolio, cost
// ---------------------------------------------------------------------------------------------

function appTip(a) {
  return {
    title: a.name,
    rows: [
      [LIFECYCLE_LABELS[a.lifecycle] || null, "lifecycle"],
      [a.endOfLife ? fmt.day(a.endOfLife) : null, "end of life"],
      [TIME_LABELS[a.timeClassification] || null, "TIME decision"],
      [a.functionalFit !== null ? `${fmt.int(a.functionalFit)} · ${FUNCTIONAL_FIT[a.functionalFit] || ""}` : null, "functional fit"],
      [a.technicalFit !== null ? `${fmt.int(a.technicalFit)} · ${TECHNICAL_FIT[a.technicalFit] || ""}` : null, "technical fit"],
      [CRITICALITY_LABELS[a.criticality] || null, "business criticality"],
      [a.annualCostEUR !== null ? fmt.eur(a.annualCostEUR) : null, "annual cost"],
    ],
    notes: a.risk.reasons.length ? a.risk.reasons : ["No risk flags"],
  };
}

function eolChart(items, todayIso, width) {
  if (!items.length) return empty("No application carries an endOfLife date yet.");
  const labelW = Math.min(280, Math.round(width * 0.32));
  const right = 92;
  const top = 34;
  const rowH = 26;
  const first = items[0].endOfLife < todayIso ? items[0].endOfLife : todayIso;
  const last = items[items.length - 1].endOfLife;
  const start = `${Number(shiftMonths(first, -6).slice(0, 4))}-01-01`;
  const end = `${Number(shiftMonths(last > shiftMonths(todayIso, 24) ? last : shiftMonths(todayIso, 24), 6).slice(0, 4)) + 1}-01-01`;
  const x = timeScale(start, end, labelW, width - right);
  const height = top + items.length * rowH + 4;
  const root = svgRoot(width, height, "Application end-of-life timeline");
  yearAxis(root, x, top, height);
  // 12- and 24-month horizons as quiet bands behind the dots.
  root.append(s("rect", { x: x(todayIso), y: top, width: Math.max(0, x(shiftMonths(todayIso, 24)) - x(todayIso)), height: height - top, style: "fill:var(--grid);opacity:0.55" }));
  todayMarker(root, x, todayIso, top, height);
  items.forEach((a, i) => {
    const cy = top + i * rowH + rowH / 2;
    const status = a.endOfLife < todayIso ? "critical" : a.risk.reasons.some((r) => r.startsWith("End of life within 12")) ? "serious" : a.risk.reasons.some((r) => r.startsWith("End of life within 24")) ? "warning" : "neutral";
    root.append(s("text", { class: "row-text", x: 0, y: cy + 4, text: truncate(a.name, labelW - 10) }));
    const g = s("g", { class: "mark" });
    g.append(s("circle", { cx: x(a.endOfLife), cy, r: 5, style: `fill:var(--${status});stroke:var(--surface);stroke-width:2` }));
    g.append(s("rect", { class: "hit", x: x(a.endOfLife) - 12, y: cy - 12, width: 24, height: 24 }));
    root.append(g);
    root.append(s("text", { class: "val-text", x: width - right + 10, y: cy + 4 }, s("tspan", { style: `fill:var(--${status})`, text: `${STATUS_ICON[status]} ` }), fmt.month(a.endOfLife)));
    tip(g, appTip({ ...a, functionalFit: null, technicalFit: null, annualCostEUR: null }));
  });
  return root;
}

function timeMatrix(apps) {
  const rated = apps.filter((a) => a.functionalFit !== null && a.technicalFit !== null);
  if (!rated.length) return empty("No application carries both functionalFit and technicalFit yet.");
  const grid = h("div", { class: "time-matrix", role: "group", "aria-label": "TIME portfolio matrix: functional fit by technical fit" });
  const quadrantOf = (f, t) => (f >= 3 ? (t >= 3 ? "invest" : "migrate") : t >= 3 ? "tolerate" : "eliminate");
  const labelCell = { "1,4": "tolerate", "4,4": "invest", "1,1": "eliminate", "4,1": "migrate" };
  for (let t = 4; t >= 1; t--) {
    grid.append(h("div", { class: "time-axis", text: t, title: TECHNICAL_FIT[t] }));
    for (let f = 1; f <= 4; f++) {
      const quadrant = quadrantOf(f, t);
      const cell = h("div", { class: `time-cell${quadrant === "invest" || quadrant === "eliminate" ? " q-strong" : ""}` });
      const label = labelCell[`${f},${t}`];
      if (label) cell.append(h("span", { class: `q-label${t === 1 ? " bottom" : ""}`, text: TIME_LABELS[label] }));
      rated
        .filter((a) => clamp(Math.round(a.functionalFit), 1, 4) === f && clamp(Math.round(a.technicalFit), 1, 4) === t)
        .forEach((a) => {
          const mismatch = a.timeClassification && a.timeClassification !== a.fitQuadrant;
          const risk = a.risk.level === "critical" || a.risk.level === "serious" ? RISK[a.risk.level][0] : null;
          const node = h(
            "span",
            { class: `app-chip${mismatch ? " mismatch" : ""}` },
            risk ? h("span", { class: `icon-only st-${risk}`, "aria-label": RISK[a.risk.level][1], text: STATUS_ICON[risk] }) : null,
            h("span", { text: a.name }),
          );
          const content = appTip(a);
          if (mismatch) content.notes = [`Recorded decision ${TIME_LABELS[a.timeClassification]} differs from the fit-based quadrant ${TIME_LABELS[a.fitQuadrant]}`, ...content.notes];
          cell.append(tip(node, content));
        });
      grid.append(cell);
    }
  }
  grid.append(h("div"));
  for (let f = 1; f <= 4; f++) grid.append(h("div", { class: "time-axis", text: f, title: FUNCTIONAL_FIT[f] }));
  const unrated = apps.filter((a) => a.functionalFit === null || a.technicalFit === null);
  return h(
    "div",
    {},
    grid,
    h("div", { class: "axis-caption" }, h("span", { text: "↑ Technical fit (1 inappropriate – 4 fully appropriate)" }), h("span", { text: "Functional fit (1 unreasonable – 4 perfect) →" })),
    unrated.length ? h("p", { class: "chart-note", text: `Not rated: ${unrated.map((a) => a.name).join(", ")}` }) : null,
  );
}

function applicationSection(d) {
  const a = d.application;
  const k = a.kpis;
  const lifecycle = card({
    title: "Lifecycle distribution",
    subtitle: "Applications per lifecycle phase with their annual cost.",
    chart: (w) =>
      hbars(
        a.lifecycle.map((p) => ({
          label: p.label,
          value: p.count,
          display: `${fmt.int(p.count)} · ${fmt.eur(p.annualCostEUR)}`,
          tip: { title: p.label, rows: [[fmt.int(p.count), "applications"], [fmt.eur(p.annualCostEUR), "annual cost"]], notes: a.applications.filter((x) => x.lifecycle === p.phase).map((x) => x.name) },
        })),
        w,
        { label: "Applications per lifecycle phase" },
      ),
    table: () =>
      dataTable(
        [
          { label: "Phase", value: (r) => r.label },
          { label: "Applications", num: true, value: (r) => r.count },
          { label: "Annual cost", num: true, value: (r) => fmt.eur(r.annualCostEUR) },
        ],
        a.lifecycle,
      ),
    note: a.unknownLifecycle ? `${a.unknownLifecycle} application(s) without a lifecycle property.` : null,
  });
  const eol = card({
    title: "End-of-life timeline",
    subtitle: "Applications with an endOfLife date. The shaded band is the next 24 months.",
    legend: statusLegend([
      ["critical", "Past end of life"],
      ["serious", "Within 12 months"],
      ["warning", "Within 24 months"],
      ["neutral", "Later"],
    ]),
    chart: (w) => eolChart(a.endOfLife, d.asOf, w),
    table: () =>
      dataTable(
        [
          { label: "Application", value: (r) => r.name },
          { label: "End of life", num: true, value: (r) => fmt.day(r.endOfLife) },
          { label: "When", value: (r) => fmt.months(r.monthsToEndOfLife) },
          { label: "Lifecycle", value: (r) => LIFECYCLE_LABELS[r.lifecycle] || "–" },
          { label: "Criticality", value: (r) => CRITICALITY_LABELS[r.criticality] || "–" },
          { label: "Risk", value: (r) => statusChip(RISK, r.risk.level) },
        ],
        a.endOfLife,
      ),
    span: true,
  });
  const matrix = card({
    span: true,
    title: "TIME portfolio (Gartner)",
    subtitle:
      "Applications placed by functional and technical fit (1–4). Fit of 3 or more counts as high. A dashed chip means the recorded TIME decision differs from the fit-based quadrant.",
    chart: () => timeMatrix(a.applications),
    table: () =>
      dataTable(
        [
          { label: "Application", value: (r) => r.name },
          { label: "Functional fit", num: true, value: (r) => fmt.int(r.functionalFit) },
          { label: "Technical fit", num: true, value: (r) => fmt.int(r.technicalFit) },
          { label: "Fit quadrant", value: (r) => TIME_LABELS[r.fitQuadrant] || "–" },
          { label: "Recorded decision", value: (r) => TIME_LABELS[r.timeClassification] || "–" },
          { label: "Risk", value: (r) => statusChip(RISK, r.risk.level) },
        ],
        a.applications,
      ),
  });
  const cost = card({
    title: "Annual cost by TIME decision",
    subtitle: "Run cost of the portfolio by recorded timeClassification.",
    chart: (w) =>
      hbars(
        a.timeClasses.map((t) => ({
          label: `${t.label} · ${plural(t.count, "app")}`,
          value: t.annualCostEUR,
          display: fmt.eur(t.annualCostEUR),
          tip: { title: t.label, rows: [[fmt.eur(t.annualCostEUR), "annual cost"], [fmt.int(t.count), "applications"]] },
        })),
        w,
        { label: "Annual cost by TIME decision", valueWidth: 70 },
      ),
    table: () =>
      dataTable(
        [
          { label: "TIME", value: (r) => r.label },
          { label: "Applications", num: true, value: (r) => r.count },
          { label: "Annual cost", num: true, value: (r) => fmt.eur(r.annualCostEUR) },
        ],
        a.timeClasses,
      ),
    note: k.migrateEliminateCostShare !== null ? `${fmt.pct(k.migrateEliminateCostShare)} of the run cost (${fmt.eur(k.migrateEliminateCostEUR)}) sits in Migrate or Eliminate.` : null,
  });
  const redundancy = card({
    span: true,
    title: "Capabilities served by several applications",
    subtitle: "Leaf capabilities realised directly by two or more live applications (phase in, active or phase out): planned transitions or overlap worth a consolidation review.",
    chart: () =>
      a.redundancy.length
        ? h("dl", { class: "pair-list" }, a.redundancy.map((r) => h("div", {}, h("dt", { text: r.name }), h("dd", { text: r.applications.join(" · ") }))))
        : empty("No capability is realised by more than one live application."),
  });
  return section(
    "application",
    "Application · Portfolio health",
    `${k.applicationCount} applications · ${fmt.eur(k.annualCostEUR)} annual run cost · ${k.pastEndOfLife} past end of life · ${k.endOfLifeWithin24Months} reach end of life within 24 months · ${k.criticalAppsAtRisk} mission- or business-critical applications at high or critical risk.`,
    [eol, matrix, lifecycle, cost, redundancy],
  );
}

// ---------------------------------------------------------------------------------------------
// Technology: vendor support runway & radar
// ---------------------------------------------------------------------------------------------

function runwayChart(components, width) {
  const rows = components.filter((c) => c.monthsOfSupport !== null).sort((a, b) => a.monthsOfSupport - b.monthsOfSupport);
  if (!rows.length) return empty("No technology element carries a vendorSupportEnd date yet.");
  const labelW = Math.min(280, Math.round(width * 0.32));
  const right = 120;
  const top = 26;
  const rowH = 28;
  const barH = 14;
  const lo = Math.max(-60, Math.min(-12, ...rows.map((r) => r.monthsOfSupport)));
  const hi = Math.min(84, Math.max(24, ...rows.map((r) => r.monthsOfSupport)));
  const x0 = labelW;
  const x1 = width - right;
  const x = (m) => x0 + ((clamp(m, lo, hi) - lo) / (hi - lo)) * (x1 - x0);
  const height = top + rows.length * rowH + 4;
  const root = svgRoot(width, height, "Months of vendor support remaining per technology component");
  for (let m = Math.ceil(lo / 12) * 12; m <= hi; m += 12) {
    root.append(s("line", { class: "grid-line", x1: x(m), x2: x(m), y1: top - 4, y2: height }));
    root.append(s("text", { class: "ax-text", x: x(m), y: top - 8, "text-anchor": "middle", text: m === 0 ? "" : `${m > 0 ? "+" : ""}${m / 12}y` }));
  }
  root.append(s("line", { class: "today-line", x1: x(0), x2: x(0), y1: top - 18, y2: height }));
  root.append(s("text", { class: "today-text", x: x(0), y: top - 20, "text-anchor": "middle", text: "Today" }));
  rows.forEach((c, i) => {
    const y = top + i * rowH + (rowH - barH) / 2;
    const m = c.monthsOfSupport;
    const negative = m < 0;
    const length = x(m) - x(0);
    root.append(s("text", { class: "row-text", x: 0, y: y + barH - 3, text: truncate(c.name, labelW - 10) }));
    const g = s("g", { class: "mark" });
    if (Math.abs(length) >= 1) g.append(s("path", { d: barPath(x(0), y, length, barH, negative ? -1 : 1), style: `fill:var(${negative ? "--div-neg" : "--div-pos"})` }));
    g.append(s("rect", { class: "hit", x: Math.min(x(0), x(m)) - 6, y: y - 6, width: Math.abs(length) + 12, height: barH + 12 }));
    root.append(g);
    const [status] = SUPPORT[c.supportStatus];
    root.append(
      s(
        "text",
        { class: "val-text", x: width - right + 10, y: y + barH - 3 },
        s("tspan", { style: `fill:var(--${status})`, text: `${STATUS_ICON[status]} ` }),
        `${fmt.month(c.vendorSupportEnd)} · ${plural(c.applications.length, "app")}`,
      ),
    );
    tip(g, {
      title: c.name,
      rows: [
        [fmt.day(c.vendorSupportEnd), "vendor support end"],
        [fmt.months(m), negative ? "out of support since" : "support remaining"],
        [SUPPORT[c.supportStatus][1], "status"],
        [c.radar ? c.radar[0].toUpperCase() + c.radar.slice(1) : null, "technology radar"],
        [[c.vendor, c.version].filter(Boolean).join(" ") || null, "vendor / version"],
      ],
      notes: c.applications.length ? [`Serves: ${c.applications.map((a) => a.name).join(", ")}`] : ["Serves no application in the model"],
    });
  });
  return root;
}

function technologySection(d) {
  const t = d.technology;
  const k = t.kpis;
  const noDate = t.components.filter((c) => c.supportStatus === "unknown" && c.monthsOfSupport === null);
  const runway = card({
    title: "Vendor support runway",
    subtitle: "Months from today to each component's vendorSupportEnd: red bars are already out of support, blue bars show the remaining support window (clipped at −5 / +7 years).",
    legend: legend([
      ["rect", "var(--div-neg)", "Out of support (months since end)"],
      ["rect", "var(--div-pos)", "Support remaining"],
    ]),
    chart: (w) => runwayChart(t.components, w),
    table: () =>
      dataTable(
        [
          { label: "Component", value: (r) => r.name },
          { label: "Category", value: (r) => r.category || "–" },
          { label: "Support end", num: true, value: (r) => fmt.day(r.vendorSupportEnd) },
          { label: "Status", value: (r) => statusChip(SUPPORT, r.supportStatus) },
          { label: "Radar", value: (r) => r.radar || "–" },
          { label: "Applications served", value: (r) => r.applications.map((a) => a.name).join(", ") || "–" },
        ],
        t.components,
      ),
    note: noDate.length ? `No support date recorded: ${noDate.map((c) => c.name).join(", ")}.` : null,
    span: true,
  });
  const exposure = card({
    span: true,
    title: "Applications on out-of-support technology",
    subtitle: "Risk propagated upwards: applications served by a technology component whose vendor support has ended, most critical first.",
    chart: () => {
      const byApp = new Map();
      for (const c of t.components.filter((x) => x.supportStatus === "out")) {
        for (const app of c.applications) {
          const row = byApp.get(app.name) || { app: app.name, criticality: app.criticality, techs: [], earliest: c.vendorSupportEnd };
          row.techs.push(c.name);
          if (c.vendorSupportEnd && (!row.earliest || c.vendorSupportEnd < row.earliest)) row.earliest = c.vendorSupportEnd;
          byApp.set(app.name, row);
        }
      }
      const rows = [...byApp.values()];
      if (!rows.length) return empty("No application runs on out-of-support technology.");
      const order = ["mission-critical", "business-critical", "business-operational", "administrative"];
      const rank = (r) => (order.includes(r.criticality) ? order.indexOf(r.criticality) : order.length);
      rows.sort((a, b) => rank(a) - rank(b) || a.app.localeCompare(b.app));
      return h(
        "div",
        { class: "data-table-wrap" },
        dataTable(
          [
            { label: "Application", value: (r) => r.app },
            { label: "Criticality", value: (r) => CRITICALITY_LABELS[r.criticality] || "–" },
            { label: "Unsupported technology", value: (r) => r.techs.join(", ") },
            { label: "Out of support since", num: true, value: (r) => fmt.month(r.earliest) },
          ],
          rows,
        ),
      );
    },
  });
  const supportLabels = { out: "Out of support", "expiring-12": "Ends within 12 months", "expiring-24": "Ends within 24 months", supported: "Supported", unknown: "No support date" };
  const supportStatus = card({
    title: "Vendor support status",
    subtitle: "Technology platforms (nodes, devices, system software) by vendorSupportEnd relative to the as-of date.",
    chart: (w) =>
      hbars(
        t.supportBuckets.map((b) => ({
          label: supportLabels[b.status],
          status: SUPPORT[b.status][0],
          value: b.count,
          color: `var(--${SUPPORT[b.status][0]})`,
          tip: { title: supportLabels[b.status], rows: [[fmt.int(b.count), "platforms"]], notes: t.components.filter((c) => c.supportStatus === b.status).map((c) => c.name) },
        })),
        w,
        { label: "Technology platforms by vendor support status", valueWidth: 50 },
      ),
    table: () => dataTable([{ label: "Status", value: (r) => supportLabels[r.status] }, { label: "Platforms", num: true, value: (r) => r.count }], t.supportBuckets),
  });
  const radar = card({
    title: "Technology radar",
    subtitle: "Components per techRadar ring (adopt, trial, assess, hold).",
    chart: (w) =>
      hbars(
        t.radar.map((r) => ({
          label: r.ring[0].toUpperCase() + r.ring.slice(1),
          value: r.count,
          tip: { title: r.ring, rows: [[fmt.int(r.count), "components"]], notes: t.components.filter((c) => c.radar === r.ring).map((c) => c.name) },
        })),
        w,
        { label: "Technology components per radar ring", valueWidth: 50 },
      ),
    table: () => dataTable([{ label: "Ring", value: (r) => r.ring }, { label: "Components", num: true, value: (r) => r.count }], t.radar),
  });
  return section(
    "technology",
    "Technology · Obsolescence risk",
    `${k.outOfSupport} of ${k.withSupportDate} platforms with a support date are out of vendor support (${fmt.pct(k.outOfSupportShare)}) · ${k.expiringWithin12Months} more within 12 months · ${k.criticalApplicationsOnUnsupported.length} mission- or business-critical applications affected.`,
    [runway, supportStatus, radar, exposure],
  );
}

// ---------------------------------------------------------------------------------------------
// Motivation: outcome KPIs
// ---------------------------------------------------------------------------------------------

function meterRow({ title, chipNode, sub, value, expected, status, tipContent, note }) {
  const fillClass = status === "warning" || status === "serious" || status === "critical" ? ` st-${status}` : "";
  const track = h(
    "div",
    { class: "meter-track", role: "meter", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": value === null || value === undefined ? null : String(Math.round(clamp(value, 0, 1) * 100)) },
    value !== null && value !== undefined ? h("div", { class: `meter-fill${fillClass}`, style: { width: `${clamp(value, 0, 1) * 100}%` } }) : null,
    expected !== null && expected !== undefined ? h("div", { class: "meter-expected", style: { left: `calc(${clamp(expected, 0, 1) * 100}% - 1px)` }, title: "Expected progress for this date" }) : null,
  );
  if (tipContent) tip(track, tipContent);
  return h(
    "div",
    { class: "meter-row" },
    h("div", { class: "meter-top" }, h("strong", { text: title }), chipNode || null),
    track,
    sub ? h("div", { class: "meter-sub", text: sub }) : null,
    note ? h("div", { class: "meter-sub", text: note }) : null,
  );
}

const OUTCOME_ORDER = ["missed", "off-track", "at-risk", "tracking", "unknown", "on-track", "achieved"];

function motivationSection(d) {
  const m = d.motivation;
  const k = m.kpis;
  const outcomeList = [...m.outcomes].sort((a, b) => OUTCOME_ORDER.indexOf(a.status) - OUTCOME_ORDER.indexOf(b.status) || a.kpi.localeCompare(b.kpi));
  const outcomes = card({
    title: "Outcome KPIs: progress from baseline to target",
    subtitle:
      "Bar = share of the way from baseline to target that has been achieved (works for lower-is-better KPIs too). The black tick marks where the KPI should be by now, " +
      "based on the time elapsed between baselineDate and targetDate.",
    legend: statusLegend([
      ["good", "On track / achieved"],
      ["warning", "At risk (> 10 pts behind)"],
      ["critical", "Off track (> 25 pts behind) / missed"],
      ["neutral", "No baseline date"],
    ]),
    chart: () =>
      outcomeList.length
        ? h(
            "div",
            { class: "meter-list" },
            outcomeList.map((o) => {
              const [status, label] = OUTCOME[o.status] || OUTCOME.unknown;
              return meterRow({
                title: o.kpi,
                chipNode: chip(status, label),
                value: o.progress,
                expected: o.expectedProgress,
                status,
                sub: `${fmt.num(o.current, o.unit)} now · baseline ${fmt.num(o.baseline, o.unit)} → target ${fmt.num(o.target, o.unit)} by ${fmt.month(o.targetDate)} · ${fmt.pct(o.progress)} of the way`,
                note: `Outcome “${o.name}”${o.goals.length ? ` → ${o.goals.join(", ")}` : ""}`,
                tipContent: {
                  title: o.kpi,
                  rows: [
                    [fmt.pct(o.progress), "progress to target"],
                    [o.expectedProgress !== null ? fmt.pct(o.expectedProgress) : null, "expected by now"],
                    [o.direction === "lower" ? "Lower is better" : "Higher is better", "direction"],
                    [o.measuredAt ? fmt.day(o.measuredAt) : null, "last measured"],
                  ],
                  notes: [o.capabilities.length ? `Realised by: ${o.capabilities.join(", ")}` : null],
                },
              });
            }),
          )
        : empty("No Outcome element carries kpi / baseline / current / target properties yet."),
    table: () =>
      dataTable(
        [
          { label: "KPI", value: (r) => r.kpi },
          { label: "Baseline", num: true, value: (r) => fmt.num(r.baseline, r.unit) },
          { label: "Current", num: true, value: (r) => fmt.num(r.current, r.unit) },
          { label: "Target", num: true, value: (r) => fmt.num(r.target, r.unit) },
          { label: "Progress", num: true, value: (r) => fmt.pct(r.progress) },
          { label: "Expected", num: true, value: (r) => fmt.pct(r.expectedProgress) },
          { label: "Status", value: (r) => statusChip(OUTCOME, r.status) },
        ],
        outcomeList,
      ),
    span: true,
  });
  const goals = card({
    span: true,
    title: "Goals",
    subtitle: "Average progress of the outcomes that realise each goal, and the drivers behind it.",
    chart: () =>
      m.goals.length
        ? h(
            "div",
            { class: "meter-list two-col" },
            m.goals.map((g) =>
              meterRow({
                title: g.name,
                value: g.progress,
                sub: `${fmt.pct(g.progress)} average outcome progress · ${g.outcomes.length} outcome${g.outcomes.length === 1 ? "" : "s"}${g.targetDate ? ` · target ${fmt.month(g.targetDate)}` : ""}`,
                note: g.drivers.length ? `Drivers: ${g.drivers.join(", ")}` : null,
              }),
            ),
          )
        : empty("No goals in the model."),
  });
  return section(
    "motivation",
    "Motivation · Are the outcomes moving?",
    `${k.trackedCount} of ${k.outcomeCount} outcomes measurable · ${k.onTrackOrAchieved} on track or achieved · ${k.at_risk} at risk · ${k.off_track + k.missed} off track or missed` +
      (k.tracking ? ` · ${k.tracking} without a baseline date` : ""),
    [outcomes, goals],
  );
}

// ---------------------------------------------------------------------------------------------
// Implementation & Migration: roadmap and earned value
// ---------------------------------------------------------------------------------------------

const HEALTH = { green: ["good", "Green"], amber: ["warning", "Amber"], red: ["critical", "Red"] };

function wpTip(w) {
  return {
    title: w.name,
    rows: [
      [RAG[w.rag] ? RAG[w.rag][1] : null, "reported RAG"],
      [HEALTH[w.computedHealth] ? HEALTH[w.computedHealth][1] : null, "computed health (overdue, SPI, CPI)"],
      [`${fmt.day(w.startDate)} – ${fmt.day(w.endDate)}`, "planned"],
      [fmt.pct(w.progress), "complete"],
      [fmt.pct(w.plannedProgress), "planned by today"],
      [w.spi !== null ? fmt.dec(w.spi, 2) : null, "schedule performance (SPI)"],
      [w.cpi !== null ? fmt.dec(w.cpi, 2) : null, "cost performance (CPI)"],
      [w.budgetEUR !== null ? fmt.eur(w.budgetEUR) : null, "budget"],
      [w.actualCostEUR !== null ? fmt.eur(w.actualCostEUR) : null, "actual cost"],
      [w.estimateAtCompletionEUR !== null ? fmt.eur(w.estimateAtCompletionEUR) : null, "estimate at completion"],
      [w.owner, "owner"],
    ],
    notes: [
      w.capabilities.length ? `Uplifts: ${w.capabilities.join(", ")}` : null,
      w.changes.length ? `Changes: ${w.changes.join(", ")}` : null,
      w.overdue ? "Past its end date and not complete" : null,
    ],
  };
}

function roadmapChart(impl, width) {
  const wps = impl.workPackages.filter((w) => w.startDate && w.endDate);
  if (!wps.length) return empty("No work package carries startDate and endDate yet.");
  const todayIso = impl.timeline.today;
  const startIso = `${Number((impl.timeline.start < todayIso ? impl.timeline.start : todayIso).slice(0, 4))}-01-01`;
  const endIso = `${Number((impl.timeline.end > todayIso ? impl.timeline.end : todayIso).slice(0, 4)) + 1}-01-01`;
  const labelW = Math.min(340, Math.round(width * 0.3));
  const right = 132;
  const top = 34;
  const rowH = 28;
  const groupH = 26;
  const barH = 14;
  const x = timeScale(startIso, endIso, labelW, width - right);
  const groups = [];
  for (const w of wps) {
    const key = w.plateau || "No plateau";
    let group = groups.find((g) => g.name === key);
    if (!group) groups.push((group = { name: key, items: [] }));
    group.items.push(w);
  }
  const height = top + groups.length * groupH + wps.length * rowH + 6;
  const root = svgRoot(width, height, "Roadmap of work packages by plateau");
  yearAxis(root, x, top, height);
  for (const { iso } of x.years) {
    for (const q of [3, 6, 9]) {
      const qIso = shiftMonths(iso, q);
      if (qIso < endIso) root.append(s("line", { class: "grid-line", x1: x(qIso), x2: x(qIso), y1: top, y2: height, style: "opacity:0.45" }));
    }
  }
  let y = top;
  for (const group of groups) {
    const plateau = impl.plateaus.find((p) => p.name === group.name);
    root.append(s("text", { class: "row-text", x: 0, y: y + groupH - 8, style: "font-weight:600", text: `${group.name}${plateau && plateau.targetDate ? ` · ${fmt.month(plateau.targetDate)}` : ""}` }));
    y += groupH;
    for (const w of group.items) {
      const by = y + (rowH - barH) / 2;
      const xs = x(w.startDate);
      const xe = Math.max(xs + 2, x(w.endDate));
      root.append(s("text", { class: "row-text muted", x: 10, y: by + barH - 3, text: truncate(w.name, labelW - 18) }));
      const g = s("g", { class: "mark" });
      g.append(s("rect", { x: xs, y: by, width: xe - xs, height: barH, rx: 4, style: "fill:var(--series-soft)" }));
      if (w.progress) g.append(s("rect", { x: xs, y: by, width: Math.max(2, (xe - xs) * w.progress), height: barH, rx: 4, style: "fill:var(--series)" }));
      g.append(s("rect", { class: "hit", x: xs - 4, y: y, width: xe - xs + 8, height: rowH }));
      root.append(g);
      tip(g, wpTip(w));
      const health = w.overdue ? ["critical", "Overdue"] : RAG[w.rag] || ["neutral", "No RAG"];
      root.append(
        s(
          "text",
          { class: "val-text", x: width - right + 10, y: by + barH - 3 },
          s("tspan", { style: `fill:var(--${health[0]})`, text: `${STATUS_ICON[health[0]]} ` }),
          `${health[1]} · ${fmt.pct(w.progress)}`,
        ),
      );
      y += rowH;
    }
  }
  todayMarker(root, x, todayIso, top, height);
  return root;
}

function evScatter(wps, width) {
  const points = wps.filter((w) => w.spi !== null && w.cpi !== null);
  if (!points.length) return empty("Needs work packages with progress, dates, budget and actual cost.");
  const pad = { l: 44, r: 16, t: 14, b: 34 };
  const height = 300;
  const lo = Math.min(0.4, ...points.map((p) => Math.min(p.spi, p.cpi) - 0.1));
  const hi = Math.max(1.4, ...points.map((p) => Math.max(p.spi, p.cpi) + 0.1));
  const x = (v) => pad.l + ((clamp(v, lo, hi) - lo) / (hi - lo)) * (width - pad.l - pad.r);
  const y = (v) => height - pad.b - ((clamp(v, lo, hi) - lo) / (hi - lo)) * (height - pad.t - pad.b);
  const root = svgRoot(width, height, "Schedule performance index versus cost performance index per work package");
  const ticks = [];
  for (let v = Math.ceil(lo * 4) / 4; v <= hi + 1e-9; v += 0.25) ticks.push(Math.round(v * 100) / 100);
  for (const v of ticks) {
    root.append(s("line", { class: "grid-line", x1: x(v), x2: x(v), y1: pad.t, y2: height - pad.b }));
    root.append(s("line", { class: "grid-line", x1: pad.l, x2: width - pad.r, y1: y(v), y2: y(v) }));
    root.append(s("text", { class: "ax-text", x: x(v), y: height - pad.b + 16, "text-anchor": "middle", text: v.toFixed(2) }));
    root.append(s("text", { class: "ax-text", x: pad.l - 6, y: y(v) + 4, "text-anchor": "end", text: v.toFixed(2) }));
  }
  root.append(s("line", { class: "ax-line", x1: x(1), x2: x(1), y1: pad.t, y2: height - pad.b }));
  root.append(s("line", { class: "ax-line", x1: pad.l, x2: width - pad.r, y1: y(1), y2: y(1) }));
  root.append(s("text", { class: "ax-text", x: width - pad.r, y: pad.t + 10, "text-anchor": "end", text: "Ahead · under budget" }));
  root.append(s("text", { class: "ax-text", x: pad.l + 6, y: height - pad.b - 8, text: "Behind · over budget" }));
  root.append(s("text", { class: "ax-text", x: (pad.l + width - pad.r) / 2, y: height - 4, "text-anchor": "middle", text: "Schedule performance (SPI = earned / planned value) →" }));
  const worst = [...points].sort((a, b) => a.spi * a.cpi - b.spi * b.cpi).slice(0, 1).map((p) => p.id);
  for (const p of points) {
    const g = s("g", { class: "mark" });
    g.append(s("circle", { cx: x(p.spi), cy: y(p.cpi), r: 12, class: "hit" }));
    g.append(s("circle", { cx: x(p.spi), cy: y(p.cpi), r: 5, style: "fill:var(--series);stroke:var(--surface);stroke-width:2" }));
    root.append(g);
    tip(g, wpTip(p));
    if (worst.includes(p.id) && p.spi * p.cpi < 1) {
      // Leader line into the empty band above the x axis (or below the top edge for a point
      // that sits low), so the label never lands on neighbouring dots.
      const px = x(p.spi);
      const py = y(p.cpi);
      const below = height - pad.b - 26;
      const labelY = py + 7 < below - 10 ? below : pad.t + 12;
      const anchor = px > width * 0.6 ? "end" : "start";
      root.append(s("line", { class: "ax-line", x1: px, x2: px, y1: labelY > py ? py + 7 : py - 7, y2: labelY > py ? labelY - 11 : labelY + 4 }));
      root.append(s("text", { class: "val-text strong", x: px + (anchor === "end" ? -4 : 4), y: labelY, "text-anchor": anchor, text: `Lowest: ${truncate(p.name, 230, 11.5)}` }));
    }
  }
  root.append(s("text", { class: "ax-text", x: 12, y: pad.t + 2, transform: `rotate(-90 12 ${pad.t + 2})`, "text-anchor": "end", text: "Cost performance (CPI) →" }));
  return root;
}

function implementationSection(d) {
  const impl = d.implementation;
  const k = impl.kpis;
  const roadmap = card({
    title: "Roadmap by plateau",
    subtitle: "Light bar = planned span, dark fill = percent complete laid onto that span. A fill that ends left of the today line is behind schedule.",
    legend: h(
      "div",
      {},
      legend([
        ["rect", "var(--series-soft)", "Planned span"],
        ["rect", "var(--series)", "Complete"],
      ]),
      statusLegend([
        ["good", "Green"],
        ["warning", "Amber"],
        ["critical", "Red / overdue"],
      ]),
    ),
    chart: (w) => roadmapChart(impl, w),
    table: () =>
      dataTable(
        [
          { label: "Work package", value: (r) => r.name },
          { label: "Plateau", value: (r) => r.plateau || "–" },
          { label: "Start", num: true, value: (r) => fmt.day(r.startDate) },
          { label: "End", num: true, value: (r) => fmt.day(r.endDate) },
          { label: "Complete", num: true, value: (r) => fmt.pct(r.progress) },
          { label: "Planned", num: true, value: (r) => fmt.pct(r.plannedProgress) },
          { label: "Reported RAG", value: (r) => (r.overdue ? chip("critical", "Overdue") : statusChip(RAG, r.rag)) },
          { label: "Computed health", value: (r) => (r.computedHealth ? statusChip(HEALTH, r.computedHealth) : "–") },
        ],
        impl.workPackages,
      ),
    span: true,
  });
  const scatter = card({
    title: "Schedule and cost performance",
    subtitle: "Earned-value view per work package: SPI = earned / planned value, CPI = earned value / actual cost. Below 1.0 means behind plan or over budget.",
    chart: (w) => evScatter(impl.workPackages, w),
    table: () =>
      dataTable(
        [
          { label: "Work package", value: (r) => r.name },
          { label: "SPI", num: true, value: (r) => fmt.dec(r.spi, 2) },
          { label: "CPI", num: true, value: (r) => fmt.dec(r.cpi, 2) },
          { label: "Budget", num: true, value: (r) => fmt.eur(r.budgetEUR) },
          { label: "Actual", num: true, value: (r) => fmt.eur(r.actualCostEUR) },
          { label: "EAC", num: true, value: (r) => fmt.eur(r.estimateAtCompletionEUR) },
        ],
        impl.workPackages,
      ),
  });
  const totals = card({
    title: "Programme totals",
    subtitle: "Sums over all work packages with budget, actual cost and progress.",
    chart: () =>
      h(
        "div",
        { class: "data-table-wrap" },
        dataTable(
          [
            { label: "Measure", value: (r) => r[0] },
            { label: "Value", num: true, value: (r) => r[1] },
          ],
          [
            ["Budget", fmt.eur(k.budgetEUR)],
            ["Planned value (by today)", fmt.eur(k.plannedValueEUR)],
            ["Earned value", fmt.eur(k.earnedValueEUR)],
            ["Actual cost", fmt.eur(k.actualCostEUR)],
            ["Schedule performance (SPI)", fmt.dec(k.spi, 2)],
            ["Cost performance (CPI)", fmt.dec(k.cpi, 2)],
            ["Budget burn", fmt.pct(k.burn)],
            ["Work packages red / amber / green", `${k.rag_red} / ${k.rag_amber} / ${k.rag_green}`],
            ["Overdue work packages", fmt.int(k.overdue)],
          ],
        ),
      ),
  });
  return section(
    "implementation",
    "Implementation & Migration · Roadmap execution",
    `${k.workPackageCount} work packages · ${fmt.pct(k.progress)} complete (earned value) against ${fmt.pct(k.plannedProgress)} planned · SPI ${fmt.dec(k.spi, 2)} · CPI ${fmt.dec(k.cpi, 2)} · ${k.overdue} overdue.`,
    [roadmap, scatter, totals],
  );
}

// ---------------------------------------------------------------------------------------------
// Data completeness
// ---------------------------------------------------------------------------------------------

function qualitySection(d) {
  const rows = d.dataQuality;
  const quality = card({
    title: "Property completeness per element type",
    subtitle: "Elements that carry every property a metric needs. Incomplete elements are left out of the corresponding charts rather than guessed.",
    chart: () =>
      h(
        "div",
        { class: "meter-list" },
        rows.map((r) =>
          meterRow({
            title: `${r.label} · ${r.layer}`,
            value: r.total ? r.complete / r.total : null,
            sub: r.total ? `${r.complete} of ${r.total} complete (${fmt.pct(r.complete / r.total)})` : "None in the model",
            chipNode: h("span", { class: "key-chips" }, r.keys.map((key) => h("span", { class: "chip", text: `${key.key} ${key.filled}/${r.total}` }))),
          }),
        ),
      ),
    table: () =>
      dataTable(
        [
          { label: "Element type", value: (r) => r.label },
          { label: "Total", num: true, value: (r) => r.total },
          { label: "Complete", num: true, value: (r) => r.complete },
          { label: "Missing per property", value: (r) => r.keys.filter((key) => key.filled < r.total).map((key) => `${key.key}: ${r.total - key.filled}`).join(", ") || "–" },
        ],
        rows,
      ),
    span: true,
  });
  return section("quality", "Data completeness", "How much of the model already carries the dashboard's property schema.", [quality]);
}

// ---------------------------------------------------------------------------------------------
// Loading & rendering
// ---------------------------------------------------------------------------------------------

function renderAll() {
  state.cards.forEach((render) => render());
}

function render(data) {
  state.data = data;
  state.cards = [];
  const m = data.model;
  const fetched = new Date(data.fetchedAt);
  ui.modelLine.textContent =
    `${m.name} · ${fmt.int(m.elementCount)} elements · ${fmt.int(m.relationshipCount)} relationships · ` +
    `as of ${fmt.day(data.asOf)} · read live from Archi via MCP at ${fetched.toLocaleTimeString("en-GB")}`;
  document.title = `Transformation Dashboard · ${m.name}`;
  renderKpis(data.headline);
  ui.sections.replaceChildren(
    strategySection(data),
    gapsSection(data),
    businessSection(data),
    applicationSection(data),
    technologySection(data),
    motivationSection(data),
    implementationSection(data),
    qualitySection(data),
  );
  renderAll();
}

function showError(message) {
  ui.error.replaceChildren(
    h("strong", { text: "Could not load the dashboard" }),
    h("span", { text: message }),
    h("div", { class: "dash-subtle", text: "Check that Archi is running with a model open and that MCP Server › Start MCP Server is active, then refresh." }),
  );
  ui.error.hidden = false;
}

async function load() {
  if (state.loading) return;
  state.loading = true;
  ui.refresh.disabled = true;
  ui.main.classList.add("is-loading");
  ui.main.setAttribute("aria-busy", "true");
  try {
    const params = new URLSearchParams();
    if (ui.asOf.value) params.set("asOf", ui.asOf.value);
    const response = await fetch(`/api/dashboard?${params}`);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    ui.error.hidden = true;
    render(body);
  } catch (error) {
    showError(error.message || String(error));
    if (!state.data) ui.modelLine.textContent = "Not connected";
  } finally {
    state.loading = false;
    ui.refresh.disabled = false;
    ui.main.classList.remove("is-loading");
    ui.main.setAttribute("aria-busy", "false");
  }
}

function todayLocalIso() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

ui.asOf.value = todayLocalIso();
ui.asOf.addEventListener("change", load);
ui.refresh.addEventListener("click", load);
window.addEventListener("resize", () => {
  clearTimeout(state.resizeTimer);
  state.resizeTimer = setTimeout(() => state.data && renderAll(), 150);
});
window.addEventListener("scroll", hideTip, { passive: true });
load();
