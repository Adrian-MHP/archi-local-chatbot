"""Transformation dashboard metrics, computed from the active Archi model read live via MCP.

Every number here is derived from model elements, their properties and their relationships at
request time -- nothing is cached or entered elsewhere. The property schema the metrics read is
documented in docs/transformation-dashboard.md. Property keys and enumeration values are matched
loosely (case, spaces, '-', '_' are ignored), so "End of Life", "endOfLife" and "end_of_life" all
resolve to the same key; real-world models rarely follow one spelling.

The module is split into fetching (fetch_snapshot) and pure computation (compute_dashboard), so
the metrics can be tested against a saved snapshot without Archi running.
"""

from __future__ import annotations

import calendar
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from statistics import mean
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .mcp_client import McpClient

# ---------------------------------------------------------------------------------------------
# Vocabularies (canonical value -> accepted spellings after normalisation)
# ---------------------------------------------------------------------------------------------

LIFECYCLE_PHASES = ["plan", "phase-in", "active", "phase-out", "end-of-life"]
LIFECYCLE_LABELS = {"plan": "Plan", "phase-in": "Phase in", "active": "Active", "phase-out": "Phase out", "end-of-life": "End of life"}
_LIFECYCLE_ALIASES = {
    "plan": "plan", "planned": "plan", "planning": "plan",
    "phasein": "phase-in", "introduction": "phase-in", "rollout": "phase-in",
    "active": "active", "production": "active", "inproduction": "active", "live": "active",
    "phaseout": "phase-out", "sunset": "phase-out", "sunsetting": "phase-out", "decommissioning": "phase-out",
    "endoflife": "end-of-life", "eol": "end-of-life", "retired": "end-of-life", "decommissioned": "end-of-life",
}

TIME_CLASSES = ["invest", "tolerate", "migrate", "eliminate"]
TIME_LABELS = {"invest": "Invest", "tolerate": "Tolerate", "migrate": "Migrate", "eliminate": "Eliminate"}

CRITICALITY_LEVELS = ["mission-critical", "business-critical", "business-operational", "administrative"]
CRITICALITY_LABELS = {
    "mission-critical": "Mission critical", "business-critical": "Business critical",
    "business-operational": "Business operational", "administrative": "Administrative service",
}
_CRITICALITY_ALIASES = {
    "missioncritical": "mission-critical", "businesscritical": "business-critical",
    "businessoperational": "business-operational", "administrative": "administrative",
    "administrativeservice": "administrative",
}

RADAR_RINGS = ["adopt", "trial", "assess", "hold"]
IMPORTANCE_LEVELS = ["high", "medium", "low"]
_IMPORTANCE_ALIASES = {"high": "high", "critical": "high", "differentiating": "high", "medium": "medium",
                       "moderate": "medium", "low": "low", "commodity": "low"}
IMPORTANCE_WEIGHT = {"high": 3, "medium": 2, "low": 1}
AUTOMATION_LEVELS = ["manual", "partial", "automated"]
_AUTOMATION_ALIASES = {"manual": "manual", "partial": "partial", "partiallyautomated": "partial",
                       "semiautomated": "partial", "automated": "automated", "fullyautomated": "automated"}
RAG_LEVELS = ["green", "amber", "red"]
_RAG_ALIASES = {"green": "green", "amber": "amber", "yellow": "amber", "orange": "amber", "red": "red"}
_DIRECTION_ALIASES = {"higher": "higher", "higherisbetter": "higher", "up": "higher", "increase": "higher",
                      "lower": "lower", "lowerisbetter": "lower", "down": "lower", "decrease": "lower"}

TECHNOLOGY_TYPES = {
    "Node", "Device", "SystemSoftware", "TechnologyService", "TechnologyInterface", "TechnologyFunction",
    "TechnologyProcess", "TechnologyInteraction", "TechnologyEvent", "TechnologyCollaboration",
    "CommunicationNetwork", "Path", "Equipment", "Facility", "DistributionNetwork",
}
# Technology element types whose support status can put the applications they serve at risk.
TECHNOLOGY_PLATFORM_TYPES = {"Node", "Device", "SystemSoftware", "TechnologyService", "Equipment"}

RISK_ORDER = ["ok", "warning", "serious", "critical"]

# Expected properties per element type -- the basis of the data-completeness panel.
EXPECTED_PROPERTIES: List[Tuple[str, str, str, List[str]]] = [
    ("Strategy", "Capability", "Capabilities (leaf)", ["maturity", "targetMaturity", "strategicImportance"]),
    ("Business", "BusinessProcess", "Business processes", ["maturity", "automationLevel", "mediaBreaks", "valueStreamStage"]),
    ("Application", "ApplicationComponent", "Application components",
     ["lifecycle", "timeClassification", "functionalFit", "technicalFit", "businessCriticality", "annualCostEUR"]),
    ("Technology", "*technology", "Technology platforms", ["lifecycle", "vendorSupportEnd", "techRadar"]),
    ("Motivation", "Outcome", "Outcomes", ["kpi", "baseline", "current", "target", "direction", "baselineDate", "targetDate"]),
    ("Implementation & Migration", "WorkPackage", "Work packages",
     ["startDate", "endDate", "progress", "budgetEUR", "actualCostEUR", "plateau"]),
]


# ---------------------------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------------------------

def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _enum(value: Optional[str], aliases: Dict[str, str]) -> Optional[str]:
    if value is None:
        return None
    return aliases.get(_key(value))


def _num(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    s = str(value).strip()
    for token in ("€", "EUR", "eur", "%", " ", " ", "'"):
        s = s.replace(token, "")
    if not s:
        return None
    if re.fullmatch(r"[-+]?\d{1,3}(\.\d{3})+", s):  # German thousands: 1.250.000
        s = s.replace(".", "")
    elif re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+(\.\d+)?", s):  # English thousands: 1,250,000
        s = s.replace(",", "")
    elif re.fullmatch(r"[-+]?\d+,\d+", s):  # German decimal: 3,5
        s = s.replace(",", ".")
    match = re.match(r"[-+]?\d+(\.\d+)?", s)
    return float(match.group(0)) if match else None


def _date(value: Optional[str]) -> Optional[date]:
    s = str(value or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19] if "T" in s else s, fmt).date()
        except ValueError:
            continue
    # Partial dates mean "until the end of that period".
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", s) or None
    if m:
        year, month = int(m.group(1)), int(m.group(2))
    else:
        m = re.fullmatch(r"(\d{1,2})[./](\d{4})", s)
        if m:
            year, month = int(m.group(2)), int(m.group(1))
        elif re.fullmatch(r"\d{4}", s):
            year, month = int(s), 12
        else:
            return None
    if not 1 <= month <= 12:
        return None
    return date(year, month, calendar.monthrange(year, month)[1])


def _months_between(start: date, end: date) -> float:
    """Display value only -- rounding can turn "yesterday" into -0.0, so compare dates, not this."""
    return round((end - start).days / 30.4375, 1)


def _within_months(today: date, end: date, months: int) -> bool:
    """True when `end` is today or later and at most `months` calendar months away."""
    year, month = divmod(today.month - 1 + months, 12)
    limit_year, limit_month = today.year + year, month + 1
    limit = date(limit_year, limit_month, min(today.day, calendar.monthrange(limit_year, limit_month)[1]))
    return today <= end <= limit


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None else round(value, digits)


def _avg(values: Iterable[Optional[float]]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    return round(mean(clean), 2) if clean else None


def _worst_risk(levels: Iterable[str]) -> str:
    worst = "ok"
    for level in levels:
        if RISK_ORDER.index(level) > RISK_ORDER.index(worst):
            worst = level
    return worst


# ---------------------------------------------------------------------------------------------
# Model snapshot
# ---------------------------------------------------------------------------------------------

@dataclass
class Element:
    id: str
    name: str
    type: str
    layer: str
    props: Dict[str, str]          # normalised key -> value
    raw_props: Dict[str, str]      # original key -> value

    def prop(self, key: str) -> Optional[str]:
        value = self.props.get(_key(key))
        return value if value not in (None, "") else None

    def has_any(self, keys: Iterable[str]) -> bool:
        return any(self.prop(k) is not None for k in keys)

    def ref(self) -> Dict[str, str]:
        return {"id": self.id, "name": self.name, "type": self.type}


@dataclass
class Model:
    info: Dict[str, Any]
    elements: Dict[str, Element]
    relationships: List[Dict[str, Any]]
    outgoing: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)  # id -> [(relType, targetId)]
    incoming: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)  # id -> [(relType, sourceId)]

    def __post_init__(self) -> None:
        for rel in self.relationships:
            src, tgt, rel_type = rel.get("sourceId"), rel.get("targetId"), rel.get("type", "")
            if src in self.elements and tgt in self.elements:
                self.outgoing.setdefault(src, []).append((rel_type, tgt))
                self.incoming.setdefault(tgt, []).append((rel_type, src))

    def of_type(self, *types: str) -> List[Element]:
        return sorted((e for e in self.elements.values() if e.type in types), key=lambda e: e.name.lower())

    def targets(self, element_id: str, rel_types: Iterable[str], target_types: Optional[Iterable[str]] = None) -> List[Element]:
        rel_types, target_types = set(rel_types), set(target_types) if target_types else None
        found = [self.elements[t] for r, t in self.outgoing.get(element_id, []) if r in rel_types]
        return _unique(e for e in found if target_types is None or e.type in target_types)

    def sources(self, element_id: str, rel_types: Iterable[str], source_types: Optional[Iterable[str]] = None) -> List[Element]:
        rel_types, source_types = set(rel_types), set(source_types) if source_types else None
        found = [self.elements[s] for r, s in self.incoming.get(element_id, []) if r in rel_types]
        return _unique(e for e in found if source_types is None or e.type in source_types)


def _unique(elements: Iterable[Element]) -> List[Element]:
    seen, out = set(), []
    for e in elements:
        if e.id not in seen:
            seen.add(e.id)
            out.append(e)
    return sorted(out, key=lambda e: e.name.lower())


def _payload(mcp: McpClient, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    raw = mcp.call_tool(tool, args)
    blocks = raw.get("content", []) if isinstance(raw, dict) else []
    for block in reversed(blocks):
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        try:
            data = json.loads(block.get("text") or "")
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            if isinstance(data.get("error"), dict):
                error = data["error"]
                raise RuntimeError(f"{tool} failed: {error.get('message') or error.get('code') or error}")
            return data
    raise RuntimeError(f"{tool} returned no JSON payload.")


def _fetch_all(mcp: McpClient, tool: str, args: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    page_args = {**args, "limit": 500}
    for _ in range(400):  # 200k items -- a backstop against a cursor that never ends, not a real limit
        data = _payload(mcp, tool, page_args)
        result = data.get("result")
        if isinstance(result, list):
            items.extend(result)
        cursor = (data.get("_meta") or {}).get("cursor")
        if not cursor:
            return items
        page_args = {"cursor": cursor, "limit": 500}
    raise RuntimeError(f"{tool} kept returning pages; aborted after {len(items)} items.")


def fetch_snapshot(mcp: McpClient) -> Dict[str, Any]:
    """Read everything the dashboard needs from the active Archi model in three tool calls."""
    info = _payload(mcp, "get-model-info", {})
    elements = _fetch_all(mcp, "search-elements", {"query": "", "exclude": ["documentation"]})
    relationships = _fetch_all(mcp, "search-relationships", {"query": "", "exclude": ["documentation"]})
    return {
        "info": {**(info.get("result") or {}), "modelVersion": (info.get("_meta") or {}).get("modelVersion")},
        "elements": elements,
        "relationships": relationships,
    }


def _build_model(snapshot: Dict[str, Any]) -> Model:
    elements: Dict[str, Element] = {}
    for item in snapshot.get("elements", []):
        raw_props: Dict[str, str] = {}
        properties = item.get("properties") or []
        if isinstance(properties, dict):
            properties = [{"key": k, "value": v} for k, v in properties.items()]
        for p in properties:
            key, value = str(p.get("key") or "").strip(), p.get("value")
            if key:
                raw_props[key] = "" if value is None else str(value).strip()
        elements[item["id"]] = Element(
            id=item["id"],
            name=str(item.get("name") or "(unnamed)"),
            type=str(item.get("type") or ""),
            layer=str(item.get("layer") or ""),
            props={_key(k): v for k, v in raw_props.items()},
            raw_props=raw_props,
        )
    return Model(info=snapshot.get("info") or {}, elements=elements, relationships=snapshot.get("relationships") or [])


# ---------------------------------------------------------------------------------------------
# Shared derivations
# ---------------------------------------------------------------------------------------------

@dataclass
class Context:
    model: Model
    today: date
    app_risk: Dict[str, Dict[str, Any]] = field(default_factory=dict)        # app id -> risk record
    tech_support: Dict[str, Dict[str, Any]] = field(default_factory=dict)    # tech id -> support record
    capability_apps: Dict[str, List[Element]] = field(default_factory=dict)  # capability id -> supporting apps
    capability_wps: Dict[str, List[Element]] = field(default_factory=dict)  # capability id -> work packages
    wp_capability_budget: Dict[str, float] = field(default_factory=dict)     # capability id -> allocated budget


def _support_record(e: Element, today: date) -> Dict[str, Any]:
    end = _date(e.prop("vendorSupportEnd"))
    lifecycle = _enum(e.prop("lifecycle"), _LIFECYCLE_ALIASES)
    months = _months_between(today, end) if end else None
    if end is not None:
        if end < today:
            status = "out"
        elif _within_months(today, end, 12):
            status = "expiring-12"
        elif _within_months(today, end, 24):
            status = "expiring-24"
        else:
            status = "supported"
    elif lifecycle == "end-of-life":
        status = "out"
    else:
        status = "unknown"
    return {"vendorSupportEnd": end.isoformat() if end else None, "monthsOfSupport": months, "status": status,
            "lifecycle": lifecycle}


def _app_risk(app: Element, ctx: Context) -> Dict[str, Any]:
    reasons: List[Tuple[str, str]] = []  # (level, text)
    eol = _date(app.prop("endOfLife"))
    lifecycle = _enum(app.prop("lifecycle"), _LIFECYCLE_ALIASES)
    time_class = _enum(app.prop("timeClassification"), {t: t for t in TIME_CLASSES})
    if eol is not None:
        if eol < ctx.today:
            reasons.append(("critical", f"Past end of life ({eol.isoformat()})"))
        elif _within_months(ctx.today, eol, 12):
            reasons.append(("serious", f"End of life within 12 months ({eol.isoformat()})"))
        elif _within_months(ctx.today, eol, 24):
            reasons.append(("warning", f"End of life within 24 months ({eol.isoformat()})"))
    elif lifecycle == "end-of-life":
        reasons.append(("critical", "Lifecycle is end of life"))
    if time_class in ("eliminate", "migrate"):
        reasons.append(("warning", f"TIME classification: {TIME_LABELS[time_class]}"))
    for tech in ctx.model.sources(app.id, ["ServingRelationship", "RealizationRelationship"], TECHNOLOGY_PLATFORM_TYPES):
        support = ctx.tech_support.get(tech.id)
        if support and support["status"] == "out":
            reasons.append(("critical", f"Runs on out-of-support technology: {tech.name}"))
        elif support and support["status"] == "expiring-12":
            reasons.append(("serious", f"Technology support ends within 12 months: {tech.name}"))
    return {"level": _worst_risk(level for level, _ in reasons), "reasons": [text for _, text in reasons]}


def _prepare(model: Model, today: date) -> Context:
    ctx = Context(model=model, today=today)
    for tech in model.elements.values():
        if tech.type in TECHNOLOGY_TYPES:
            ctx.tech_support[tech.id] = _support_record(tech, today)
    for app in model.of_type("ApplicationComponent"):
        ctx.app_risk[app.id] = _app_risk(app, ctx)

    # Applications support a capability directly (app realizes capability). Only when a model has
    # no direct mapping for a capability do we fall back to the processes realising it (app serves
    # process, process realizes capability) -- mixing both would credit every tool a process touches
    # to every capability that process realises, which floods the heat map with false risk.
    for cap in model.of_type("Capability"):
        direct = model.sources(cap.id, ["RealizationRelationship"], ["ApplicationComponent"])
        if direct:
            ctx.capability_apps[cap.id] = direct
            continue
        via_processes: List[Element] = []
        for proc in model.sources(cap.id, ["RealizationRelationship"], ["BusinessProcess", "BusinessFunction"]):
            via_processes.extend(model.sources(proc.id, ["ServingRelationship"], ["ApplicationComponent"]))
        ctx.capability_apps[cap.id] = _unique(via_processes)

    # Work packages change capabilities through a (directed) association or a realization chain
    # work package -> deliverable -> capability. Budget is split evenly over the capabilities.
    for wp in model.of_type("WorkPackage"):
        caps = model.targets(wp.id, ["AssociationRelationship", "RealizationRelationship", "InfluenceRelationship"], ["Capability"])
        for deliverable in model.targets(wp.id, ["RealizationRelationship"], ["Deliverable"]):
            caps += model.targets(deliverable.id, ["RealizationRelationship", "AssociationRelationship"], ["Capability"])
        caps = _unique(caps)
        budget = _num(wp.prop("budgetEUR"))
        for cap in caps:
            ctx.capability_wps.setdefault(cap.id, []).append(wp)
            if budget:
                ctx.wp_capability_budget[cap.id] = ctx.wp_capability_budget.get(cap.id, 0.0) + budget / len(caps)
    return ctx


# ---------------------------------------------------------------------------------------------
# Strategy layer: capability maturity heat map
# ---------------------------------------------------------------------------------------------

def _capability_tree(model: Model) -> Tuple[List[Element], Dict[str, List[Element]], Dict[str, str]]:
    caps = model.of_type("Capability")
    by_name = {c.name.lower(): c for c in caps}
    children: Dict[str, List[Element]] = {}
    parent_of: Dict[str, str] = {}
    for cap in caps:
        for child in model.targets(cap.id, ["CompositionRelationship", "AggregationRelationship"], ["Capability"]):
            if child.id not in parent_of and child.id != cap.id:
                parent_of[child.id] = cap.id
    for cap in caps:  # property fallback for models without composition relationships
        parent = by_name.get((cap.prop("parentCapability") or "").lower())
        if cap.id not in parent_of and parent and parent.id != cap.id:
            parent_of[cap.id] = parent.id
    for child_id, parent_id in parent_of.items():
        children.setdefault(parent_id, []).append(model.elements[child_id])

    def root(cap_id: str) -> str:
        seen = set()
        while cap_id in parent_of and cap_id not in seen:
            seen.add(cap_id)
            cap_id = parent_of[cap_id]
        return cap_id

    roots = sorted({root(c.id) for c in caps}, key=lambda cid: model.elements[cid].name.lower())
    return [model.elements[r] for r in roots], children, parent_of


def _leaves_under(cap: Element, children: Dict[str, List[Element]], seen: Optional[set] = None) -> List[Element]:
    seen = set() if seen is None else seen
    if cap.id in seen:  # composition cycle in the model -- stop instead of recursing forever
        return []
    seen.add(cap.id)
    kids = [k for k in children.get(cap.id, []) if k.id not in seen]
    if not kids:
        return [cap]
    out: List[Element] = []
    for kid in kids:
        out.extend(_leaves_under(kid, children, seen))
    return out


def _capability_record(cap: Element, group: Element, ctx: Context) -> Dict[str, Any]:
    maturity = _num(cap.prop("maturity"))
    target = _num(cap.prop("targetMaturity"))
    importance = _enum(cap.prop("strategicImportance"), _IMPORTANCE_ALIASES)
    gap = round(target - maturity, 2) if maturity is not None and target is not None else None
    apps = ctx.capability_apps.get(cap.id, [])
    app_records = [{**a.ref(), "risk": ctx.app_risk[a.id]["level"],
                    "lifecycle": _enum(a.prop("lifecycle"), _LIFECYCLE_ALIASES)} for a in apps]
    wps = _unique(ctx.capability_wps.get(cap.id, []) + ctx.capability_wps.get(group.id, []))
    return {
        **cap.ref(),
        "group": group.name,
        "maturity": maturity,
        "targetMaturity": target,
        "gap": gap,
        "importance": importance,
        "priorityScore": round(gap * IMPORTANCE_WEIGHT[importance], 2) if gap is not None and gap > 0 and importance else None,
        "isPriorityGap": bool(gap is not None and gap >= 2 and importance == "high"),
        # TOGAF Business Capabilities guide heat-map convention: at target / one level / two or more.
        "gapBand": None if gap is None else "at-target" if gap <= 0 else "one-level" if gap < 2 else "two-plus",
        "applications": app_records,
        "appRisk": _worst_risk(a["risk"] for a in app_records),
        "workPackages": [w.name for w in wps],
        "investmentEUR": round(ctx.wp_capability_budget.get(cap.id, 0.0)) or None,
        "owner": cap.prop("owner"),
    }


def strategy_section(ctx: Context) -> Dict[str, Any]:
    roots, children, _parents = _capability_tree(ctx.model)
    groups, leaves = [], []
    for root in roots:
        root_leaves = _leaves_under(root, children)
        group_name = root.name if children.get(root.id) else "Ungrouped capabilities"
        records = [_capability_record(leaf, root, ctx) for leaf in root_leaves]
        leaves.extend(records)
        groups.append({
            **root.ref(), "name": group_name, "isGroup": bool(children.get(root.id)),
            "maturity": _avg(r["maturity"] for r in records),
            "targetMaturity": _avg(r["targetMaturity"] for r in records),
            "capabilities": records,
        })
    # Merge single-capability "ungrouped" roots into one group so the heat map stays readable.
    grouped = [g for g in groups if g["isGroup"]]
    loose = [c for g in groups if not g["isGroup"] for c in g["capabilities"]]
    if loose:
        grouped.append({"id": "ungrouped", "name": "Ungrouped capabilities", "type": "Capability", "isGroup": False,
                        "maturity": _avg(c["maturity"] for c in loose),
                        "targetMaturity": _avg(c["targetMaturity"] for c in loose), "capabilities": loose})
    rated = [c for c in leaves if c["maturity"] is not None]
    with_target = [c for c in rated if c["targetMaturity"] is not None]
    supported = [c for c in leaves if c["applications"]]
    priority = [c for c in leaves if c["isPriorityGap"]]

    value_stream = []
    stages = [vs for vs in ctx.model.of_type("ValueStream") if _num(vs.prop("stageOrder")) is not None]
    for stage in sorted(stages, key=lambda s: _num(s.prop("stageOrder")) or 0):
        serving = ctx.model.sources(stage.id, ["ServingRelationship", "RealizationRelationship"], ["Capability"])
        stage_leaves = [r for cap in serving for r in leaves
                        if r["id"] == cap.id or r["group"] == cap.name]
        value_stream.append({**stage.ref(), "order": _num(stage.prop("stageOrder")),
                             "maturity": _avg(r["maturity"] for r in stage_leaves),
                             "targetMaturity": _avg(r["targetMaturity"] for r in stage_leaves),
                             "capabilities": [c.name for c in serving]})
    return {
        "groups": grouped,
        "valueStream": value_stream,
        "kpis": {
            "capabilityCount": len(leaves),
            "ratedCount": len(rated),
            "avgMaturity": _avg(c["maturity"] for c in rated),
            "avgTargetMaturity": _avg(c["targetMaturity"] for c in with_target),
            "avgGap": _avg(c["gap"] for c in with_target),
            "priorityGapCount": len(priority),
            "coverageShare": round(len(supported) / len(leaves), 3) if leaves else None,
            "unsupportedCapabilities": [c["name"] for c in leaves if not c["applications"]],
        },
    }


# ---------------------------------------------------------------------------------------------
# Business layer: process maturity, automation, media breaks along the value stream
# ---------------------------------------------------------------------------------------------

BUSINESS_KEYS = ["maturity", "automationLevel", "mediaBreaks", "valueStreamStage"]


def business_section(ctx: Context) -> Dict[str, Any]:
    model = ctx.model
    processes = model.of_type("BusinessProcess")
    rated = [p for p in processes if p.has_any(BUSINESS_KEYS)]
    stage_order = {vs.name: _num(vs.prop("stageOrder")) for vs in model.of_type("ValueStream") if vs.prop("stageOrder")}
    stages: Dict[str, Dict[str, Any]] = {}
    for proc in rated:
        stage_name = proc.prop("valueStreamStage") or "Unassigned"
        stage = stages.setdefault(stage_name, {"name": stage_name, "order": stage_order.get(stage_name), "processes": []})
        apps = model.sources(proc.id, ["ServingRelationship"], ["ApplicationComponent"])
        stage["processes"].append({
            **proc.ref(),
            "maturity": _num(proc.prop("maturity")),
            "automation": _enum(proc.prop("automationLevel"), _AUTOMATION_ALIASES),
            "mediaBreaks": _num(proc.prop("mediaBreaks")),
            "applications": [a.name for a in apps],
            "roles": [r.name for r in model.sources(proc.id, ["AssignmentRelationship"], ["BusinessRole", "BusinessActor"])],
        })
    ordered = sorted(stages.values(), key=lambda s: (s["order"] is None, s["order"] or 0, s["name"]))
    for stage in ordered:
        procs = stage["processes"]
        stage["avgMaturity"] = _avg(p["maturity"] for p in procs)
        stage["mediaBreaks"] = sum(p["mediaBreaks"] or 0 for p in procs)
        stage["automation"] = {level: sum(1 for p in procs if p["automation"] == level) for level in AUTOMATION_LEVELS}
        stage["automation"]["unknown"] = sum(1 for p in procs if p["automation"] is None)
    all_procs = [p for s in ordered for p in s["processes"]]
    with_automation = [p for p in all_procs if p["automation"]]
    return {
        "stages": ordered,
        "kpis": {
            "processCount": len(processes),
            "ratedCount": len(rated),
            "avgMaturity": _avg(p["maturity"] for p in all_procs),
            "mediaBreaks": sum(p["mediaBreaks"] or 0 for p in all_procs),
            "manualShare": round(sum(1 for p in with_automation if p["automation"] == "manual") / len(with_automation), 3)
            if with_automation else None,
            "unsupportedProcesses": [p["name"] for p in all_procs if not p["applications"]],
        },
    }


# ---------------------------------------------------------------------------------------------
# Application layer: lifecycle, end of life, TIME portfolio, cost, redundancy
# ---------------------------------------------------------------------------------------------

def fit_quadrant(functional: Optional[float], technical: Optional[float]) -> Optional[str]:
    """Gartner TIME quadrant from 1-4 fit scores: >= 3 counts as high fit."""
    if functional is None or technical is None:
        return None
    high_f, high_t = functional >= 3, technical >= 3
    if high_f and high_t:
        return "invest"
    if high_f and not high_t:
        return "migrate"
    if not high_f and high_t:
        return "tolerate"
    return "eliminate"


def application_section(ctx: Context) -> Dict[str, Any]:
    model, today = ctx.model, ctx.today
    apps = []
    for app in model.of_type("ApplicationComponent"):
        eol = _date(app.prop("endOfLife"))
        functional, technical = _num(app.prop("functionalFit")), _num(app.prop("technicalFit"))
        caps = model.targets(app.id, ["RealizationRelationship"], ["Capability"])
        techs = model.sources(app.id, ["ServingRelationship", "RealizationRelationship"], TECHNOLOGY_PLATFORM_TYPES)
        apps.append({
            **app.ref(),
            "category": app.prop("applicationCategory") or app.prop("category"),
            "vendor": app.prop("vendor"),
            "lifecycle": _enum(app.prop("lifecycle"), _LIFECYCLE_ALIASES),
            "endOfLife": eol.isoformat() if eol else None,
            "monthsToEndOfLife": _months_between(today, eol) if eol else None,
            "timeClassification": _enum(app.prop("timeClassification"), {t: t for t in TIME_CLASSES}),
            "functionalFit": functional,
            "technicalFit": technical,
            "fitQuadrant": fit_quadrant(functional, technical),
            "criticality": _enum(app.prop("businessCriticality"), _CRITICALITY_ALIASES),
            "annualCostEUR": _num(app.prop("annualCostEUR")),
            "users": _num(app.prop("users")),
            "hosting": app.prop("hosting"),
            "risk": ctx.app_risk[app.id],
            "capabilities": [c.name for c in caps],
            "technology": [{"name": t.name, "supportStatus": ctx.tech_support.get(t.id, {}).get("status", "unknown")} for t in techs],
        })
    in_portfolio = [a for a in apps if a["lifecycle"] != "end-of-life" or (a["endOfLife"] and a["endOfLife"] >= today.isoformat())]

    lifecycle = [{"phase": p, "label": LIFECYCLE_LABELS[p],
                  "count": sum(1 for a in apps if a["lifecycle"] == p),
                  "annualCostEUR": sum(a["annualCostEUR"] or 0 for a in apps if a["lifecycle"] == p)}
                 for p in LIFECYCLE_PHASES]
    unknown_lifecycle = sum(1 for a in apps if a["lifecycle"] is None)
    time_classes = [{"time": t, "label": TIME_LABELS[t],
                     "count": sum(1 for a in apps if a["timeClassification"] == t),
                     "annualCostEUR": sum(a["annualCostEUR"] or 0 for a in apps if a["timeClassification"] == t)}
                    for t in TIME_CLASSES]
    total_cost = sum(a["annualCostEUR"] or 0 for a in apps)
    at_risk_cost = sum(t["annualCostEUR"] for t in time_classes if t["time"] in ("migrate", "eliminate"))

    eol_apps = sorted((a for a in apps if a["endOfLife"]), key=lambda a: a["endOfLife"])
    past = [a for a in eol_apps if date.fromisoformat(a["endOfLife"]) < today]
    within_24 = [a for a in eol_apps if _within_months(today, date.fromisoformat(a["endOfLife"]), 24)]

    # Redundancy: a leaf capability realised directly by two or more live applications.
    redundancy = []
    _roots, children, _parents = _capability_tree(model)
    for cap in model.of_type("Capability"):
        if children.get(cap.id):
            continue
        live = [a for a in model.sources(cap.id, ["RealizationRelationship"], ["ApplicationComponent"])
                if _enum(a.prop("lifecycle"), _LIFECYCLE_ALIASES) in ("phase-in", "active", "phase-out")]
        if len(live) >= 2:
            redundancy.append({**cap.ref(), "applications": [a.name for a in live]})
    return {
        "applications": apps,
        "lifecycle": lifecycle,
        "unknownLifecycle": unknown_lifecycle,
        "timeClasses": time_classes,
        "endOfLife": [{k: a[k] for k in ("id", "name", "endOfLife", "monthsToEndOfLife", "lifecycle", "criticality",
                                          "timeClassification", "risk")} for a in eol_apps],
        "redundancy": sorted(redundancy, key=lambda r: (-len(r["applications"]), r["name"])),
        "kpis": {
            "applicationCount": len(apps),
            "inPortfolio": len(in_portfolio),
            "pastEndOfLife": len(past),
            "endOfLifeWithin24Months": len(within_24),
            "annualCostEUR": total_cost,
            "migrateEliminateCostEUR": at_risk_cost,
            "migrateEliminateCostShare": round(at_risk_cost / total_cost, 3) if total_cost else None,
            "redundantCapabilities": len(redundancy),
            "criticalAppsAtRisk": sum(1 for a in apps if a["criticality"] in ("mission-critical", "business-critical")
                                      and a["risk"]["level"] in ("serious", "critical")),
        },
    }


# ---------------------------------------------------------------------------------------------
# Technology layer: vendor support runway, technology radar, risk propagation to applications
# ---------------------------------------------------------------------------------------------

def technology_section(ctx: Context) -> Dict[str, Any]:
    model = ctx.model
    components = []
    for tech in model.of_type(*sorted(TECHNOLOGY_TYPES)):
        support = ctx.tech_support[tech.id]
        served = model.targets(tech.id, ["ServingRelationship", "RealizationRelationship"], ["ApplicationComponent"])
        components.append({
            **tech.ref(),
            "category": tech.prop("technologyCategory") or tech.prop("category"),
            "vendor": tech.prop("vendor"),
            "version": tech.prop("version"),
            "lifecycle": support["lifecycle"],
            "vendorSupportEnd": support["vendorSupportEnd"],
            "monthsOfSupport": support["monthsOfSupport"],
            "supportStatus": support["status"],
            "radar": _enum(tech.prop("techRadar"), {r: r for r in RADAR_RINGS}),
            "hosting": tech.prop("hosting"),
            "applications": [{"name": a.name, "criticality": _enum(a.prop("businessCriticality"), _CRITICALITY_ALIASES)}
                             for a in served],
        })
    platforms = [c for c in components if c["type"] in TECHNOLOGY_PLATFORM_TYPES]
    known = [c for c in platforms if c["supportStatus"] != "unknown"]
    out = [c for c in platforms if c["supportStatus"] == "out"]
    apps_on_out = sorted({a["name"] for c in out for a in c["applications"]})
    critical_on_out = sorted({a["name"] for c in out for a in c["applications"]
                              if a["criticality"] in ("mission-critical", "business-critical")})
    buckets = ["out", "expiring-12", "expiring-24", "supported", "unknown"]
    return {
        "components": components,
        "supportBuckets": [{"status": b, "count": sum(1 for c in platforms if c["supportStatus"] == b)} for b in buckets],
        "radar": [{"ring": r, "count": sum(1 for c in components if c["radar"] == r)} for r in RADAR_RINGS],
        "kpis": {
            "componentCount": len(components),
            "platformCount": len(platforms),
            "withSupportDate": len(known),
            "outOfSupport": len(out),
            "outOfSupportShare": round(len(out) / len(known), 3) if known else None,
            "expiringWithin12Months": sum(1 for c in platforms if c["supportStatus"] == "expiring-12"),
            "applicationsOnUnsupported": apps_on_out,
            "criticalApplicationsOnUnsupported": critical_on_out,
        },
    }


# ---------------------------------------------------------------------------------------------
# Motivation layer: outcome KPIs against baseline and target
# ---------------------------------------------------------------------------------------------

def _outcome_status(progress: Optional[float], expected: Optional[float]) -> str:
    if progress is None:
        return "unknown"
    if progress >= 1:
        return "achieved"
    if expected is None:
        return "tracking"
    if progress >= expected - 0.1:
        return "on-track"
    if progress >= expected - 0.25:
        return "at-risk"
    return "off-track"


def motivation_section(ctx: Context) -> Dict[str, Any]:
    model, today = ctx.model, ctx.today
    outcomes = []
    for o in model.of_type("Outcome"):
        baseline, current, target = _num(o.prop("baseline")), _num(o.prop("current")), _num(o.prop("target"))
        direction = _enum(o.prop("direction"), _DIRECTION_ALIASES)
        if direction is None and baseline is not None and target is not None:
            direction = "higher" if target >= baseline else "lower"
        progress = None
        if None not in (baseline, current, target):
            if target != baseline:
                progress = (current - baseline) / (target - baseline)
            else:
                progress = 1.0 if (current >= target if direction != "lower" else current <= target) else 0.0
        baseline_date, target_date = _date(o.prop("baselineDate")), _date(o.prop("targetDate"))
        measured = _date(o.prop("measuredAt")) or today
        expected = None
        if baseline_date and target_date and target_date > baseline_date:
            expected = min(max((measured - baseline_date).days / (target_date - baseline_date).days, 0.0), 1.0)
        status = _outcome_status(progress, expected)
        if status in ("on-track", "at-risk", "off-track", "tracking") and target_date and target_date < today:
            status = "missed"
        outcomes.append({
            **o.ref(),
            "kpi": o.prop("kpi") or o.name,
            "unit": o.prop("unit"),
            "baseline": baseline, "current": current, "target": target, "direction": direction,
            "baselineDate": baseline_date.isoformat() if baseline_date else None,
            "targetDate": target_date.isoformat() if target_date else None,
            "measuredAt": measured.isoformat() if o.prop("measuredAt") else None,
            "progress": _round(progress, 3),
            "expectedProgress": _round(expected, 3),
            "status": status,
            "goals": [g.name for g in model.targets(o.id, ["RealizationRelationship", "InfluenceRelationship"], ["Goal"])],
            "capabilities": [c.name for c in model.sources(o.id, ["RealizationRelationship", "InfluenceRelationship"], ["Capability"])],
        })
    by_goal: Dict[str, List[Dict[str, Any]]] = {}
    for o in outcomes:
        for g in o["goals"]:
            by_goal.setdefault(g, []).append(o)
    goals = []
    for g in model.of_type("Goal"):
        linked = by_goal.get(g.name, [])
        goals.append({**g.ref(), "targetDate": g.prop("targetDate"),
                      "progress": _avg(min(max(o["progress"], 0), 1) for o in linked if o["progress"] is not None),
                      "outcomes": [o["name"] for o in linked],
                      "drivers": [d.name for d in model.sources(g.id, ["InfluenceRelationship", "AssociationRelationship"], ["Driver"])]})
    tracked = [o for o in outcomes if o["progress"] is not None]
    counts = {s: sum(1 for o in tracked if o["status"] == s) for s in ("achieved", "on-track", "at-risk", "off-track", "missed", "tracking")}
    return {
        "outcomes": outcomes,
        "goals": goals,
        "drivers": [{**d.ref(), "goals": [g.name for g in model.targets(d.id, ["InfluenceRelationship", "AssociationRelationship"], ["Goal"])]}
                    for d in model.of_type("Driver")],
        "kpis": {
            "outcomeCount": len(outcomes),
            "trackedCount": len(tracked),
            **{s.replace("-", "_"): n for s, n in counts.items()},
            "onTrackOrAchieved": counts["achieved"] + counts["on-track"],
            "avgProgress": _avg(min(max(o["progress"], 0), 1) for o in tracked),
        },
    }


# ---------------------------------------------------------------------------------------------
# Implementation & Migration: roadmap, schedule and cost performance (earned-value lite)
# ---------------------------------------------------------------------------------------------

def implementation_section(ctx: Context) -> Dict[str, Any]:
    model, today = ctx.model, ctx.today
    plateau_dates = {p.name: _date(p.prop("targetDate")) for p in model.of_type("Plateau")}
    packages = []
    for wp in model.of_type("WorkPackage"):
        start, end = _date(wp.prop("startDate")), _date(wp.prop("endDate"))
        progress = _num(wp.prop("progress"))
        progress = None if progress is None else min(max(progress, 0.0), 100.0) / 100.0
        budget, actual = _num(wp.prop("budgetEUR")), _num(wp.prop("actualCostEUR"))
        planned = None
        if start and end and end > start:
            planned = min(max((today - start).days / (end - start).days, 0.0), 1.0)
        earned = budget * progress if budget is not None and progress is not None else None
        spi = progress / planned if progress is not None and planned else None
        cpi = earned / actual if earned is not None and actual else None
        overdue = bool(end and end < today and (progress is None or progress < 1))
        rag = _enum(wp.prop("rag"), _RAG_ALIASES)
        computed = "green"
        if overdue or (spi is not None and spi < 0.8) or (cpi is not None and cpi < 0.8):
            computed = "red"
        elif (spi is not None and spi < 0.95) or (cpi is not None and cpi < 0.95):
            computed = "amber"
        targets = model.targets(wp.id, ["AssociationRelationship", "RealizationRelationship", "InfluenceRelationship"])
        packages.append({
            **wp.ref(),
            "plateau": wp.prop("plateau"),
            "owner": wp.prop("owner"),
            "startDate": start.isoformat() if start else None,
            "endDate": end.isoformat() if end else None,
            "progress": _round(progress, 3),
            "plannedProgress": _round(planned, 3),
            "spi": _round(spi),
            "budgetEUR": budget,
            "actualCostEUR": actual,
            "earnedValueEUR": _round(earned, 0),
            "cpi": _round(cpi),
            "estimateAtCompletionEUR": _round(budget / cpi, 0) if budget is not None and cpi else None,
            "burn": _round(actual / budget, 3) if budget and actual is not None else None,
            "overdue": overdue,
            "rag": rag,
            "computedHealth": computed if progress is not None or start else None,
            "capabilities": [t.name for t in targets if t.type == "Capability"],
            "changes": [t.name for t in targets if t.type != "Capability"],
        })
    order = {name: i for i, (name, _d) in enumerate(sorted(plateau_dates.items(), key=lambda kv: (kv[1] is None, kv[1] or date.max)))}
    packages.sort(key=lambda w: (order.get(w["plateau"] or "", len(order)), w["startDate"] or "9999", w["name"]))
    plateaus = [{"name": name, "targetDate": d.isoformat() if d else None,
                 "workPackages": [w["id"] for w in packages if w["plateau"] == name]}
                for name, d in sorted(plateau_dates.items(), key=lambda kv: (kv[1] is None, kv[1] or date.max))]
    scheduled = [w for w in packages if w["startDate"] and w["endDate"]]
    budget_total = sum(w["budgetEUR"] or 0 for w in packages)
    actual_total = sum(w["actualCostEUR"] or 0 for w in packages)
    earned_total = sum(w["earnedValueEUR"] or 0 for w in packages)
    planned_value = sum((w["budgetEUR"] or 0) * (w["plannedProgress"] or 0) for w in packages)
    return {
        "workPackages": packages,
        "plateaus": plateaus,
        "timeline": {"start": min((w["startDate"] for w in scheduled), default=None),
                     "end": max((w["endDate"] for w in scheduled), default=None),
                     "today": today.isoformat()},
        "kpis": {
            "workPackageCount": len(packages),
            "budgetEUR": budget_total,
            "actualCostEUR": actual_total,
            "earnedValueEUR": round(earned_total),
            "plannedValueEUR": round(planned_value),
            "burn": round(actual_total / budget_total, 3) if budget_total else None,
            "progress": round(earned_total / budget_total, 3) if budget_total else None,
            "plannedProgress": round(planned_value / budget_total, 3) if budget_total else None,
            "spi": round(earned_total / planned_value, 2) if planned_value else None,
            "cpi": round(earned_total / actual_total, 2) if actual_total else None,
            "overdue": sum(1 for w in packages if w["overdue"]),
            **{f"rag_{r}": sum(1 for w in packages if w["rag"] == r) for r in RAG_LEVELS},
        },
    }


# ---------------------------------------------------------------------------------------------
# Cross-layer: are the capability gaps that matter being invested in?
# ---------------------------------------------------------------------------------------------

def cross_layer_section(strategy: Dict[str, Any]) -> Dict[str, Any]:
    gaps = [c for g in strategy["groups"] for c in g["capabilities"] if c["gap"] is not None and c["gap"] > 0]
    gaps.sort(key=lambda c: (-(c["priorityScore"] or 0), -(c["gap"] or 0), c["name"]))
    priority = [c for c in gaps if c["isPriorityGap"]]
    return {
        "gapCoverage": [{k: c[k] for k in ("id", "name", "group", "maturity", "targetMaturity", "gap", "importance",
                                           "priorityScore", "isPriorityGap", "investmentEUR", "workPackages", "appRisk")}
                        for c in gaps],
        "kpis": {
            "capabilitiesWithGap": len(gaps),
            "priorityGaps": len(priority),
            "priorityGapsAddressed": sum(1 for c in priority if c["workPackages"]),
            "priorityGapsUnaddressed": [c["name"] for c in priority if not c["workPackages"]],
            "investedCapabilitiesWithoutGap": [c["name"] for g in strategy["groups"] for c in g["capabilities"]
                                               if c["workPackages"] and c["gap"] is not None and c["gap"] <= 0],
        },
    }


# ---------------------------------------------------------------------------------------------
# Data completeness
# ---------------------------------------------------------------------------------------------

def data_quality_section(ctx: Context) -> List[Dict[str, Any]]:
    model = ctx.model
    _roots, children, _parents = _capability_tree(model)
    rows = []
    for layer, element_type, label, keys in EXPECTED_PROPERTIES:
        if element_type == "*technology":
            elements = [e for e in model.elements.values() if e.type in TECHNOLOGY_PLATFORM_TYPES]
        elif element_type == "Capability":
            elements = [c for c in model.of_type("Capability") if not children.get(c.id)]
        else:
            elements = model.of_type(element_type)
        rows.append({
            "layer": layer, "elementType": element_type, "label": label, "total": len(elements),
            "complete": sum(1 for e in elements if all(e.prop(k) is not None for k in keys)),
            "keys": [{"key": k, "filled": sum(1 for e in elements if e.prop(k) is not None)} for k in keys],
        })
    return rows


# ---------------------------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------------------------

def _headline(strategy, business, application, technology, motivation, implementation, cross) -> List[Dict[str, Any]]:
    s, b, a, t, m, i, x = (strategy["kpis"], business["kpis"], application["kpis"], technology["kpis"],
                           motivation["kpis"], implementation["kpis"], cross["kpis"])
    return [
        {"id": "strategy", "layer": "Strategy", "label": "Average capability maturity",
         "value": s["avgMaturity"], "format": "decimal", "target": s["avgTargetMaturity"], "targetLabel": "target",
         "detail": f"{x['priorityGaps']} priority gaps · {s['ratedCount']} of {s['capabilityCount']} capabilities rated"},
        {"id": "business", "layer": "Business", "label": "Media breaks in the engineering process chain",
         "value": b["mediaBreaks"] if b["ratedCount"] else None, "format": "integer",
         "detail": f"{b['ratedCount']} of {b['processCount']} processes rated"
                   + (f" · {round(b['manualShare'] * 100)}% manual" if b["manualShare"] is not None else "")},
        {"id": "application", "layer": "Application", "label": "Applications reaching end of life within 24 months",
         "value": a["endOfLifeWithin24Months"] if a["applicationCount"] else None, "format": "integer",
         "detail": f"{a['pastEndOfLife']} already past end of life · {a['applicationCount']} applications"},
        {"id": "technology", "layer": "Technology", "label": "Technology out of vendor support",
         "value": t["outOfSupportShare"], "format": "percent",
         "detail": f"{t['outOfSupport']} of {t['withSupportDate']} platforms · "
                   f"{len(t['criticalApplicationsOnUnsupported'])} critical apps affected"},
        {"id": "motivation", "layer": "Motivation", "label": "Outcome KPIs on track",
         "value": m["onTrackOrAchieved"] if m["trackedCount"] else None, "format": "integer", "of": m["trackedCount"],
         "detail": f"{m['at_risk']} at risk · {m['off_track'] + m['missed']} off track"},
        {"id": "implementation", "layer": "Implementation & Migration", "label": "Roadmap progress (earned value)",
         "value": i["progress"], "format": "percent", "target": i["plannedProgress"], "targetLabel": "planned",
         "detail": f"{i['overdue']} overdue · {i['rag_red']} red · budget burn "
                   + (f"{round(i['burn'] * 100)}%" if i["burn"] is not None else "n/a")},
    ]


def compute_dashboard(snapshot: Dict[str, Any], today: date) -> Dict[str, Any]:
    model = _build_model(snapshot)
    ctx = _prepare(model, today)
    strategy = strategy_section(ctx)
    business = business_section(ctx)
    application = application_section(ctx)
    technology = technology_section(ctx)
    motivation = motivation_section(ctx)
    implementation = implementation_section(ctx)
    cross = cross_layer_section(strategy)
    info = model.info
    return {
        "model": {
            "name": info.get("name") or "(unnamed model)",
            "elementCount": info.get("elementCount", len(model.elements)),
            "relationshipCount": info.get("relationshipCount", len(model.relationships)),
            "viewCount": info.get("viewCount"),
            "modelVersion": info.get("modelVersion"),
        },
        "asOf": today.isoformat(),
        "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "headline": _headline(strategy, business, application, technology, motivation, implementation, cross),
        "strategy": strategy,
        "business": business,
        "application": application,
        "technology": technology,
        "motivation": motivation,
        "implementation": implementation,
        "crossLayer": cross,
        "dataQuality": data_quality_section(ctx),
    }


def build_dashboard(mcp: McpClient, today: date) -> Dict[str, Any]:
    return compute_dashboard(fetch_snapshot(mcp), today)
