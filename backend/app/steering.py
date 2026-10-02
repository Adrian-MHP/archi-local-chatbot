"""Assessment step "Ratings & Roadmap": the steering information of the active model.

The step turns the assessment into the data the transformation dashboard reads: capabilities with
current and target maturity, process ratings, application portfolio attributes, goals and outcome
KPIs, plateaus, work packages and the plateau each gap is closed in. Three pure functions, so the
logic is testable without Archi or a language model:

    current_state(snapshot, ...)       -> editable tables built from the model
    merge_proposal(state, proposal)    -> AI suggestions merged in; existing values are never overwritten
    plan_changes(snapshot, state)      -> the bulk-mutate operations that write the reviewed tables back

Every element type, relationship pair and property value is checked against meta_model.py.
"Managed links" (a capability's / work package's / gap's plateau, an outcome's goal and capability)
are single-valued in the tables: changing one replaces the earlier relationship of that kind.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from . import meta_model
from .dashboard import Element, Model, _build_model, _date, _key

# table name -> element type
SECTIONS: Dict[str, str] = {
    "goals": "Goal",
    "outcomes": "Outcome",
    "capabilities": "Capability",
    "processes": "BusinessProcess",
    "applications": "ApplicationComponent",
    "plateaus": "Plateau",
    "workPackages": "WorkPackage",
    "gaps": "Gap",
}

# (table, link field, relationship type, source side, target table)
# source side "row" means the row's element is the relationship source, "other" the target.
MANAGED_LINKS: List[Tuple[str, str, str, str, str]] = [
    ("capabilities", "plateau", "RealizationRelationship", "other", "plateaus"),      # Plateau -> Capability
    ("workPackages", "plateau", "RealizationRelationship", "row", "plateaus"),        # WorkPackage -> Plateau
    ("gaps", "plateau", "AssociationRelationship", "other", "plateaus"),              # Plateau -> Gap
    ("outcomes", "goal", "RealizationRelationship", "row", "goals"),                  # Outcome -> Goal
    ("outcomes", "capability", "RealizationRelationship", "other", "capabilities"),   # Capability -> Outcome
]

MAX_PROCESSES = 200


def _fields(element_type: str) -> List[str]:
    return [p["key"] for p in meta_model.properties_for(element_type)]


def _normal(element_type: str, key: str, value: Any) -> Optional[str]:
    normalised, _error = meta_model.normalize_property_value(element_type, key, value)
    return normalised


def _shown(element_type: str, key: str, raw: Optional[str]) -> Optional[str]:
    # Keep an unparseable model value visible (e.g. a German date) so the user can correct it;
    # plan_changes reports it instead of silently dropping it.
    normalised = _normal(element_type, key, raw) if raw is not None else None
    return normalised if normalised is not None else raw


def _row(element: Element, element_type: str) -> Dict[str, Any]:
    row: Dict[str, Any] = {"key": element.id, "id": element.id, "name": element.name, "origin": "model",
                           "include": True, "aiFields": [], "rationale": ""}
    for field in _fields(element_type):
        row[field] = _shown(element_type, field, element.prop(field))
    # The values as loaded: plan_changes writes only what was edited in the table since, so an
    # older table (kept in the browser session, or shown while a proposal waits for approval)
    # never reverts changes made in Archi in the meantime.
    row["loaded"] = {"name": element.name, **{field: row[field] for field in _fields(element_type)}}
    return row


def _plateau_date(model: Model, plateau: Element) -> date:
    return _date(plateau.prop("targetDate")) or date.max


def _earliest(model: Model, plateaus: List[Element]) -> Optional[Element]:
    return min(plateaus, key=lambda p: (_plateau_date(model, p), p.name.lower())) if plateaus else None


def current_state(snapshot: Dict[str, Any], *, as_is_process_ids: List[str], to_be_process_ids: List[str]) -> Dict[str, Any]:
    """Editable tables for every steering element type in the model. Processes are limited to the
    assessment's As-Is / To-Be views when those are given (else every process, capped)."""
    model = _build_model(snapshot)
    state: Dict[str, Any] = {name: [] for name in SECTIONS}
    state["warnings"] = []
    # Names of every process, so capability rows can show processes outside the assessment views.
    state["processNames"] = {p.id: p.name for p in model.of_type("BusinessProcess")}

    for goal in model.of_type("Goal"):
        state["goals"].append(_row(goal, "Goal"))
    for capability in model.of_type("Capability"):
        row = _row(capability, "Capability")
        row["processes"] = [p.id for p in model.sources(capability.id, ["RealizationRelationship"], ["BusinessProcess"])]
        plateau = _earliest(model, model.sources(capability.id, ["RealizationRelationship"], ["Plateau"]))
        row["plateau"] = plateau.id if plateau else None
        state["capabilities"].append(row)
    for outcome in model.of_type("Outcome"):
        row = _row(outcome, "Outcome")
        goals = model.targets(outcome.id, ["RealizationRelationship"], ["Goal"])
        capabilities = model.sources(outcome.id, ["RealizationRelationship"], ["Capability"])
        row["goal"] = goals[0].id if goals else None
        row["capability"] = capabilities[0].id if capabilities else None
        state["outcomes"].append(row)

    scoped = list(dict.fromkeys(as_is_process_ids + to_be_process_ids))
    to_be = set(to_be_process_ids)
    processes = [model.elements[i] for i in scoped if i in model.elements] or model.of_type("BusinessProcess")[:MAX_PROCESSES]
    if not scoped and len(model.of_type("BusinessProcess")) > MAX_PROCESSES:
        state["warnings"].append(f"Showing the first {MAX_PROCESSES} business processes of the model.")
    for process in processes:
        row = _row(process, "BusinessProcess")
        if row.get("status") is None and scoped:
            # The assessment view tells which side a process is on; record it in the model.
            row["status"] = "target" if process.id in to_be else "current"
            row["derivedFields"] = ["status"]
        row["capabilities"] = [c.name for c in model.targets(process.id, ["RealizationRelationship"], ["Capability"])]
        state["processes"].append(row)

    for application in model.of_type("ApplicationComponent"):
        state["applications"].append(_row(application, "ApplicationComponent"))
    for plateau in sorted(model.of_type("Plateau"), key=lambda p: (_plateau_date(model, p), p.name.lower())):
        state["plateaus"].append(_row(plateau, "Plateau"))
    for package in model.of_type("WorkPackage"):
        row = _row(package, "WorkPackage")
        plateau = _earliest(model, model.targets(package.id, ["RealizationRelationship"], ["Plateau"]))
        row["plateau"] = plateau.id if plateau else None
        state["workPackages"].append(row)
    for gap in model.of_type("Gap"):
        row = _row(gap, "Gap")
        plateau = _earliest(model, model.associated(gap.id, ["Plateau"]))
        row["plateau"] = plateau.id if plateau else None
        row["processes"] = [p.name for p in model.associated(gap.id, ["BusinessProcess"])]
        state["gaps"].append(row)
    for table, field, _rel_type, _side, _target in MANAGED_LINKS:
        for row in state[table]:
            row["loaded"][field] = row.get(field)
    return state


# ---------------------------------------------------------------------------------------------
# AI proposal merge
# ---------------------------------------------------------------------------------------------

def _find(rows: List[Dict[str, Any]], ref: Any) -> Optional[Dict[str, Any]]:
    """Find a row by key/id or by (normalised) name."""
    if ref is None:
        return None
    text = str(ref).strip()
    for row in rows:
        if text in (row.get("key"), row.get("id")):
            return row
    wanted = _key(text)
    return next((row for row in rows if _key(row.get("name", "")) == wanted), None) if wanted else None


def _fill(row: Dict[str, Any], element_type: str, values: Dict[str, Any], warnings: List[str]) -> None:
    """Fill empty schema fields of a row from a proposal; never overwrite an existing value."""
    for field, raw in values.items():
        if field not in _fields(element_type) or raw in (None, ""):
            continue
        normalised, error = meta_model.normalize_property_value(element_type, field, raw)
        if error:
            warnings.append(f"Ignored AI value for {row.get('name')}: {error}")
            continue
        if row.get(field) in (None, "") and normalised is not None:
            row[field] = normalised
            if field not in row["aiFields"]:
                row["aiFields"].append(field)


def _new_row(state: Dict[str, Any], table: str, name: str, rationale: str = "") -> Dict[str, Any]:
    element_type = SECTIONS[table]
    count = sum(1 for r in state[table] if str(r.get("key", "")).startswith("new:")) + 1
    row: Dict[str, Any] = {"key": f"new:{table}:{count}", "id": None, "name": name.strip()[:120], "origin": "ai",
                           "include": True, "aiFields": [], "rationale": rationale.strip()[:300]}
    for field in _fields(element_type):
        row[field] = None
    state[table].append(row)
    return row


def _upsert(state: Dict[str, Any], table: str, item: Dict[str, Any], warnings: List[str]) -> Optional[Dict[str, Any]]:
    name = str(item.get("name") or "").strip()
    row = _find(state[table], item.get("ref")) or _find(state[table], name)
    if row is None:
        if not name:
            return None
        row = _new_row(state, table, name, str(item.get("rationale") or ""))
    elif item.get("rationale") and not row.get("rationale"):
        row["rationale"] = str(item["rationale"]).strip()[:300]
    _fill(row, SECTIONS[table], item, warnings)
    return row


def _link(row: Dict[str, Any], field: str, target: Optional[Dict[str, Any]]) -> None:
    if target is not None and not row.get(field):
        row[field] = target["key"]
        if field not in row["aiFields"]:
            row["aiFields"].append(field)


def merge_proposal(state: Dict[str, Any], proposal: Dict[str, Any]) -> Dict[str, Any]:
    """Merge an AI proposal into the tables. Rows the model already has keep their values; the AI
    only fills empty fields and adds new rows (origin "ai"), which the user reviews before applying."""
    warnings: List[str] = list(state.get("warnings", []))

    for item in _items(proposal, "plateaus"):
        _upsert(state, "plateaus", item, warnings)
    for item in _items(proposal, "goals"):
        _upsert(state, "goals", item, warnings)

    for item in _items(proposal, "capabilities"):
        row = _upsert(state, "capabilities", {**item, "capabilityDomain": item.get("domain") or item.get("capabilityDomain")}, warnings)
        if row is None:
            continue
        process_ids = {p["key"] for p in state["processes"]}
        for pid in item.get("processIds") or []:
            if pid in process_ids and pid not in row.setdefault("processes", []):
                row["processes"].append(pid)
                if "processes" not in row["aiFields"]:
                    row["aiFields"].append("processes")
        _link(row, "plateau", _find(state["plateaus"], item.get("plateau")))

    for item in _items(proposal, "processRatings"):
        row = _find(state["processes"], item.get("id"))
        if row is None:
            continue
        _fill(row, "BusinessProcess", {**item, "processPhase": item.get("phase") or item.get("processPhase")}, warnings)
        if item.get("rationale") and not row.get("rationale"):
            row["rationale"] = str(item["rationale"]).strip()[:300]

    for item in _items(proposal, "workPackages"):
        row = _upsert(state, "workPackages", {"status": "planned", **item}, warnings)
        if row is not None:
            _link(row, "plateau", _find(state["plateaus"], item.get("plateau")))

    for item in _items(proposal, "gapAssignments"):
        row = _find(state["gaps"], item.get("id"))
        if row is None:
            continue
        _fill(row, "Gap", item, warnings)
        _link(row, "plateau", _find(state["plateaus"], item.get("plateau")))

    for item in _items(proposal, "outcomes"):
        row = _upsert(state, "outcomes", item, warnings)
        if row is None:
            continue
        _link(row, "goal", _find(state["goals"], item.get("goal")))
        _link(row, "capability", _find(state["capabilities"], item.get("capability")))

    state["warnings"] = warnings
    return state


def _items(proposal: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    value = proposal.get(key)
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


# ---------------------------------------------------------------------------------------------
# Change planning
# ---------------------------------------------------------------------------------------------

def _alias(key: str) -> str:
    return "n_" + re.sub(r"[^A-Za-z0-9]", "_", key)


def _loaded(row: Dict[str, Any]) -> Dict[str, Any]:
    """The row's values as loaded from the model (empty for new rows and for older saved tables)."""
    loaded = row.get("loaded")
    return loaded if isinstance(loaded, dict) else {}


def _same(row_value: Any, model_value: Optional[str]) -> bool:
    empty_row = row_value is None or (isinstance(row_value, str) and not row_value.strip())
    if empty_row or model_value is None:
        return empty_row and model_value is None
    return str(row_value).strip() == str(model_value).strip()


def plan_changes(snapshot: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    """Operations that make the model match the reviewed tables.

    Returns {"create": [...], "linked": [...], "independent": [...], "errors": [...], "counts": {...}}:
    `create` holds create-element operations (named with `as`), `linked` the relationship operations
    that reference a new element (both must go into the same bulk-mutate call), `independent` all
    other operations. Nothing is deleted except the previous relationship of a managed link."""
    model = _build_model(snapshot)
    errors: List[str] = []
    create: List[Dict[str, Any]] = []
    linked: List[Dict[str, Any]] = []
    independent: List[Dict[str, Any]] = []
    counts = {"created": 0, "updated": 0, "relationships": 0, "removedRelationships": 0}
    ref_of: Dict[str, str] = {}      # row key -> element id or "$alias.id"
    type_of: Dict[str, str] = {}     # row key -> element type
    included: Dict[str, List[Dict[str, Any]]] = {}

    for table, element_type in SECTIONS.items():
        table_rows = state.get(table) or []
        if not isinstance(table_rows, list) or not all(isinstance(r, dict) for r in table_rows):
            errors.append(f"{table}: expected a list of rows -- reload the step")
            table_rows = []
        rows = [r for r in table_rows if r.get("include", True)]
        included[table] = rows
        names_taken = {e.name.strip().lower() for e in model.of_type(element_type)}
        for row in rows:
            key = str(row.get("key") or "")
            name = str(row.get("name") or "").strip()
            element = model.elements.get(str(row.get("id") or ""))
            loaded = _loaded(row)
            if not key:
                errors.append(f"{name or element_type}: row without a key -- reload the step")
                continue
            if row.get("id") and element is None:
                errors.append(f"{name or key}: element {row['id']} no longer exists in the model -- reload the step")
                continue
            if element is not None and element.type != element_type:
                errors.append(f"{name}: is a {element.type}, not a {element_type}")
                continue
            if not name:
                errors.append(f"A {element_type} row has no name")
                continue
            if element is None and name.lower() in names_taken:
                # Also catches a second apply of rows whose first apply has since been approved in Archi.
                errors.append(f"{name}: there is already a {element_type} with this name -- edit that one "
                              "(reload the step if it was just approved in Archi)")
                continue
            properties: Dict[str, Optional[str]] = {}
            for field in _fields(element_type):
                current_raw = element.prop(field) if element is not None else None
                if element is not None:
                    shown_now = _shown(element_type, field, current_raw)
                    base = loaded[field] if field in loaded else shown_now
                    if _same(row.get(field), base):
                        continue  # not edited in the table: the model keeps whatever it holds now
                    if not _same(base, shown_now) and not _same(row.get(field), shown_now):
                        errors.append(f"{name}: {field} was changed in Archi after this step was loaded -- reload the step")
                        continue
                desired, error = meta_model.normalize_property_value(element_type, field, row.get(field))
                if error:
                    errors.append(f"{name}: {error}")
                    continue
                if element is None:
                    if desired is not None:
                        properties[field] = desired
                elif desired != _normal(element_type, field, current_raw) or (desired is None and current_raw is not None):
                    properties[field] = desired  # None removes the property
            if element is None:
                alias = _alias(key)
                create.append({"tool": "create-element", "as": alias, "params": {
                    "type": element_type, "name": name, "properties": properties, "force": True}})
                ref_of[key] = f"${alias}.id"
                names_taken.add(name.lower())
                counts["created"] += 1
            else:
                ref_of[key] = element.id
                params: Dict[str, Any] = {"id": element.id}
                if properties:
                    params["properties"] = properties
                # Surrounding blanks are not a rename: model names often carry a trailing space.
                loaded_name = str(loaded.get("name") or element.name).strip()
                model_name = element.name.strip()
                if name != loaded_name:
                    if model_name not in (loaded_name, name):
                        errors.append(f"{name}: was renamed in Archi (now '{model_name}') after this step was loaded -- reload the step")
                    elif name != model_name:
                        params["name"] = name
                if len(params) > 1:
                    independent.append({"tool": "update-element", "params": params})
                    counts["updated"] += 1
            type_of[key] = element_type

    existing = {(r.get("type"), r.get("sourceId"), r.get("targetId")): r.get("id") for r in snapshot.get("relationships") or []}

    def add_relationship(rel_type: str, source_key: str, target_key: str, name: str = "") -> None:
        source_type, target_type = type_of.get(source_key), type_of.get(target_key)
        if not source_type or not target_type:
            return
        if not meta_model.is_declared_pair(source_type, rel_type, target_type):
            errors.append(f"{source_type} --{rel_type}--> {target_type} is not declared in the meta-model")
            return
        source_ref, target_ref = ref_of[source_key], ref_of[target_key]
        if (rel_type, source_ref, target_ref) in existing:
            return
        params = {"type": rel_type, "sourceId": source_ref, "targetId": target_ref}
        if name:
            params["name"] = name
        op = {"tool": "create-relationship", "params": params}
        (linked if source_ref.startswith("$") or target_ref.startswith("$") else independent).append(op)
        existing[(rel_type, source_ref, target_ref)] = "planned"
        counts["relationships"] += 1

    # Capability realised by processes (additive: links are only added here, never removed).
    for row in included["capabilities"]:
        processes = row.get("processes")
        for process_key in map(str, processes if isinstance(processes, list) else []):
            if process_key in type_of:
                add_relationship("RealizationRelationship", process_key, str(row.get("key") or ""))

    # Managed single-valued links.
    for table, field, rel_type, source_side, target_table in MANAGED_LINKS:
        for row in included[table]:
            if str(row.get("key") or "") not in ref_of:
                continue
            desired_key = str(row[field]) if row.get(field) else None
            element = model.elements.get(str(row.get("id") or ""))
            loaded = _loaded(row)
            if element is not None and field in loaded and desired_key == (loaded[field] or None):
                continue  # link not edited in the table: the model keeps whatever it holds now
            if desired_key and desired_key not in ref_of:
                errors.append(f"{row.get('name')}: {field} refers to an element that is not part of this apply")
                continue
            current_id = None
            if element is not None:
                current_id = _current_link(model, element, table, field)
            desired_ref = ref_of.get(desired_key) if desired_key else None
            if element is not None and field in loaded and (loaded[field] or None) != current_id and desired_ref != current_id:
                errors.append(f"{row.get('name')}: {field} was changed in Archi after this step was loaded -- reload the step")
                continue
            if desired_ref == current_id:
                continue
            if current_id and element is not None:
                source_id, target_id = (element.id, current_id) if source_side == "row" else (current_id, element.id)
                rel_id = _relationship_id(snapshot, rel_type, source_id, target_id)
                if rel_id:
                    independent.append({"tool": "delete-relationship", "params": {"relationshipId": rel_id}})
                    counts["removedRelationships"] += 1
            if desired_key:
                if source_side == "row":
                    add_relationship(rel_type, str(row["key"]), desired_key)
                else:
                    add_relationship(rel_type, desired_key, str(row["key"]))

    return {"create": create, "linked": linked, "independent": independent, "errors": errors, "counts": counts}


def _current_link(model: Model, element: Element, table: str, field: str) -> Optional[str]:
    if (table, field) == ("capabilities", "plateau"):
        plateau = _earliest(model, model.sources(element.id, ["RealizationRelationship"], ["Plateau"]))
    elif (table, field) == ("workPackages", "plateau"):
        plateau = _earliest(model, model.targets(element.id, ["RealizationRelationship"], ["Plateau"]))
    elif (table, field) == ("gaps", "plateau"):
        plateau = _earliest(model, model.associated(element.id, ["Plateau"]))
    elif (table, field) == ("outcomes", "goal"):
        goals = model.targets(element.id, ["RealizationRelationship"], ["Goal"])
        return goals[0].id if goals else None
    elif (table, field) == ("outcomes", "capability"):
        capabilities = model.sources(element.id, ["RealizationRelationship"], ["Capability"])
        return capabilities[0].id if capabilities else None
    else:
        return None
    return plateau.id if plateau else None


def _relationship_id(snapshot: Dict[str, Any], rel_type: str, source_id: str, target_id: str) -> Optional[str]:
    for rel in snapshot.get("relationships") or []:
        if rel.get("type") == rel_type and rel.get("sourceId") == source_id and rel.get("targetId") == target_id:
            return rel.get("id")
    if rel_type == "AssociationRelationship":  # undirected in the meta-model
        for rel in snapshot.get("relationships") or []:
            if rel.get("type") == rel_type and rel.get("sourceId") == target_id and rel.get("targetId") == source_id:
                return rel.get("id")
    return None


def proposal_prompt(state: Dict[str, Any], *, mappings: List[Tuple[str, str]], today: date) -> str:
    """User prompt for the AI proposal: the current tables in compact form plus the rules."""
    def processes_line(row: Dict[str, Any]) -> str:
        side = "To-Be" if row.get("status") == "target" else "As-Is"
        ratings = ", ".join(f"{f}={row[f]}" for f in ("processPhase", "maturity", "automationLevel", "mediaBreaks") if row.get(f))
        return f"{row['key']} | {row['name']} | {side}" + (f" | {ratings}" if ratings else "")

    lines = [
        f"Today is {today.isoformat()}.",
        "You prepare the steering data of an architecture assessment so a transformation can be planned and tracked.",
        "Return strict JSON only with this schema (omit sections you have nothing for):",
        "{",
        '  "capabilities": [{"ref": "existing key or null", "name": "...", "domain": "...", "maturity": 1, "targetMaturity": 3,'
        ' "strategicImportance": "high|medium|low", "processIds": ["process key"], "plateau": "plateau name", "rationale": "..."}],',
        '  "processRatings": [{"id": "process key", "phase": "...", "maturity": 1, "automationLevel": "manual|partial|automated",'
        ' "mediaBreaks": 0, "rationale": "..."}],',
        '  "plateaus": [{"name": "...", "targetDate": "YYYY-MM-DD"}],',
        '  "workPackages": [{"name": "...", "startDate": "YYYY-MM-DD", "endDate": "YYYY-MM-DD", "plateau": "plateau name", "rationale": "..."}],',
        '  "gapAssignments": [{"id": "gap key", "plateau": "plateau name", "criticality": "high|medium|low"}],',
        '  "goals": [{"name": "...", "targetDate": "YYYY-MM-DD"}],',
        '  "outcomes": [{"name": "...", "kpi": "...", "unit": "...", "baseline": null, "target": 0, "direction": "higher|lower",'
        ' "targetDate": "YYYY-MM-DD", "goal": "goal name", "capability": "capability name"}]',
        "}",
        "Rules:",
        "- Reuse an existing capability (give its key as ref) when it fits; propose a new one only for As-Is or To-Be processes no existing capability covers. Every capability needs a business process that realises it (processIds).",
        "- Capability maturity reflects the As-Is evidence (manual steps, media breaks, missing tool support); targetMaturity reflects what the To-Be processes require.",
        "- Rate every As-Is process that has no rating yet: maturity, automationLevel, mediaBreaks = number of tool or data handovers without integration visible in the process and its gaps.",
        "- Plateaus: if none exist, propose 2-3 (transition(s) and target) with realistic dates after today; otherwise use the existing ones.",
        "- Work packages close gaps: propose one per gap or per group of related gaps, each realising the plateau where the gap is closed. Dates must lie before or on that plateau's target date.",
        "- Assign every gap without a plateau to one, and give it a criticality.",
        "- Goals and outcomes only where the processes and gaps make the intended effect clear; leave baseline null when it is not known.",
        "- Use only keys listed below; never invent ids. Keep names short and business-friendly. No text outside the JSON.",
        "",
        meta_model.render_property_block(["Capability", "BusinessProcess", "Plateau", "WorkPackage", "Gap", "Goal", "Outcome"]),
        "",
        "Existing capabilities (key | name | domain | maturity -> target | importance | realised by):",
    ]
    for row in state["capabilities"]:
        lines.append(f"{row['key']} | {row['name']} | {row.get('capabilityDomain') or '-'} | "
                     f"{row.get('maturity') or '?'} -> {row.get('targetMaturity') or '?'} | {row.get('strategicImportance') or '-'} | "
                     f"{', '.join(row.get('processes') or []) or '-'}")
    lines.append("Business processes (key | name | side | ratings):")
    lines += [processes_line(row) for row in state["processes"]]
    if mappings:
        lines.append("To-Be process realises As-Is process (To-Be key -> As-Is key):")
        lines += [f"{a} -> {b}" for a, b in mappings]
    lines.append("Gaps (key | name | criticality | plateau | affected processes):")
    for row in state["gaps"]:
        lines.append(f"{row['key']} | {row['name']} | {row.get('criticality') or '-'} | {row.get('plateau') or '-'} | "
                     f"{', '.join(row.get('processes') or []) or '-'}")
    lines.append("Plateaus (key | name | target date):")
    lines += [f"{r['key']} | {r['name']} | {r.get('targetDate') or '-'}" for r in state["plateaus"]] or ["(none)"]
    lines.append("Work packages (key | name | start | end | plateau):")
    lines += [f"{r['key']} | {r['name']} | {r.get('startDate') or '-'} | {r.get('endDate') or '-'} | {r.get('plateau') or '-'}"
              for r in state["workPackages"]] or ["(none)"]
    lines.append("Goals: " + (", ".join(r["name"] for r in state["goals"]) or "(none)"))
    lines.append("Outcomes: " + (", ".join(r["name"] for r in state["outcomes"]) or "(none)"))
    return "\n".join(lines)
