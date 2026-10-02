"use strict";

// Transformation Dashboard. All figures come from GET /api/dashboard, which reads the active Archi
// model live via MCP and follows the relationships of the governance meta-model; nothing here is
// stored or entered by hand. Charts are plain SVG/HTML built with DOM APIs (model names are
// untrusted text -> textContent only, never innerHTML).

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

// Bar with a 4px rounded data-end and a square baseline end.
function barPath(x0, y, length, height) {
  const w = Math.abs(length);
  if (w <= 0) return "";
  const r = Math.min(4, w, height / 2);
  return `M${x0},${y} h${w - r} a${r},${r} 0 0 1 ${r},${r} v${height - 2 * r} a${r},${r} 0 0 1 ${-r},${r} h${-(w - r)} Z`;
}

// ---------------------------------------------------------------------------------------------
// Formatting & vocabularies
// ---------------------------------------------------------------------------------------------

const NUM = new Intl.NumberFormat("en-GB");

const fmt = {
  int: (v) => (v === null || v === undefined ? "–" : NUM.format(Math.round(v))),
  dec: (v, digits = 1) => (v === null || v === undefined ? "–" : Number(v).toFixed(digits)),
  pct: (v) => (v === null || v === undefined ? "–" : `${Math.round(v * 100)}%`),
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
const OUTCOME = {
  achieved: ["good", "Achieved"],
  "on-track": ["good", "On track"],
  "at-risk": ["warning", "At risk"],
  "off-track": ["critical", "Off track"],
  missed: ["critical", "Target date missed"],
  tracking: ["neutral", "No baseline date"],
  unknown: ["neutral", "No KPI data"],
};
const ROADMAP = {
  unplanned: ["critical", "Not in roadmap"],
  planned: ["neutral", "Planned, not started"],
  "in-delivery": ["neutral", "In delivery"],
  delivered: ["good", "Delivered"],
  "at-target": ["good", "At target"],
  "not-rated": ["neutral", "Not rated"],
};
const WORK_STATUS = { planned: "Planned", "in-progress": "In progress", completed: "Completed", "on-hold": "On hold" };
const WORK_COLOR = { planned: "var(--auto-1)", "in-progress": "var(--auto-2)", completed: "var(--auto-3)" };
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

function tableCard(columns, rows, message) {
  return rows.length ? h("div", { class: "data-table-wrap" }, dataTable(columns, rows)) : empty(message);
}

function empty(message) {
  return h("div", { class: "chart-empty", text: message });
}

function inlineBar(value, max, label) {
  const width = max > 0 && value ? Math.max(2, Math.round((value / max) * 110)) : 0;
  return h("div", { class: "inline-bar" }, width ? h("span", { class: "bar", style: { width: `${width}px` } }) : null, h("span", { text: label }));
}

function nameList(items, message) {
  return items.length ? h("ul", { class: "list-plain" }, items.map((n) => h("li", { text: n }))) : h("p", { class: "chart-note", text: message });
}

// ---------------------------------------------------------------------------------------------
// Generic charts
// ---------------------------------------------------------------------------------------------

function hbars(rows, width, { label = "Bar chart", color = "var(--series)", labelWidth, valueWidth = 120 } = {}) {
  if (!rows.length) return empty("No data in the model yet.");
  const labelW = labelWidth ?? Math.min(220, Math.round(width * 0.36));
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
    root.append(s("text", { class: "row-text", x: labelW - 10, y: y + barH - 3, "text-anchor": "end", text: truncate(row.label, labelW - 14) }));
    const g = s("g", { class: "mark" });
    if (barW) g.append(s("path", { d: barPath(labelW, y, barW, barH), style: `fill:${row.color || color}` }));
    g.append(s("rect", { class: "hit", x: labelW, y: i * rowH, width: plotW + valueWidth, height: rowH }));
    root.append(g);
    root.append(s("text", { class: "val-text", x: labelW + barW + 6, y: y + barH - 3, text: row.display ?? fmt.int(row.value) }));
    if (row.tip) tip(g, row.tip);
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

function ratio(part, whole) {
  return whole ? part / whole : null;
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
// Motivation: outcome KPIs and goals
// ---------------------------------------------------------------------------------------------

const OUTCOME_ORDER = ["missed", "off-track", "at-risk", "tracking", "unknown", "on-track", "achieved"];

function motivationSection(d) {
  const m = d.motivation;
  const k = m.kpis;
  const outcomeList = [...m.outcomes].sort((a, b) => OUTCOME_ORDER.indexOf(a.status) - OUTCOME_ORDER.indexOf(b.status) || a.kpi.localeCompare(b.kpi));
  const outcomes = card({
    span: true,
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
                note: `Outcome “${o.name}”${o.goals.length ? ` → ${o.goals.join(", ")}` : ""}${o.capabilities.length ? ` · realised by ${o.capabilities.join(", ")}` : ""}`,
                tipContent: {
                  title: o.kpi,
                  rows: [
                    [fmt.pct(o.progress), "progress to target"],
                    [o.expectedProgress !== null ? fmt.pct(o.expectedProgress) : null, "expected by now"],
                    [o.direction === "lower" ? "Lower is better" : "Higher is better", "direction"],
                    [o.measuredAt ? fmt.day(o.measuredAt) : null, "last measured"],
                  ],
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
  });
  const goals = card({
    span: true,
    title: "Goals",
    subtitle: "Average progress of the outcomes that realise each goal.",
    chart: () =>
      m.goals.length
        ? h(
            "div",
            { class: "meter-list two-col" },
            m.goals.map((g) =>
              meterRow({
                title: g.name,
                value: g.progress,
                sub: g.outcomes.length
                  ? `${fmt.pct(g.progress)} average outcome progress · ${plural(g.outcomes.length, "outcome")}${g.targetDate ? ` · target ${fmt.month(g.targetDate)}` : ""}`
                  : "No outcome realises this goal yet",
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
// Strategy: capability maturity heat map & support
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

function capabilityTip(c) {
  const [, roadmapLabel] = ROADMAP[c.roadmapStatus] || ["neutral", c.roadmapStatus];
  return {
    title: c.name,
    rows: [
      [c.maturity !== null ? `${fmt.dec(c.maturity, 0)} · ${MATURITY_LABELS[maturityLevel(c.maturity)]}` : null, "current maturity"],
      [c.targetMaturity !== null ? `${fmt.dec(c.targetMaturity, 0)} · ${MATURITY_LABELS[maturityLevel(c.targetMaturity)]}` : null, "target maturity"],
      [IMPORTANCE_LABELS[c.importance] || null, "strategic importance"],
      [roadmapLabel, "roadmap"],
    ],
    notes: [
      c.processes.length ? `Realised by: ${c.processes.join(", ")}` : "No business process realises this capability",
      c.applications.length
        ? `Applications: ${c.applications.map((a) => `${a.name}${a.risk !== "ok" ? ` (${RISK[a.risk][1].toLowerCase()})` : ""}`).join(", ")}`
        : null,
      c.plateaus.length ? `Plateau: ${c.plateaus.map((p) => `${p.name} (${fmt.month(p.targetDate)})`).join(", ")}` : null,
    ],
  };
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

function heatMap(groups, mode) {
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
              c.roadmapStatus === "unplanned" ? chip("critical", "Not in roadmap") : null,
            ),
          );
          return tip(tile, capabilityTip(c));
        }),
      ),
    ),
  );
}

function strategySection(d) {
  const st = d.strategy;
  const leaves = st.groups.flatMap((g) => g.capabilities);
  const k = st.kpis;
  const heat = card({
    title: "Capability maturity heat map",
    subtitle:
      "Each tile is a capability, grouped by capabilityDomain; the text shows current → target maturity. Colour by current maturity, or by the gap to target " +
      "(TOGAF Business Capabilities guide: at target, one level away, two or more levels away). “Priority gap” = 2+ levels on a high-importance capability; " +
      "“Not in roadmap” = a gap that no plateau realises.",
    chart: () => heatMapCard(st.groups),
    table: () =>
      dataTable(
        [
          { label: "Capability", value: (c) => c.name },
          { label: "Domain", value: (c) => c.group },
          { label: "Current", num: true, value: (c) => fmt.dec(c.maturity, 0) },
          { label: "Target", num: true, value: (c) => fmt.dec(c.targetMaturity, 0) },
          { label: "Importance", value: (c) => IMPORTANCE_LABELS[c.importance] || "–" },
          { label: "Roadmap", value: (c) => statusChip(ROADMAP, c.roadmapStatus) },
          { label: "Realised by processes", value: (c) => c.processes.join(", ") || "–" },
        ],
        leaves,
      ),
    span: true,
  });
  const support = card({
    span: true,
    title: "Capability support along the meta-model",
    subtitle: "Capability ← realised by a business process ← served by an application service ← realised by an application component.",
    chart: () =>
      h(
        "div",
        { class: "meter-list two-col" },
        h(
          "div",
          { class: "meter-row" },
          meterRow({
            title: `${fmt.pct(k.processCoverage)} realised by at least one business process`,
            value: k.processCoverage,
            sub: `${k.capabilityCount - k.withoutProcess.length} of ${k.capabilityCount} capabilities`,
          }),
          nameList(k.withoutProcess, "Every capability is realised by a process."),
        ),
        h(
          "div",
          { class: "meter-row" },
          meterRow({
            title: `${fmt.pct(k.applicationCoverage)} supported by applications`,
            value: k.applicationCoverage,
            sub: `${Math.round((k.applicationCoverage || 0) * k.capabilityCount)} of ${k.capabilityCount} capabilities · listed below: realised by a process, but no application service serves it`,
          }),
          nameList(k.withoutApplication, "Every realised capability has application support."),
        ),
      ),
  });
  return section(
    "strategy",
    "Strategy · Capability-based planning",
    `${k.ratedCount} of ${k.capabilityCount} capabilities rated · average maturity ${fmt.dec(k.avgMaturity)} against a target of ${fmt.dec(k.avgTargetMaturity)} · ${k.priorityGapCount} priority gaps.`,
    [heat, support],
  );
}

// ---------------------------------------------------------------------------------------------
// Gaps & roadmap: are the capability gaps covered by plateaus and work packages?
// ---------------------------------------------------------------------------------------------

function gapsSection(d) {
  const rc = d.roadmapCoverage;
  const rows = rc.capabilityGaps;
  const columns = [
    { label: "Capability", value: (r) => h("div", {}, h("div", { text: r.name }), h("div", { class: "meter-sub", text: r.group })) },
    { label: "Maturity gap", value: (r) => inlineBar(r.gap, 4, `${fmt.dec(r.maturity, 0)} → ${fmt.dec(r.targetMaturity, 0)}`) },
    { label: "Importance", value: (r) => IMPORTANCE_LABELS[r.importance] || "–" },
    { label: "Priority", num: true, value: (r) => fmt.dec(r.priorityScore, 0) },
    { label: "Plateau", value: (r) => (r.plateaus.length ? r.plateaus.map((p) => `${p.name} (${fmt.month(p.targetDate)})`).join(", ") : "–") },
    {
      // The meta-model links work packages to plateaus only, so a capability's work packages are
      // all packages realising its plateau -- summarised here; the names are in the title.
      label: "Work packages of the plateau",
      value: (r) =>
        r.workPackages.length
          ? h("span", {
              title: r.workPackages.map((w) => `${w.name} (${WORK_STATUS[w.status] || "no status"})`).join("\n"),
              text: `${plural(r.workPackages.length, "package")} · ${r.workPackages.filter((w) => w.status === "completed").length} completed · ${r.workPackages.filter((w) => w.status === "in-progress").length} in progress`,
            })
          : "–",
    },
    { label: "Roadmap", value: (r) => statusChip(ROADMAP, r.roadmapStatus) },
  ];
  let showAll = false;
  const limit = 12;
  const gapCard = card({
    title: "Capability gaps and the plateaus that close them",
    subtitle:
      "Capabilities below target maturity, ordered by priority score (gap × strategic importance: high 3, medium 2, low 1). A gap is in the roadmap when a plateau realises the capability; " +
      "its work packages are the ones realising that plateau.",
    chart: () => {
      if (!rows.length) return empty("No capability has both a maturity and a target maturity yet.");
      const wrap = h("div", { class: "data-table-wrap" });
      const draw = () => {
        wrap.replaceChildren(dataTable(columns, showAll ? rows : rows.slice(0, limit)));
        if (rows.length > limit) {
          wrap.append(
            h("button", {
              class: "dash-btn small",
              type: "button",
              text: showAll ? `Show top ${limit}` : `Show all ${rows.length}`,
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
  const k = rc.kpis;
  return section(
    "gaps",
    "Gaps & roadmap · Are the gaps that matter planned?",
    `${k.priorityGapsPlanned} of ${k.priorityGaps} priority gaps are planned in a plateau` +
      (k.priorityGapsUnplanned.length ? ` · not in the roadmap: ${k.priorityGapsUnplanned.join(", ")}` : "") +
      ` · ${k.unplannedGaps} of ${k.capabilitiesWithGap} capability gaps unplanned in total.`,
    [gapCard],
  );
}

// ---------------------------------------------------------------------------------------------
// Business: As-Is / To-Be from the assessment, process ratings
// ---------------------------------------------------------------------------------------------

function businessSection(d) {
  const b = d.business;
  const k = b.kpis;
  const rated = b.processes.filter((p) => p.rated).sort((a, c) => (c.mediaBreaks || 0) - (a.mediaBreaks || 0));
  const traceability = card({
    title: "As-Is → To-Be traceability",
    subtitle: "From the assessment's Mapping & Gap Analysis: To-Be processes (status = target) linked to the As-Is process they replace, and gaps affecting processes.",
    chart: () =>
      h(
        "div",
        { class: "meter-list" },
        meterRow({ title: "To-Be processes traced to an As-Is process", value: ratio(k.toBeTraced, k.toBeCount), sub: `${k.toBeTraced} of ${k.toBeCount} To-Be processes` }),
        meterRow({ title: "As-Is processes covered by a To-Be process", value: ratio(k.asIsCovered, k.asIsCount), sub: `${k.asIsCovered} of ${k.asIsCount} As-Is processes` }),
        meterRow({ title: "Processes affected by a gap", value: ratio(k.processesWithGaps, k.processCount), sub: `${k.processesWithGaps} of ${k.processCount} processes` }),
      ),
  });
  const breaks = card({
    title: "Media breaks per process phase",
    subtitle: "Sum of the mediaBreaks property of the rated processes: tool or data handovers without integration in the digital thread.",
    chart: (w) =>
      hbars(
        b.phases.map((ph) => ({
          label: ph.name,
          value: ph.mediaBreaks,
          display: `${plural(ph.mediaBreaks, "break")} · ${plural(ph.processes.length, "process", "processes")}`,
          tip: { title: ph.name, rows: [[fmt.int(ph.mediaBreaks), "media breaks"]], notes: ph.processes },
        })),
        w,
        { label: "Media breaks per process phase", valueWidth: 170 },
      ),
    table: () =>
      dataTable(
        [
          { label: "Phase", value: (r) => r.name },
          { label: "Processes", num: true, value: (r) => r.processes.length },
          { label: "Media breaks", num: true, value: (r) => fmt.int(r.mediaBreaks) },
        ],
        b.phases,
      ),
  });
  const scorecard = card({
    span: true,
    title: "Process scorecard",
    subtitle: "Processes with maturity, automationLevel or mediaBreaks, most media breaks first.",
    chart: () =>
      tableCard(
        [
          { label: "Process", value: (p) => p.name },
          { label: "Phase", value: (p) => p.phase || "–" },
          { label: "State", value: (p) => (p.state === "to-be" ? "To\u2011Be" : "As\u2011Is") },
          { label: "Maturity", num: true, value: (p) => fmt.dec(p.maturity, 0) },
          { label: "Automation", value: (p) => AUTOMATION_LABELS[p.automation] || "–" },
          { label: "Media breaks", num: true, value: (p) => fmt.int(p.mediaBreaks) },
          { label: "Application services", value: (p) => p.services.join(", ") || chip("warning", "None") },
          { label: "Gaps", value: (p) => p.gaps.join(", ") || "–" },
        ],
        rated,
        "No business process carries maturity, automationLevel or mediaBreaks properties yet.",
      ),
  });
  return section(
    "business",
    "Business · Processes from the assessment",
    `${k.asIsCount} As-Is and ${k.toBeCount} To-Be processes · ${k.ratedCount} rated · ${fmt.int(k.mediaBreaks)} media breaks` +
      (k.manualShare !== null ? ` · ${fmt.pct(k.manualShare)} of rated processes manual` : ""),
    [traceability, breaks, scorecard],
  );
}

// ---------------------------------------------------------------------------------------------
// Application: end of life, TIME portfolio, lifecycle, business use
// ---------------------------------------------------------------------------------------------

function appTip(a) {
  return {
    title: a.name,
    rows: [
      [LIFECYCLE_LABELS[a.lifecycle] || null, "lifecycle"],
      [a.endOfLife ? fmt.day(a.endOfLife) : null, "end of life"],
      [TIME_LABELS[a.timeClassification] || null, "TIME decision"],
      [a.functionalFit ? `${fmt.int(a.functionalFit)} · ${FUNCTIONAL_FIT[a.functionalFit] || ""}` : null, "functional fit"],
      [a.technicalFit ? `${fmt.int(a.technicalFit)} · ${TECHNICAL_FIT[a.technicalFit] || ""}` : null, "technical fit"],
      [CRITICALITY_LABELS[a.criticality] || null, "business criticality"],
    ],
    notes: [...(a.risk.reasons.length ? a.risk.reasons : ["No risk flags"]), a.services && a.services.length ? `Realises: ${a.services.join(", ")}` : null],
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
  const horizon = shiftMonths(todayIso, 24);
  const start = `${Number(shiftMonths(first, -6).slice(0, 4))}-01-01`;
  const end = `${Number(shiftMonths(last > horizon ? last : horizon, 6).slice(0, 4)) + 1}-01-01`;
  const x = timeScale(start, end, labelW, width - right);
  const height = top + items.length * rowH + 4;
  const root = svgRoot(width, height, "Application end-of-life timeline");
  yearAxis(root, x, top, height);
  root.append(s("rect", { x: x(todayIso), y: top, width: Math.max(0, x(horizon) - x(todayIso)), height: height - top, style: "fill:var(--grid);opacity:0.55" }));
  todayMarker(root, x, todayIso, top, height);
  items.forEach((a, i) => {
    const cy = top + i * rowH + rowH / 2;
    const status =
      a.endOfLife < todayIso
        ? "critical"
        : a.risk.reasons.some((r) => r.startsWith("End of life within 12"))
          ? "serious"
          : a.risk.reasons.some((r) => r.startsWith("End of life within 24"))
            ? "warning"
            : "neutral";
    root.append(s("text", { class: "row-text", x: 0, y: cy + 4, text: truncate(a.name, labelW - 10) }));
    const g = s("g", { class: "mark" });
    g.append(s("circle", { cx: x(a.endOfLife), cy, r: 5, style: `fill:var(--${status});stroke:var(--surface);stroke-width:2` }));
    g.append(s("rect", { class: "hit", x: x(a.endOfLife) - 12, y: cy - 12, width: 24, height: 24 }));
    root.append(g);
    root.append(s("text", { class: "val-text", x: width - right + 10, y: cy + 4 }, s("tspan", { style: `fill:var(--${status})`, text: `${STATUS_ICON[status]} ` }), fmt.month(a.endOfLife)));
    tip(g, appTip(a));
  });
  return root;
}

function timeMatrix(apps) {
  const rated = apps.filter((a) => a.functionalFit && a.technicalFit);
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
  const unrated = apps.filter((a) => !a.functionalFit || !a.technicalFit);
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
  const eol = card({
    span: true,
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
  });
  const matrix = card({
    span: true,
    title: "TIME portfolio (Gartner)",
    subtitle: "Applications placed by functional and technical fit (1–4); 3 or more counts as high. A dashed chip means the recorded TIME decision differs from the fit-based quadrant.",
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
  const lifecycle = card({
    title: "Lifecycle distribution",
    subtitle: "Application components per lifecycle phase.",
    chart: (w) =>
      hbars(
        a.lifecycle.map((p) => ({
          label: p.label,
          value: p.count,
          tip: { title: p.label, rows: [[fmt.int(p.count), "applications"]], notes: a.applications.filter((x) => x.lifecycle === p.phase).map((x) => x.name) },
        })),
        w,
        { label: "Applications per lifecycle phase", valueWidth: 50 },
      ),
    table: () => dataTable([{ label: "Phase", value: (r) => r.label }, { label: "Applications", num: true, value: (r) => r.count }], a.lifecycle),
    note: a.unknownLifecycle ? `${plural(a.unknownLifecycle, "application")} without a lifecycle property.` : null,
  });
  const unused = card({
    title: "Applications without business use in the model",
    subtitle: "Components that realise no application service serving a business process: retirement candidates, or relationships still to be modelled.",
    chart: () => nameList(k.withoutBusinessUse, "Every application component serves at least one business process."),
  });
  return section(
    "application",
    "Application · Portfolio health",
    `${k.applicationCount} applications · ${k.pastEndOfLife} past end of life · ${k.endOfLifeWithin24Months} reach end of life within 24 months · ${k.migrateOrEliminate} marked Migrate or Eliminate · ${k.criticalAppsAtRisk} mission- or business-critical applications at high or critical risk.`,
    [eol, matrix, lifecycle, unused],
  );
}

// ---------------------------------------------------------------------------------------------
// Implementation & Migration: roadmap by plateau, plateaus, gap register
// ---------------------------------------------------------------------------------------------

function workFlag(w) {
  if (w.overdue) return ["critical", "Overdue"];
  if (w.endsAfterPlateau) return ["serious", "Ends after its plateau"];
  if (w.lateStart) return ["warning", "Not started yet"];
  if (w.status === "completed") return ["good", "Completed"];
  return ["neutral", WORK_STATUS[w.status] || "No status"];
}

function roadmapChart(impl, width) {
  const wps = impl.workPackages.filter((w) => w.startDate && w.endDate);
  if (!wps.length) return empty("No work package carries startDate and endDate yet.");
  const todayIso = impl.timeline.today;
  const plateauDates = impl.plateaus.map((p) => p.targetDate).filter(Boolean);
  const earliest = [impl.timeline.start, todayIso].filter(Boolean).sort()[0];
  const latest = [impl.timeline.end, todayIso, ...plateauDates].filter(Boolean).sort().slice(-1)[0];
  const startIso = `${earliest.slice(0, 4)}-01-01`;
  const endIso = `${Number(latest.slice(0, 4)) + 1}-01-01`;
  const labelW = Math.min(340, Math.round(width * 0.3));
  const right = 168;
  const top = 34;
  const rowH = 28;
  const groupH = 28;
  const barH = 14;
  const x = timeScale(startIso, endIso, labelW, width - right);
  const groups = impl.plateaus.map((p) => ({ name: p.name, targetDate: p.targetDate, items: wps.filter((w) => w.plateaus[0] === p.name) }));
  const loose = wps.filter((w) => !w.plateaus.length);
  if (loose.length) groups.push({ name: "Not assigned to a plateau", targetDate: null, items: loose });
  const visible = groups.filter((g) => g.items.length);
  const height = top + visible.length * groupH + wps.length * rowH + 6;
  const root = svgRoot(width, height, "Roadmap of work packages by plateau");
  yearAxis(root, x, top, height);
  let y = top;
  for (const group of visible) {
    root.append(s("text", { class: "row-text", x: 0, y: y + groupH - 9, style: "font-weight:600", text: truncate(`${group.name}${group.targetDate ? ` · ${fmt.month(group.targetDate)}` : ""}`, labelW + 80) }));
    if (group.targetDate) {
      // Plateau target date: a small diamond on the group's header row.
      const px = x(group.targetDate);
      const py = y + groupH / 2;
      const marker = s("path", { d: `M${px},${py - 6} l6,6 l-6,6 l-6,-6 Z`, class: "mark", style: "fill:var(--ink)" });
      root.append(marker);
      tip(marker, { title: group.name, rows: [[fmt.day(group.targetDate), "plateau target date"]] });
    }
    y += groupH;
    for (const w of group.items) {
      const by = y + (rowH - barH) / 2;
      const xs = x(w.startDate);
      const xe = Math.max(xs + 2, x(w.endDate));
      root.append(s("text", { class: "row-text muted", x: 10, y: by + barH - 3, text: truncate(w.name, labelW - 18) }));
      const g = s("g", { class: "mark" });
      g.append(s("rect", { x: xs, y: by, width: xe - xs, height: barH, rx: 4, style: `fill:${WORK_COLOR[w.status] || "var(--series-soft)"}` }));
      g.append(s("rect", { class: "hit", x: xs - 4, y, width: xe - xs + 8, height: rowH }));
      root.append(g);
      const [flagStatus, flagLabel] = workFlag(w);
      tip(g, {
        title: w.name,
        rows: [
          [`${fmt.day(w.startDate)} – ${fmt.day(w.endDate)}`, "planned span"],
          [WORK_STATUS[w.status] || "–", "status"],
          [w.owner, "owner"],
        ],
        notes: [`Realises: ${w.plateaus.join(", ") || "no plateau"}`, flagStatus !== "neutral" && flagStatus !== "good" ? flagLabel : null],
      });
      root.append(
        s("text", { class: "val-text", x: width - right + 10, y: by + barH - 3 }, s("tspan", { style: `fill:var(--${flagStatus})`, text: `${STATUS_ICON[flagStatus]} ` }), flagLabel),
      );
      y += rowH;
    }
  }
  todayMarker(root, x, todayIso, top, height);
  return root;
}

function implementationSection(d) {
  const impl = d.implementation;
  const k = impl.kpis;
  const roadmap = card({
    span: true,
    title: "Roadmap by plateau",
    subtitle: "Work packages grouped by the plateau they realise; bars span startDate to endDate and are shaded by status. ◆ marks the plateau's target date.",
    legend: h(
      "div",
      {},
      legend([
        ["rect", WORK_COLOR.planned, "Planned"],
        ["rect", WORK_COLOR["in-progress"], "In progress"],
        ["rect", WORK_COLOR.completed, "Completed"],
      ]),
      statusLegend([
        ["critical", "Overdue: end date passed, not completed"],
        ["serious", "Ends after its plateau's target date"],
        ["warning", "Start date passed, still planned"],
      ]),
    ),
    chart: (w) => roadmapChart(impl, w),
    table: () =>
      dataTable(
        [
          { label: "Work package", value: (r) => r.name },
          { label: "Plateau", value: (r) => r.plateaus.join(", ") || "–" },
          { label: "Start", num: true, value: (r) => fmt.day(r.startDate) },
          { label: "End", num: true, value: (r) => fmt.day(r.endDate) },
          { label: "Status", value: (r) => WORK_STATUS[r.status] || "–" },
          { label: "Flag", value: (r) => chip(...workFlag(r)) },
        ],
        impl.workPackages,
      ),
  });
  const plateaus = card({
    title: "Plateaus",
    subtitle: "Work packages completed per plateau, and the capability increments and gaps the plateau carries.",
    chart: () =>
      impl.plateaus.length
        ? h(
            "div",
            { class: "meter-list" },
            impl.plateaus.map((p) =>
              meterRow({
                title: p.name,
                value: ratio(p.completed, p.workPackages.length),
                sub: `${p.completed} of ${plural(p.workPackages.length, "work package")} completed · target ${fmt.month(p.targetDate)}`,
                note: `${plural(p.capabilities.length, "capability increment")} · ${plural(p.gaps.length, "gap")} associated`,
                tipContent: { title: p.name, notes: [p.capabilities.length ? `Realises: ${p.capabilities.join(", ")}` : null, p.gaps.length ? `Gaps: ${p.gaps.join(", ")}` : null] },
              }),
            ),
          )
        : empty("No plateaus in the model."),
  });
  const register = card({
    title: "Gap register",
    subtitle: "Gap elements (from the assessment or modelled directly) with the processes they affect and the plateau that closes them.",
    chart: () =>
      tableCard(
        [
          { label: "Gap", value: (g) => g.name },
          { label: "Affected processes", value: (g) => g.processes.join(", ") || "–" },
          { label: "Plateau", value: (g) => (g.plateaus.length ? g.plateaus.join(", ") : chip("critical", "Not planned")) },
        ],
        impl.gaps,
        "No Gap elements in the model yet.",
      ),
  });
  return section(
    "implementation",
    "Implementation & Migration · Roadmap execution",
    `${k.workPackageCount} work packages · ${k.completed} completed · ${k.in_progress} in progress · ${k.planned} planned · ${k.overdue} overdue · ${k.endsAfterPlateau} end after their plateau · ` +
      `${k.gapsWithoutPlateau} of ${k.gapCount} gaps not yet assigned to a plateau.`,
    [roadmap, plateaus, register],
  );
}

// ---------------------------------------------------------------------------------------------
// Data completeness
// ---------------------------------------------------------------------------------------------

function qualitySection(d) {
  const rows = d.dataQuality;
  const quality = card({
    span: true,
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
    motivationSection(data),
    strategySection(data),
    gapsSection(data),
    businessSection(data),
    applicationSection(data),
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
