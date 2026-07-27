from __future__ import annotations

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
