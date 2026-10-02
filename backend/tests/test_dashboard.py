"""Offline tests for the dashboard metrics (no Archi / MCP needed).

Run:  docker run --rm -v "$PWD/backend:/app" -w /app archi-local-chatbot-backend python -m unittest discover -s tests
"""

from __future__ import annotations

import unittest
from datetime import date

from app import dashboard as d

TODAY = date(2026, 10, 1)


def el(id_, name, type_, layer, **props):
    return {"id": id_, "name": name, "type": type_, "layer": layer,
            "properties": [{"key": k, "value": v} for k, v in props.items()]}


def rel(src, tgt, type_):
    return {"id": f"{src}-{type_}-{tgt}", "type": type_, "sourceId": src, "targetId": tgt}


def snapshot():
    elements = [
        # Strategy: L1 with two L2 via composition, plus one L2 linked only by property.
        el("c1", "Design", "Capability", "Strategy", capabilityLevel="L1"),
        el("c11", "Mechanical Design", "Capability", "Strategy", maturity="4", targetMaturity="4", strategicImportance="high"),
        el("c12", "Design Reuse", "Capability", "Strategy", maturity="2", targetMaturity="4", strategicImportance="High"),
        el("c13", "Drawing", "Capability", "Strategy", maturity="3 - Defined", targetMaturity="3",
           strategicImportance="low", parentCapability="design"),
        el("c2", "Loose capability", "Capability", "Strategy", maturity="1", targetMaturity="2", strategicImportance="medium"),
        # Business
        el("p1", "Design Parts", "BusinessProcess", "Business", maturity="3", automationLevel="Partially automated",
           mediaBreaks="2", valueStreamStage="Design"),
        el("p2", "Unrated process", "BusinessProcess", "Business"),
        # Application
        el("a1", "CAD New", "ApplicationComponent", "Application", lifecycle="Active", functionalFit="4", technicalFit="4",
           timeClassification="invest", businessCriticality="Mission critical", annualCostEUR="1.000.000"),
        el("a2", "CAD Old", "ApplicationComponent", "Application", lifecycle="phase out", endOfLife="2026-09-30",
           functionalFit="3", technicalFit="1", timeClassification="Migrate", businessCriticality="business-critical",
           annualCostEUR="250,000"),
        el("a3", "Excel tool", "ApplicationComponent", "Application", lifecycle="phase-out", endOfLife="10.2028",
           functionalFit="2", technicalFit="2", timeClassification="eliminate", businessCriticality="administrative"),
        # Technology
        el("t1", "Old OS", "SystemSoftware", "Technology", lifecycle="end-of-life", vendorSupportEnd="2023-10-10", techRadar="hold"),
        el("t2", "New OS", "SystemSoftware", "Technology", vendorSupportEnd="2027-09-30", techRadar="adopt"),
        el("t3", "Cloud", "Node", "Technology"),
        # Motivation
        el("g1", "Faster", "Goal", "Motivation"),
        el("o1", "Shorter change lead time", "Outcome", "Motivation", kpi="ECR lead time", unit="days", baseline="38",
           current="27", target="12", direction="lower", baselineDate="2025-01-01", targetDate="2028-12-31",
           measuredAt="2026-09-30"),
        el("o2", "Reuse", "Outcome", "Motivation", baseline="20", current="20", target="50",
           baselineDate="2025-01-01", targetDate="2026-06-30"),
        # Implementation & Migration
        el("pl1", "Transition 2027", "Plateau", "Implementation & Migration", targetDate="2027-12-31"),
        el("w1", "Harmonize CAD", "WorkPackage", "Implementation & Migration", startDate="2026-01-01", endDate="2026-12-31",
           progress="50", budgetEUR="1000", actualCostEUR="800", rag="amber", plateau="Transition 2027"),
        el("w2", "Late project", "WorkPackage", "Implementation & Migration", startDate="2025-01-01", endDate="2026-06-30",
           progress="90", budgetEUR="100", actualCostEUR="120", rag="Red"),
    ]
    relationships = [
        rel("c1", "c11", "CompositionRelationship"),
        rel("c1", "c12", "CompositionRelationship"),
        rel("a1", "c11", "RealizationRelationship"),
        rel("a2", "c11", "RealizationRelationship"),
        rel("p1", "c12", "RealizationRelationship"),
        rel("p1", "c11", "RealizationRelationship"),  # c11 also has direct apps -> process apps must not count
        rel("a3", "p1", "ServingRelationship"),
        rel("t1", "a2", "ServingRelationship"),
        rel("t2", "a1", "ServingRelationship"),
        rel("o1", "g1", "RealizationRelationship"),
        rel("o2", "g1", "RealizationRelationship"),
        rel("c12", "o2", "RealizationRelationship"),
        rel("w1", "c12", "AssociationRelationship"),
        rel("w1", "a2", "AssociationRelationship"),
    ]
    return {"info": {"name": "Test", "elementCount": len(elements)}, "elements": elements, "relationships": relationships}


class ParsingTests(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(d._num("1.250.000"), 1250000)
        self.assertEqual(d._num("1,250,000"), 1250000)
        self.assertEqual(d._num("3,5"), 3.5)
        self.assertEqual(d._num("3 - Defined"), 3)
        self.assertEqual(d._num("45 %"), 45)
        self.assertEqual(d._num("€ 90000"), 90000)
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


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = d.compute_dashboard(snapshot(), TODAY)

    def test_capability_hierarchy_and_gaps(self):
        groups = {g["name"]: g for g in self.result["strategy"]["groups"]}
        self.assertEqual({c["name"] for c in groups["Design"]["capabilities"]}, {"Mechanical Design", "Design Reuse", "Drawing"})
        self.assertIn("Ungrouped capabilities", groups)
        reuse = next(c for c in groups["Design"]["capabilities"] if c["name"] == "Design Reuse")
        self.assertEqual(reuse["gap"], 2)
        self.assertTrue(reuse["isPriorityGap"])
        self.assertEqual(reuse["priorityScore"], 6)
        self.assertEqual(reuse["workPackages"], ["Harmonize CAD"])
        self.assertEqual(reuse["investmentEUR"], 1000)  # w1 budget, only one capability target
        self.assertEqual([a["name"] for a in reuse["applications"]], ["Excel tool"])  # via the process it serves
        self.assertEqual(reuse["gapBand"], "two-plus")
        mech = next(c for c in groups["Design"]["capabilities"] if c["name"] == "Mechanical Design")
        self.assertEqual([a["name"] for a in mech["applications"]], ["CAD New", "CAD Old"])  # direct only
        self.assertEqual(mech["gapBand"], "at-target")
        loose = groups["Ungrouped capabilities"]["capabilities"][0]
        self.assertEqual(loose["gapBand"], "one-level")
        kpis = self.result["strategy"]["kpis"]
        self.assertEqual(kpis["capabilityCount"], 4)
        self.assertEqual(kpis["priorityGapCount"], 1)

    def test_application_risk_and_end_of_life(self):
        apps = {a["name"]: a for a in self.result["application"]["applications"]}
        self.assertEqual(apps["CAD Old"]["risk"]["level"], "critical")  # past EOL and on out-of-support OS
        self.assertEqual(apps["CAD New"]["risk"]["level"], "serious")   # its OS support ends within 12 months
        self.assertEqual(apps["Excel tool"]["endOfLife"], "2028-10-31")
        self.assertEqual(apps["CAD Old"]["fitQuadrant"], "migrate")
        kpis = self.result["application"]["kpis"]
        self.assertEqual(kpis["pastEndOfLife"], 1)
        self.assertEqual(kpis["endOfLifeWithin24Months"], 0)  # Oct 31 2028 is beyond Oct 1 2028
        self.assertEqual(kpis["annualCostEUR"], 1250000)
        self.assertEqual(kpis["migrateEliminateCostEUR"], 250000)
        self.assertEqual(self.result["application"]["redundancy"][0]["name"], "Mechanical Design")

    def test_technology_support(self):
        kpis = self.result["technology"]["kpis"]
        self.assertEqual(kpis["withSupportDate"], 2)
        self.assertEqual(kpis["outOfSupport"], 1)
        self.assertEqual(kpis["outOfSupportShare"], 0.5)
        self.assertEqual(kpis["criticalApplicationsOnUnsupported"], ["CAD Old"])
        statuses = {c["name"]: c["supportStatus"] for c in self.result["technology"]["components"]}
        self.assertEqual(statuses, {"Old OS": "out", "New OS": "expiring-12", "Cloud": "unknown"})

    def test_outcome_progress_lower_is_better(self):
        outcomes = {o["name"]: o for o in self.result["motivation"]["outcomes"]}
        lead_time = outcomes["Shorter change lead time"]
        self.assertAlmostEqual(lead_time["progress"], round((27 - 38) / (12 - 38), 3))
        # 638 of 1460 days elapsed -> expected 0.437; progress 0.423 is within 0.1 -> on track
        self.assertEqual(lead_time["status"], "on-track")
        self.assertEqual(outcomes["Reuse"]["status"], "missed")  # target date passed without progress
        self.assertEqual(self.result["motivation"]["goals"][0]["progress"], round((0.423 + 0) / 2, 2))

    def test_work_package_performance(self):
        wps = {w["name"]: w for w in self.result["implementation"]["workPackages"]}
        cad = wps["Harmonize CAD"]
        self.assertAlmostEqual(cad["plannedProgress"], round(273 / 364, 3))
        self.assertAlmostEqual(cad["cpi"], round(500 / 800, 2))
        self.assertEqual(cad["computedHealth"], "red")  # CPI 0.63 < 0.8
        self.assertEqual(cad["capabilities"], ["Design Reuse"])
        self.assertEqual(cad["changes"], ["CAD Old"])
        late = wps["Late project"]
        self.assertTrue(late["overdue"])
        self.assertEqual(late["rag"], "red")
        kpis = self.result["implementation"]["kpis"]
        self.assertEqual(kpis["overdue"], 1)
        self.assertEqual(kpis["earnedValueEUR"], 590)
        self.assertEqual(kpis["budgetEUR"], 1100)

    def test_business_and_data_quality(self):
        kpis = self.result["business"]["kpis"]
        self.assertEqual((kpis["processCount"], kpis["ratedCount"], kpis["mediaBreaks"]), (2, 1, 2))
        stage = self.result["business"]["stages"][0]
        self.assertEqual(stage["automation"]["partial"], 1)
        quality = {r["elementType"]: r for r in self.result["dataQuality"]}
        self.assertEqual((quality["BusinessProcess"]["complete"], quality["BusinessProcess"]["total"]), (1, 2))
        self.assertEqual(quality["Capability"]["total"], 4)  # leaves only

    def test_capability_cycle_does_not_hang(self):
        snap = snapshot()
        snap["elements"] += [el("x1", "Cycle A", "Capability", "Strategy", maturity="1", targetMaturity="2"),
                             el("x2", "Cycle B", "Capability", "Strategy", maturity="2", targetMaturity="2")]
        snap["relationships"] += [rel("x1", "x2", "CompositionRelationship"), rel("x2", "x1", "CompositionRelationship")]
        result = d.compute_dashboard(snap, TODAY)
        names = {c["name"] for g in result["strategy"]["groups"] for c in g["capabilities"]}
        self.assertTrue({"Cycle A", "Cycle B"} & names)

    def test_cross_layer_gap_coverage(self):
        kpis = self.result["crossLayer"]["kpis"]
        self.assertEqual(kpis["priorityGaps"], 1)
        self.assertEqual(kpis["priorityGapsAddressed"], 1)
        self.assertEqual(self.result["crossLayer"]["gapCoverage"][0]["name"], "Design Reuse")


if __name__ == "__main__":
    unittest.main()
