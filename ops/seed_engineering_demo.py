#!/usr/bin/env python3
"""Seed the active Archi model with an engineering / product-development demo dataset.

The dataset covers every ArchiMate layer the Transformation Dashboard reads (Strategy, Business,
Application, Technology, Motivation, Implementation & Migration), carries the property schema
documented in docs/transformation-dashboard.md, and adds one view per layer.

Everything is created inside "Engineering Demo" subfolders, so `--purge` removes it again
without touching anything else in the model.

Usage (Archi running, MCP server started, Approval Mode OFF):
    python3 ops/seed_engineering_demo.py            # seed
    python3 ops/seed_engineering_demo.py --purge    # remove the demo dataset
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, List, Tuple

from mcp_seed_client import McpError, SeedMcpClient

DEMO_FOLDER = "Engineering Demo"
SOURCE = {"dataset": "engineering-demo-v1"}

# ---------------------------------------------------------------------------------------------
# Strategy layer: engineering capability map (L1 -> L2) and the value stream it serves.
# L2 tuple: (name, maturity 1-5, targetMaturity 1-5, strategicImportance)
# ---------------------------------------------------------------------------------------------
CAPABILITIES: List[Tuple[str, str, str, List[Tuple[str, int, int, str]]]] = [
    ("Product Strategy & Portfolio Management", "Head of Product Management", "Define Product", [
        ("Product Portfolio Planning", 2, 3, "medium"),
        ("Product Roadmapping", 2, 3, "medium"),
        ("Market & Customer Requirements Capture", 2, 4, "high"),
        ("Product Cost Engineering", 2, 4, "high"),
    ]),
    ("Requirements & Systems Engineering", "Head of Systems Engineering", "Specify Requirements", [
        ("Requirements Management", 2, 4, "high"),
        ("System Architecture Modeling (MBSE)", 1, 4, "high"),
        ("Requirements Traceability", 1, 4, "high"),
        ("Verification & Validation Planning", 2, 3, "medium"),
    ]),
    ("Mechanical & Electrical Design", "Head of Design Engineering", "Design & Develop", [
        ("Mechanical Design (3D CAD)", 4, 4, "high"),
        ("Electrical & Fluid Power Design", 3, 4, "high"),
        ("Design Reuse & Standard Parts Management", 2, 4, "high"),
        ("Technical Drawing & PMI", 3, 3, "low"),
    ]),
    ("Embedded Software Engineering", "Head of Software Engineering", "Design & Develop", [
        ("Software Development & Version Control", 3, 4, "medium"),
        ("Continuous Integration & Delivery", 2, 4, "high"),
        ("Software Testing & Hardware-in-the-Loop", 3, 4, "high"),
        ("Software Variant Management", 1, 3, "medium"),
    ]),
    ("Simulation & Virtual Validation", "Head of Simulation", "Validate & Verify", [
        ("Structural Simulation (FEA)", 3, 4, "high"),
        ("Multibody & System Simulation", 3, 4, "medium"),
        ("Simulation Data Management", 1, 3, "medium"),
        ("Virtual Prototyping", 2, 4, "high"),
    ]),
    ("Test & Prototype Validation", "Head of Testing", "Validate & Verify", [
        ("Physical Prototype Testing", 3, 3, "medium"),
        ("Test Data Management", 1, 3, "medium"),
        ("Certification & Compliance Management", 2, 4, "high"),
    ]),
    ("Product Data & Configuration Management", "Head of PLM", "Release & Hand Over", [
        ("Engineering BOM Management", 2, 4, "high"),
        ("Variant & Configuration Management", 2, 5, "high"),
        ("Engineering Change Management", 2, 4, "high"),
        ("Product Release Management", 3, 4, "medium"),
    ]),
    ("Technical Documentation & Handover", "Head of Technical Documentation", "Release & Hand Over", [
        ("Technical Documentation Authoring", 3, 3, "low"),
        ("EBOM-to-MBOM Handover", 2, 4, "high"),
        ("Service Information Provision", 2, 3, "medium"),
    ]),
]

VALUE_STREAM = "Idea to Released Product"
VALUE_STREAM_STAGES = ["Define Product", "Specify Requirements", "Design & Develop", "Validate & Verify", "Release & Hand Over"]

# ---------------------------------------------------------------------------------------------
# Business layer. Process tuple:
# (name, stage, maturity 1-5, automationLevel, mediaBreaks, [realized L2 capabilities], [roles])
# ---------------------------------------------------------------------------------------------
PROCESSES = [
    ("Manage Product Portfolio", "Define Product", 2, "manual", 4,
     ["Product Portfolio Planning", "Product Roadmapping"], ["Product Manager"]),
    ("Capture & Analyze Requirements", "Specify Requirements", 2, "partial", 5,
     ["Requirements Management", "Market & Customer Requirements Capture"], ["Product Manager", "Systems Engineer"]),
    ("Develop System Architecture", "Specify Requirements", 2, "manual", 3,
     ["System Architecture Modeling (MBSE)", "Requirements Traceability"], ["Systems Engineer"]),
    ("Design Mechanical Components", "Design & Develop", 4, "partial", 2,
     ["Mechanical Design (3D CAD)", "Technical Drawing & PMI"], ["Mechanical Design Engineer"]),
    ("Design Electrical & Hydraulic Systems", "Design & Develop", 3, "partial", 3,
     ["Electrical & Fluid Power Design"], ["Electrical Design Engineer"]),
    ("Develop Embedded Software", "Design & Develop", 3, "partial", 2,
     ["Software Development & Version Control", "Continuous Integration & Delivery", "Software Variant Management"],
     ["Software Engineer"]),
    ("Perform Simulation & Analysis", "Validate & Verify", 3, "partial", 3,
     ["Structural Simulation (FEA)", "Multibody & System Simulation", "Simulation Data Management"], ["Simulation Engineer"]),
    ("Build & Test Prototypes", "Validate & Verify", 2, "manual", 4,
     ["Physical Prototype Testing", "Test Data Management", "Software Testing & Hardware-in-the-Loop"], ["Test Engineer"]),
    ("Manage Engineering BOM", "Release & Hand Over", 2, "partial", 5,
     ["Engineering BOM Management", "Variant & Configuration Management"], ["Configuration & Change Manager"]),
    ("Process Engineering Change (ECR/ECO)", "Release & Hand Over", 2, "partial", 6,
     ["Engineering Change Management"], ["Configuration & Change Manager"]),
    ("Release Product Data", "Release & Hand Over", 3, "automated", 2,
     ["Product Release Management", "EBOM-to-MBOM Handover"], ["Configuration & Change Manager"]),
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

# role -> (headcount, value-stream column used on the business view)
ROLES = {
    "Product Manager": (6, 0),
    "Systems Engineer": (9, 1),
    "Mechanical Design Engineer": (48, 2),
    "Electrical Design Engineer": (17, 2),
    "Software Engineer": (26, 2),
    "Simulation Engineer": (8, 3),
    "Test Engineer": (14, 3),
    "Configuration & Change Manager": (7, 4),
    "Technical Writer": (6, 4),
}

# ---------------------------------------------------------------------------------------------
# Application layer. Dict per application component; see docs/transformation-dashboard.md.
# ---------------------------------------------------------------------------------------------
def app(name, category, vendor, lifecycle, eol, time, ff, tf, crit, cost, users, hosting, caps, procs, status="current"):
    return {
        "name": name, "category": category, "caps": caps, "procs": procs,
        "properties": {
            "status": status,
            "applicationCategory": category,
            "vendor": vendor,
            "lifecycle": lifecycle,
            "endOfLife": eol,
            "timeClassification": time,
            "functionalFit": str(ff) if ff else "",
            "technicalFit": str(tf) if tf else "",
            "businessCriticality": crit,
            "annualCostEUR": str(cost),
            "users": str(users),
            "hosting": hosting,
        },
    }


APPLICATIONS = [
    # Requirements & ALM
    app("Siemens Polarion ALM", "Requirements & ALM", "Siemens", "phase-in", "", "invest", 3, 4, "business-critical", 210000, 220, "cloud",
        ["Requirements Management", "Requirements Traceability", "Verification & Validation Planning"],
        ["Capture & Analyze Requirements"]),
    app("IBM DOORS 9.7", "Requirements & ALM", "IBM", "phase-out", "2027-03-31", "migrate", 2, 2, "business-critical", 160000, 120, "on-premise",
        ["Requirements Management"], ["Capture & Analyze Requirements"]),
    app("Atlassian Jira Data Center", "Requirements & ALM", "Atlassian", "active", "2029-03-28", "tolerate", 3, 3, "business-operational", 120000, 900, "on-premise",
        ["Software Development & Version Control"], ["Capture & Analyze Requirements", "Develop Embedded Software"]),
    # MBSE
    app("CATIA Magic Cameo Systems Modeler", "MBSE", "Dassault Systemes", "phase-in", "", "invest", 3, 4, "business-operational", 95000, 35, "on-premise",
        ["System Architecture Modeling (MBSE)"], ["Develop System Architecture"]),
    app("Sparx Enterprise Architect", "MBSE", "Sparx Systems", "phase-out", "2027-12-31", "migrate", 2, 2, "administrative", 15000, 25, "on-premise",
        ["System Architecture Modeling (MBSE)"], ["Develop System Architecture"]),
    # Mechanical CAD
    app("Siemens NX", "Mechanical CAD", "Siemens", "active", "", "invest", 4, 4, "mission-critical", 980000, 320, "on-premise",
        ["Mechanical Design (3D CAD)", "Technical Drawing & PMI", "Design Reuse & Standard Parts Management"],
        ["Design Mechanical Components"]),
    app("PTC Creo 4.0", "Mechanical CAD", "PTC", "phase-out", "2027-12-31", "migrate", 3, 2, "business-critical", 260000, 70, "on-premise",
        ["Mechanical Design (3D CAD)"], ["Design Mechanical Components"]),
    app("Autodesk AutoCAD Mechanical", "Mechanical CAD", "Autodesk", "end-of-life", "2026-09-30", "eliminate", 2, 2, "administrative", 45000, 60, "on-premise",
        ["Technical Drawing & PMI"], ["Design Mechanical Components"]),
    # Electrical & Fluid CAD
    app("EPLAN Electric P8", "Electrical & Fluid CAD", "EPLAN", "active", "", "invest", 4, 3, "business-critical", 210000, 75, "on-premise",
        ["Electrical & Fluid Power Design"], ["Design Electrical & Hydraulic Systems"]),
    app("EPLAN Fluid", "Electrical & Fluid CAD", "EPLAN", "active", "", "tolerate", 3, 3, "business-operational", 60000, 30, "on-premise",
        ["Electrical & Fluid Power Design"], ["Design Electrical & Hydraulic Systems"]),
    # CAE & Simulation
    app("Ansys Mechanical", "CAE & Simulation", "Ansys", "active", "", "invest", 4, 3, "business-critical", 340000, 45, "on-premise",
        ["Structural Simulation (FEA)", "Virtual Prototyping"], ["Perform Simulation & Analysis"]),
    app("MATLAB / Simulink", "CAE & Simulation", "MathWorks", "active", "", "invest", 4, 4, "business-critical", 180000, 90, "on-premise",
        ["Multibody & System Simulation", "Software Testing & Hardware-in-the-Loop"],
        ["Perform Simulation & Analysis", "Develop Embedded Software"]),
    app("MSC Adams", "CAE & Simulation", "Hexagon", "active", "", "tolerate", 3, 2, "business-operational", 70000, 12, "on-premise",
        ["Multibody & System Simulation"], ["Perform Simulation & Analysis"]),
    app("Teamcenter Simulation (SDM)", "CAE & Simulation", "Siemens", "plan", "", "invest", 0, 0, "business-operational", 150000, 0, "cloud",
        ["Simulation Data Management"], ["Perform Simulation & Analysis"], status="target"),
    # PLM & Configuration
    app("Siemens Teamcenter", "PLM & Configuration", "Siemens", "active", "", "invest", 4, 4, "mission-critical", 1250000, 650, "on-premise",
        ["Engineering BOM Management", "Engineering Change Management", "Product Release Management",
         "Design Reuse & Standard Parts Management"],
        ["Manage Engineering BOM", "Process Engineering Change (ECR/ECO)", "Release Product Data"]),
    app("PTC Windchill 10.2", "PLM & Configuration", "PTC", "phase-out", "2027-06-30", "migrate", 2, 1, "business-critical", 380000, 140, "on-premise",
        ["Engineering BOM Management", "Engineering Change Management"],
        ["Manage Engineering BOM", "Process Engineering Change (ECR/ECO)"]),
    app("Teamcenter Visualization", "PLM & Configuration", "Siemens", "active", "", "tolerate", 3, 3, "business-operational", 90000, 400, "on-premise",
        ["Virtual Prototyping"], ["Design Mechanical Components", "Create Technical Documentation"]),
    app("BOM Excel Toolkit (in-house)", "PLM & Configuration", "In-house", "phase-out", "2026-12-31", "eliminate", 2, 1, "business-critical", 40000, 85, "on-premise",
        ["Engineering BOM Management", "EBOM-to-MBOM Handover"], ["Manage Engineering BOM"]),
    app("Variant Configurator (in-house)", "PLM & Configuration", "In-house", "active", "2028-06-30", "migrate", 3, 1, "mission-critical", 220000, 210, "on-premise",
        ["Variant & Configuration Management"], ["Manage Engineering BOM"]),
    # Software & Test Toolchain
    app("GitLab (self-managed)", "Software & Test Toolchain", "GitLab", "active", "", "invest", 4, 4, "business-critical", 85000, 180, "cloud",
        ["Software Development & Version Control", "Continuous Integration & Delivery"], ["Develop Embedded Software"]),
    app("Jenkins CI", "Software & Test Toolchain", "Jenkins (OSS)", "phase-out", "2026-12-31", "eliminate", 2, 1, "business-operational", 25000, 60, "on-premise",
        ["Continuous Integration & Delivery"], ["Develop Embedded Software"]),
    app("dSPACE HIL Test Suite", "Software & Test Toolchain", "dSPACE", "active", "", "tolerate", 3, 3, "business-critical", 150000, 25, "on-premise",
        ["Software Testing & Hardware-in-the-Loop"], ["Build & Test Prototypes"]),
    app("NI TestStand / LabVIEW", "Software & Test Toolchain", "NI (Emerson)", "active", "", "tolerate", 3, 2, "business-operational", 65000, 30, "on-premise",
        ["Physical Prototype Testing", "Test Data Management"], ["Build & Test Prototypes"]),
    app("Test Data Management DB (MS Access)", "Software & Test Toolchain", "In-house", "end-of-life", "2025-12-31", "eliminate", 1, 1, "business-operational", 8000, 40, "on-premise",
        ["Test Data Management"], ["Build & Test Prototypes"]),
    # Documentation & Enterprise
    app("SAP S/4HANA (PP/MM)", "Documentation & Enterprise", "SAP", "active", "", "tolerate", 3, 4, "mission-critical", 300000, 1500, "on-premise",
        ["EBOM-to-MBOM Handover", "Product Cost Engineering"], ["Release Product Data"]),
    app("SCHEMA ST4", "Documentation & Enterprise", "Quanos", "active", "", "tolerate", 3, 3, "business-operational", 110000, 28, "on-premise",
        ["Technical Documentation Authoring", "Service Information Provision"], ["Create Technical Documentation"]),
]

# ---------------------------------------------------------------------------------------------
# Technology layer. Tuple: (name, ArchiMate type, category, vendor, version, lifecycle,
#   vendorSupportEnd, techRadar, hosting, [served applications])
# ---------------------------------------------------------------------------------------------
TECHNOLOGY = [
    ("PLM Server Cluster (VMware vSphere 8)", "Node", "Compute", "VMware / Dell", "8.0", "active", "2027-10-11", "adopt", "on-premise",
     ["Siemens Teamcenter", "Teamcenter Visualization", "PTC Windchill 10.2", "Variant Configurator (in-house)", "IBM DOORS 9.7", "Atlassian Jira Data Center"]),
    ("Oracle Database 19c", "SystemSoftware", "Database", "Oracle", "19c", "active", "2029-12-31", "adopt", "on-premise",
     ["Siemens Teamcenter"]),
    ("Oracle Database 12c", "SystemSoftware", "Database", "Oracle", "12.2", "end-of-life", "2022-03-31", "hold", "on-premise",
     ["PTC Windchill 10.2", "Variant Configurator (in-house)"]),
    ("Windows Server 2012 R2", "SystemSoftware", "Operating System", "Microsoft", "2012 R2", "end-of-life", "2023-10-10", "hold", "on-premise",
     ["Jenkins CI", "Test Data Management DB (MS Access)", "IBM DOORS 9.7"]),
    ("Windows Server 2022", "SystemSoftware", "Operating System", "Microsoft", "2022", "active", "2031-10-14", "adopt", "on-premise",
     ["Siemens Teamcenter", "Atlassian Jira Data Center", "SCHEMA ST4"]),
    ("CAD Workstations (Windows 10)", "Device", "Workstation", "Dell / Microsoft", "Windows 10 22H2", "end-of-life", "2025-10-14", "hold", "on-premise",
     ["PTC Creo 4.0", "Autodesk AutoCAD Mechanical", "EPLAN Fluid", "Sparx Enterprise Architect"]),
    ("CAD Workstations (Windows 11)", "Device", "Workstation", "Dell / Microsoft", "Windows 11 24H2", "phase-in", "2027-10-12", "adopt", "on-premise",
     ["Siemens NX", "Ansys Mechanical", "MATLAB / Simulink", "CATIA Magic Cameo Systems Modeler", "EPLAN Electric P8"]),
    ("HPC Simulation Cluster", "Node", "Compute", "HPE", "Apollo 2000 Gen10", "active", "2027-06-30", "hold", "on-premise",
     ["Ansys Mechanical", "MSC Adams"]),
    ("Red Hat Enterprise Linux 7", "SystemSoftware", "Operating System", "Red Hat", "7.9", "end-of-life", "2024-06-30", "hold", "on-premise",
     ["Ansys Mechanical", "MSC Adams"]),
    ("Azure HPC (Cloud Bursting)", "Node", "Compute", "Microsoft", "HBv4", "plan", "", "assess", "cloud",
     ["Teamcenter Simulation (SDM)", "Ansys Mechanical"]),
    ("Azure Kubernetes Service", "Node", "Container Platform", "Microsoft", "1.30", "active", "", "adopt", "cloud",
     ["GitLab (self-managed)", "Siemens Polarion ALM"]),
    ("PostgreSQL 16", "SystemSoftware", "Database", "PostgreSQL Global Development Group", "16", "active", "2028-11-09", "adopt", "cloud",
     ["GitLab (self-managed)", "Siemens Polarion ALM"]),
    ("FlexNet License Server", "SystemSoftware", "Licensing", "Flexera", "11.19", "active", "2027-12-31", "trial", "on-premise",
     ["Siemens NX", "PTC Creo 4.0", "Ansys Mechanical", "MATLAB / Simulink", "EPLAN Electric P8"]),
    ("Engineering File Server (NAS)", "Device", "Storage", "NetApp", "ONTAP 9.8", "phase-out", "2026-06-30", "hold", "on-premise",
     ["Autodesk AutoCAD Mechanical", "BOM Excel Toolkit (in-house)", "Test Data Management DB (MS Access)", "PTC Creo 4.0"]),
    ("SAP HANA Platform", "SystemSoftware", "Database", "SAP", "2.0 SPS07", "active", "2030-12-31", "adopt", "on-premise",
     ["SAP S/4HANA (PP/MM)"]),
]

# ---------------------------------------------------------------------------------------------
# Motivation layer.
# ---------------------------------------------------------------------------------------------
DRIVERS = [
    ("Shorter innovation cycles in construction machinery", ["Reduce time-to-market by 25%", "Increase engineering efficiency through reuse"]),
    ("Shortage of skilled engineers", ["Establish an end-to-end digital thread", "Increase engineering efficiency through reuse"]),
    ("EU Machinery Regulation & Cyber Resilience Act", ["Ensure compliance by design"]),
    ("Growing mechatronic & software complexity", ["Establish an end-to-end digital thread"]),
]
GOALS = [
    ("Reduce time-to-market by 25%", "2028-12-31"),
    ("Increase engineering efficiency through reuse", "2028-12-31"),
    ("Ensure compliance by design", "2027-12-11"),
    ("Establish an end-to-end digital thread", "2028-12-31"),
]
# Baselines were measured at programme start; on-track status compares progress with the share of
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
# Implementation & Migration layer.
# ---------------------------------------------------------------------------------------------
PLATEAUS = [("Baseline 2026", "2026-12-31"), ("Transition 2027", "2027-12-31"), ("Target 2028", "2028-12-31")]
# (name, start, end, progress %, budgetEUR, actualCostEUR, rag, plateau, owner, [associated elements])
WORK_PACKAGES = [
    ("Digital Engineering Change Process", "2025-04-01", "2026-12-31", 75, 500000, 470000, "red", "Baseline 2026",
     "Head of PLM", ["Engineering Change Management"]),
    ("Workplace & Server Modernization", "2025-01-01", "2026-09-30", 90, 600000, 640000, "red", "Baseline 2026",
     "Head of Engineering IT", ["CAD Workstations (Windows 10)", "Windows Server 2012 R2"]),
    ("PLM Consolidation: Windchill to Teamcenter", "2025-07-01", "2027-06-30", 45, 2400000, 1250000, "amber", "Transition 2027",
     "Head of PLM", ["Engineering BOM Management", "Engineering Change Management", "Product Release Management", "PTC Windchill 10.2"]),
    ("ALM Rollout: Polarion & DOORS Retirement", "2025-10-01", "2027-03-31", 40, 900000, 420000, "green", "Transition 2027",
     "Head of Systems Engineering", ["Requirements Management", "Requirements Traceability", "Verification & Validation Planning", "IBM DOORS 9.7"]),
    ("MBSE Enablement with Cameo", "2026-01-01", "2027-12-31", 20, 650000, 160000, "green", "Transition 2027",
     "Head of Systems Engineering", ["System Architecture Modeling (MBSE)", "Requirements Traceability"]),
    ("Simulation Data Management & Cloud HPC", "2026-07-01", "2027-12-31", 10, 750000, 60000, "green", "Transition 2027",
     "Head of Simulation", ["Simulation Data Management", "Virtual Prototyping", "Structural Simulation (FEA)"]),
    ("Software Factory: GitLab CI & HIL Automation", "2026-01-01", "2027-06-30", 35, 400000, 150000, "green", "Transition 2027",
     "Head of Software Engineering", ["Continuous Integration & Delivery", "Software Testing & Hardware-in-the-Loop", "Jenkins CI"]),
    ("Compliance by Design (Machinery Regulation & CRA)", "2026-03-01", "2027-12-11", 30, 450000, 140000, "amber", "Transition 2027",
     "Head of Testing", ["Certification & Compliance Management"]),
    ("CAD Harmonization: Creo & AutoCAD to NX", "2026-04-01", "2028-06-30", 15, 1800000, 300000, "amber", "Target 2028",
     "Head of Design Engineering", ["Mechanical Design (3D CAD)", "Design Reuse & Standard Parts Management", "PTC Creo 4.0"]),
    ("PLM-integrated Variant Configurator", "2027-01-01", "2028-12-31", 0, 1200000, 0, "green", "Target 2028",
     "Head of PLM", ["Variant & Configuration Management", "Variant Configurator (in-house)"]),
]

# Technology view groups (infrastructure landscape); every TECHNOLOGY entry must appear once.
TECH_GROUPS = [
    ("Data Center: Compute & Storage", ["PLM Server Cluster (VMware vSphere 8)", "HPC Simulation Cluster", "Engineering File Server (NAS)"]),
    ("Data Center: Operating Systems", ["Windows Server 2012 R2", "Windows Server 2022", "Red Hat Enterprise Linux 7"]),
    ("Data Center: Databases & Licensing", ["Oracle Database 19c", "Oracle Database 12c", "SAP HANA Platform", "FlexNet License Server"]),
    ("Azure Cloud", ["Azure Kubernetes Service", "PostgreSQL 16", "Azure HPC (Cloud Bursting)"]),
    ("Engineering Workplace", ["CAD Workstations (Windows 10)", "CAD Workstations (Windows 11)"]),
]

VIEW_NAMES = [
    "Engineering Capability Map",
    "Engineering Value Stream & Processes",
    "Engineering Application Landscape",
    "Engineering Technology Infrastructure",
    "Engineering Motivation & KPIs",
    "Engineering Transformation Roadmap",
]


# ---------------------------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------------------------
class Seeder:
    def __init__(self, client: SeedMcpClient):
        self.c = client
        self.ids: Dict[str, str] = {}  # element name -> id
        self.rel_count = 0

    # -- folders ------------------------------------------------------------------------------
    def demo_folders(self, create: bool) -> Dict[str, str]:
        roots = {f["type"]: f["id"] for f in self.c.call("get-folders")}
        folders: Dict[str, str] = {}
        for folder_type in ("STRATEGY", "BUSINESS", "APPLICATION", "TECHNOLOGY", "MOTIVATION", "IMPLEMENTATION_MIGRATION", "DIAGRAMS"):
            existing = [f for f in self.c.call("get-folders", {"parentId": roots[folder_type]}) if f["name"] == DEMO_FOLDER]
            if existing:
                folders[folder_type] = existing[0]["id"]
            elif create:
                created = self.c.call("create-folder", {"parentId": roots[folder_type], "name": DEMO_FOLDER})
                folders[folder_type] = created.get("id") or created.get("folder", {}).get("id")
        return folders

    # -- elements -----------------------------------------------------------------------------
    def create_elements(self, folders: Dict[str, str]) -> None:
        specs: List[Tuple[str, str, str, Dict[str, str], str]] = []  # (name, type, folder, props, documentation)

        for l1, owner, _stage, children in CAPABILITIES:
            specs.append((l1, "Capability", "STRATEGY", {"capabilityLevel": "L1", "owner": owner}, ""))
            for name, maturity, target, importance in children:
                specs.append((name, "Capability", "STRATEGY", {
                    "capabilityLevel": "L2", "parentCapability": l1, "owner": owner,
                    "maturity": str(maturity), "targetMaturity": str(target), "strategicImportance": importance,
                }, ""))
        specs.append((VALUE_STREAM, "ValueStream", "STRATEGY", {}, "End-to-end engineering value stream."))
        for order, stage in enumerate(VALUE_STREAM_STAGES, start=1):
            specs.append((stage, "ValueStream", "STRATEGY", {"stageOrder": str(order)}, ""))

        for name, stage, maturity, automation, media_breaks, _caps, _roles in PROCESSES:
            specs.append((name, "BusinessProcess", "BUSINESS", {
                "status": "current", "valueStreamStage": stage, "maturity": str(maturity),
                "automationLevel": automation, "mediaBreaks": str(media_breaks),
            }, ""))
        for role, (headcount, _col) in ROLES.items():
            specs.append((role, "BusinessRole", "BUSINESS", {"headcount": str(headcount)}, ""))

        for a in APPLICATIONS:
            specs.append((a["name"], "ApplicationComponent", "APPLICATION", a["properties"], ""))

        for name, el_type, category, vendor, version, lifecycle, support_end, radar, hosting, _apps in TECHNOLOGY:
            specs.append((name, el_type, "TECHNOLOGY", {
                "technologyCategory": category, "vendor": vendor, "version": version, "lifecycle": lifecycle,
                "vendorSupportEnd": support_end, "techRadar": radar, "hosting": hosting,
            }, ""))

        for name, _goals in DRIVERS:
            specs.append((name, "Driver", "MOTIVATION", {}, ""))
        for name, target_date in GOALS:
            specs.append((name, "Goal", "MOTIVATION", {"targetDate": target_date}, ""))
        for name, kpi, unit, baseline, current, target, direction, target_date, _goals, _caps in OUTCOMES:
            specs.append((name, "Outcome", "MOTIVATION", {
                "kpi": kpi, "unit": unit, "baseline": str(baseline), "current": str(current), "target": str(target),
                "direction": direction, "baselineDate": OUTCOME_BASELINE_DATE, "targetDate": target_date,
                "measuredAt": "2026-09-30",
            }, ""))

        for name, target_date in PLATEAUS:
            specs.append((name, "Plateau", "IMPLEMENTATION_MIGRATION", {"targetDate": target_date}, ""))
        for name, start, end, progress, budget, actual, rag, plateau, owner, _assoc in WORK_PACKAGES:
            specs.append((name, "WorkPackage", "IMPLEMENTATION_MIGRATION", {
                "startDate": start, "endDate": end, "progress": str(progress), "budgetEUR": str(budget),
                "actualCostEUR": str(actual), "rag": rag, "plateau": plateau, "owner": owner,
            }, ""))

        names = [s[0] for s in specs]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise SystemExit(f"Duplicate element names in dataset: {sorted(duplicates)}")

        operations = []
        for name, el_type, folder, props, doc in specs:
            params: Dict[str, Any] = {
                "type": el_type, "name": name, "folderId": folders[folder], "force": True,
                "properties": {k: v for k, v in props.items() if v != ""}, "source": SOURCE,
            }
            if doc:
                params["documentation"] = doc
            operations.append({"tool": "create-element", "params": params})
        results = self.c.bulk(operations, "Engineering demo: elements")
        for (name, *_rest), result in zip(specs, results):
            self.ids[name] = result["entityId"]
        print(f"  created {len(results)} elements")

    # -- relationships ------------------------------------------------------------------------
    def create_relationships(self) -> None:
        rels: List[Tuple[str, str, str]] = []  # (type, source name, target name)
        for l1, _owner, stage, children in CAPABILITIES:
            rels.append(("ServingRelationship", l1, stage))
            for name, *_ in children:
                rels.append(("CompositionRelationship", l1, name))
        for stage in VALUE_STREAM_STAGES:
            rels.append(("CompositionRelationship", VALUE_STREAM, stage))
        for first, second in zip(VALUE_STREAM_STAGES, VALUE_STREAM_STAGES[1:]):
            rels.append(("FlowRelationship", first, second))
        for name, _stage, _m, _a, _mb, caps, roles in PROCESSES:
            for cap in caps:
                rels.append(("RealizationRelationship", name, cap))
            for role in roles:
                rels.append(("AssignmentRelationship", role, name))
        for source, target in PROCESS_FLOW:
            rels.append(("TriggeringRelationship", source, target))
        for a in APPLICATIONS:
            for cap in a["caps"]:
                rels.append(("RealizationRelationship", a["name"], cap))
            for proc in a["procs"]:
                rels.append(("ServingRelationship", a["name"], proc))
        for name, *_rest, apps in TECHNOLOGY:
            for app_name in apps:
                rels.append(("ServingRelationship", name, app_name))
        for name, goals in DRIVERS:
            for goal in goals:
                rels.append(("InfluenceRelationship", name, goal))
        for name, *_rest, goals, caps in OUTCOMES:
            for goal in goals:
                rels.append(("RealizationRelationship", name, goal))
            for cap in caps:
                rels.append(("RealizationRelationship", cap, name))
        for first, second in zip(PLATEAUS, PLATEAUS[1:]):
            rels.append(("TriggeringRelationship", first[0], second[0]))
        for name, *_rest, plateau, _owner, assoc in WORK_PACKAGES:
            for target in assoc:
                rels.append(("AssociationRelationship", name, target))

        missing = {n for _t, s, d in rels for n in (s, d) if n not in self.ids}
        if missing:
            raise SystemExit(f"Relationships reference unknown elements: {sorted(missing)}")

        operations = []
        for rel_type, source, target in rels:
            params: Dict[str, Any] = {"type": rel_type, "sourceId": self.ids[source], "targetId": self.ids[target], "source": SOURCE}
            if rel_type == "AssociationRelationship":
                params["associationDirected"] = True
            operations.append({"tool": "create-relationship", "params": params})
        self.rel_count = len(self.c.bulk(operations, "Engineering demo: relationships"))
        print(f"  created {self.rel_count} relationships")

    # -- views --------------------------------------------------------------------------------
    def _view(self, name: str, viewpoint: str, folder_id: str, body: List[Dict[str, Any]]) -> str:
        # An empty viewpoint means "no viewpoint": Archi ghosts every element a viewpoint does not
        # allow, so mixed-layer views (value stream + processes + roles) must not set one.
        params = {"name": name, "folderId": folder_id, "connectionRouterType": "manhattan"}
        if viewpoint:
            params["viewpoint"] = viewpoint
        operations = [{"tool": "create-view", "as": "view", "params": params}] + body
        results = self.c.bulk(operations, f"Engineering demo: view {name}")
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
                         per_row: int = 4) -> List[Dict[str, Any]]:
        """Lay out containers in a grid; each container stacks its children vertically."""
        ops: List[Dict[str, Any]] = []
        child_h, gap, header, pad = 45, 10, 36, 14
        heights = [header + len(children) * (child_h + gap) + pad for _label, children in columns]
        y = 20
        for row_start in range(0, len(columns), per_row):
            row = columns[row_start : row_start + per_row]
            row_h = max(heights[row_start : row_start + per_row])
            for offset, (label, children) in enumerate(row):
                idx = row_start + offset
                x = 20 + offset * (col_w + 24)
                alias = f"c{idx}"
                if container == "element":
                    ops.append(self._place(alias, label, x, y, col_w, row_h))
                else:
                    ops.append(self._group(alias, label, x, y, col_w, row_h))
                for j, child in enumerate(children):
                    ops.append(self._place(f"c{idx}_{j}", child, pad, header + j * (child_h + gap), col_w - 2 * pad, child_h, parent=alias))
            y += row_h + 24
        return ops

    def create_views(self, folders: Dict[str, str]) -> None:
        views_folder = folders["DIAGRAMS"]
        created: Dict[str, str] = {}

        # Strategy: capability map, L2 nested in L1.
        columns = [(l1, [c[0] for c in children]) for l1, _o, _s, children in CAPABILITIES]
        created["cap"] = self._view(VIEW_NAMES[0], "capability", views_folder, self._stacked_columns(columns, "element"))

        # Business: value stream stages on top, processes per stage below, roles at the bottom.
        body: List[Dict[str, Any]] = []
        col_w, col_gap = 220, 40
        for i, stage in enumerate(VALUE_STREAM_STAGES):
            body.append(self._place(f"s{i}", stage, 20 + i * (col_w + col_gap), 20, col_w, 60))
        per_stage: Dict[str, int] = {}
        for p, (name, stage, *_rest) in enumerate(PROCESSES):
            col = VALUE_STREAM_STAGES.index(stage)
            row = per_stage.get(stage, 0)
            per_stage[stage] = row + 1
            body.append(self._place(f"p{p}", name, 20 + col * (col_w + col_gap), 140 + row * 90, col_w, 55))
        roles_top = 140 + max(per_stage.values()) * 90 + 60
        per_col: Dict[int, int] = {}
        for r, (role, (_hc, col)) in enumerate(ROLES.items()):
            row = per_col.get(col, 0)
            per_col[col] = row + 1
            body.append(self._place(f"r{r}", role, 20 + col * (col_w + col_gap), roles_top + row * 80, col_w, 50))
        created["bus"] = self._view(VIEW_NAMES[1], "", views_folder, body)
        self.c.call("auto-connect-view", {"viewId": created["bus"], "showLabel": False,
                                          "relationshipTypes": ["FlowRelationship", "TriggeringRelationship", "AssignmentRelationship"]})
        self.c.call("auto-route-connections", {"viewId": created["bus"]})

        # Application: landscape grouped by category.
        categories: Dict[str, List[str]] = {}
        for a in APPLICATIONS:
            categories.setdefault(a["category"], []).append(a["name"])
        created["app"] = self._view(VIEW_NAMES[2], "application_cooperation", views_folder,
                                    self._stacked_columns(list(categories.items()), "group"))

        # Technology: infrastructure landscape grouped by platform. The technology-serves-application
        # relationships stay in the model (the dashboard uses them) but are not drawn: with ~40 of
        # them across 26 applications any layout turns into a wall of crossing lines.
        grouped = [n for _label, names in TECH_GROUPS for n in names]
        if sorted(grouped) != sorted(t[0] for t in TECHNOLOGY):
            raise SystemExit("TECH_GROUPS must list every TECHNOLOGY element exactly once.")
        created["tech"] = self._view(VIEW_NAMES[3], "technology", views_folder,
                                     self._stacked_columns(TECH_GROUPS, "group", col_w=300, per_row=5))

        # Motivation: drivers -> goals -> outcomes <- capabilities.
        body = []
        goal_order = ["Reduce time-to-market by 25%", "Increase engineering efficiency through reuse",
                      "Ensure compliance by design", "Establish an end-to-end digital thread"]
        outcome_order = [o[0] for o in OUTCOMES]
        cap_order: List[str] = []
        for o in OUTCOMES:
            for cap in o[9]:
                if cap not in cap_order:
                    cap_order.append(cap)
        slot = 210
        width = len(outcome_order) * slot

        def spread(items: List[str], prefix: str, y: int) -> None:
            step = width / len(items)
            for i, name in enumerate(items):
                body.append(self._place(f"{prefix}{i}", name, int(20 + i * step + (step - 190) / 2), y, 190, 60))

        spread([d[0] for d in DRIVERS], "d", 20)
        spread(goal_order, "g", 170)
        spread(outcome_order, "o", 320)
        spread(cap_order, "k", 470)
        created["mot"] = self._view(VIEW_NAMES[4], "", views_folder, body)
        self.c.call("auto-connect-view", {"viewId": created["mot"], "showLabel": False,
                                          "relationshipTypes": ["InfluenceRelationship", "RealizationRelationship"]})
        self.c.call("auto-route-connections", {"viewId": created["mot"]})

        # Implementation & Migration: plateaus with their work packages.
        body = []
        for i, (plateau, _date) in enumerate(PLATEAUS):
            x = 20 + i * 340
            body.append(self._place(f"pl{i}", plateau, x, 20, 310, 60))
            packages = [w[0] for w in WORK_PACKAGES if w[7] == plateau]
            body.append(self._group(f"wg{i}", "Work packages", x, 110, 310, 40 + len(packages) * 65 + 10))
            for j, wp in enumerate(packages):
                body.append(self._place(f"wp{i}_{j}", wp, 14, 36 + j * 65, 282, 55, parent=f"wg{i}"))
        created["impl"] = self._view(VIEW_NAMES[5], "implementation_migration", views_folder, body)
        self.c.call("auto-connect-view", {"viewId": created["impl"], "showLabel": False, "relationshipTypes": ["TriggeringRelationship"]})
        self.c.call("auto-route-connections", {"viewId": created["impl"]})
        print(f"  created {len(created)} views")


def purge(client: SeedMcpClient) -> None:
    seeder = Seeder(client)
    folders = seeder.demo_folders(create=False)
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
        seeder.create_elements(folders)
        seeder.create_relationships()
        seeder.create_views(folders)
    except McpError as exc:
        print(f"Seeding failed: {exc}\nRun with --purge to remove the partial dataset.")
        return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
