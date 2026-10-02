"""Offline tests for the Ratings & Roadmap step (backend/app/steering.py) and the meta-model property schema."""

from __future__ import annotations

import copy
import unittest
from datetime import date

from app import meta_model
from app import steering as st


def el(id_, name, type_, **props):
    return {"id": id_, "name": name, "type": type_, "layer": "",
            "properties": [{"key": k, "value": v} for k, v in props.items()]}


def rel(id_, src, tgt, type_):
    return {"id": id_, "type": type_, "sourceId": src, "targetId": tgt}


def snapshot():
    return {
        "info": {"name": "Test"},
        "elements": [
            el("c1", "Change Management", "Capability", maturity="2", targetMaturity="4", strategicImportance="high"),
            el("c2", "Legacy Capability", "Capability", maturity="2", owner="IT", targetDate="31.12.2027"),
            el("p1", "Process Change Request", "BusinessProcess"),
            el("p2", "Digital Change Workflow", "BusinessProcess", status="target"),
            el("p3", "Other Process", "BusinessProcess", maturity="3"),
            el("pl1", "Transition 2027", "Plateau", targetDate="2027-12-31"),
            el("pl2", "Target 2028", "Plateau", targetDate="2028-12-31"),
            el("w1", "PLM Consolidation", "WorkPackage", startDate="2026-01-01", endDate="2027-06-30", status="In progress"),
            el("g1", "Parallel PLM systems", "Gap"),
            el("g2", "Manual BOM transfer", "Gap", criticality="medium"),
            el("goal1", "Faster time to market", "Goal"),
            el("o1", "Shorter change lead time", "Outcome", kpi="ECR lead time", baseline="38", target="12"),
            el("a1", "Teamcenter", "ApplicationComponent", lifecycle="Phase Out"),
        ],
        "relationships": [
            rel("r1", "p1", "c1", "RealizationRelationship"),
            rel("r2", "pl1", "c1", "RealizationRelationship"),
            rel("r3", "w1", "pl1", "RealizationRelationship"),
            rel("r4", "g1", "pl1", "AssociationRelationship"),   # stored gap -> plateau: associations are undirected
            rel("r5", "g1", "p1", "AssociationRelationship"),
            rel("r6", "o1", "goal1", "RealizationRelationship"),
            rel("r7", "p2", "p1", "AssociationRelationship"),
        ],
    }


def state():
    return st.current_state(snapshot(), as_is_process_ids=["p1"], to_be_process_ids=["p2"])


def row(state_, table, key):
    return next(r for r in state_[table] if r["key"] == key)


class SchemaTests(unittest.TestCase):
    def test_core_properties_and_normalisation(self):
        self.assertEqual(meta_model.core_property_keys("Capability"), ["maturity", "targetMaturity", "strategicImportance"])
        self.assertEqual(meta_model.normalize_property_value("WorkPackage", "status", "In progress"), ("in-progress", None))
        self.assertEqual(meta_model.normalize_property_value("Capability", "maturity", "3"), ("3", None))
        self.assertIsNotNone(meta_model.normalize_property_value("Capability", "maturity", "7")[1])
        self.assertIsNotNone(meta_model.normalize_property_value("Plateau", "targetDate", "31.12.2027")[1])
        self.assertIsNotNone(meta_model.normalize_property_value("Capability", "unknownKey", "x")[1])

    def test_every_managed_link_is_declared(self):
        for table, _field, rel_type, side, target_table in st.MANAGED_LINKS:
            row_type, other_type = st.SECTIONS[table], st.SECTIONS[target_table]
            source, target = (row_type, other_type) if side == "row" else (other_type, row_type)
            self.assertTrue(meta_model.is_declared_pair(source, rel_type, target), (source, rel_type, target))


class CurrentStateTests(unittest.TestCase):
    def test_tables_from_the_model(self):
        s = state()
        cap = row(s, "capabilities", "c1")
        self.assertEqual((cap["maturity"], cap["processes"], cap["plateau"]), ("2", ["p1"], "pl1"))
        self.assertEqual([r["key"] for r in s["processes"]], ["p1", "p2"])  # scoped to the assessment views
        self.assertEqual(row(s, "processes", "p1")["status"], "current")
        self.assertEqual(row(s, "processes", "p1")["derivedFields"], ["status"])
        self.assertEqual(row(s, "processes", "p2")["status"], "target")
        self.assertEqual(row(s, "gaps", "g1")["plateau"], "pl1")
        self.assertEqual(row(s, "gaps", "g1")["processes"], ["Process Change Request"])
        self.assertEqual(row(s, "workPackages", "w1")["status"], "in-progress")
        self.assertEqual(row(s, "outcomes", "o1")["goal"], "goal1")
        self.assertEqual(row(s, "applications", "a1")["lifecycle"], "phase-out")
        self.assertEqual([r["key"] for r in s["plateaus"]], ["pl1", "pl2"])  # by target date


class MergeTests(unittest.TestCase):
    def test_ai_fills_gaps_but_never_overwrites(self):
        s = st.merge_proposal(state(), {
            "plateaus": [{"name": "Quick Wins 2026", "targetDate": "2026-12-31"}],
            "capabilities": [
                {"ref": "c1", "name": "Change Management", "maturity": 1, "domain": "PLM", "plateau": "Target 2028"},
                {"name": "Release Management", "maturity": 2, "targetMaturity": 4, "strategicImportance": "High",
                 "processIds": ["p1", "unknown"], "plateau": "Quick Wins 2026", "rationale": "Release steps are manual"},
            ],
            "processRatings": [{"id": "p1", "maturity": 2, "automationLevel": "Manual", "mediaBreaks": 4, "phase": "Change"}],
            "workPackages": [{"name": "Digital ECR Workflow", "startDate": "2026-01-01", "endDate": "2026-12-31", "plateau": "Quick Wins 2026"}],
            "gapAssignments": [{"id": "g2", "plateau": "Target 2028", "criticality": "high"}],
            "goals": [{"name": "Faster time to market"}],
            "outcomes": [{"name": "Fewer manual handovers", "kpi": "Media breaks", "target": 10, "direction": "lower",
                          "goal": "Faster time to market", "capability": "Release Management"}],
        })
        c1 = row(s, "capabilities", "c1")
        self.assertEqual(c1["maturity"], "2")              # existing value kept
        self.assertEqual(c1["capabilityDomain"], "PLM")    # empty field filled
        self.assertEqual(c1["plateau"], "pl1")             # existing link kept
        self.assertEqual(c1["aiFields"], ["capabilityDomain"])
        new_cap = next(r for r in s["capabilities"] if r["origin"] == "ai")
        new_plateau = next(r for r in s["plateaus"] if r["origin"] == "ai")
        self.assertEqual((new_cap["name"], new_cap["strategicImportance"], new_cap["processes"]), ("Release Management", "high", ["p1"]))
        self.assertEqual(new_cap["plateau"], new_plateau["key"])
        p1 = row(s, "processes", "p1")
        self.assertEqual((p1["maturity"], p1["automationLevel"], p1["mediaBreaks"], p1["processPhase"]), ("2", "manual", "4", "Change"))
        wp = next(r for r in s["workPackages"] if r["origin"] == "ai")
        self.assertEqual((wp["status"], wp["plateau"]), ("planned", new_plateau["key"]))
        g2 = row(s, "gaps", "g2")
        self.assertEqual((g2["plateau"], g2["criticality"]), ("pl2", "medium"))  # criticality kept, plateau filled
        self.assertEqual(len(s["goals"]), 1)                                     # matched by name, not duplicated
        outcome = next(r for r in s["outcomes"] if r["origin"] == "ai")
        self.assertEqual((outcome["goal"], outcome["capability"]), ("goal1", new_cap["key"]))

    def test_invalid_ai_values_are_dropped_with_a_warning(self):
        s = st.merge_proposal(state(), {"processRatings": [{"id": "p1", "maturity": 9, "automationLevel": "robotic"}]})
        self.assertIsNone(row(s, "processes", "p1")["maturity"])
        self.assertEqual(len(s["warnings"]), 2)


class PlanTests(unittest.TestCase):
    def test_untouched_tables_change_only_derived_status(self):
        plan = st.plan_changes(snapshot(), state())
        self.assertEqual(plan["errors"], [])
        self.assertEqual(plan["create"], [])
        ops = plan["independent"]
        # p1 gets status=current from its assessment view; p2 already has status=target.
        self.assertEqual(ops, [{"tool": "update-element", "params": {"id": "p1", "properties": {"status": "current"}}}])

    def test_new_elements_links_and_managed_link_moves(self):
        s = state()
        row(s, "capabilities", "c1")["maturity"] = "3"
        row(s, "capabilities", "c2")["owner"] = ""                       # clearing a value removes the property
        new_plateau = st._new_row(s, "plateaus", "Quick Wins 2026")
        new_plateau["targetDate"] = "2026-12-31"
        new_cap = st._new_row(s, "capabilities", "Release Management")
        new_cap.update({"maturity": "2", "targetMaturity": "4", "processes": ["p1"], "plateau": new_plateau["key"]})
        row(s, "gaps", "g1")["plateau"] = new_plateau["key"]           # move the gap to the new plateau
        plan = st.plan_changes(snapshot(), s)
        self.assertEqual(plan["errors"], [])
        self.assertEqual([op["params"]["name"] for op in plan["create"]], ["Release Management", "Quick Wins 2026"])
        updates = {op["params"]["id"]: op["params"].get("properties") for op in plan["independent"] if op["tool"] == "update-element"}
        self.assertEqual(updates["c1"], {"maturity": "3"})
        self.assertEqual(updates["c2"], {"owner": None})
        deletes = [op["params"]["relationshipId"] for op in plan["independent"] if op["tool"] == "delete-relationship"]
        self.assertEqual(deletes, ["r4"])                                # old gap <-> plateau association
        linked = [(op["params"]["type"], op["params"]["sourceId"], op["params"]["targetId"]) for op in plan["linked"]]
        cap_ref, plateau_ref = f"${st._alias(new_cap['key'])}.id", f"${st._alias(new_plateau['key'])}.id"
        self.assertIn(("RealizationRelationship", "p1", cap_ref), linked)
        self.assertIn(("RealizationRelationship", plateau_ref, cap_ref), linked)
        self.assertIn(("AssociationRelationship", plateau_ref, "g1"), linked)

    def test_legacy_values_do_not_block_but_changed_invalid_values_do(self):
        s = state()
        self.assertEqual(st.plan_changes(snapshot(), s)["errors"], [])   # untouched odd values are fine
        row(s, "plateaus", "pl2")["targetDate"] = "31.12.2028"
        errors = st.plan_changes(snapshot(), s)["errors"]
        self.assertEqual(len(errors), 1)
        self.assertIn("YYYY-MM-DD", errors[0])

    def test_excluded_new_rows_are_not_created(self):
        s = state()
        new_cap = st._new_row(s, "capabilities", "Not wanted")
        new_cap["include"] = False
        self.assertEqual(st.plan_changes(snapshot(), s)["create"], [])

    def test_blanks_around_a_model_name_are_not_a_rename(self):
        snap = snapshot()
        next(e for e in snap["elements"] if e["id"] == "a1")["name"] = "Teamcenter "
        s = st.current_state(snap, as_is_process_ids=["p1"], to_be_process_ids=["p2"])
        self.assertNotIn("a1", [op["params"]["id"] for op in st.plan_changes(snap, s)["independent"]])
        row(s, "applications", "a1")["name"] = "Teamcenter PLM"
        renames = [op["params"] for op in st.plan_changes(snap, s)["independent"] if op["params"]["id"] == "a1"]
        self.assertEqual(renames, [{"id": "a1", "name": "Teamcenter PLM"}])

    def test_malformed_tables_are_reported_not_crashing(self):
        s = state()
        s["goals"] = 5                                                     # not a list
        s["plateaus"].append({"name": "No key", "targetDate": "2029-12-31"})
        row(s, "capabilities", "c1").update({"processes": "p1", "plateau": ["pl2"]})  # wrong value types
        errors = st.plan_changes(snapshot(), s)["errors"]
        self.assertEqual(len(errors), 3, errors)
        self.assertTrue(any(e.startswith("goals: expected a list") for e in errors))
        self.assertTrue(any("row without a key" in e for e in errors))
        self.assertTrue(any("plateau refers to an element" in e for e in errors))

    def test_untouched_link_to_an_excluded_plateau_does_not_block(self):
        s = state()
        row(s, "plateaus", "pl1")["include"] = False
        self.assertEqual(st.plan_changes(snapshot(), s)["errors"], [])

    def test_prompt_lists_keys_and_schema(self):
        prompt = st.proposal_prompt(state(), mappings=[("p2", "p1")], today=date(2026, 10, 2))
        self.assertIn("c1 | Change Management", prompt)
        self.assertIn("p2 -> p1", prompt)
        self.assertIn("WorkPackage.status: one of planned|in-progress|completed|on-hold", prompt)


def set_props(snap, element_id, **props):
    element = next(e for e in snap["elements"] if e["id"] == element_id)
    values = {p["key"]: p["value"] for p in element["properties"]} | props
    element["properties"] = [{"key": k, "value": v} for k, v in values.items()]
    return snap


class StaleTableTests(unittest.TestCase):
    """The table can be older than the model (kept in the browser session, or still shown while a
    proposal waits for approval in Archi): only what was edited in the table may be written."""

    def newer_model(self):
        snap = set_props(snapshot(), "c1", maturity="3")                          # re-rated in Archi
        next(r for r in snap["relationships"] if r["id"] == "r2")["sourceId"] = "pl2"  # moved to 2028 in Archi
        return snap

    def test_untouched_rows_do_not_revert_changes_made_in_archi(self):
        plan = st.plan_changes(self.newer_model(), state())
        self.assertEqual(plan["errors"], [])
        self.assertEqual(plan["independent"], [{"tool": "update-element", "params": {"id": "p1", "properties": {"status": "current"}}}])

    def test_editing_a_value_changed_in_archi_is_reported(self):
        s = state()
        row(s, "capabilities", "c1").update({"maturity": "4", "plateau": None})
        errors = st.plan_changes(self.newer_model(), s)["errors"]
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("changed in Archi" in e for e in errors), errors)

    def test_the_same_change_on_both_sides_is_no_conflict(self):
        s = state()
        row(s, "capabilities", "c1")["maturity"] = "3"
        plan = st.plan_changes(self.newer_model(), s)
        self.assertEqual(plan["errors"], [])
        self.assertNotIn("c1", [op["params"]["id"] for op in plan["independent"]])

    def test_new_rows_never_duplicate_an_element(self):
        s = state()
        st._new_row(s, "capabilities", "change management")   # the model already has "Change Management"
        st._new_row(s, "plateaus", "Quick Wins 2026")
        st._new_row(s, "plateaus", "Quick wins 2026")         # twice in the same table
        errors = st.plan_changes(snapshot(), s)["errors"]
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("already a" in e for e in errors), errors)

    def test_tables_saved_without_load_values_still_apply(self):
        s = state()
        for table in st.SECTIONS:
            for r in s[table]:
                r.pop("loaded")
        row(s, "capabilities", "c1")["maturity"] = "4"
        plan = st.plan_changes(snapshot(), s)
        updates = {op["params"]["id"]: op["params"].get("properties") for op in plan["independent"]}
        self.assertEqual((plan["errors"], updates["c1"]), ([], {"maturity": "4"}))


if __name__ == "__main__":
    unittest.main()
