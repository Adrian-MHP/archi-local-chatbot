"""Transformation dashboard metrics, computed from the active Archi model read live via MCP.

Every number is derived at request time from elements, their properties and the relationships the
governance meta-model declares (meta_model.py) -- nothing is cached, entered elsewhere or assumed:

    Outcome --realizes--> Goal               Capability --realizes--> Outcome
    BusinessProcess --realizes--> Capability BusinessRole --assigned to--> BusinessProcess
    ApplicationService --serves--> BusinessProcess
    ApplicationComponent --realizes--> ApplicationService
    WorkPackage --realizes--> Plateau        Plateau --realizes--> Capability
    Plateau --associated with--> Gap         Gap --affects (association)--> BusinessProcess
    BusinessProcess (To-Be) --association "realizes"--> BusinessProcess (As-Is)

Cost, budget and project-progress figures are deliberately out of scope: they live in finance and
portfolio tools, not in the architecture model. The property schema is documented in
docs/transformation-dashboard.md. Property keys and enumeration values are matched loosely (case,
spaces, '-' and '_' ignored), because real-world models rarely follow one spelling.

The module separates fetching (fetch_snapshot) from pure computation (compute_dashboard), so the
metrics can be tested against a saved snapshot without Archi running.
"""

from __future__ import annotations

import calendar
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import meta_model
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
_CRITICALITY_ALIASES = {
    "missioncritical": "mission-critical", "businesscritical": "business-critical",
    "businessoperational": "business-operational", "administrative": "administrative",
    "administrativeservice": "administrative",
}
_IMPORTANCE_ALIASES = {"high": "high", "critical": "high", "differentiating": "high", "medium": "medium",
                       "moderate": "medium", "low": "low", "commodity": "low"}
IMPORTANCE_WEIGHT = {"high": 3, "medium": 2, "low": 1}
AUTOMATION_LEVELS = ["manual", "partial", "automated"]
_AUTOMATION_ALIASES = {"manual": "manual", "partial": "partial", "partiallyautomated": "partial",
                       "semiautomated": "partial", "automated": "automated", "fullyautomated": "automated"}
WORK_STATUSES = ["planned", "in-progress", "completed", "on-hold"]
_WORK_STATUS_ALIASES = {
    "planned": "planned", "notstarted": "planned", "open": "planned", "new": "planned",
    "inprogress": "in-progress", "active": "in-progress", "running": "in-progress", "ongoing": "in-progress",
    "completed": "completed", "done": "completed", "closed": "completed", "finished": "completed",
    "onhold": "on-hold", "paused": "on-hold", "blocked": "on-hold",
}
_DIRECTION_ALIASES = {"higher": "higher", "higherisbetter": "higher", "up": "higher", "increase": "higher",
                      "lower": "lower", "lowerisbetter": "lower", "down": "lower", "decrease": "lower"}

RISK_ORDER = ["ok", "warning", "serious", "critical"]

# Expected properties per element type -- the basis of the data-completeness panel: the "core"
# properties of the meta-model's property schema (the ones a dashboard metric needs).
EXPECTED_PROPERTIES: List[Tuple[str, str, str, List[str]]] = [
    (meta_model.layer_label(t), t, meta_model.ELEMENT_LABELS[t], meta_model.core_property_keys(t))
    for t in ("Outcome", "Capability", "BusinessProcess", "ApplicationComponent", "Plateau", "WorkPackage", "Gap")
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
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", s)
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


def _iso(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value else None


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

    def associated(self, element_id: str, other_types: Iterable[str]) -> List[Element]:
        """Associations are undirected in the meta-model, so follow them both ways."""
        return _unique(self.targets(element_id, ["AssociationRelationship"], other_types)
                       + self.sources(element_id, ["AssociationRelationship"], other_types))


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
        # The plugin validates every page's arguments ('query' is required), not only the first one's.
        page_args = {**args, "cursor": cursor, "limit": 500}
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
        props: Dict[str, str] = {}
        properties = item.get("properties") or []
        if isinstance(properties, dict):
            properties = [{"key": k, "value": v} for k, v in properties.items()]
        for p in properties:
            key, value = str(p.get("key") or "").strip(), p.get("value")
            if key:
                props[_key(key)] = "" if value is None else str(value).strip()
        elements[item["id"]] = Element(
            id=item["id"], name=str(item.get("name") or "(unnamed)"), type=str(item.get("type") or ""),
            layer=str(item.get("layer") or ""), props=props,
        )
    return Model(info=snapshot.get("info") or {}, elements=elements, relationships=snapshot.get("relationships") or [])


# ---------------------------------------------------------------------------------------------
# Shared derivations along the meta-model's relationship chains
# ---------------------------------------------------------------------------------------------

@dataclass
class Context:
    model: Model
    today: date
    app_risk: Dict[str, Dict[str, Any]] = field(default_factory=dict)          # app id -> risk record
    process_services: Dict[str, List[Element]] = field(default_factory=dict)   # process id -> serving services
    service_apps: Dict[str, List[Element]] = field(default_factory=dict)       # service id -> realizing apps
    capability_processes: Dict[str, List[Element]] = field(default_factory=dict)
    capability_plateaus: Dict[str, List[Element]] = field(default_factory=dict)
    plateau_packages: Dict[str, List[Element]] = field(default_factory=dict)  # plateau id -> work packages
    wp_status: Dict[str, Optional[str]] = field(default_factory=dict)

    def process_apps(self, process_id: str) -> List[Element]:
        return _unique(a for s in self.process_services.get(process_id, []) for a in self.service_apps.get(s.id, []))

    def capability_apps(self, capability_id: str) -> List[Element]:
        return _unique(a for p in self.capability_processes.get(capability_id, []) for a in self.process_apps(p.id))


def _app_risk(app: Element, today: date) -> Dict[str, Any]:
    reasons: List[Tuple[str, str]] = []  # (level, text)
    eol = _date(app.prop("endOfLife"))
    lifecycle = _enum(app.prop("lifecycle"), _LIFECYCLE_ALIASES)
    time_class = _enum(app.prop("timeClassification"), {t: t for t in TIME_CLASSES})
    if eol is not None:
        if eol < today:
            reasons.append(("critical", f"Past end of life ({eol.isoformat()})"))
        elif _within_months(today, eol, 12):
            reasons.append(("serious", f"End of life within 12 months ({eol.isoformat()})"))
        elif _within_months(today, eol, 24):
            reasons.append(("warning", f"End of life within 24 months ({eol.isoformat()})"))
    elif lifecycle == "end-of-life":
        reasons.append(("critical", "Lifecycle is end of life"))
    if time_class in ("eliminate", "migrate"):
        reasons.append(("warning", f"TIME classification: {TIME_LABELS[time_class]}"))
    return {"level": _worst_risk(level for level, _ in reasons), "reasons": [text for _, text in reasons]}


def _prepare(model: Model, today: date) -> Context:
    ctx = Context(model=model, today=today)
    for app in model.of_type("ApplicationComponent"):
        ctx.app_risk[app.id] = _app_risk(app, today)
    for service in model.of_type("ApplicationService"):
        ctx.service_apps[service.id] = model.sources(service.id, ["RealizationRelationship"], ["ApplicationComponent"])
    for proc in model.of_type("BusinessProcess"):
        ctx.process_services[proc.id] = model.sources(proc.id, ["ServingRelationship"], ["ApplicationService"])
    for cap in model.of_type("Capability"):
        ctx.capability_processes[cap.id] = model.sources(cap.id, ["RealizationRelationship"], ["BusinessProcess"])
        ctx.capability_plateaus[cap.id] = sorted(
            model.sources(cap.id, ["RealizationRelationship"], ["Plateau"]),
            key=lambda p: (_date(p.prop("targetDate")) or date.max, p.name.lower()))
    for plateau in model.of_type("Plateau"):
        ctx.plateau_packages[plateau.id] = model.sources(plateau.id, ["RealizationRelationship"], ["WorkPackage"])
    for wp in model.of_type("WorkPackage"):
        ctx.wp_status[wp.id] = _enum(wp.prop("status"), _WORK_STATUS_ALIASES)
    return ctx


# ---------------------------------------------------------------------------------------------
# Strategy: capability maturity heat map and roadmap status per capability
# ---------------------------------------------------------------------------------------------

def _capability_groups(model: Model) -> List[Tuple[str, List[Element]]]:
    """Group capabilities by the capabilityDomain property; where a model uses composition between
    capabilities instead, the composing (parent) capability names the group."""
    caps = model.of_type("Capability")
    parent_of: Dict[str, Element] = {}
    for cap in caps:
        for child in model.targets(cap.id, ["CompositionRelationship", "AggregationRelationship"], ["Capability"]):
            if child.id != cap.id and child.id not in parent_of:
                parent_of[child.id] = cap
    parents = {p.id for p in parent_of.values()}
    groups: Dict[str, List[Element]] = {}
    for cap in caps:
        if cap.id in parents and not cap.prop("maturity"):
            continue  # a pure grouping capability: its children carry the ratings
        domain = cap.prop("capabilityDomain") or (parent_of[cap.id].name if cap.id in parent_of else None)
        groups.setdefault(domain or "Ungrouped capabilities", []).append(cap)
    return sorted(groups.items(), key=lambda kv: (kv[0] == "Ungrouped capabilities", kv[0].lower()))


def _roadmap_status(gap: Optional[float], plateaus: List[Element], ctx: Context) -> str:
    if gap is None:
        return "not-rated"
    if gap <= 0:
        return "at-target"
    if not plateaus:
        return "unplanned"
    statuses = [ctx.wp_status.get(wp.id) for wp in ctx.plateau_packages.get(plateaus[0].id, [])]
    if statuses and all(s == "completed" for s in statuses):
        return "delivered"
    if any(s in ("in-progress", "completed") for s in statuses):
        return "in-delivery"
    return "planned"


def _capability_record(cap: Element, group: str, ctx: Context) -> Dict[str, Any]:
    maturity = _num(cap.prop("maturity"))
    target = _num(cap.prop("targetMaturity"))
    importance = _enum(cap.prop("strategicImportance"), _IMPORTANCE_ALIASES)
    gap = round(target - maturity, 2) if maturity is not None and target is not None else None
    apps = ctx.capability_apps(cap.id)
    app_records = [{**a.ref(), "risk": ctx.app_risk[a.id]["level"]} for a in apps]
    plateaus = ctx.capability_plateaus.get(cap.id, [])
    first = plateaus[0] if plateaus else None
    packages = ctx.plateau_packages.get(first.id, []) if first else []
    return {
        **cap.ref(),
        "group": group,
        "maturity": maturity,
        "targetMaturity": target,
        "gap": gap,
        # TOGAF Business Capabilities guide heat-map convention: at target / one level / two or more.
        "gapBand": None if gap is None else "at-target" if gap <= 0 else "one-level" if gap < 2 else "two-plus",
        "importance": importance,
        "priorityScore": round(gap * IMPORTANCE_WEIGHT[importance], 2) if gap is not None and gap > 0 and importance else None,
        "isPriorityGap": bool(gap is not None and gap >= 2 and importance == "high"),
        "processes": [p.name for p in ctx.capability_processes.get(cap.id, [])],
        "applications": app_records,
        "appRisk": _worst_risk(a["risk"] for a in app_records),
        "plateaus": [{"name": p.name, "targetDate": _iso(_date(p.prop("targetDate")))} for p in plateaus],
        "workPackages": [{"name": w.name, "status": ctx.wp_status.get(w.id)} for w in packages],
        "roadmapStatus": _roadmap_status(gap, plateaus, ctx),
        "owner": cap.prop("owner"),
    }


def strategy_section(ctx: Context) -> Dict[str, Any]:
    groups, leaves = [], []
    for name, caps in _capability_groups(ctx.model):
        records = [_capability_record(c, name, ctx) for c in caps]
        leaves.extend(records)
        groups.append({"name": name, "maturity": _avg(r["maturity"] for r in records),
                       "targetMaturity": _avg(r["targetMaturity"] for r in records), "capabilities": records})
    rated = [c for c in leaves if c["maturity"] is not None]
    with_target = [c for c in rated if c["targetMaturity"] is not None]
    priority = [c for c in leaves if c["isPriorityGap"]]
    return {
        "groups": groups,
        "kpis": {
            "capabilityCount": len(leaves),
            "ratedCount": len(rated),
            "avgMaturity": _avg(c["maturity"] for c in rated),
            "avgTargetMaturity": _avg(c["targetMaturity"] for c in with_target),
            "priorityGapCount": len(priority),
            "processCoverage": round(sum(1 for c in leaves if c["processes"]) / len(leaves), 3) if leaves else None,
            "applicationCoverage": round(sum(1 for c in leaves if c["applications"]) / len(leaves), 3) if leaves else None,
            "withoutProcess": [c["name"] for c in leaves if not c["processes"]],
            "withoutApplication": [c["name"] for c in leaves if c["processes"] and not c["applications"]],
        },
    }


def roadmap_coverage_section(strategy: Dict[str, Any]) -> Dict[str, Any]:
    gaps = [c for g in strategy["groups"] for c in g["capabilities"] if c["gap"] is not None and c["gap"] > 0]
    gaps.sort(key=lambda c: (-(c["priorityScore"] or 0), -(c["gap"] or 0), c["name"]))
    priority = [c for c in gaps if c["isPriorityGap"]]
    keys = ("id", "name", "group", "maturity", "targetMaturity", "gap", "importance", "priorityScore", "isPriorityGap",
            "plateaus", "workPackages", "roadmapStatus", "appRisk")
    return {
        "capabilityGaps": [{k: c[k] for k in keys} for c in gaps],
        "kpis": {
            "capabilitiesWithGap": len(gaps),
            "unplannedGaps": sum(1 for c in gaps if c["roadmapStatus"] == "unplanned"),
            "priorityGaps": len(priority),
            "priorityGapsPlanned": sum(1 for c in priority if c["roadmapStatus"] != "unplanned"),
            "priorityGapsUnplanned": [c["name"] for c in priority if c["roadmapStatus"] == "unplanned"],
        },
    }


# ---------------------------------------------------------------------------------------------
# Business: As-Is / To-Be processes from the assessment, process ratings
# ---------------------------------------------------------------------------------------------

BUSINESS_KEYS = ["maturity", "automationLevel", "mediaBreaks"]


def business_section(ctx: Context) -> Dict[str, Any]:
    model = ctx.model
    processes = model.of_type("BusinessProcess")
    is_target = {p.id: _key(p.prop("status") or "") in ("target", "tobe", "future") for p in processes}
    records = []
    for proc in processes:
        mapped = model.associated(proc.id, ["BusinessProcess"])
        records.append({
            **proc.ref(),
            "state": "to-be" if is_target[proc.id] else "as-is",
            "phase": proc.prop("processPhase"),
            "maturity": _num(proc.prop("maturity")),
            "automation": _enum(proc.prop("automationLevel"), _AUTOMATION_ALIASES),
            "mediaBreaks": _num(proc.prop("mediaBreaks")),
            "rated": proc.has_any(BUSINESS_KEYS),
            "services": [s.name for s in ctx.process_services.get(proc.id, [])],
            "capabilities": [c.name for c in model.targets(proc.id, ["RealizationRelationship"], ["Capability"])],
            "mappedTo": [m.name for m in mapped if is_target[m.id] != is_target[proc.id]],
            "gaps": [g.name for g in model.associated(proc.id, ["Gap"])],
        })
    rated = [r for r in records if r["rated"]]
    phases: Dict[str, Dict[str, Any]] = {}
    for r in rated:
        phase = phases.setdefault(r["phase"] or "No phase", {"name": r["phase"] or "No phase", "processes": [], "mediaBreaks": 0.0})
        phase["processes"].append(r["name"])
        phase["mediaBreaks"] += r["mediaBreaks"] or 0
    as_is = [r for r in records if r["state"] == "as-is"]
    to_be = [r for r in records if r["state"] == "to-be"]
    with_automation = [r for r in rated if r["automation"]]
    return {
        "processes": records,
        "phases": list(phases.values()),
        "kpis": {
            "processCount": len(records),
            "asIsCount": len(as_is),
            "toBeCount": len(to_be),
            "toBeTraced": sum(1 for r in to_be if r["mappedTo"]),
            "asIsCovered": sum(1 for r in as_is if r["mappedTo"]),
            "processesWithGaps": sum(1 for r in records if r["gaps"]),
            "ratedCount": len(rated),
            "avgMaturity": _avg(r["maturity"] for r in rated),
            "mediaBreaks": sum(r["mediaBreaks"] or 0 for r in rated),
            "manualShare": round(sum(1 for r in with_automation if r["automation"] == "manual") / len(with_automation), 3)
            if with_automation else None,
            "ratedWithoutService": [r["name"] for r in rated if not r["services"]],
        },
    }


# ---------------------------------------------------------------------------------------------
# Application: lifecycle, end of life, TIME portfolio, business use
# ---------------------------------------------------------------------------------------------

def fit_quadrant(functional: Optional[float], technical: Optional[float]) -> Optional[str]:
    """Gartner TIME quadrant from 1-4 fit scores: >= 3 counts as high fit."""
    if functional is None or technical is None:
        return None
    high_f, high_t = functional >= 3, technical >= 3
    if high_f and high_t:
        return "invest"
    if high_f:
        return "migrate"
    if high_t:
        return "tolerate"
    return "eliminate"


def application_section(ctx: Context) -> Dict[str, Any]:
    model, today = ctx.model, ctx.today
    apps = []
    for app in model.of_type("ApplicationComponent"):
        eol = _date(app.prop("endOfLife"))
        functional, technical = _num(app.prop("functionalFit")), _num(app.prop("technicalFit"))
        services = model.targets(app.id, ["RealizationRelationship"], ["ApplicationService"])
        processes = _unique(p for s in services for p in model.targets(s.id, ["ServingRelationship"], ["BusinessProcess"]))
        apps.append({
            **app.ref(),
            "category": app.prop("applicationCategory") or app.prop("category"),
            "vendor": app.prop("vendor"),
            "lifecycle": _enum(app.prop("lifecycle"), _LIFECYCLE_ALIASES),
            "endOfLife": _iso(eol),
            "monthsToEndOfLife": _months_between(today, eol) if eol else None,
            "timeClassification": _enum(app.prop("timeClassification"), {t: t for t in TIME_CLASSES}),
            "functionalFit": functional,
            "technicalFit": technical,
            "fitQuadrant": fit_quadrant(functional, technical),
            "criticality": _enum(app.prop("businessCriticality"), _CRITICALITY_ALIASES),
            "risk": ctx.app_risk[app.id],
            "services": [s.name for s in services],
            "processes": [p.name for p in processes],
        })
    eol_apps = sorted((a for a in apps if a["endOfLife"]), key=lambda a: a["endOfLife"])
    past = [a for a in eol_apps if date.fromisoformat(a["endOfLife"]) < today]
    within_24 = [a for a in eol_apps if _within_months(today, date.fromisoformat(a["endOfLife"]), 24)]
    return {
        "applications": apps,
        "lifecycle": [{"phase": p, "label": LIFECYCLE_LABELS[p], "count": sum(1 for a in apps if a["lifecycle"] == p)}
                      for p in LIFECYCLE_PHASES],
        "unknownLifecycle": sum(1 for a in apps if a["lifecycle"] is None),
        "endOfLife": [{k: a[k] for k in ("id", "name", "endOfLife", "monthsToEndOfLife", "lifecycle", "criticality",
                                          "timeClassification", "risk")} for a in eol_apps],
        "kpis": {
            "applicationCount": len(apps),
            "withEndOfLife": len(eol_apps),
            "pastEndOfLife": len(past),
            "endOfLifeWithin24Months": len(within_24),
            "migrateOrEliminate": sum(1 for a in apps if a["timeClassification"] in ("migrate", "eliminate")),
            "criticalAppsAtRisk": sum(1 for a in apps if a["criticality"] in ("mission-critical", "business-critical")
                                      and a["risk"]["level"] in ("serious", "critical")),
            "withoutBusinessUse": [a["name"] for a in apps if not a["processes"]],
        },
    }


# ---------------------------------------------------------------------------------------------
# Motivation: outcome KPIs against baseline and target
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
            "baselineDate": _iso(baseline_date),
            "targetDate": _iso(target_date),
            "measuredAt": measured.isoformat() if o.prop("measuredAt") else None,
            "progress": _round(progress, 3),
            "expectedProgress": _round(expected, 3),
            "status": status,
            "goals": [g.name for g in model.targets(o.id, ["RealizationRelationship"], ["Goal"])],
            "capabilities": [c.name for c in model.sources(o.id, ["RealizationRelationship"], ["Capability"])],
        })
    by_goal: Dict[str, List[Dict[str, Any]]] = {}
    for o in outcomes:
        for g in o["goals"]:
            by_goal.setdefault(g, []).append(o)
    goals = []
    for g in model.of_type("Goal"):
        linked = by_goal.get(g.name, [])
        goals.append({**g.ref(), "targetDate": _iso(_date(g.prop("targetDate"))),
                      "progress": _avg(min(max(o["progress"], 0), 1) for o in linked if o["progress"] is not None),
                      "outcomes": [o["name"] for o in linked]})
    tracked = [o for o in outcomes if o["progress"] is not None]
    counts = {s: sum(1 for o in tracked if o["status"] == s) for s in ("achieved", "on-track", "at-risk", "off-track", "missed", "tracking")}
    return {
        "outcomes": outcomes,
        "goals": goals,
        "kpis": {
            "outcomeCount": len(outcomes),
            "trackedCount": len(tracked),
            **{s.replace("-", "_"): n for s, n in counts.items()},
            "onTrackOrAchieved": counts["achieved"] + counts["on-track"],
            "goalsWithoutOutcome": [g["name"] for g in goals if not g["outcomes"]],
        },
    }


# ---------------------------------------------------------------------------------------------
# Implementation & Migration: plateaus, work packages, gaps
# ---------------------------------------------------------------------------------------------

def implementation_section(ctx: Context) -> Dict[str, Any]:
    model, today = ctx.model, ctx.today
    plateau_dates = {p.id: _date(p.prop("targetDate")) for p in model.of_type("Plateau")}
    packages = []
    for wp in model.of_type("WorkPackage"):
        start, end = _date(wp.prop("startDate")), _date(wp.prop("endDate"))
        status = ctx.wp_status.get(wp.id)
        plateaus = sorted(model.targets(wp.id, ["RealizationRelationship"], ["Plateau"]),
                          key=lambda p: (plateau_dates.get(p.id) or date.max, p.name.lower()))
        first_date = plateau_dates.get(plateaus[0].id) if plateaus else None
        packages.append({
            **wp.ref(),
            "owner": wp.prop("owner"),
            "status": status,
            "startDate": _iso(start),
            "endDate": _iso(end),
            "plateaus": [p.name for p in plateaus],
            "overdue": bool(end and end < today and status != "completed"),
            "lateStart": bool(start and start < today and status == "planned"),
            "endsAfterPlateau": bool(end and first_date and end > first_date and status != "completed"),
        })
    plateaus = []
    for p in sorted(model.of_type("Plateau"), key=lambda p: (plateau_dates.get(p.id) or date.max, p.name.lower())):
        wps = [w for w in packages if p.name in w["plateaus"]]
        plateaus.append({
            **p.ref(),
            "targetDate": _iso(plateau_dates.get(p.id)),
            "workPackages": [w["id"] for w in wps],
            "completed": sum(1 for w in wps if w["status"] == "completed"),
            "capabilities": [c.name for c in model.targets(p.id, ["RealizationRelationship"], ["Capability"])],
            "gaps": [g.name for g in model.associated(p.id, ["Gap"])],
        })
    gaps = []
    for g in model.of_type("Gap"):
        assigned = model.associated(g.id, ["Plateau"])
        gaps.append({**g.ref(), "plateaus": [p.name for p in assigned],
                     "processes": [p.name for p in model.associated(g.id, ["BusinessProcess"])]})
    scheduled = [w for w in packages if w["startDate"] and w["endDate"]]
    return {
        "workPackages": packages,
        "plateaus": plateaus,
        "gaps": sorted(gaps, key=lambda g: (bool(g["plateaus"]), g["name"].lower())),
        "timeline": {"start": min((w["startDate"] for w in scheduled), default=None),
                     "end": max((w["endDate"] for w in scheduled), default=None), "today": today.isoformat()},
        "kpis": {
            "workPackageCount": len(packages),
            **{s.replace("-", "_"): sum(1 for w in packages if w["status"] == s) for s in WORK_STATUSES},
            "overdue": sum(1 for w in packages if w["overdue"]),
            "lateStart": sum(1 for w in packages if w["lateStart"]),
            "endsAfterPlateau": sum(1 for w in packages if w["endsAfterPlateau"]),
            "withoutPlateau": [w["name"] for w in packages if not w["plateaus"]],
            "gapCount": len(gaps),
            "gapsWithoutPlateau": sum(1 for g in gaps if not g["plateaus"]),
        },
    }


# ---------------------------------------------------------------------------------------------
# Data completeness
# ---------------------------------------------------------------------------------------------

def data_quality_section(ctx: Context) -> List[Dict[str, Any]]:
    rows = []
    for layer, element_type, label, keys in EXPECTED_PROPERTIES:
        elements = ctx.model.of_type(element_type)
        rows.append({
            "layer": layer, "elementType": element_type, "label": label, "total": len(elements),
            "complete": sum(1 for e in elements if all(e.prop(k) is not None for k in keys)),
            "keys": [{"key": k, "filled": sum(1 for e in elements if e.prop(k) is not None)} for k in keys],
        })
    return rows


# ---------------------------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------------------------

def _headline(strategy, coverage, business, application, motivation, implementation) -> List[Dict[str, Any]]:
    s, c, b, a, m, i = (strategy["kpis"], coverage["kpis"], business["kpis"], application["kpis"],
                        motivation["kpis"], implementation["kpis"])
    return [
        {"id": "motivation", "layer": "Motivation", "label": "Outcome KPIs on track",
         "value": m["onTrackOrAchieved"] if m["trackedCount"] else None, "format": "integer", "of": m["trackedCount"],
         "detail": f"{m['at_risk']} at risk · {m['off_track'] + m['missed']} off track"},
        {"id": "strategy", "layer": "Strategy", "label": "Average capability maturity",
         "value": s["avgMaturity"], "format": "decimal", "target": s["avgTargetMaturity"], "targetLabel": "target",
         "detail": f"{c['priorityGaps']} priority gaps · {len(c['priorityGapsUnplanned'])} not planned in any plateau"},
        {"id": "business", "layer": "Business", "label": "To-Be processes traced to the As-Is",
         "value": b["toBeTraced"] if b["toBeCount"] else None, "format": "integer", "of": b["toBeCount"],
         "detail": f"{b['asIsCount']} As-Is processes · {b['processesWithGaps']} affected by gaps"},
        {"id": "application", "layer": "Application", "label": "Applications reaching end of life within 24 months",
         # Without any endOfLife date a 0 would read as "nothing at risk": show "no data" instead.
         "value": a["endOfLifeWithin24Months"] if a["withEndOfLife"] else None, "format": "integer",
         "detail": f"{a['pastEndOfLife']} already past end of life · {a['withEndOfLife']} of {a['applicationCount']} applications dated"},
        {"id": "implementation", "layer": "Implementation & Migration", "label": "Work packages completed",
         "value": i["completed"] if i["workPackageCount"] else None, "format": "integer", "of": i["workPackageCount"],
         "detail": f"{i['in_progress']} in progress · {i['overdue']} overdue · {i['gapsWithoutPlateau']} gaps without plateau"},
    ]


def compute_dashboard(snapshot: Dict[str, Any], today: date) -> Dict[str, Any]:
    model = _build_model(snapshot)
    ctx = _prepare(model, today)
    strategy = strategy_section(ctx)
    coverage = roadmap_coverage_section(strategy)
    business = business_section(ctx)
    application = application_section(ctx)
    motivation = motivation_section(ctx)
    implementation = implementation_section(ctx)
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
        "headline": _headline(strategy, coverage, business, application, motivation, implementation),
        "motivation": motivation,
        "strategy": strategy,
        "roadmapCoverage": coverage,
        "business": business,
        "application": application,
        "implementation": implementation,
        "dataQuality": data_quality_section(ctx),
    }


def build_dashboard(mcp: McpClient, today: date) -> Dict[str, Any]:
    return compute_dashboard(fetch_snapshot(mcp), today)
