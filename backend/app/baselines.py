"""Assessment baselines: the model frozen at the close of an assessment cycle, versioned in git.

Archi only knows the current state of a model, but steering a transformation needs time series:
progress is a re-assessment against a baseline. A baseline is the complete model -- elements with
their properties and the relationships between them, without documentation texts and views -- at
the close of an assessment cycle. Each one is a folder plus one commit and tag in a git repository
outside this app (default ~/Documents/Archi/assessment-baselines; it holds client models, so it
never goes into the app's own repository):

    <model>/<baseline id>/baseline.json    name, date, note, source (live model or coArchi commit)
    <model>/<baseline id>/snapshot.json    elements and relationships, one per line (readable diffs)
    <model>/<baseline id>/kpis.json        the trend figures at that date, for people reading the repo

Every figure of a baseline is computed with the dashboard code from its snapshot, as of the
baseline's date -- the same definitions as for the live model, so all points of a trend compare
like with like. Snapshots come from the live model via MCP or from a commit of the model's coArchi
repository (coarchi.py).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from . import dashboard, meta_model

FORMAT = 1
DEFAULT_AUTHOR = "Archi Local Chatbot <baselines@archi-local-chatbot.local>"
BASELINE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,119}$")
CURRENT = "current"  # the id of the live model in a progress report

README = """# Assessment baselines

Written by Archi Local Chatbot. A baseline is the complete Archi model -- elements with their
properties and the relationships between them, without documentation texts and views -- at the
close of an assessment cycle. The transformation dashboard measures progress against these
baselines ("Progress" section) and the assessment shows the baseline ratings during a re-assessment.

    <model>/<baseline id>/baseline.json   name, date, note, source (live model via MCP or a coArchi commit)
    <model>/<baseline id>/snapshot.json   elements and relationships, one per line
    <model>/<baseline id>/kpis.json       the dashboard's trend figures at that date

Every baseline is one commit with the tag baseline/<model>/<baseline id>. What changed in the model
between two cycles:

    git diff --no-index <model>/<id 1>/snapshot.json <model>/<id 2>/snapshot.json

Confidentiality: this repository contains client models. Keep it private and push it only to a
remote the client has approved.
"""


class BaselineError(RuntimeError):
    """A baseline operation failed; the message is meant for the user."""


class BaselineNotFound(BaselineError):
    pass


class BaselineConflict(BaselineError):
    pass


class BaselineStorageError(BaselineError):
    """git or the file system failed (not the user's input)."""


# ---------------------------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------------------------

_TRANSLITERATE = str.maketrans({"ß": "ss", "ẞ": "SS", "æ": "ae", "Æ": "AE", "ø": "o", "Ø": "O", "œ": "oe", "Œ": "OE"})


def slugify(text: str, fallback: str = "baseline", max_len: int = 60) -> str:
    value = unicodedata.normalize("NFKD", str(text or "").translate(_TRANSLITERATE)).encode("ascii", "ignore").decode().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")[:max_len].strip("-")
    return value or fallback


def normalize_model_name(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip().casefold()


def _properties(raw: Any) -> List[Dict[str, str]]:
    if isinstance(raw, dict):
        raw = [{"key": k, "value": v} for k, v in raw.items()]
    out = []
    for prop in raw or []:
        if not isinstance(prop, dict):
            continue
        key = str(prop.get("key") or "").strip()
        if key:
            out.append({"key": key, "value": "" if prop.get("value") is None else str(prop.get("value"))})
    return out


def normalize_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """The stored form of a model: stable keys, sorted by id, so equal models give equal files."""
    info = snapshot.get("info") or {}
    elements = []
    for item in snapshot.get("elements") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        element = {"id": str(item["id"]), "name": str(item.get("name") or ""), "type": str(item.get("type") or ""),
                   "layer": str(item.get("layer") or "")}
        props = _properties(item.get("properties"))
        if props:
            element["properties"] = props
        elements.append(element)
    relationships = []
    for item in snapshot.get("relationships") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        relationship = {"id": str(item["id"]), "type": str(item.get("type") or ""),
                        "sourceId": str(item.get("sourceId") or ""), "targetId": str(item.get("targetId") or "")}
        if item.get("name"):
            relationship["name"] = str(item["name"])
        props = _properties(item.get("properties"))
        if props:
            relationship["properties"] = props
        relationships.append(relationship)
    elements.sort(key=lambda e: e["id"])
    relationships.sort(key=lambda r: r["id"])
    model = {
        "name": str(info.get("name") or ""),
        "id": info.get("id") or None,
        "elementCount": len(elements),
        "relationshipCount": len(relationships),
        "viewCount": info.get("viewCount"),
        "modelVersion": None if info.get("modelVersion") is None else str(info.get("modelVersion")),
    }
    return {"format": FORMAT, "model": model, "elements": elements, "relationships": relationships}


def serialize_snapshot(snapshot: Dict[str, Any]) -> str:
    """JSON with one element / relationship per line, so `git diff` shows exactly what changed."""
    dump = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True)  # noqa: E731

    def block(name: str, items: List[Dict[str, Any]]) -> str:
        if not items:
            return f'  "{name}": []'
        return f'  "{name}": [\n' + ",\n".join(f"    {dump(item)}" for item in items) + "\n  ]"

    return (
        "{\n"
        f'  "format": {snapshot["format"]},\n'
        f'  "model": {dump(snapshot["model"])},\n'
        f'{block("elements", snapshot["elements"])},\n'
        f'{block("relationships", snapshot["relationships"])}\n'
        "}\n"
    )


def dashboard_input(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """A stored snapshot in the shape dashboard.compute_dashboard() reads."""
    model = snapshot.get("model") or {}
    return {
        "info": {"name": model.get("name"), "elementCount": len(snapshot["elements"]),
                 "relationshipCount": len(snapshot["relationships"]), "viewCount": model.get("viewCount"),
                 "modelVersion": model.get("modelVersion")},
        "elements": snapshot["elements"],
        "relationships": snapshot["relationships"],
    }


def evaluate(snapshot: Dict[str, Any], as_of: date) -> Dict[str, Any]:
    return dashboard.compute_dashboard(dashboard_input(snapshot), as_of)


# ---------------------------------------------------------------------------------------------
# Trend figures: one value per assessment cycle, from the dashboard's own metrics
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Metric:
    id: str
    label: str
    section: str
    format: str                                  # integer | decimal | percent
    better: Optional[str]                        # higher | lower | None (neutral, e.g. model size)
    value: Callable[[Dict[str, Any]], Optional[float]]
    of: Optional[Callable[[Dict[str, Any]], Optional[float]]] = None
    reference: Optional[Callable[[Dict[str, Any]], Optional[float]]] = None
    reference_label: Optional[str] = None
    domain: Optional[Tuple[float, float]] = None
    headline: Optional[str] = None               # the dashboard KPI tile this metric belongs to


def _k(d: Dict[str, Any], section: str) -> Dict[str, Any]:
    return d[section]["kpis"]


def _completeness(d: Dict[str, Any]) -> Optional[float]:
    total = sum(row["total"] for row in d["dataQuality"])
    return round(sum(row["complete"] for row in d["dataQuality"]) / total, 3) if total else None


def _rated(d: Dict[str, Any]) -> bool:
    return bool(_k(d, "strategy")["ratedCount"])


METRICS: List[Metric] = [
    Metric("avgMaturity", "Average capability maturity", "Strategy", "decimal", "higher",
           lambda d: _k(d, "strategy")["avgMaturity"], reference=lambda d: _k(d, "strategy")["avgTargetMaturity"],
           reference_label="target", domain=(0, 5), headline="strategy"),
    Metric("priorityGaps", "Priority capability gaps", "Gaps & roadmap", "integer", "lower",
           lambda d: _k(d, "roadmapCoverage")["priorityGaps"] if _rated(d) else None),
    Metric("unplannedGaps", "Capability gaps not planned in a plateau", "Gaps & roadmap", "integer", "lower",
           lambda d: _k(d, "roadmapCoverage")["unplannedGaps"] if _rated(d) else None),
    Metric("outcomesOnTrack", "Outcome KPIs on track or achieved", "Motivation", "integer", "higher",
           lambda d: _k(d, "motivation")["onTrackOrAchieved"] if _k(d, "motivation")["trackedCount"] else None,
           of=lambda d: _k(d, "motivation")["trackedCount"] or None, headline="motivation"),
    Metric("toBeTraced", "To-Be processes traced to the As-Is", "Business", "integer", "higher",
           lambda d: _k(d, "business")["toBeTraced"] if _k(d, "business")["toBeCount"] else None,
           of=lambda d: _k(d, "business")["toBeCount"] or None, headline="business"),
    Metric("processMaturity", "Average process maturity", "Business", "decimal", "higher",
           lambda d: _k(d, "business")["avgMaturity"], domain=(0, 5)),
    Metric("mediaBreaks", "Media breaks in the rated processes", "Business", "integer", "lower",
           lambda d: _k(d, "business")["mediaBreaks"] if _k(d, "business")["ratedCount"] else None),
    Metric("eolWithin24", "Applications reaching end of life within 24 months", "Application", "integer", "lower",
           lambda d: _k(d, "application")["endOfLifeWithin24Months"] if _k(d, "application")["withEndOfLife"] else None,
           headline="application"),
    Metric("pastEol", "Applications past end of life", "Application", "integer", "lower",
           lambda d: _k(d, "application")["pastEndOfLife"] if _k(d, "application")["withEndOfLife"] else None),
    Metric("wpCompleted", "Work packages completed", "Implementation & Migration", "integer", "higher",
           lambda d: _k(d, "implementation")["completed"] if _k(d, "implementation")["workPackageCount"] else None,
           of=lambda d: _k(d, "implementation")["workPackageCount"] or None, headline="implementation"),
    Metric("wpOverdue", "Overdue work packages", "Implementation & Migration", "integer", "lower",
           lambda d: _k(d, "implementation")["overdue"] if _k(d, "implementation")["workPackageCount"] else None),
    Metric("gapsWithoutPlateau", "Gaps not assigned to a plateau", "Implementation & Migration", "integer", "lower",
           lambda d: _k(d, "implementation")["gapsWithoutPlateau"] if _k(d, "implementation")["gapCount"] else None),
    Metric("dataCompleteness", "Data completeness (core properties)", "Data completeness", "percent", "higher",
           _completeness, domain=(0, 1)),
    Metric("elements", "Elements in the model", "Model", "integer", None, lambda d: d["model"]["elementCount"]),
    Metric("relationships", "Relationships in the model", "Model", "integer", None,
           lambda d: d["model"]["relationshipCount"]),
]
METRIC_BY_ID = {m.id: m for m in METRICS}


def _round(value: Any, digits: int = 3) -> Optional[float]:
    if value is None:
        return None
    number = float(value)
    return int(number) if number.is_integer() else round(number, digits)


def metric_definition(metric: Metric) -> Dict[str, Any]:
    return {"id": metric.id, "label": metric.label, "section": metric.section, "format": metric.format,
            "better": metric.better, "referenceLabel": metric.reference_label,
            "domain": list(metric.domain) if metric.domain else None, "headline": metric.headline}


def figures(d: Dict[str, Any]) -> Dict[str, Dict[str, Optional[float]]]:
    """The trend values of one evaluated model: value, reference (e.g. target) and total per metric."""
    out = {}
    for m in METRICS:
        out[m.id] = {"value": _round(m.value(d)),
                     "reference": _round(m.reference(d)) if m.reference else None,
                     "of": _round(m.of(d)) if m.of else None}
    return out


def assess(metric: Metric, before: Optional[float], after: Optional[float]) -> str:
    if before is None or after is None:
        return "n/a"
    if abs(after - before) < 1e-9:
        return "same"
    if metric.better is None:
        return "changed"
    return "better" if (after > before) == (metric.better == "higher") else "worse"


def compare_figures(before: Dict[str, Any], after: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Metric by metric between two evaluated models (only metrics with a value on either side)."""
    a, b = figures(before), figures(after)
    rows = []
    for m in METRICS:
        x, y = a[m.id]["value"], b[m.id]["value"]
        if x is None and y is None:
            continue
        rows.append({**metric_definition(m), "from": x, "to": y,
                     "fromOf": a[m.id]["of"], "toOf": b[m.id]["of"],
                     "delta": _round(y - x) if x is not None and y is not None else None,
                     "assessment": assess(m, x, y)})
    return rows


def _capabilities(d: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {c["id"]: c for g in d["strategy"]["groups"] for c in g["capabilities"]}


def highlights(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
    """Plain-language progress between two evaluated models, for the dashboard and the summary."""
    lines: List[str] = []
    caps_a, caps_b = _capabilities(before), _capabilities(after)
    improved = [c for i, c in caps_b.items() if i in caps_a and None not in (c["maturity"], caps_a[i]["maturity"])
                and c["maturity"] > caps_a[i]["maturity"]]
    declined = [c for i, c in caps_b.items() if i in caps_a and None not in (c["maturity"], caps_a[i]["maturity"])
                and c["maturity"] < caps_a[i]["maturity"]]
    newly_rated = [c for i, c in caps_b.items() if c["maturity"] is not None and (i not in caps_a or caps_a[i]["maturity"] is None)]
    reached = [c for c in improved if c["targetMaturity"] is not None and c["maturity"] >= c["targetMaturity"]]
    if improved or declined:
        text = f"{len(improved)} capabilit{'y' if len(improved) == 1 else 'ies'} improved in maturity"
        if improved:
            text += " (" + ", ".join(f"{c['name']} {_fmt(caps_a[c['id']]['maturity'])}→{_fmt(c['maturity'])}" for c in improved[:4])
            text += ", …)" if len(improved) > 4 else ")"
        if declined:
            text += f"; {len(declined)} declined (" + ", ".join(c["name"] for c in declined[:3]) + ("…" if len(declined) > 3 else "") + ")"
        lines.append(text)
    if reached:
        lines.append(f"{len(reached)} capabilit{'y' if len(reached) == 1 else 'ies'} reached the target maturity: "
                     + ", ".join(c["name"] for c in reached[:5]))
    if newly_rated:
        lines.append(f"{len(newly_rated)} capabilit{'y' if len(newly_rated) == 1 else 'ies'} rated for the first time")

    wp_a = {w["id"]: w for w in before["implementation"]["workPackages"]}
    completed = [w for w in after["implementation"]["workPackages"]
                 if w["status"] == "completed" and (wp_a.get(w["id"]) or {}).get("status") != "completed"]
    if completed:
        lines.append(f"{len(completed)} work package{'' if len(completed) == 1 else 's'} completed: "
                     + ", ".join(w["name"] for w in completed[:5]) + ("…" if len(completed) > 5 else ""))
    gaps_a = {g["id"]: g for g in before["implementation"]["gaps"]}
    gaps_b = {g["id"]: g for g in after["implementation"]["gaps"]}
    closed = [g for i, g in gaps_a.items() if i not in gaps_b]
    new = [g for i, g in gaps_b.items() if i not in gaps_a]
    if closed:
        lines.append(f"{len(closed)} gap{'' if len(closed) == 1 else 's'} closed (no longer in the model): "
                     + ", ".join(g["name"] for g in closed[:5]) + ("…" if len(closed) > 5 else ""))
    if new:
        lines.append(f"{len(new)} new gap{'' if len(new) == 1 else 's'} identified")

    outcomes_a = {o["id"]: o for o in before["motivation"]["outcomes"]}
    for o in after["motivation"]["outcomes"]:
        prev = outcomes_a.get(o["id"])
        if prev and None not in (prev["current"], o["current"]) and prev["current"] != o["current"]:
            unit = f" {o['unit']}" if o["unit"] else ""
            target = f" (target {_fmt(o['target'])}{unit})" if o["target"] is not None else ""
            lines.append(f"{o['kpi']}: {_fmt(prev['current'])} → {_fmt(o['current'])}{unit}{target}")
    return lines


def _fmt(value: Any) -> str:
    if value is None:
        return "–"
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:.1f}"


# ---------------------------------------------------------------------------------------------
# What changed in the model between two snapshots
# ---------------------------------------------------------------------------------------------

_TYPE_ORDER = {t: i for i, t in enumerate(meta_model.PROPERTIES)}


def _prop_map(item: Dict[str, Any]) -> Dict[str, str]:
    return {p["key"]: str(p.get("value") or "").strip() for p in item.get("properties") or []}


def diff_snapshots(before: Dict[str, Any], after: Dict[str, Any], limit: int = 400) -> Dict[str, Any]:
    old_el = {e["id"]: e for e in before["elements"]}
    new_el = {e["id"]: e for e in after["elements"]}
    added = sorted((new_el[i] for i in new_el.keys() - old_el.keys()), key=lambda e: (e["type"], e["name"].lower()))
    removed = sorted((old_el[i] for i in old_el.keys() - new_el.keys()), key=lambda e: (e["type"], e["name"].lower()))
    changes: List[Dict[str, Any]] = []
    renamed: List[Dict[str, Any]] = []
    changed_ids = set()
    for element_id in old_el.keys() & new_el.keys():
        a, b = old_el[element_id], new_el[element_id]
        if a["name"] != b["name"]:
            renamed.append({"id": element_id, "type": b["type"], "from": a["name"], "to": b["name"]})
            changed_ids.add(element_id)
        if a["type"] != b["type"]:
            changes.append({"id": element_id, "name": b["name"], "type": b["type"], "key": "(type)", "label": "Element type",
                            "from": a["type"], "to": b["type"], "steering": False})
            changed_ids.add(element_id)
        pa, pb = _prop_map(a), _prop_map(b)
        for key in sorted(pa.keys() | pb.keys()):
            va, vb = pa.get(key) or None, pb.get(key) or None
            if va == vb:
                continue
            definition = meta_model.property_definition(b["type"], key)
            changes.append({"id": element_id, "name": b["name"], "type": b["type"], "key": key,
                            "label": definition["label"] if definition else key, "from": va, "to": vb,
                            "steering": definition is not None})
            changed_ids.add(element_id)
    changes.sort(key=lambda c: (not c["steering"], _TYPE_ORDER.get(c["type"], 99), c["type"], c["name"].lower(), c["key"]))

    old_rel = {r["id"]: r for r in before["relationships"]}
    new_rel = {r["id"]: r for r in after["relationships"]}
    rel_added = [new_rel[i] for i in new_rel.keys() - old_rel.keys()]
    rel_removed = [old_rel[i] for i in old_rel.keys() - new_rel.keys()]
    rel_changed = [i for i in old_rel.keys() & new_rel.keys()
                   if (old_rel[i]["sourceId"], old_rel[i]["targetId"], old_rel[i]["type"])
                   != (new_rel[i]["sourceId"], new_rel[i]["targetId"], new_rel[i]["type"])]

    by_type: Dict[str, Dict[str, int]] = {}
    for kind, items in (("added", added), ("removed", removed), ("changed", [new_el[i] for i in changed_ids])):
        for e in items:
            by_type.setdefault(e["type"], {"added": 0, "removed": 0, "changed": 0})[kind] += 1
    rel_by_type: Dict[str, Dict[str, int]] = {}
    for kind, items in (("added", rel_added), ("removed", rel_removed)):
        for r in items:
            rel_by_type.setdefault(r["type"], {"added": 0, "removed": 0})[kind] += 1

    names = {**{i: e["name"] for i, e in old_el.items()}, **{i: e["name"] for i, e in new_el.items()}}

    def describe(r: Dict[str, Any]) -> Dict[str, str]:
        return {"type": r["type"], "source": names.get(r["sourceId"], "?"), "target": names.get(r["targetId"], "?")}

    return {
        "summary": {
            "elementsAdded": len(added), "elementsRemoved": len(removed), "elementsChanged": len(changed_ids),
            "propertyChanges": len(changes), "steeringChanges": sum(1 for c in changes if c["steering"]),
            "relationshipsAdded": len(rel_added), "relationshipsRemoved": len(rel_removed),
            "relationshipsChanged": len(rel_changed),
        },
        "byType": [{"type": t, **counts} for t, counts in sorted(by_type.items(), key=lambda kv: (_TYPE_ORDER.get(kv[0], 99), kv[0]))],
        "relationshipsByType": [{"type": t, **counts} for t, counts in sorted(rel_by_type.items())],
        "propertyChanges": changes[:limit],
        "renamed": sorted(renamed, key=lambda r: (r["type"], r["to"].lower()))[:limit],
        "added": [{"id": e["id"], "type": e["type"], "name": e["name"]} for e in added[:limit]],
        "removed": [{"id": e["id"], "type": e["type"], "name": e["name"]} for e in removed[:limit]],
        "relationshipsAddedSample": [describe(r) for r in rel_added[:50]],
        "relationshipsRemovedSample": [describe(r) for r in rel_removed[:50]],
        "truncated": len(changes) > limit or len(added) > limit or len(removed) > limit,
    }


# ---------------------------------------------------------------------------------------------
# Progress report: every cycle of a model plus the live model
# ---------------------------------------------------------------------------------------------

def build_progress(points: List[Dict[str, Any]], compare_from: Optional[str] = None,
                   compare_to: Optional[str] = None) -> Dict[str, Any]:
    """points: [{"id", "label", "date", "kind", "snapshot", "dashboard", ...}] in time order. Returns the
    trend per metric, maturity per capability, outcome KPI measurements per cycle, and the comparison
    of two points (defaults: the latest baseline against the live model)."""
    public_keys = ("id", "label", "date", "moment", "kind", "source", "note")
    values = {p["id"]: figures(p["dashboard"]) for p in points}
    metrics = []
    for m in METRICS:
        series = {pid: v[m.id]["value"] for pid, v in values.items()}
        if all(v is None for v in series.values()):
            continue
        metrics.append({**metric_definition(m), "values": series,
                        "references": {pid: v[m.id]["reference"] for pid, v in values.items()} if m.reference else None,
                        "of": {pid: v[m.id]["of"] for pid, v in values.items()} if m.of else None})

    capabilities: Dict[str, Dict[str, Any]] = {}
    outcomes: Dict[str, Dict[str, Any]] = {}
    for p in points:
        for c in _capabilities(p["dashboard"]).values():
            row = capabilities.setdefault(c["id"], {"id": c["id"], "values": {}, "targets": {}})
            row.update(name=c["name"], group=c["group"])
            row["values"][p["id"]] = c["maturity"]
            row["targets"][p["id"]] = c["targetMaturity"]
        for o in p["dashboard"]["motivation"]["outcomes"]:
            row = outcomes.setdefault(o["id"], {"id": o["id"], "values": {}, "measuredAt": {}})
            row.update({k: o[k] for k in ("name", "kpi", "unit", "direction", "baseline", "baselineDate", "target", "targetDate")})
            row["values"][p["id"]] = o["current"]
            row["measuredAt"][p["id"]] = o["measuredAt"]

    ids = [p["id"] for p in points]
    baseline_ids = [p["id"] for p in points if p["kind"] == "baseline"]
    if compare_from not in ids:
        compare_from = baseline_ids[-1] if baseline_ids else None
    if compare_to not in ids:
        compare_to = CURRENT if CURRENT in ids else (ids[-1] if ids else None)
    comparison = None
    if compare_from and compare_to and compare_from != compare_to:
        a = next(p for p in points if p["id"] == compare_from)
        b = next(p for p in points if p["id"] == compare_to)
        comparison = {
            "from": {k: a.get(k) for k in public_keys},
            "to": {k: b.get(k) for k in public_keys},
            "kpis": compare_figures(a["dashboard"], b["dashboard"]),
            "highlights": highlights(a["dashboard"], b["dashboard"]),
            "changes": diff_snapshots(a["snapshot"], b["snapshot"]),
        }
    return {
        "points": [{k: p.get(k) for k in public_keys} for p in points],
        "metrics": metrics,
        "capabilities": sorted((c for c in capabilities.values() if any(v is not None for v in c["values"].values())),
                               key=lambda c: (c["group"].lower(), c["name"].lower())),
        "outcomes": sorted((o for o in outcomes.values()
                            if any(v is not None for v in o["values"].values()) or o["baseline"] is not None),
                           key=lambda o: o["kpi"].lower()),
        "comparison": comparison,
    }


def steering_values(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """element id -> {property: value} for the meta-model's steering properties: what the previous
    assessment cycle rated, shown next to the inputs of a re-assessment."""
    out: Dict[str, Dict[str, str]] = {}
    for e in snapshot["elements"]:
        keys = {p["key"] for p in meta_model.properties_for(e["type"])}
        if not keys:
            continue
        values = {k: v for k, v in _prop_map(e).items() if k in keys and v}
        if values:
            out[e["id"]] = {k: (meta_model.normalize_property_value(e["type"], k, v)[0] or v) for k, v in values.items()}
    return out


# ---------------------------------------------------------------------------------------------
# Git store
# ---------------------------------------------------------------------------------------------

def _split_author(author: str) -> Tuple[str, str]:
    match = re.match(r"^\s*(.+?)\s*<([^>]+)>\s*$", author or "")
    return (match.group(1), match.group(2)) if match else ("Archi Local Chatbot", "baselines@archi-local-chatbot.local")


class BaselineStore:
    def __init__(self, root: Path, *, author: str = DEFAULT_AUTHOR, display_path: str = ""):
        self.root = Path(root)
        self.author_name, self.author_email = _split_author(author)
        self.display_path = display_path or str(self.root)
        self._lock = threading.Lock()
        self._snapshots: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._dashboards: Dict[Tuple[str, float, str], Dict[str, Any]] = {}

    # -- git -------------------------------------------------------------------------------
    def _git(self, *args: str, check: bool = True) -> str:
        command = ["git", "-c", f"safe.directory={self.root}", "-c", f"user.name={self.author_name}",
                   "-c", f"user.email={self.author_email}", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args]
        try:
            result = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=60)
        except FileNotFoundError as exc:
            raise BaselineStorageError("git is not installed in the backend; baselines cannot be versioned.") from exc
        if check and result.returncode != 0:
            raise BaselineStorageError(f"git {args[0]} failed: {(result.stderr or result.stdout).strip()[:300]}")
        return result.stdout

    @property
    def initialized(self) -> bool:
        return (self.root / ".git").exists()

    def _ensure_repo(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BaselineStorageError(f"The baseline folder {self.display_path} cannot be created: {exc}") from exc
        if self.initialized:
            return
        self._git("init", "-q", "-b", "main")
        (self.root / "README.md").write_text(README, encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-q", "-m", "Create the assessment baseline repository")

    def status(self) -> Dict[str, Any]:
        info: Dict[str, Any] = {"path": self.display_path, "initialized": self.initialized, "commits": 0, "lastCommit": None,
                                "error": None}
        if not self.initialized:
            return info
        try:
            info["commits"] = int(self._git("rev-list", "--count", "HEAD").strip() or 0)
            sha, when, subject = (self._git("log", "-1", "--format=%h%x1f%cI%x1f%s").strip().split("\x1f") + ["", "", ""])[:3]
            info["lastCommit"] = {"commit": sha, "date": when, "message": subject}
        except BaselineError as exc:
            info["error"] = str(exc)
        return info

    # -- reading ------------------------------------------------------------------------------
    def list(self, model_name: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.root.exists():
            return []
        wanted = normalize_model_name(model_name) if model_name else None
        metas = []
        for path in self.root.glob("*/*/baseline.json"):
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(meta, dict) or meta.get("format") != FORMAT or not BASELINE_ID_RE.match(str(meta.get("id") or "")):
                continue
            if wanted and normalize_model_name((meta.get("model") or {}).get("name")) != wanted:
                continue
            meta["folder"] = f"{path.parent.parent.name}/{path.parent.name}"
            metas.append(meta)
        metas.sort(key=lambda m: (m.get("date") or "", moment(m)))
        return metas

    def get(self, baseline_id: str, model_name: Optional[str] = None) -> Dict[str, Any]:
        if not BASELINE_ID_RE.match(str(baseline_id or "")):
            raise BaselineNotFound(f"Unknown baseline '{baseline_id}'.")
        for meta in self.list(model_name):
            if meta["id"] == baseline_id:
                return meta
        raise BaselineNotFound(f"Unknown baseline '{baseline_id}'.")

    def snapshot(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        path = self.root / meta["folder"] / "snapshot.json"
        try:
            mtime = path.stat().st_mtime
            cached = self._snapshots.get(meta["folder"])
            if cached and cached[0] == mtime:
                return cached[1]
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BaselineStorageError(f"The snapshot of baseline '{meta['id']}' cannot be read: {exc}") from exc
        self._snapshots[meta["folder"]] = (mtime, data)
        return data

    def evaluated(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        """The dashboard of a baseline as of its own date (cached: a stored snapshot never changes)."""
        snapshot = self.snapshot(meta)
        key = (meta["folder"], self._snapshots[meta["folder"]][0], meta["date"])
        if key not in self._dashboards:
            self._dashboards[key] = evaluate(snapshot, date.fromisoformat(meta["date"]))
        return self._dashboards[key]

    # -- writing ------------------------------------------------------------------------------
    def create(self, snapshot: Dict[str, Any], *, name: str, note: str = "", day: date,
               source: Dict[str, Any]) -> Dict[str, Any]:
        name = re.sub(r"\s+", " ", str(name or "")).strip()
        note = str(note or "").strip()
        if not name:
            raise BaselineError("A baseline needs a name, e.g. 'Assessment Q4 2026'.")
        if len(name) > 80 or len(note) > 1000:
            raise BaselineError("The name is limited to 80 characters and the note to 1000.")
        model_name = (snapshot.get("model") or {}).get("name") or ""
        if not model_name:
            raise BaselineError("The model has no name, so the baseline cannot be assigned to it.")
        with self._lock:
            self._ensure_repo()
            if source.get("type") == "coarchi":
                for meta in self.list(model_name):
                    if (meta.get("source") or {}).get("commit") == source.get("commit"):
                        raise BaselineConflict(f"This coArchi commit is already the baseline '{meta['name']}'.")
            model_folder = slugify(model_name, "model")
            base_id = f"{day.isoformat()}-{slugify(name)}"[:110].rstrip("-")
            baseline_id, n = base_id, 2
            while (self.root / model_folder / baseline_id).exists():
                baseline_id, n = f"{base_id}-{n}", n + 1
            folder = self.root / model_folder / baseline_id
            tag = f"baseline/{model_folder}/{baseline_id}"
            meta = {
                "format": FORMAT,
                "id": baseline_id,
                "name": name,
                "note": note,
                "date": day.isoformat(),
                "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "model": snapshot["model"],
                "source": source,
                "tag": tag,
            }
            values = figures(evaluate(snapshot, day))
            kpis = {"date": day.isoformat(), "metrics": {m.id: {"label": m.label, **values[m.id]} for m in METRICS}}
            relative = f"{model_folder}/{baseline_id}"
            try:
                folder.mkdir(parents=True)
                (folder / "baseline.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                (folder / "snapshot.json").write_text(serialize_snapshot(snapshot), encoding="utf-8")
                (folder / "kpis.json").write_text(json.dumps(kpis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                self._git("add", "--", relative)
                message = f"Baseline: {name} ({model_name}, {day.isoformat()})"
                body = "\n".join(filter(None, [
                    note,
                    f"Source: {_describe_source(source)}",
                    f"{snapshot['model']['elementCount']} elements, {snapshot['model']['relationshipCount']} relationships",
                ]))
                self._git("commit", "-q", "-m", message, "-m", body)
                self._git("tag", "-a", tag, "-m", f"{name}\n\n{note}".strip())
            except (OSError, BaselineError) as exc:
                self._git("reset", "-q", "--", relative, check=False)
                shutil.rmtree(folder, ignore_errors=True)
                if isinstance(exc, BaselineError):
                    raise
                raise BaselineStorageError(f"The baseline could not be written to {self.display_path}: {exc}") from exc
            meta["folder"] = relative
            meta["commit"] = self._git("rev-parse", "--short", "HEAD").strip()
            return meta

    def delete(self, baseline_id: str, model_name: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            meta = self.get(baseline_id, model_name)
            self._git("rm", "-r", "-q", "--ignore-unmatch", "--", meta["folder"])
            shutil.rmtree(self.root / meta["folder"], ignore_errors=True)
            if self._git("status", "--porcelain", "--", meta["folder"], check=False).strip() or \
                    self._git("diff", "--cached", "--name-only", check=False).strip():
                self._git("commit", "-q", "-m", f"Remove baseline: {meta['name']} ({meta['model']['name']}, {meta['date']})",
                          "-m", "The baseline stays in the history of this repository.")
            self._git("tag", "-d", meta.get("tag") or f"baseline/{meta['folder']}", check=False)
            self._snapshots.pop(meta["folder"], None)
            return meta


def _describe_source(source: Dict[str, Any]) -> str:
    if source.get("type") == "coarchi":
        return (f"coArchi repository {source.get('repository')}, commit {str(source.get('commit') or '')[:10]} "
                f"of {source.get('commitDate')} ({source.get('message')})")
    version = source.get("modelVersion")
    return "live model in Archi via MCP" + (f" (model version {version})" if version else "")


def moment(meta: Dict[str, Any]) -> str:
    """When the baseline's model state existed, in UTC: the commit time of a coArchi import, else the
    time it was saved -- as long as that agrees with the baseline's date (the date wins; the time only
    orders baselines of the same day). Places baselines on a time axis."""
    fallback = f"{meta.get('date')}T12:00:00+00:00"
    try:
        day = date.fromisoformat(str(meta.get("date")))
    except ValueError:
        return fallback
    for raw in ((meta.get("source") or {}).get("commitDate"), meta.get("createdAt")):
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
        if abs((parsed.date() - day).days) <= 1:
            return parsed.isoformat(timespec="seconds")
    return fallback


def latest_before(metas: Iterable[Dict[str, Any]], day: date) -> Optional[Dict[str, Any]]:
    """The most recent baseline taken on or before the given day (else the most recent one)."""
    metas = list(metas)
    eligible = [m for m in metas if m["date"] <= day.isoformat()]
    return (eligible or metas or [None])[-1]
