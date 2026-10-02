from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Tuple

# Governance meta-model reconstructed from the organization's reference diagram
# (layers, element types, and the specific cross-layer relationship pairs that are
# allowed). Element and relationship type strings are the exact ArchiMate EClass
# names Archi's MCP server expects for `create-element`/`create-relationship`.
#
# This is the single source of truth for: the extraction prompts (business-process
# and requirements automation), the chat system prompt, the meta-model API endpoint,
# and the "Meta model" viewer in the UI.

LAYERS: List[Dict[str, Any]] = [
    {
        "key": "motivation",
        "label": "Motivation Layer",
        "elements": ["Goal", "Outcome"],
    },
    {
        "key": "strategy",
        "label": "Strategy Layer",
        "elements": ["Capability"],
    },
    {
        "key": "business",
        "label": "Business Layer",
        "elements": ["BusinessActor", "BusinessRole", "BusinessProcess", "BusinessObject"],
    },
    {
        "key": "application",
        "label": "Application Layer",
        "elements": [
            "ApplicationComponent",
            "ApplicationService",
            "ApplicationFunction",
            "ApplicationInterface",
            "DataObject",
        ],
    },
    {
        "key": "implementation",
        "label": "Information Layer (Implementation & Migration)",
        "elements": ["Plateau", "Gap", "WorkPackage"],
    },
]

# (source_type, relationship_type, target_type, human label, note)
RELATIONSHIPS: List[Tuple[str, str, str, str, str]] = [
    ("Outcome", "RealizationRelationship", "Goal", "Realizes", ""),
    ("Capability", "RealizationRelationship", "Outcome", "Realizes", ""),
    ("BusinessProcess", "RealizationRelationship", "Capability", "Realizes", ""),
    ("BusinessActor", "AssignmentRelationship", "BusinessRole", "Assigned to", ""),
    ("BusinessRole", "AssignmentRelationship", "BusinessProcess", "Assigned to", ""),
    ("BusinessProcess", "AccessRelationship", "BusinessObject", "Accesses", ""),
    ("DataObject", "RealizationRelationship", "BusinessObject", "Realizes", ""),
    ("ApplicationService", "ServingRelationship", "BusinessProcess", "Serves", ""),
    ("ApplicationFunction", "RealizationRelationship", "ApplicationService", "Realizes", ""),
    ("ApplicationComponent", "RealizationRelationship", "ApplicationService", "Realizes", ""),
    ("ApplicationComponent", "CompositionRelationship", "ApplicationInterface", "Composed of", ""),
    ("ApplicationComponent", "TriggeringRelationship", "ApplicationFunction", "Triggers", ""),
    ("ApplicationComponent", "AccessRelationship", "DataObject", "Accesses", ""),
    ("Plateau", "RealizationRelationship", "Capability", "Realizes", ""),
    ("WorkPackage", "RealizationRelationship", "Plateau", "Realizes", ""),
    (
        "Plateau",
        "AssociationRelationship",
        "Gap",
        "Associated with",
        "Drawn as a plain line in the source diagram; modeled as an undirected association.",
    ),
    (
        "BusinessProcess",
        "TriggeringRelationship",
        "BusinessProcess",
        "Triggers",
        "Standard ArchiMate process sequencing. Not explicitly drawn in the source diagram "
        "(which focuses on cross-layer realization/serving), but required to model process flow order.",
    ),
    (
        "BusinessProcess",
        "AssociationRelationship",
        "BusinessProcess",
        "Realizes (mapping)",
        "As-Is/To-Be traceability in the Mapping & Gap Analysis view: the target (To-Be) process is "
        "linked to the as-is (As-Is) process it replaces or fulfills, labeled 'realizes' on the "
        "connection. RealizationRelationship itself is not a valid ArchiMate pairing between two "
        "BusinessProcess elements (rejected by the MCP server's validator), so Association is the "
        "correct type here with the semantic intent carried in the relationship name instead.",
    ),
    (
        "Gap",
        "AssociationRelationship",
        "BusinessProcess",
        "Affects",
        "Mapping & Gap Analysis: each identified gap is modeled as its own Gap element and linked "
        "to the As-Is/To-Be process(es) it relates to, labeled 'affects' on the connection.",
    ),
]


def allowed_element_types() -> List[str]:
    seen: List[str] = []
    for layer in LAYERS:
        for element_type in layer["elements"]:
            if element_type not in seen:
                seen.append(element_type)
    return seen


def allowed_relationship_types() -> List[str]:
    seen: List[str] = []
    for _source, rel_type, _target, _label, _note in RELATIONSHIPS:
        if rel_type not in seen:
            seen.append(rel_type)
    return seen


# Steering properties per element type: the information the assessment's "Ratings & Roadmap" step
# maintains and the transformation dashboard reads. `core` marks the properties a dashboard metric
# needs (they drive the data-completeness view). Types: text, integer (with min/max and optional
# level labels), number, date (YYYY-MM-DD), enum (fixed values).
def _prop(key: str, label: str, type_: str, description: str, *, core: bool = False,
          values: List[Tuple[str, str]] | None = None, minimum: int | None = None,
          maximum: int | None = None, levels: Dict[int, str] | None = None) -> Dict[str, Any]:
    prop: Dict[str, Any] = {"key": key, "label": label, "type": type_, "description": description, "core": core}
    if values is not None:
        prop["values"] = [{"value": v, "label": lbl} for v, lbl in values]
    if minimum is not None:
        prop["min"] = minimum
    if maximum is not None:
        prop["max"] = maximum
    if levels:
        prop["levels"] = {str(k): v for k, v in levels.items()}
    return prop


MATURITY_LEVELS = {1: "Initial", 2: "Managed", 3: "Defined", 4: "Quantitatively managed", 5: "Optimizing"}
IMPORTANCE_VALUES = [("high", "High"), ("medium", "Medium"), ("low", "Low")]

PROPERTIES: Dict[str, List[Dict[str, Any]]] = {
    "Goal": [
        _prop("targetDate", "Target date", "date", "Date the goal should be met."),
    ],
    "Outcome": [
        _prop("kpi", "KPI", "text", "Name of the measurable indicator.", core=True),
        _prop("unit", "Unit", "text", "Unit of the KPI, e.g. %, days, months, count."),
        _prop("baseline", "Baseline", "number", "KPI value when the assessment started.", core=True),
        _prop("current", "Current", "number", "Latest measured KPI value.", core=True),
        _prop("target", "Target", "number", "KPI value to reach by the target date.", core=True),
        _prop("direction", "Direction", "enum", "Whether a higher or a lower value is better.",
              values=[("higher", "Higher is better"), ("lower", "Lower is better")]),
        _prop("baselineDate", "Baseline date", "date", "Date the baseline was measured.", core=True),
        _prop("targetDate", "Target date", "date", "Date the target should be reached.", core=True),
        _prop("measuredAt", "Measured at", "date", "Date of the current value."),
    ],
    "Capability": [
        _prop("capabilityDomain", "Domain", "text", "Group on the capability map."),
        _prop("maturity", "Current maturity", "integer", "Current capability maturity (1-5).",
              core=True, minimum=1, maximum=5, levels=MATURITY_LEVELS),
        _prop("targetMaturity", "Target maturity", "integer", "Maturity the target state requires (1-5).",
              core=True, minimum=1, maximum=5, levels=MATURITY_LEVELS),
        _prop("strategicImportance", "Strategic importance", "enum", "Weight of the capability for the strategy.",
              core=True, values=IMPORTANCE_VALUES),
        _prop("owner", "Owner", "text", "Accountable role or person."),
    ],
    "BusinessProcess": [
        _prop("status", "State", "enum", "As-Is (current) or To-Be (target) process.",
              values=[("current", "As-Is"), ("target", "To-Be")]),
        _prop("processPhase", "Phase", "text", "Phase of the process chain the process belongs to."),
        _prop("maturity", "Maturity", "integer", "Process maturity (1-5).", core=True, minimum=1, maximum=5,
              levels=MATURITY_LEVELS),
        _prop("automationLevel", "Automation", "enum", "Degree of automation.", core=True,
              values=[("manual", "Manual"), ("partial", "Partially automated"), ("automated", "Automated")]),
        _prop("mediaBreaks", "Media breaks", "integer",
              "Tool or data handovers without integration (breaks in the digital thread).", core=True, minimum=0),
    ],
    "ApplicationComponent": [
        _prop("status", "State", "enum", "Existing (current) or planned (target) application.",
              values=[("current", "Current"), ("target", "Target")]),
        _prop("applicationCategory", "Category", "text", "Functional category of the application."),
        _prop("vendor", "Vendor", "text", "Vendor or 'In-house'."),
        _prop("lifecycle", "Lifecycle", "enum", "Lifecycle phase.", core=True,
              values=[("plan", "Plan"), ("phase-in", "Phase in"), ("active", "Active"),
                      ("phase-out", "Phase out"), ("end-of-life", "End of life")]),
        _prop("endOfLife", "End of life", "date", "Planned or vendor end of life."),
        _prop("timeClassification", "TIME decision", "enum", "Gartner TIME decision.", core=True,
              values=[("tolerate", "Tolerate"), ("invest", "Invest"), ("migrate", "Migrate"), ("eliminate", "Eliminate")]),
        _prop("functionalFit", "Functional fit", "integer", "How well the application meets business needs (1-4).",
              core=True, minimum=1, maximum=4,
              levels={1: "Unreasonable", 2: "Insufficient", 3: "Appropriate", 4: "Perfect"}),
        _prop("technicalFit", "Technical fit", "integer", "Technical health of the application (1-4).",
              core=True, minimum=1, maximum=4,
              levels={1: "Inappropriate", 2: "Unreasonable", 3: "Adequate", 4: "Fully appropriate"}),
        _prop("businessCriticality", "Business criticality", "enum", "Criticality class.", core=True,
              values=[("mission-critical", "Mission critical"), ("business-critical", "Business critical"),
                      ("business-operational", "Business operational"), ("administrative", "Administrative service")]),
    ],
    "Plateau": [
        _prop("targetDate", "Target date", "date", "Date the plateau should be reached.", core=True),
    ],
    "WorkPackage": [
        _prop("startDate", "Start", "date", "Planned start.", core=True),
        _prop("endDate", "End", "date", "Planned end.", core=True),
        _prop("status", "Status", "enum", "Delivery status as recorded in the architecture roadmap.", core=True,
              values=[("planned", "Planned"), ("in-progress", "In progress"), ("completed", "Completed"),
                      ("on-hold", "On hold")]),
        _prop("owner", "Owner", "text", "Accountable role or person."),
    ],
    "Gap": [
        _prop("criticality", "Criticality", "enum", "Business criticality of the gap.", core=True,
              values=IMPORTANCE_VALUES),
        _prop("gapCategory", "Category", "enum", "Kind of gap found in the Mapping & Gap Analysis.",
              values=[("missing_process", "Missing process"), ("redundancy", "Redundancy"),
                      ("structural_difference", "Structural difference"), ("tooling_data_gap", "Tooling/data gap")]),
    ],
}

ELEMENT_LABELS = {
    "Goal": "Goals", "Outcome": "Outcomes", "Capability": "Capabilities", "BusinessProcess": "Business processes",
    "ApplicationComponent": "Application components", "Plateau": "Plateaus", "WorkPackage": "Work packages",
    "Gap": "Gaps",
}


def properties_for(element_type: str) -> List[Dict[str, Any]]:
    return PROPERTIES.get(element_type, [])


def property_definition(element_type: str, key: str) -> Dict[str, Any] | None:
    return next((p for p in properties_for(element_type) if p["key"] == key), None)


def core_property_keys(element_type: str) -> List[str]:
    return [p["key"] for p in properties_for(element_type) if p["core"]]


def layer_label(element_type: str) -> str:
    for layer in LAYERS:
        if element_type in layer["elements"]:
            return layer["label"].replace(" Layer", "").replace("Information (", "").rstrip(")")
    return ""


def normalize_property_value(element_type: str, key: str, value: Any) -> Tuple[str | None, str | None]:
    """Validate one property value against the schema. Returns (normalised string value, error).
    Empty values normalise to None (= no value) without an error."""
    definition = property_definition(element_type, key)
    if definition is None:
        return None, f"'{key}' is not a property of {element_type} in the meta-model"
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None
    text = str(value).strip()
    kind = definition["type"]
    if kind == "text":
        return text[:200], None
    if kind in ("integer", "number"):
        try:
            number = float(text.replace(",", "."))
        except ValueError:
            return None, f"{definition['label']}: '{text}' is not a number"
        if kind == "integer":
            if number != int(number):
                return None, f"{definition['label']}: '{text}' is not a whole number"
            number = int(number)
        if "min" in definition and number < definition["min"]:
            return None, f"{definition['label']}: {text} is below {definition['min']}"
        if "max" in definition and number > definition["max"]:
            return None, f"{definition['label']}: {text} is above {definition['max']}"
        return str(number) if kind == "integer" else f"{number:g}", None
    if kind == "date":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return None, f"{definition['label']}: '{text}' is not a YYYY-MM-DD date"
        try:
            date.fromisoformat(text)
        except ValueError:
            return None, f"{definition['label']}: '{text}' is not a valid date"
        return text, None
    if kind == "enum":
        allowed = [v["value"] for v in definition["values"]]
        squashed = re.sub(r"[^a-z0-9]", "", text.lower())
        for candidate in allowed:
            label = next(v["label"] for v in definition["values"] if v["value"] == candidate)
            if squashed in (re.sub(r"[^a-z0-9]", "", candidate), re.sub(r"[^a-z0-9]", "", label.lower())):
                return candidate, None
        return None, f"{definition['label']}: '{text}' is not one of {', '.join(allowed)}"
    return None, f"{definition['label']}: unknown property type {kind}"


def render_property_block(element_types: List[str]) -> str:
    """Property definitions for LLM prompts, so proposals use the exact keys and allowed values."""
    lines = ["Steering properties (use exactly these keys and values):"]
    for element_type in element_types:
        for p in properties_for(element_type):
            if p["type"] == "enum":
                allowed = "one of " + "|".join(v["value"] for v in p["values"])
            elif p["type"] == "integer":
                allowed = f"integer {p.get('min', '')}..{p.get('max', '')}".rstrip(".")
                if p.get("levels"):
                    allowed += " (" + ", ".join(f"{k} {v}" for k, v in p["levels"].items()) + ")"
            elif p["type"] == "date":
                allowed = "date YYYY-MM-DD"
            else:
                allowed = p["type"]
            lines.append(f"- {element_type}.{p['key']}: {allowed} -- {p['description']}")
    return "\n".join(lines)


def find_relationship_type(source_type: str, target_type: str) -> str | None:
    for source, rel_type, target, _label, _note in RELATIONSHIPS:
        if source == source_type and target == target_type:
            return rel_type
    return None


def is_declared_pair(source_type: str, relationship_type: str, target_type: str) -> bool:
    for source, rel_type, target, _label, _note in RELATIONSHIPS:
        if source == source_type and target == target_type and rel_type == relationship_type:
            return True
    return False


def as_dict() -> Dict[str, Any]:
    return {
        "layers": LAYERS,
        "relationships": [
            {"source": s, "type": t, "target": tgt, "label": label, "note": note}
            for s, t, tgt, label, note in RELATIONSHIPS
        ],
        "properties": PROPERTIES,
    }


def render_prompt_block() -> str:
    lines = ["Governance meta-model (follow exactly; do not invent other element or relationship types):"]
    for layer in LAYERS:
        lines.append(f"- {layer['label']}: {', '.join(layer['elements'])}")
    lines.append("Allowed relationships (source type -- relationship type --> target type):")
    for source, rel_type, target, label, _note in RELATIONSHIPS:
        lines.append(f"- {source} --{rel_type} ({label})--> {target}")
    lines.append(
        "If a fact does not fit any of these element types or relationship pairs, prefer the closest "
        "listed type over inventing a new one, and if nothing reasonably fits, use AssociationRelationship "
        "(undirected, general-purpose) rather than a fabricated or unsupported type."
    )
    return "\n".join(lines)
