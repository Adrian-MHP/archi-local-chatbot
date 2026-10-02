#!/usr/bin/env python3
"""Seed the active Archi model with an engineering / product-development demo dataset.

The dataset follows the governance meta-model in backend/app/meta_model.py strictly: only its
element types and its declared relationship pairs are used (the script checks this before writing
anything). Measures that architecture modelling does not own -- costs, budgets, project progress --
are deliberately not part of it. Property schema: docs/transformation-dashboard.md.

Everything is created inside "Engineering Demo" subfolders, so `--purge` removes it again without
touching anything else in the model.

Usage (Archi running, MCP server started, Approval Mode OFF, target model opened last):
    python3 ops/seed_engineering_demo.py            # seed
    python3 ops/seed_engineering_demo.py --purge    # remove the demo dataset
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from mcp_seed_client import McpError, SeedMcpClient

DEMO_FOLDER = "Engineering Demo"
SOURCE = {"dataset": "engineering-demo-v2"}

# ---------------------------------------------------------------------------------------------
# Motivation
# ---------------------------------------------------------------------------------------------
GOALS = [
    ("Reduce time-to-market by 25%", "2028-12-31"),
    ("Increase engineering efficiency through reuse", "2028-12-31"),
    ("Ensure compliance by design", "2027-12-11"),
    ("Establish an end-to-end digital thread", "2028-12-31"),
]
# Baselines were measured at the assessment; on-track status compares progress with the share of
# time elapsed between OUTCOME_BASELINE_DATE and each targetDate.
OUTCOME_BASELINE_DATE = "2025-01-01"
# (name, kpi, unit, baseline, current, target, direction, targetDate, [goals], [capabilities realizing it])
OUTCOMES = [
    ("Faster engineering change processing", "ECR-to-ECO lead time", "days", 38, 27, 12, "lower", "2028-12-31",
     ["Reduce time-to-market by 25%"], ["Engineering Change Management"]),
    ("Shorter concept-to-SOP lead time", "Concept-to-SOP lead time", "months", 30, 28, 22, "lower", "2028-12-31",
     ["Reduce time-to-market by 25%"], ["Virtual Prototyping"]),
    ("Simulation-driven design", "Designs validated virtually before first prototype", "%", 30, 45, 70, "higher", "2028-06-30",
     ["Reduce time-to-market by 25%"], ["Structural Simulation (FEA)"]),
    ("Fewer physical prototype loops", "Prototype iterations per machine generation", "count", 4, 3.5, 2, "lower", "2028-12-31",
     ["Reduce time-to-market by 25%", "Increase engineering efficiency through reuse"], ["Virtual Prototyping"]),
    ("Higher design reuse", "Design reuse rate", "%", 22, 31, 50, "higher", "2028-12-31",
     ["Increase engineering efficiency through reuse"], ["Design Reuse & Standard Parts Management"]),
    ("Full requirements traceability", "Requirements traceability coverage", "%", 35, 48, 90, "higher", "2027-12-31",
     ["Ensure compliance by design", "Establish an end-to-end digital thread"], ["Requirements Traceability"]),
    ("Documented cybersecurity conformity", "Products with CRA conformity documentation", "%", 0, 20, 100, "higher", "2027-12-11",
     ["Ensure compliance by design"], ["Certification & Compliance Management"]),
    ("First-time-right EBOM-to-MBOM handover", "EBOM-to-MBOM first-time-right rate", "%", 78, 84, 98, "higher", "2028-06-30",
     ["Establish an end-to-end digital thread"], ["EBOM-to-MBOM Handover"]),
]

# ---------------------------------------------------------------------------------------------
# Strategy: capabilities grouped by domain (capabilityDomain property -- the meta-model has no
# capability-to-capability relationship). Tuple: (name, maturity 1-5, targetMaturity 1-5, importance)
# ---------------------------------------------------------------------------------------------
CAPABILITY_DOMAINS: List[Tuple[str, str, List[Tuple[str, int, int, str]]]] = [
    ("Product Strategy & Portfolio Management", "Head of Product Management", [
        ("Product Portfolio Planning", 2, 3, "medium"),
        ("Product Roadmapping", 2, 3, "medium"),
        ("Market & Customer Requirements Capture", 2, 4, "high"),
        ("Product Cost Engineering", 2, 4, "high"),
    ]),
    ("Requirements & Systems Engineering", "Head of Systems Engineering", [
        ("Requirements Management", 2, 4, "high"),
        ("System Architecture Modeling (MBSE)", 1, 4, "high"),
        ("Requirements Traceability", 1, 4, "high"),
        ("Verification & Validation Planning", 2, 3, "medium"),
    ]),
    ("Mechanical & Electrical Design", "Head of Design Engineering", [
        ("Mechanical Design (3D CAD)", 4, 4, "high"),
        ("Electrical & Fluid Power Design", 3, 4, "high"),
        ("Design Reuse & Standard Parts Management", 2, 4, "high"),
        ("Technical Drawing & PMI", 3, 3, "low"),
    ]),
    ("Embedded Software Engineering", "Head of Software Engineering", [
        ("Software Development & Version Control", 3, 4, "medium"),
        ("Continuous Integration & Delivery", 2, 4, "high"),
        ("Software Testing & Hardware-in-the-Loop", 3, 4, "high"),
        ("Software Variant Management", 1, 3, "medium"),
    ]),
    ("Simulation & Virtual Validation", "Head of Simulation", [
        ("Structural Simulation (FEA)", 3, 4, "high"),
        ("Multibody & System Simulation", 3, 4, "medium"),
        ("Simulation Data Management", 1, 3, "medium"),
        ("Virtual Prototyping", 2, 4, "high"),
    ]),
    ("Test & Prototype Validation", "Head of Testing", [
        ("Physical Prototype Testing", 3, 3, "medium"),
        ("Test Data Management", 1, 3, "medium"),
        ("Certification & Compliance Management", 2, 4, "high"),
    ]),
    ("Product Data & Configuration Management", "Head of PLM", [
        ("Engineering BOM Management", 2, 4, "high"),
        ("Variant & Configuration Management", 2, 5, "high"),
        ("Engineering Change Management", 2, 4, "high"),
        ("Product Release Management", 3, 4, "medium"),
    ]),
    ("Technical Documentation & Handover", "Head of Technical Documentation", [
        ("Technical Documentation Authoring", 3, 3, "low"),
        ("EBOM-to-MBOM Handover", 2, 4, "high"),
        ("Service Information Provision", 2, 3, "medium"),
    ]),
]

# ---------------------------------------------------------------------------------------------
# Business. Process tuple: (name, phase, maturity 1-5, automationLevel, mediaBreaks,
#   [realized capabilities], [assigned roles])
# ---------------------------------------------------------------------------------------------
PHASES = ["Define Product", "Specify Requirements", "Design & Develop", "Validate & Verify", "Release & Hand Over"]
PROCESSES = [
    ("Manage Product Portfolio", "Define Product", 2, "manual", 4,
     ["Product Portfolio Planning", "Product Roadmapping"], ["Product Manager"]),
    ("Capture & Analyze Requirements", "Specify Requirements", 2, "partial", 5,
     ["Requirements Management", "Market & Customer Requirements Capture"], ["Product Manager", "Systems Engineer"]),
    ("Develop System Architecture", "Specify Requirements", 2, "manual", 3,
     ["System Architecture Modeling (MBSE)", "Requirements Traceability"], ["Systems Engineer"]),
    ("Design Mechanical Components", "Design & Develop", 4, "partial", 2,
     ["Mechanical Design (3D CAD)", "Technical Drawing & PMI", "Design Reuse & Standard Parts Management"],
     ["Mechanical Design Engineer"]),
    ("Design Electrical & Hydraulic Systems", "Design & Develop", 3, "partial", 3,
     ["Electrical & Fluid Power Design"], ["Electrical Design Engineer"]),
    ("Develop Embedded Software", "Design & Develop", 3, "partial", 2,
     ["Software Development & Version Control", "Continuous Integration & Delivery", "Software Variant Management"],
     ["Software Engineer"]),
    ("Perform Simulation & Analysis", "Validate & Verify", 3, "partial", 3,
     ["Structural Simulation (FEA)", "Multibody & System Simulation", "Simulation Data Management", "Virtual Prototyping"],
     ["Simulation Engineer"]),
    ("Build & Test Prototypes", "Validate & Verify", 2, "manual", 4,
     ["Physical Prototype Testing", "Test Data Management", "Software Testing & Hardware-in-the-Loop",
      "Certification & Compliance Management", "Verification & Validation Planning"], ["Test Engineer"]),
    ("Manage Engineering BOM", "Release & Hand Over", 2, "partial", 5,
     ["Engineering BOM Management", "Variant & Configuration Management"], ["Configuration & Change Manager"]),
    ("Process Engineering Change (ECR/ECO)", "Release & Hand Over", 2, "partial", 6,
     ["Engineering Change Management"], ["Configuration & Change Manager"]),
    ("Release Product Data", "Release & Hand Over", 3, "automated", 2,
     ["Product Release Management", "EBOM-to-MBOM Handover", "Product Cost Engineering"], ["Configuration & Change Manager"]),
    ("Create Technical Documentation", "Release & Hand Over", 3, "partial", 3,
     ["Technical Documentation Authoring", "Service Information Provision"], ["Technical Writer"]),
]
PROCESS_FLOW = [
    ("Manage Product Portfolio", "Capture & Analyze Requirements"),
    ("Capture & Analyze Requirements", "Develop System Architecture"),
    ("Develop System Architecture", "Design Mechanical Components"),
    ("Develop System Architecture", "Design Electrical & Hydraulic Systems"),
    ("Develop System Architecture", "Develop Embedded Software"),
    ("Design Mechanical Components", "Perform Simulation & Analysis"),
    ("Perform Simulation & Analysis", "Build & Test Prototypes"),
    ("Design Electrical & Hydraulic Systems", "Build & Test Prototypes"),
    ("Develop Embedded Software", "Build & Test Prototypes"),
    ("Build & Test Prototypes", "Manage Engineering BOM"),
    ("Process Engineering Change (ECR/ECO)", "Manage Engineering BOM"),
    ("Manage Engineering BOM", "Release Product Data"),
    ("Release Product Data", "Create Technical Documentation"),
]
ROLES = ["Product Manager", "Systems Engineer", "Mechanical Design Engineer", "Electrical Design Engineer",
         "Software Engineer", "Simulation Engineer", "Test Engineer", "Configuration & Change Manager", "Technical Writer"]

# ---------------------------------------------------------------------------------------------
# Application. Component tuple: (name, category, vendor, lifecycle, endOfLife, TIME, functional fit 1-4,
#   technical fit 1-4, businessCriticality, status)
# ---------------------------------------------------------------------------------------------
APPLICATIONS = [
    ("Siemens Polarion ALM", "Requirements & ALM", "Siemens", "phase-in", "", "invest", 3, 4, "business-critical", "current"),
    ("IBM DOORS 9.7", "Requirements & ALM", "IBM", "phase-out", "2027-03-31", "migrate", 2, 2, "business-critical", "current"),
    ("Atlassian Jira Data Center", "Requirements & ALM", "Atlassian", "active", "2029-03-28", "tolerate", 3, 3, "business-operational", "current"),
    ("CATIA Magic Cameo Systems Modeler", "MBSE", "Dassault Systemes", "phase-in", "", "invest", 3, 4, "business-operational", "current"),
    ("Sparx Enterprise Architect", "MBSE", "Sparx Systems", "phase-out", "2027-12-31", "migrate", 2, 2, "administrative", "current"),
    ("Siemens NX", "Mechanical CAD", "Siemens", "active", "", "invest", 4, 4, "mission-critical", "current"),
    ("PTC Creo 4.0", "Mechanical CAD", "PTC", "phase-out", "2027-12-31", "migrate", 3, 2, "business-critical", "current"),
    ("Autodesk AutoCAD Mechanical", "Mechanical CAD", "Autodesk", "end-of-life", "2026-09-30", "eliminate", 2, 2, "administrative", "current"),
    ("EPLAN Electric P8", "Electrical & Fluid CAD", "EPLAN", "active", "", "invest", 4, 3, "business-critical", "current"),
    ("EPLAN Fluid", "Electrical & Fluid CAD", "EPLAN", "active", "", "tolerate", 3, 3, "business-operational", "current"),
    ("Ansys Mechanical", "CAE & Simulation", "Ansys", "active", "", "invest", 4, 3, "business-critical", "current"),
    ("MATLAB / Simulink", "CAE & Simulation", "MathWorks", "active", "", "invest", 4, 4, "business-critical", "current"),
    ("MSC Adams", "CAE & Simulation", "Hexagon", "active", "", "tolerate", 3, 2, "business-operational", "current"),
    ("Teamcenter Simulation (SDM)", "CAE & Simulation", "Siemens", "plan", "", "invest", 0, 0, "business-operational", "target"),
    ("Siemens Teamcenter", "PLM & Configuration", "Siemens", "active", "", "invest", 4, 4, "mission-critical", "current"),
    ("PTC Windchill 10.2", "PLM & Configuration", "PTC", "phase-out", "2027-06-30", "migrate", 2, 1, "business-critical", "current"),
    ("Teamcenter Visualization", "PLM & Configuration", "Siemens", "active", "", "tolerate", 3, 3, "business-operational", "current"),
    ("BOM Excel Toolkit (in-house)", "PLM & Configuration", "In-house", "phase-out", "2026-12-31", "eliminate", 2, 1, "business-critical", "current"),
    ("Variant Configurator (in-house)", "PLM & Configuration", "In-house", "active", "2028-06-30", "migrate", 3, 1, "mission-critical", "current"),
    ("GitLab (self-managed)", "Software & Test Toolchain", "GitLab", "active", "", "invest", 4, 4, "business-critical", "current"),
    ("Jenkins CI", "Software & Test Toolchain", "Jenkins (OSS)", "phase-out", "2026-12-31", "eliminate", 2, 1, "business-operational", "current"),
    ("dSPACE HIL Test Suite", "Software & Test Toolchain", "dSPACE", "active", "", "tolerate", 3, 3, "business-critical", "current"),
    ("NI TestStand / LabVIEW", "Software & Test Toolchain", "NI (Emerson)", "active", "", "tolerate", 3, 2, "business-operational", "current"),
    ("Test Data Management DB (MS Access)", "Software & Test Toolchain", "In-house", "end-of-life", "2025-12-31", "eliminate", 1, 1, "business-operational", "current"),
    ("SAP S/4HANA (PP/MM)", "Documentation & Enterprise", "SAP", "active", "", "tolerate", 3, 4, "mission-critical", "current"),
    ("SCHEMA ST4", "Documentation & Enterprise", "Quanos", "active", "", "tolerate", 3, 3, "business-operational", "current"),
]
# (service, [realizing application components], [business processes it serves])
SERVICES = [
    ("Requirements Management Service", ["Siemens Polarion ALM", "IBM DOORS 9.7"], ["Capture & Analyze Requirements"]),
    ("Work Item Tracking Service", ["Atlassian Jira Data Center"], ["Capture & Analyze Requirements", "Develop Embedded Software"]),
    ("System Modeling Service", ["CATIA Magic Cameo Systems Modeler", "Sparx Enterprise Architect"], ["Develop System Architecture"]),
    ("3D CAD Design Service", ["Siemens NX", "PTC Creo 4.0"], ["Design Mechanical Components"]),
    ("2D Drafting Service", ["Autodesk AutoCAD Mechanical"], ["Design Mechanical Components"]),
    ("Electrical & Fluid CAD Service", ["EPLAN Electric P8", "EPLAN Fluid"], ["Design Electrical & Hydraulic Systems"]),
    ("Simulation Service", ["Ansys Mechanical", "MATLAB / Simulink", "MSC Adams"], ["Perform Simulation & Analysis"]),
    ("Simulation Data Management Service", ["Teamcenter Simulation (SDM)"], ["Perform Simulation & Analysis"]),
    ("Source Control & CI Service", ["GitLab (self-managed)", "Jenkins CI"], ["Develop Embedded Software"]),
    ("Model-based Software Development Service", ["MATLAB / Simulink"], ["Develop Embedded Software"]),
    ("Test Automation Service", ["dSPACE HIL Test Suite", "NI TestStand / LabVIEW"], ["Build & Test Prototypes"]),
    ("Test Data Service", ["Test Data Management DB (MS Access)"], ["Build & Test Prototypes"]),
    ("Product Data Management Service", ["Siemens Teamcenter", "PTC Windchill 10.2", "BOM Excel Toolkit (in-house)"],
     ["Manage Engineering BOM", "Process Engineering Change (ECR/ECO)", "Release Product Data"]),
    ("3D Visualization Service", ["Teamcenter Visualization"], ["Design Mechanical Components", "Create Technical Documentation"]),
    ("Variant Configuration Service", ["Variant Configurator (in-house)"], ["Manage Engineering BOM"]),
    ("Material & Production Data Service", ["SAP S/4HANA (PP/MM)"], ["Release Product Data"]),
    ("Technical Authoring Service", ["SCHEMA ST4"], ["Create Technical Documentation"]),
]

# ---------------------------------------------------------------------------------------------
# Implementation & Migration: plateaus (with the capability increments they realize), work
# packages realizing them, and gaps associated with plateaus and the processes they affect.
# ---------------------------------------------------------------------------------------------
PLATEAUS = [
    ("Transition 2026: Quick Wins", "2026-12-31", ["Engineering Change Management"]),
    ("Transition 2027: Digital Thread Foundation", "2027-12-31", [
        "Requirements Management", "Requirements Traceability", "Verification & Validation Planning",
        "System Architecture Modeling (MBSE)", "Continuous Integration & Delivery", "Software Testing & Hardware-in-the-Loop",
        "Simulation Data Management", "Structural Simulation (FEA)", "Engineering BOM Management",
        "Product Release Management", "Certification & Compliance Management"]),
    ("Target 2028: Integrated Engineering", "2028-12-31", [
        "Mechanical Design (3D CAD)", "Design Reuse & Standard Parts Management", "Variant & Configuration Management",
        "Virtual Prototyping", "Software Variant Management", "Multibody & System Simulation"]),
]
# (name, startDate, endDate, status, owner, plateau it realizes)
WORK_PACKAGES = [
    ("Digital Engineering Change Process", "2025-04-01", "2026-06-30", "completed", "Head of PLM",
     "Transition 2026: Quick Wins"),
    ("Engineering Workplace Modernization", "2025-01-01", "2026-09-30", "in progress", "Head of Engineering IT",
     "Transition 2026: Quick Wins"),
    ("PLM Consolidation: Windchill to Teamcenter", "2025-07-01", "2027-06-30", "in progress", "Head of PLM",
     "Transition 2027: Digital Thread Foundation"),
    ("ALM Rollout: Polarion & DOORS Retirement", "2025-10-01", "2027-03-31", "in progress", "Head of Systems Engineering",
     "Transition 2027: Digital Thread Foundation"),
    ("MBSE Enablement with Cameo", "2026-01-01", "2027-12-31", "in progress", "Head of Systems Engineering",
     "Transition 2027: Digital Thread Foundation"),
    ("Simulation Data Management & Cloud HPC", "2026-07-01", "2028-03-31", "in progress", "Head of Simulation",
     "Transition 2027: Digital Thread Foundation"),
    ("Software Factory: GitLab CI & HIL Automation", "2026-01-01", "2027-06-30", "in progress", "Head of Software Engineering",
     "Transition 2027: Digital Thread Foundation"),
    ("Compliance by Design (Machinery Regulation & CRA)", "2026-03-01", "2027-12-11", "in progress", "Head of Testing",
     "Transition 2027: Digital Thread Foundation"),
    ("CAD Harmonization: Creo & AutoCAD to NX", "2026-04-01", "2028-06-30", "in progress", "Head of Design Engineering",
     "Target 2028: Integrated Engineering"),
    ("PLM-integrated Variant Configurator", "2027-01-01", "2028-12-31", "planned", "Head of PLM",
     "Target 2028: Integrated Engineering"),
]
# (name, description, plateau or None when not yet planned, [affected processes])
GAPS = [
    ("Requirements not traceable from customer need to test",
     "Requirements, architecture and test cases live in separate tools without trace links.",
     "Transition 2027: Digital Thread Foundation", ["Capture & Analyze Requirements", "Develop System Architecture"]),
    ("Two PLM systems for one product structure",
     "Windchill (acquired unit) and Teamcenter hold overlapping product structures and change processes.",
     "Transition 2027: Digital Thread Foundation", ["Manage Engineering BOM", "Process Engineering Change (ECR/ECO)"]),
    ("Simulation results stored on local drives",
     "Simulation models and results are not versioned or linked to the design they validate.",
     "Transition 2027: Digital Thread Foundation", ["Perform Simulation & Analysis"]),
    ("Three CAD systems for mechanical design",
     "NX, Creo and AutoCAD are used in parallel, which blocks design reuse and doubles training effort.",
     "Target 2028: Integrated Engineering", ["Design Mechanical Components"]),
    ("Variant rules maintained outside PLM",
     "The in-house variant configurator holds configuration rules that PLM cannot validate.",
     "Target 2028: Integrated Engineering", ["Manage Engineering BOM"]),
    ("Manual EBOM-to-MBOM transfer to ERP",
     "The engineering BOM is re-keyed into SAP through an Excel toolkit for every release.",
     None, ["Release Product Data", "Manage Engineering BOM"]),
    ("No tool support for product portfolio decisions",
     "Portfolio and roadmap decisions are prepared in slide decks without structured data.",
     None, ["Manage Product Portfolio"]),
    ("Test data kept in local databases",
     "Test results are stored in MS Access databases per test bench.",
     None, ["Build & Test Prototypes"]),
]

VIEW_NAMES = [
    "Engineering Capability Map",
    "Engineering Processes & Roles",
    "Engineering Application Landscape",
    "Engineering Application Services",
    "Engineering Goals & Outcomes",
    "Engineering Transformation Roadmap",
]


def load_meta_model():
    path = Path(__file__).resolve().parent.parent / "backend" / "app" / "meta_model.py"
    spec = importlib.util.spec_from_file_location("meta_model", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------------------------
# Dataset assembly (pure) -- elements and relationships, validated against the meta-model
# ---------------------------------------------------------------------------------------------
def build_dataset() -> Tuple[List[Tuple[str, str, str, Dict[str, str], str]], List[Tuple[str, str, str]]]:
    """Return (elements, relationships). Element: (name, type, folder, properties, documentation).
    Relationship: (type, source name, target name)."""
    elements: List[Tuple[str, str, str, Dict[str, str], str]] = []
    for name, target_date in GOALS:
        elements.append((name, "Goal", "MOTIVATION", {"targetDate": target_date}, ""))
    for name, kpi, unit, baseline, current, target, direction, target_date, _goals, _caps in OUTCOMES:
        elements.append((name, "Outcome", "MOTIVATION", {
            "kpi": kpi, "unit": unit, "baseline": str(baseline), "current": str(current), "target": str(target),
            "direction": direction, "baselineDate": OUTCOME_BASELINE_DATE, "targetDate": target_date,
            "measuredAt": "2026-09-30"}, ""))
    for domain, owner, caps in CAPABILITY_DOMAINS:
        for name, maturity, target, importance in caps:
            elements.append((name, "Capability", "STRATEGY", {
                "capabilityDomain": domain, "owner": owner, "maturity": str(maturity),
                "targetMaturity": str(target), "strategicImportance": importance}, ""))
    for role in ROLES:
        elements.append((role, "BusinessRole", "BUSINESS", {}, ""))
    for name, phase, maturity, automation, media_breaks, _caps, _roles in PROCESSES:
        elements.append((name, "BusinessProcess", "BUSINESS", {
            "status": "current", "processPhase": phase, "maturity": str(maturity),
            "automationLevel": automation, "mediaBreaks": str(media_breaks)}, ""))
    for name, category, vendor, lifecycle, eol, time, ff, tf, crit, status in APPLICATIONS:
        elements.append((name, "ApplicationComponent", "APPLICATION", {
            "status": status, "applicationCategory": category, "vendor": vendor, "lifecycle": lifecycle,
            "endOfLife": eol, "timeClassification": time, "functionalFit": str(ff) if ff else "",
            "technicalFit": str(tf) if tf else "", "businessCriticality": crit}, ""))
    for name, _apps, _procs in SERVICES:
        elements.append((name, "ApplicationService", "APPLICATION", {}, ""))
    for name, target_date, _caps in PLATEAUS:
        elements.append((name, "Plateau", "IMPLEMENTATION_MIGRATION", {"targetDate": target_date}, ""))
    for name, start, end, status, owner, _plateau in WORK_PACKAGES:
        elements.append((name, "WorkPackage", "IMPLEMENTATION_MIGRATION", {
            "startDate": start, "endDate": end, "status": status, "owner": owner}, ""))
    for name, description, _plateau, _procs in GAPS:
        elements.append((name, "Gap", "IMPLEMENTATION_MIGRATION", {}, description))

    rels: List[Tuple[str, str, str]] = []
    for name, *_rest, goals, caps in OUTCOMES:
        rels += [("RealizationRelationship", name, g) for g in goals]
        rels += [("RealizationRelationship", c, name) for c in caps]
    for name, _phase, _m, _a, _mb, caps, roles in PROCESSES:
        rels += [("RealizationRelationship", name, c) for c in caps]
        rels += [("AssignmentRelationship", r, name) for r in roles]
    rels += [("TriggeringRelationship", s, t) for s, t in PROCESS_FLOW]
    for name, apps, procs in SERVICES:
        rels += [("RealizationRelationship", a, name) for a in apps]
        rels += [("ServingRelationship", name, p) for p in procs]
    for name, _date, caps in PLATEAUS:
        rels += [("RealizationRelationship", name, c) for c in caps]
    for name, *_rest, plateau in WORK_PACKAGES:
        rels.append(("RealizationRelationship", name, plateau))
    for name, _description, plateau, procs in GAPS:
        if plateau:
            rels.append(("AssociationRelationship", plateau, name))
        rels += [("AssociationRelationship", name, p) for p in procs]
    return elements, rels


def validate(elements, rels) -> None:
    meta = load_meta_model()
    names = [e[0] for e in elements]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise SystemExit(f"Duplicate element names in dataset: {duplicates}")
    types = {e[0]: e[1] for e in elements}
    not_allowed = sorted({t for t in types.values() if t not in meta.allowed_element_types()})
    if not_allowed:
        raise SystemExit(f"Element types outside the meta-model: {not_allowed}")
    missing = sorted({n for _t, s, d in rels for n in (s, d) if n not in types})
    if missing:
        raise SystemExit(f"Relationships reference unknown elements: {missing}")
    undeclared = sorted({(types[s], t, types[d]) for t, s, d in rels if not meta.is_declared_pair(types[s], t, types[d])})
    if undeclared:
        raise SystemExit(f"Relationship pairs not declared in the meta-model: {undeclared}")


# ---------------------------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------------------------
class Seeder:
    def __init__(self, client: SeedMcpClient):
        self.c = client
        self.ids: Dict[str, str] = {}

    def demo_folders(self, create: bool) -> Dict[str, str]:
        roots = {f["type"]: f["id"] for f in self.c.call("get-folders")}
        folders: Dict[str, str] = {}
        for folder_type in ("STRATEGY", "BUSINESS", "APPLICATION", "TECHNOLOGY", "MOTIVATION", "IMPLEMENTATION_MIGRATION", "DIAGRAMS"):
            existing = [f for f in self.c.call("get-folders", {"parentId": roots[folder_type]}) if f["name"] == DEMO_FOLDER]
            if existing:
                folders[folder_type] = existing[0]["id"]
            elif create and folder_type != "TECHNOLOGY":
                created = self.c.call("create-folder", {"parentId": roots[folder_type], "name": DEMO_FOLDER})
                folders[folder_type] = created.get("id") or created.get("folder", {}).get("id")
        return folders

    def create_elements(self, elements, folders: Dict[str, str]) -> None:
        operations = []
        for name, el_type, folder, props, doc in elements:
            params: Dict[str, Any] = {"type": el_type, "name": name, "folderId": folders[folder], "force": True,
                                      "properties": {k: v for k, v in props.items() if v != ""}, "source": SOURCE}
            if doc:
                params["documentation"] = doc
            operations.append({"tool": "create-element", "params": params})
        results = self.c.bulk(operations, "Engineering demo: elements")
        for (name, *_rest), result in zip(elements, results):
            self.ids[name] = result["entityId"]
        print(f"  created {len(results)} elements")

    def create_relationships(self, rels) -> None:
        operations = [{"tool": "create-relationship",
                       "params": {"type": t, "sourceId": self.ids[s], "targetId": self.ids[d], "source": SOURCE}}
                      for t, s, d in rels]
        print(f"  created {len(self.c.bulk(operations, 'Engineering demo: relationships'))} relationships")

    # -- views --------------------------------------------------------------------------------
    def _view(self, name: str, viewpoint: str, folder_id: str, body: List[Dict[str, Any]]) -> str:
        # No viewpoint on mixed views: Archi ghosts every element a viewpoint does not allow.
        params = {"name": name, "folderId": folder_id, "connectionRouterType": "manhattan"}
        if viewpoint:
            params["viewpoint"] = viewpoint
        results = self.c.bulk([{"tool": "create-view", "as": "view", "params": params}] + body,
                              f"Engineering demo: view {name}")
        return results[0]["entityId"]

    def _place(self, alias: str, element: str, x: int, y: int, w: int, h: int, parent: str | None = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"viewId": "$view.id", "elementId": self.ids[element], "x": x, "y": y, "width": w, "height": h}
        if parent:
            params["parentViewObjectId"] = f"${parent}.id"
        return {"tool": "add-to-view", "as": alias, "params": params}

    @staticmethod
    def _group(alias: str, label: str, x: int, y: int, w: int, h: int) -> Dict[str, Any]:
        return {"tool": "add-group-to-view", "as": alias,
                "params": {"viewId": "$view.id", "label": label, "x": x, "y": y, "width": w, "height": h}}

    def _stacked_columns(self, columns: List[Tuple[str, List[str]]], container: str, col_w: int = 280,
                         per_row: int = 4, top: int = 20, left: int = 20) -> Tuple[List[Dict[str, Any]], int]:
        """Lay out containers in a grid; each container stacks its children. Returns (ops, bottom y)."""
        ops: List[Dict[str, Any]] = []
        child_h, gap, header, pad = 45, 10, 36, 14
        heights = [header + max(1, len(children)) * (child_h + gap) + pad for _label, children in columns]
        y = top
        for row_start in range(0, len(columns), per_row):
            row = columns[row_start : row_start + per_row]
            row_h = max(heights[row_start : row_start + per_row])
            for offset, (label, children) in enumerate(row):
                idx = row_start + offset
                x = left + offset * (col_w + 24)
                alias = f"c{idx}"
                if container == "element":
                    ops.append(self._place(alias, label, x, y, col_w, row_h))
                else:
                    ops.append(self._group(alias, label, x, y, col_w, row_h))
                for j, child in enumerate(children):
                    ops.append(self._place(f"c{idx}_{j}", child, pad, header + j * (child_h + gap), col_w - 2 * pad, child_h, parent=alias))
            y += row_h + 24
        return ops, y

    def create_views(self, folders: Dict[str, str]) -> None:
        views_folder = folders["DIAGRAMS"]

        # Strategy: capabilities grouped by domain.
        ops, _ = self._stacked_columns([(domain, [c[0] for c in caps]) for domain, _o, caps in CAPABILITY_DOMAINS], "group")
        self._view(VIEW_NAMES[0], "capability", views_folder, ops)

        # Business: processes grouped by phase, roles below; triggering and assignment drawn.
        by_phase = [(phase, [p[0] for p in PROCESSES if p[1] == phase]) for phase in PHASES]
        ops, bottom = self._stacked_columns(by_phase, "group", col_w=240, per_row=5)
        for i, role in enumerate(ROLES):
            ops.append(self._place(f"r{i}", role, 20 + (i % 5) * 264, bottom + 60 + (i // 5) * 80, 240, 50))
        view = self._view(VIEW_NAMES[1], "", views_folder, ops)
        self.c.call("auto-connect-view", {"viewId": view, "showLabel": False,
                                          "relationshipTypes": ["TriggeringRelationship", "AssignmentRelationship"]})
        self.c.call("auto-route-connections", {"viewId": view})

        # Application: landscape by category, and services with the components realizing them nested.
        categories: Dict[str, List[str]] = {}
        for app in APPLICATIONS:
            categories.setdefault(app[1], []).append(app[0])
        ops, _ = self._stacked_columns(list(categories.items()), "group")
        self._view(VIEW_NAMES[2], "application_cooperation", views_folder, ops)
        ops, _ = self._stacked_columns([(name, apps) for name, apps, _p in SERVICES], "element", col_w=300, per_row=4)
        self._view(VIEW_NAMES[3], "", views_folder, ops)

        # Motivation: goals -> outcomes <- capabilities.
        caps_in_view: List[str] = []
        for o in OUTCOMES:
            caps_in_view += [c for c in o[9] if c not in caps_in_view]
        width = len(OUTCOMES) * 210
        ops = []
        for row, (prefix, items) in enumerate((("g", [g[0] for g in GOALS]), ("o", [o[0] for o in OUTCOMES]), ("k", caps_in_view))):
            step = width / len(items)
            for i, name in enumerate(items):
                ops.append(self._place(f"{prefix}{i}", name, int(20 + i * step + (step - 190) / 2), 20 + row * 150, 190, 60))
        view = self._view(VIEW_NAMES[4], "", views_folder, ops)
        self.c.call("auto-connect-view", {"viewId": view, "showLabel": False, "relationshipTypes": ["RealizationRelationship"]})
        self.c.call("auto-route-connections", {"viewId": view})

        # Implementation & Migration: plateau columns with their work packages and gaps.
        ops = []
        unplanned = [g[0] for g in GAPS if not g[2]]
        for i, (plateau, _date, _caps) in enumerate(PLATEAUS):
            x = 20 + i * 340
            ops.append(self._place(f"pl{i}", plateau, x, 20, 310, 60))
            packages = [w[0] for w in WORK_PACKAGES if w[5] == plateau]
            wp_h = 40 + len(packages) * 65 + 10
            ops.append(self._group(f"wg{i}", "Work packages", x, 120, 310, wp_h))
            for j, wp in enumerate(packages):
                ops.append(self._place(f"wp{i}_{j}", wp, 14, 36 + j * 65, 282, 55, parent=f"wg{i}"))
            gaps = [g[0] for g in GAPS if g[2] == plateau]
            if gaps:
                ops.append(self._group(f"gg{i}", "Gaps", x, 120 + wp_h + 30, 310, 40 + len(gaps) * 65 + 10))
                for j, gap in enumerate(gaps):
                    ops.append(self._place(f"gp{i}_{j}", gap, 14, 36 + j * 65, 282, 55, parent=f"gg{i}"))
        x = 20 + len(PLATEAUS) * 340
        ops.append(self._group("gu", "Gaps not yet planned in a plateau", x, 120, 310, 40 + len(unplanned) * 65 + 10))
        for j, gap in enumerate(unplanned):
            ops.append(self._place(f"gu{j}", gap, 14, 36 + j * 65, 282, 55, parent="gu"))
        view = self._view(VIEW_NAMES[5], "implementation_migration", views_folder, ops)
        self.c.call("auto-connect-view", {"viewId": view, "showLabel": False,
                                          "relationshipTypes": ["RealizationRelationship", "AssociationRelationship"]})
        self.c.call("auto-route-connections", {"viewId": view})
        print(f"  created {len(VIEW_NAMES)} views")


def purge(client: SeedMcpClient) -> None:
    folders = Seeder(client).demo_folders(create=False)
    if not folders:
        print("No 'Engineering Demo' folders found -- nothing to purge.")
        return
    client.bulk([{"tool": "delete-folder", "params": {"folderId": fid, "force": True}} for fid in folders.values()],
                "Engineering demo: purge")
    print(f"Removed {len(folders)} 'Engineering Demo' folders and everything in them.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:18090/mcp")
    parser.add_argument("--token", default="", help="Bearer token, if enabled in Archi's MCP preferences")
    parser.add_argument("--purge", action="store_true", help="Remove the demo dataset instead of creating it")
    args = parser.parse_args()

    elements, rels = build_dataset()
    validate(elements, rels)
    client = SeedMcpClient(args.url, args.token)
    model = client.call("get-model-info")
    print(f"Active model: {model.get('name') or '(unnamed)'}")
    if client.approval_mode_on():
        print("Approval Mode is ON in Archi -- switch off MCP Server > Approval Mode and run again.")
        return 1
    if args.purge:
        purge(client)
        return 0

    seeder = Seeder(client)
    if seeder.demo_folders(create=False):
        print("An 'Engineering Demo' dataset already exists. Run with --purge first to recreate it.")
        return 1
    try:
        folders = seeder.demo_folders(create=True)
        seeder.create_elements(elements, folders)
        seeder.create_relationships(rels)
        seeder.create_views(folders)
    except McpError as exc:
        print(f"Seeding failed: {exc}\nRun with --purge to remove the partial dataset.")
        return 1
    print(f"Done: {len(elements)} elements, {len(rels)} relationships, all within the meta-model.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
