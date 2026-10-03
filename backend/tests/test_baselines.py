"""Offline tests for assessment baselines (backend/app/baselines.py): snapshot format, the git store,
trend figures, the comparison of two cycles and the list of model changes. Needs git."""

from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

from app import baselines as b
from test_dashboard import TODAY, snapshot as dashboard_fixture


def model_v1():
    snap = dashboard_fixture()
    snap["info"] = {**snap.get("info", {}), "name": "Engineering Test Model"}
    return b.normalize_snapshot(snap)


def element(snap, name):
    return next(e for e in snap["elements"] if e["name"] == name)


def set_prop(item, key, value):
    props = [p for p in item.get("properties", []) if p["key"] != key]
    if value is not None:
        props.append({"key": key, "value": value})
    item["properties"] = props


def model_v2():
    """The re-assessment: one capability matured, a work package finished, a gap closed, an outcome moved."""
    snap = copy.deepcopy(model_v1())
    cap = next(e for e in snap["elements"] if e["type"] == "Capability" and any(p["key"] == "maturity" for p in e.get("properties", [])))
    level = int(next(p["value"] for p in cap["properties"] if p["key"] == "maturity"))
    set_prop(cap, "maturity", str(level + 1))
    wp = next(e for e in snap["elements"] if e["type"] == "WorkPackage"
              and next((p["value"] for p in e.get("properties", []) if p["key"] == "status"), "") != "completed")
    set_prop(wp, "status", "completed")
    gap = next(e for e in snap["elements"] if e["type"] == "Gap")
    snap["elements"] = [e for e in snap["elements"] if e["id"] != gap["id"]]
    snap["relationships"] = [r for r in snap["relationships"] if gap["id"] not in (r["sourceId"], r["targetId"])]
    outcome = next(e for e in snap["elements"] if e["type"] == "Outcome" and any(p["key"] == "current" for p in e.get("properties", [])))
    current = float(next(p["value"] for p in outcome["properties"] if p["key"] == "current"))
    set_prop(outcome, "current", str(current + 1))
    snap["elements"].append({"id": "id-new-cap", "name": "Digital Thread", "type": "Capability", "layer": "Strategy"})
    return b.normalize_snapshot({"info": {"name": snap["model"]["name"]}, "elements": snap["elements"],
                                 "relationships": snap["relationships"]}), cap, wp, gap, outcome


class SnapshotTests(unittest.TestCase):
    def test_normalized_snapshot_is_sorted_and_round_trips_as_json(self):
        snap = model_v1()
        self.assertEqual([e["id"] for e in snap["elements"]], sorted(e["id"] for e in snap["elements"]))
        text = b.serialize_snapshot(snap)
        self.assertEqual(json.loads(text), snap)
        # one element per line: a changed element is a one-line diff
        self.assertEqual(sum(1 for line in text.splitlines() if line.startswith('    {"id"')),
                         len(snap["elements"]) + len(snap["relationships"]))

    def test_slugs(self):
        self.assertEqual(b.slugify("Bauer Information Model"), "bauer-information-model")
        self.assertEqual(b.slugify("Größe & Qualität 2026/Q4"), "grosse-qualitat-2026-q4")
        self.assertEqual(b.slugify("!!!"), "baseline")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "baselines"
        self.store = b.BaselineStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, check=True).stdout

    def test_create_commits_and_tags_each_baseline(self):
        self.assertFalse(self.store.status()["initialized"])
        meta = self.store.create(model_v1(), name="Assessment Q3 2026", note="Kick-off", day=date(2026, 9, 1),
                                 source={"type": "mcp"})
        self.assertEqual(meta["id"], "2026-09-01-assessment-q3-2026")
        self.assertEqual(meta["folder"], "engineering-test-model/2026-09-01-assessment-q3-2026")
        self.assertIn("baseline/engineering-test-model/2026-09-01-assessment-q3-2026", self.git("tag"))
        self.assertIn("Baseline: Assessment Q3 2026", self.git("log", "-1", "--format=%s"))
        files = self.git("show", "--name-only", "--format=", "HEAD").split()
        self.assertEqual(sorted(Path(f).name for f in files), ["baseline.json", "kpis.json", "snapshot.json"])
        self.assertEqual(self.store.status()["commits"], 2)  # repository created + baseline

        again = self.store.create(model_v1(), name="Assessment Q3 2026", day=date(2026, 9, 1), source={"type": "mcp"})
        self.assertEqual(again["id"], "2026-09-01-assessment-q3-2026-2")
        self.assertEqual([m["id"] for m in self.store.list("engineering TEST model")], [meta["id"], again["id"]])
        self.assertEqual(self.store.list("Another model"), [])

    def test_delete_keeps_the_history(self):
        meta = self.store.create(model_v1(), name="Q3", day=date(2026, 9, 1), source={"type": "mcp"})
        self.store.delete(meta["id"], "Engineering Test Model")
        self.assertEqual(self.store.list(), [])
        self.assertFalse((self.root / meta["folder"]).exists())
        self.assertIn("Remove baseline: Q3", self.git("log", "-1", "--format=%s"))
        self.assertNotIn(meta["tag"], self.git("tag"))
        self.assertIn("Baseline: Q3", self.git("log", "--format=%s"))
        with self.assertRaises(b.BaselineNotFound):
            self.store.delete(meta["id"])

    def test_invalid_input_and_ids(self):
        with self.assertRaises(b.BaselineError):
            self.store.create(model_v1(), name="  ", day=date(2026, 9, 1), source={"type": "mcp"})
        with self.assertRaises(b.BaselineNotFound):
            self.store.get("../../etc/passwd")
        unnamed = b.normalize_snapshot({"info": {}, "elements": [], "relationships": []})
        with self.assertRaises(b.BaselineError):
            self.store.create(unnamed, name="x", day=date(2026, 9, 1), source={"type": "mcp"})

    def test_a_coarchi_commit_is_imported_once(self):
        source = {"type": "coarchi", "repository": "repo", "commit": "a" * 40}
        self.store.create(model_v1(), name="Kick-off", day=date(2026, 8, 24), source=source)
        with self.assertRaises(b.BaselineConflict):
            self.store.create(model_v1(), name="Kick-off again", day=date(2026, 8, 24), source=source)

    def test_evaluated_uses_the_baseline_date_and_caches(self):
        meta = self.store.create(model_v1(), name="Q3", day=TODAY, source={"type": "mcp"})
        first = self.store.evaluated(meta)
        self.assertEqual(first["asOf"], TODAY.isoformat())
        self.assertIs(self.store.evaluated(meta), first)


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.v1 = model_v1()
        self.v2, self.cap, self.wp, self.gap, self.outcome = model_v2()
        self.points = [
            {"id": "q3", "label": "Assessment Q3", "date": "2026-07-01", "kind": "baseline",
             "snapshot": self.v1, "dashboard": b.evaluate(self.v1, date(2026, 7, 1))},
            {"id": b.CURRENT, "label": "Now", "date": TODAY.isoformat(), "kind": "current",
             "snapshot": self.v2, "dashboard": b.evaluate(self.v2, TODAY)},
        ]

    def test_trend_metrics_and_comparison(self):
        report = b.build_progress(self.points)
        metrics = {m["id"]: m for m in report["metrics"]}
        self.assertIn("avgMaturity", metrics)
        self.assertEqual(set(metrics["avgMaturity"]["values"]), {"q3", b.CURRENT})
        self.assertIsNotNone(metrics["avgMaturity"]["references"]["q3"])          # target maturity alongside
        comparison = report["comparison"]
        self.assertEqual((comparison["from"]["id"], comparison["to"]["id"]), ("q3", b.CURRENT))
        kpis = {k["id"]: k for k in comparison["kpis"]}
        self.assertEqual(kpis["wpCompleted"]["assessment"], "better")
        self.assertEqual(kpis["wpCompleted"]["delta"], 1)
        self.assertEqual(kpis["avgMaturity"]["assessment"], "better")
        self.assertEqual(kpis["elements"]["assessment"], "same")                  # one gap out, one capability in
        self.assertEqual(kpis["relationships"]["assessment"], "changed")          # model size is neutral

    def test_highlights_name_what_moved(self):
        text = " | ".join(b.highlights(self.points[0]["dashboard"], self.points[1]["dashboard"]))
        self.assertIn("1 capability improved in maturity", text)
        self.assertIn(self.cap["name"], text)
        self.assertIn(f"1 work package completed: {self.wp['name']}", text)
        self.assertIn(f"1 gap closed (no longer in the model): {self.gap['name']}", text)

    def test_model_changes(self):
        changes = b.diff_snapshots(self.v1, self.v2)
        summary = changes["summary"]
        self.assertEqual((summary["elementsAdded"], summary["elementsRemoved"]), (1, 1))
        self.assertGreaterEqual(summary["steeringChanges"], 3)
        first = changes["propertyChanges"][0]
        self.assertTrue(first["steering"])
        maturity = next(c for c in changes["propertyChanges"] if c["id"] == self.cap["id"] and c["key"] == "maturity")
        self.assertEqual(maturity["label"], "Current maturity")
        self.assertEqual([a["name"] for a in changes["added"]], ["Digital Thread"])
        self.assertEqual([r["name"] for r in changes["removed"]], [self.gap["name"]])
        self.assertGreater(summary["relationshipsRemoved"], 0)

    def test_capability_and_outcome_series(self):
        report = b.build_progress(self.points)
        cap = next(c for c in report["capabilities"] if c["id"] == self.cap["id"])
        self.assertEqual(cap["values"][b.CURRENT] - cap["values"]["q3"], 1)
        outcome = next(o for o in report["outcomes"] if o["id"] == self.outcome["id"])
        self.assertEqual(outcome["values"][b.CURRENT] - outcome["values"]["q3"], 1)

    def test_no_comparison_without_a_baseline(self):
        self.assertIsNone(b.build_progress(self.points[1:])["comparison"])

    def test_steering_values_for_the_re_assessment(self):
        values = b.steering_values(self.v1)
        self.assertIn("maturity", values[self.cap["id"]])
        self.assertTrue(all(isinstance(v, str) for row in values.values() for v in row.values()))

    def test_moment_orders_within_a_day_but_keeps_the_date(self):
        coarchi_a = {"date": "2026-08-25", "source": {"commitDate": "2026-08-25T14:20:49+02:00"}}
        coarchi_b = {"date": "2026-08-25", "source": {"commitDate": "2026-08-25T22:25:57+02:00"}}
        self.assertLess(b.moment(coarchi_a), b.moment(coarchi_b))
        self.assertEqual(b.moment(coarchi_a), "2026-08-25T12:20:49+00:00")
        saved_late = {"date": "2026-03-31", "createdAt": "2026-10-02T16:00:00+00:00", "source": {"type": "mcp"}}
        self.assertEqual(b.moment(saved_late), "2026-03-31T12:00:00+00:00")   # the date wins
        saved_same_day = {"date": "2026-10-02", "createdAt": "2026-10-02T16:00:00+00:00", "source": {"type": "mcp"}}
        self.assertEqual(b.moment(saved_same_day), "2026-10-02T16:00:00+00:00")

    def test_latest_before(self):
        metas = [{"id": "a", "date": "2026-07-01"}, {"id": "b", "date": "2026-10-01"}]
        self.assertEqual(b.latest_before(metas, date(2026, 9, 1))["id"], "a")
        self.assertEqual(b.latest_before(metas, date(2026, 12, 1))["id"], "b")
        self.assertEqual(b.latest_before(metas, date(2026, 1, 1))["id"], "b")  # none before: the latest
        self.assertIsNone(b.latest_before([], date(2026, 1, 1)))


if __name__ == "__main__":
    unittest.main()
