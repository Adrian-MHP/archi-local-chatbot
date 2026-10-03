"""Offline tests for reading coArchi repositories (backend/app/coarchi.py) against a real git repository
in coArchi's grafico format. Needs git."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

from app import baselines, coarchi

NS = 'xmlns:archimate="http://www.archimatetool.com/archimate"'
XSI = 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'


def element_xml(kind, id_, name, **props):
    body = "".join(f'\n  <properties key="{k}" value="{v}"/>' for k, v in props.items())
    return f'<archimate:{kind}\n    {NS}\n    name="{name}"\n    id="{id_}">{body}\n</archimate:{kind}>\n'


def relation_xml(kind, id_, source, target):
    return (f'<archimate:{kind}\n    {XSI}\n    {NS}\n    id="{id_}">\n'
            f'  <source xsi:type="archimate:{source[0]}" href="{source[0]}_{source[1]}.xml#{source[1]}"/>\n'
            f'  <target xsi:type="archimate:{target[0]}" href="{target[0]}_{target[1]}.xml#{target[1]}"/>\n'
            f'</archimate:{kind}>\n')


class CoArchiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "testmodel"
        (self.repo / "model").mkdir(parents=True)
        self.git("init", "-q", "-b", "master")
        self.write("model/folder.xml", f'<archimate:ArchimateModel\n    {NS}\n    name="Test Model"\n    id="id-model"\n    version="5.0.0"/>\n')
        self.write("model/strategy/folder.xml", f'<archimate:Folder {NS} name="Strategy" id="id-f1" type="strategy"/>\n')
        self.write("model/strategy/Capability_id-c1.xml", element_xml("Capability", "id-c1", "Change Management", maturity="2", targetMaturity="4"))
        self.write("model/business/BusinessProcess_id-p1.xml", element_xml("BusinessProcess", "id-p1", "Process Change Request"))
        self.write("model/relations/RealizationRelationship_id-r1.xml",
                   relation_xml("RealizationRelationship", "id-r1", ("BusinessProcess", "id-p1"), ("Capability", "id-c1")))
        self.write("model/diagrams/ArchimateDiagramModel_id-v1.xml", f'<archimate:ArchimateDiagramModel {NS} name="View" id="id-v1"/>\n')
        self.commit("Kick-off model", "2026-08-24T10:00:00+02:00")
        self.git("tag", "-a", "assessment-1", "-m", "Assessment 1")
        self.first = self.git("rev-parse", "HEAD").strip()
        self.write("model/strategy/Capability_id-c1.xml", element_xml("Capability", "id-c1", "Change Management", maturity="3", targetMaturity="4"))
        self.write("model/business/BusinessProcess_id-p2.xml", element_xml("BusinessProcess", "id-p2", "Digital Change Workflow", status="target"))
        self.commit("Re-assessment", "2026-10-01T18:00:00+02:00")
        self.second = self.git("rev-parse", "HEAD").strip()
        (self.root / "not-a-repo").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, env=None):
        return subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=Tester", "-c", "user.email=t@example.com", *args],
                              capture_output=True, text=True, check=True, env=env).stdout

    def write(self, relative, text):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, message, when):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message, env={**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})

    def test_finds_the_repository_of_a_model(self):
        repos = coarchi.repositories(self.root)
        self.assertEqual([(r["folder"], r["modelName"], r["modelId"]) for r in repos], [("testmodel", "Test Model", "id-model")])
        self.assertEqual(coarchi.repository_for(self.root, "  test   MODEL ")["folder"], "testmodel")
        self.assertIsNone(coarchi.repository_for(self.root, "Other model"))
        self.assertEqual(coarchi.repositories(self.root / "missing"), [])

    def test_lists_commits_with_tags(self):
        commits = coarchi.commits(self.repo)
        self.assertEqual([c["message"] for c in commits], ["Re-assessment", "Kick-off model"])
        self.assertEqual(commits[1]["tags"], ["assessment-1"])
        self.assertEqual(commits[1]["date"], "2026-08-24")

    def test_rebuilds_the_model_at_a_commit(self):
        old = coarchi.snapshot_at(self.repo, self.first)
        new = coarchi.snapshot_at(self.repo, self.second[:10])
        self.assertEqual(old["model"]["name"], "Test Model")
        self.assertEqual(old["model"]["viewCount"], 1)
        self.assertEqual([e["name"] for e in old["elements"]], ["Change Management", "Process Change Request"])
        self.assertEqual(old["relationships"], [{"id": "id-r1", "type": "RealizationRelationship",
                                                 "sourceId": "id-p1", "targetId": "id-c1"}])
        cap = next(e for e in new["elements"] if e["id"] == "id-c1")
        self.assertIn({"key": "maturity", "value": "3"}, cap["properties"])
        self.assertEqual(next(e for e in old["elements"] if e["id"] == "id-c1")["layer"], "Strategy")
        # the dashboard reads a coArchi snapshot like a live one
        dash = baselines.evaluate(new, date(2026, 10, 2))
        self.assertEqual(dash["strategy"]["kpis"]["avgMaturity"], 3)
        changes = baselines.diff_snapshots(old, new)
        self.assertEqual(changes["summary"]["elementsAdded"], 1)
        self.assertEqual(changes["propertyChanges"][0]["key"], "maturity")

    def test_rejects_what_is_not_a_commit(self):
        for bad in ("HEAD", "--output=/tmp/x", "abc", "f" * 40):
            with self.assertRaises(coarchi.CoArchiError):
                coarchi.snapshot_at(self.repo, bad)


if __name__ == "__main__":
    unittest.main()
