"""Offline tests for the dashboard metrics (no Archi / MCP needed).

The fixture uses only the element types and relationship pairs declared in backend/app/meta_model.py.

Run:  docker run --rm -v "$PWD/backend:/app" -w /app archi-local-chatbot-backend python -m unittest discover -s tests
"""

from __future__ import annotations

import unittest
from datetime import date

from app import dashboard as d
from app import meta_model

TODAY = date(2026, 10, 1)


def el(id_, name, type_, layer, **props):
    return {"id": id_, "name": name, "type": type_, "layer": layer,
            "properties": [{"key": k, "value": v} for k, v in props.items()]}


def rel(src, tgt, type_):
    return {"id": f"{src}-{type_}-{tgt}", "type": type_, "sourceId": src, "targetId": tgt}


ELEMENTS = [
    # Strategy
    el("c11", "Mechanical Design", "Capability", "Strategy", maturity="4", targetMaturity="4",
       strategicImportance="high", capabilityDomain="Design"),
    el("c12", "Design Reuse", "Capability", "Strategy", maturity="2", targetMaturity="4",
       strategicImportance="High", capabilityDomain="Design"),
    el("c13", "Drawing", "Capability", "Strategy", maturity="3 - Defined", targetMaturity="3",
       strategicImportance="low", capabilityDomain="Design"),
    el("c2", "Loose capability", "Capability", "Strategy", maturity="1", targetMaturity="2", strategicImportance="medium"),
    # Business: one rated As-Is process, one unrated As-Is process, one To-Be process mapped to it
    el("p1", "Design Parts", "BusinessProcess", "Business", maturity="3", automationLevel="Partially automated",
       mediaBreaks="2", processPhase="Design", status="current"),
    el("p2", "Unrated process", "BusinessProcess", "Business"),
    el("p3", "Digital Design Workspace", "BusinessProcess", "Business", status="target"),
    # Application
    el("s1", "CAD Service", "ApplicationService", "Application"),
    el("s2", "Spreadsheet Service", "ApplicationService", "Application"),
    el("a1", "CAD New", "ApplicationComponent", "Application", lifecycle="Active", functionalFit="4", technicalFit="4",
       timeClassification="invest", businessCriticality="Mission critical"),
    el("a2", "CAD Old", "ApplicationComponent", "Application", lifecycle="phase out", endOfLife="2026-09-30",
       functionalFit="3", technicalFit="1", timeClassification="Migrate", businessCriticality="business-critical"),
    el("a3", "Excel tool", "ApplicationComponent", "Application", lifecycle="phase-out", endOfLife="10.2028",
       functionalFit="2", technicalFit="2", timeClassification="eliminate", businessCriticality="administrative"),
    el("a4", "Unused app", "ApplicationComponent", "Application", lifecycle="active"),
    # Motivation
    el("g1", "Faster", "Goal", "Motivation"),
    el("o1", "Shorter change lead time", "Outcome", "Motivation", kpi="ECR lead time", unit="days", baseline="38",
       current="27", target="12", direction="lower", baselineDate="2025-01-01", targetDate="2028-12-31",
       measuredAt="2026-09-30"),
    el("o2", "Reuse", "Outcome", "Motivation", baseline="20", current="20", target="50",
       baselineDate="2025-01-01", targetDate="2026-06-30"),
    # Implementation & Migration
    el("pl1", "Transition 2027", "Plateau", "Implementation & Migration", targetDate="2027-12-31"),
    el("w1", "Harmonize CAD", "WorkPackage", "Implementation & Migration", startDate="2026-01-01",
       endDate="2026-12-31", status="In progress"),
    el("w2", "Late project", "WorkPackage", "Implementation & Migration", startDate="2025-01-01",
       endDate="2026-06-30", status="in-progress"),
    el("w3", "Future project", "WorkPackage", "Implementation & Migration", startDate="2026-09-01",
       endDate="2028-06-30", status="planned"),
    el("gap1", "Parallel CAD tools", "Gap", "Implementation & Migration"),
    el("gap2", "Unplanned gap", "Gap", "Implementation & Migration"),
]

RELATIONSHIPS = [
    rel("p1", "c11", "RealizationRelationship"),
    rel("p1", "c12", "RealizationRelationship"),
    rel("s1", "p1", "ServingRelationship"),
    rel("s2", "p1", "ServingRelationship"),
    rel("a1", "s1", "RealizationRelationship"),
    rel("a2", "s1", "RealizationRelationship"),
    rel("a3", "s2", "RealizationRelationship"),
    rel("p3", "p2", "AssociationRelationship"),          # To-Be "realizes" As-Is (mapping)
    rel("o1", "g1", "RealizationRelationship"),
    rel("o2", "g1", "RealizationRelationship"),
    rel("c12", "o2", "RealizationRelationship"),
    rel("pl1", "c12", "RealizationRelationship"),
    rel("w1", "pl1", "RealizationRelationship"),
    rel("w2", "pl1", "RealizationRelationship"),
    rel("w3", "pl1", "RealizationRelationship"),
    rel("pl1", "gap1", "AssociationRelationship"),
    rel("gap1", "p1", "AssociationRelationship"),
    rel("gap2", "p2", "AssociationRelationship"),
]


def snapshot():
    return {"info": {"name": "Test", "elementCount": len(ELEMENTS)}, "elements": list(ELEMENTS),
            "relationships": list(RELATIONSHIPS)}


class ParsingTests(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(d._num("1.250.000"), 1250000)
        self.assertEqual(d._num("1,250,000"), 1250000)
        self.assertEqual(d._num("3,5"), 3.5)
        self.assertEqual(d._num("3 - Defined"), 3)
        self.assertEqual(d._num("45 %"), 45)
        self.assertIsNone(d._num("n/a"))

    def test_dates(self):
        self.assertEqual(d._date("2027-06-30"), date(2027, 6, 30))
        self.assertEqual(d._date("30.06.2027"), date(2027, 6, 30))
        self.assertEqual(d._date("2027-06"), date(2027, 6, 30))
        self.assertEqual(d._date("06/2027"), date(2027, 6, 30))
        self.assertEqual(d._date("2027"), date(2027, 12, 31))
        self.assertIsNone(d._date("soon"))

    def test_within_months_boundaries(self):
        self.assertFalse(d._within_months(TODAY, date(2026, 9, 30), 12))   # yesterday is past, not "within"
        self.assertTrue(d._within_months(TODAY, TODAY, 12))
        self.assertTrue(d._within_months(TODAY, date(2028, 10, 1), 24))
        self.assertFalse(d._within_months(TODAY, date(2028, 10, 2), 24))

    def test_fit_quadrant(self):
        self.assertEqual(d.fit_quadrant(4, 4), "invest")
        self.assertEqual(d.fit_quadrant(3, 1), "migrate")
        self.assertEqual(d.fit_quadrant(2, 3), "tolerate")
        self.assertEqual(d.fit_quadrant(2, 2), "eliminate")
        self.assertIsNone(d.fit_quadrant(None, 3))


class FixtureTests(unittest.TestCase):
    def test_fixture_follows_the_meta_model(self):
        types = {e["id"]: e["type"] for e in ELEMENTS}
        allowed = set(meta_model.allowed_element_types())
        self.assertTrue(set(types.values()) <= allowed, set(types.values()) - allowed)
        for r in RELATIONSHIPS:
            self.assertTrue(meta_model.is_declared_pair(types[r["sourceId"]], r["type"], types[r["targetId"]]),
                            (types[r["sourceId"]], r["type"], types[r["targetId"]]))


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = d.compute_dashboard(snapshot(), TODAY)

    def capability(self, name):
        return next(c for g in self.result["strategy"]["groups"] for c in g["capabilities"] if c["name"] == name)

    def test_capability_groups_and_gaps(self):
        groups = {g["name"]: g for g in self.result["strategy"]["groups"]}
        self.assertEqual({c["name"] for c in groups["Design"]["capabilities"]}, {"Mechanical Design", "Design Reuse", "Drawing"})
        self.assertEqual([c["name"] for c in groups["Ungrouped capabilities"]["capabilities"]], ["Loose capability"])
        reuse = self.capability("Design Reuse")
        self.assertEqual((reuse["gap"], reuse["gapBand"], reuse["priorityScore"]), (2, "two-plus", 6))
        self.assertTrue(reuse["isPriorityGap"])
        self.assertEqual(reuse["processes"], ["Design Parts"])
        # process <- serving service <- realizing application (the meta-model chain)
        self.assertEqual([a["name"] for a in reuse["applications"]], ["CAD New", "CAD Old", "Excel tool"])
        self.assertEqual(reuse["appRisk"], "critical")  # CAD Old is past end of life
        self.assertEqual(self.capability("Mechanical Design")["gapBand"], "at-target")
        self.assertEqual(self.capability("Loose capability")["gapBand"], "one-level")

    def test_roadmap_status_per_capability(self):
        reuse = self.capability("Design Reuse")
        self.assertEqual([p["name"] for p in reuse["plateaus"]], ["Transition 2027"])
        self.assertEqual(reuse["roadmapStatus"], "in-delivery")
        self.assertEqual(self.capability("Loose capability")["roadmapStatus"], "unplanned")
        self.assertEqual(self.capability("Mechanical Design")["roadmapStatus"], "at-target")
        kpis = self.result["roadmapCoverage"]["kpis"]
        self.assertEqual((kpis["priorityGaps"], kpis["priorityGapsPlanned"], kpis["unplannedGaps"]), (1, 1, 1))

    def test_business_as_is_to_be(self):
        kpis = self.result["business"]["kpis"]
        self.assertEqual((kpis["processCount"], kpis["asIsCount"], kpis["toBeCount"]), (3, 2, 1))
        self.assertEqual((kpis["toBeTraced"], kpis["asIsCovered"], kpis["processesWithGaps"]), (1, 1, 2))
        self.assertEqual((kpis["ratedCount"], kpis["mediaBreaks"]), (1, 2))
        design = next(p for p in self.result["business"]["processes"] if p["name"] == "Design Parts")
        self.assertEqual(design["services"], ["CAD Service", "Spreadsheet Service"])
        self.assertEqual(design["automation"], "partial")

    def test_application_risk_and_use(self):
        apps = {a["name"]: a for a in self.result["application"]["applications"]}
        self.assertEqual(apps["CAD Old"]["risk"]["level"], "critical")
        self.assertEqual(apps["Excel tool"]["risk"]["level"], "warning")  # TIME eliminate; EOL Oct 2028 > 24 months
        self.assertEqual(apps["Excel tool"]["endOfLife"], "2028-10-31")
        self.assertEqual(apps["CAD Old"]["fitQuadrant"], "migrate")
        self.assertEqual(apps["CAD New"]["processes"], ["Design Parts"])
        kpis = self.result["application"]["kpis"]
        self.assertEqual((kpis["pastEndOfLife"], kpis["endOfLifeWithin24Months"]), (1, 0))
        self.assertEqual(kpis["withoutBusinessUse"], ["Unused app"])

    def test_outcome_progress_lower_is_better(self):
        outcomes = {o["name"]: o for o in self.result["motivation"]["outcomes"]}
        lead_time = outcomes["Shorter change lead time"]
        self.assertAlmostEqual(lead_time["progress"], round((27 - 38) / (12 - 38), 3))
        self.assertEqual(lead_time["status"], "on-track")  # 0.423 vs expected 0.437
        self.assertEqual(outcomes["Reuse"]["status"], "missed")
        self.assertEqual(outcomes["Reuse"]["capabilities"], ["Design Reuse"])
        self.assertEqual(self.result["motivation"]["goals"][0]["progress"], round((0.423 + 0) / 2, 2))

    def test_work_packages_and_gaps(self):
        wps = {w["name"]: w for w in self.result["implementation"]["workPackages"]}
        self.assertTrue(wps["Late project"]["overdue"])
        self.assertFalse(wps["Harmonize CAD"]["overdue"])
        self.assertTrue(wps["Future project"]["lateStart"])
        self.assertTrue(wps["Future project"]["endsAfterPlateau"])
        self.assertEqual(wps["Harmonize CAD"]["plateaus"], ["Transition 2027"])
        kpis = self.result["implementation"]["kpis"]
        self.assertEqual((kpis["in_progress"], kpis["planned"], kpis["completed"]), (2, 1, 0))
        self.assertEqual((kpis["overdue"], kpis["lateStart"], kpis["endsAfterPlateau"]), (1, 1, 1))
        self.assertEqual((kpis["gapCount"], kpis["gapsWithoutPlateau"]), (2, 1))
        gaps = {g["name"]: g for g in self.result["implementation"]["gaps"]}
        self.assertEqual(gaps["Parallel CAD tools"]["processes"], ["Design Parts"])
        self.assertEqual(self.result["implementation"]["plateaus"][0]["gaps"], ["Parallel CAD tools"])

    def test_data_quality(self):
        quality = {r["elementType"]: r for r in self.result["dataQuality"]}
        self.assertEqual((quality["BusinessProcess"]["complete"], quality["BusinessProcess"]["total"]), (1, 3))
        self.assertEqual((quality["Capability"]["complete"], quality["Capability"]["total"]), (4, 4))
        self.assertEqual((quality["WorkPackage"]["complete"], quality["WorkPackage"]["total"]), (3, 3))

    def test_composition_hierarchy_still_groups_and_cycles_terminate(self):
        snap = snapshot()
        snap["elements"] += [el("x0", "Parent", "Capability", "Strategy"),
                             el("x1", "Child", "Capability", "Strategy", maturity="1", targetMaturity="2"),
                             el("y1", "Cycle A", "Capability", "Strategy", maturity="1"),
                             el("y2", "Cycle B", "Capability", "Strategy", maturity="2")]
        snap["relationships"] += [rel("x0", "x1", "CompositionRelationship"),
                                  rel("y1", "y2", "CompositionRelationship"), rel("y2", "y1", "CompositionRelationship")]
        groups = {g["name"]: [c["name"] for c in g["capabilities"]] for g in d.compute_dashboard(snap, TODAY)["strategy"]["groups"]}
        self.assertEqual(groups["Parent"], ["Child"])
        self.assertIn("Cycle A", [n for names in groups.values() for n in names])


if __name__ == "__main__":
    unittest.main()
