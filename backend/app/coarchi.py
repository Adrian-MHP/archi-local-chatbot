"""Model versions from coArchi, Archi's git collaboration plugin.

coArchi keeps a model as one XML file per element, relationship and view under model/ (the
"grafico" format) and versions it in a git repository. This module finds the repository of a
model, lists its commits and rebuilds the model at any commit as a baseline snapshot
(baselines.py) -- so a commit, e.g. the state at the kick-off of an assessment, can serve as a
baseline after the fact. It only reads: git log, ls-tree, cat-file and for-each-ref.
"""

from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import baselines

# Files under model/ that are not model concepts.
NON_CONCEPTS = {"Folder", "ArchimateModel", "ArchimateDiagramModel", "SketchModel", "CanvasModel"}
LAYER_BY_FOLDER = {
    "strategy": "Strategy", "business": "Business", "application": "Application", "technology": "Technology",
    "motivation": "Motivation", "implementation_migration": "Implementation & Migration", "other": "Other",
}
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")


class CoArchiError(RuntimeError):
    """Reading the coArchi repository failed; the message is meant for the user."""


def _git(repo: Path, *args: str, input_bytes: Optional[bytes] = None) -> bytes:
    command = ["git", "-c", "safe.directory=*", "-C", str(repo), *args]
    try:
        result = subprocess.run(command, input=input_bytes, capture_output=True, timeout=120)
    except FileNotFoundError as exc:
        raise CoArchiError("git is not installed in the backend.") from exc
    except subprocess.TimeoutExpired as exc:
        raise CoArchiError(f"git {args[0]} took too long in {repo.name}.") from exc
    if result.returncode != 0:
        raise CoArchiError(f"git {args[0]} failed in {repo.name}: {result.stderr.decode(errors='replace').strip()[:300]}")
    return result.stdout


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def repositories(root: Path) -> List[Dict[str, Any]]:
    """Every coArchi repository under root (coArchi's "model-repository" folder), with its model."""
    if not root or not Path(root).is_dir():
        return []
    found = []
    for child in sorted(Path(root).iterdir()):
        if not (child / ".git").exists():
            continue
        try:
            header = ET.fromstring(_git(child, "show", "HEAD:model/folder.xml"))
            head = _git(child, "rev-parse", "HEAD").decode().strip()
        except (CoArchiError, ET.ParseError):
            continue  # not a coArchi repository, or one without a commit yet
        found.append({"path": child, "folder": child.name, "modelName": header.get("name") or "",
                      "modelId": header.get("id"), "head": head})
    return found


def repository_for(root: Path, model_name: str) -> Optional[Dict[str, Any]]:
    wanted = baselines.normalize_model_name(model_name)
    return next((r for r in repositories(root) if baselines.normalize_model_name(r["modelName"]) == wanted), None)


def commits(repo: Path, limit: int = 200) -> List[Dict[str, Any]]:
    """Commits that changed the model, newest first, with the tags that point at them."""
    tags: Dict[str, List[str]] = {}
    refs = _git(repo, "for-each-ref", "refs/tags", "--format=%(objectname)%00%(*objectname)%00%(refname:short)").decode()
    for line in refs.splitlines():
        obj, peeled, name = (line.split("\0") + ["", "", ""])[:3]
        tags.setdefault(peeled or obj, []).append(name)
    log = _git(repo, "log", f"-n{int(limit)}", "--format=%H%x1f%cI%x1f%an%x1f%s%x1e", "--", "model").decode()
    out = []
    for record in log.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, when, author, subject = (record.split("\x1f") + ["", "", "", ""])[:4]
        out.append({"commit": sha, "shortCommit": sha[:7], "date": when[:10], "committedAt": when,
                    "author": author, "message": subject, "tags": tags.get(sha, [])})
    return out


def _read_blobs(repo: Path, commit: str, paths: List[str]) -> Dict[str, bytes]:
    """All files of a commit in one `git cat-file --batch` call."""
    if not paths:
        return {}
    data = _git(repo, "cat-file", "--batch", input_bytes="".join(f"{commit}:{p}\n" for p in paths).encode())
    out: Dict[str, bytes] = {}
    pos = 0
    for path in paths:
        end = data.index(b"\n", pos)
        header = data[pos:end].decode(errors="replace")
        pos = end + 1
        if header.endswith(" missing"):
            continue
        size = int(header.rsplit(" ", 1)[-1])
        out[path] = data[pos:pos + size]
        pos += size + 1
    return out


def _ref_id(node: Optional[ET.Element]) -> str:
    if node is None:
        return ""
    href = node.get("href") or ""
    return href.rsplit("#", 1)[-1] if "#" in href else ""


def snapshot_at(repo: Path, commit: str) -> Dict[str, Any]:
    """The model as committed: elements with properties and relationships, in baseline form."""
    if not COMMIT_RE.match(commit or ""):
        raise CoArchiError("Not a commit id.")
    sha = _git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}").decode().strip()
    paths = [p for p in _git(repo, "ls-tree", "-r", "--name-only", sha, "--", "model").decode().splitlines()
             if p.endswith(".xml")]
    info: Dict[str, Any] = {"name": "", "id": None, "viewCount": 0, "modelVersion": f"coArchi {sha[:7]}"}
    elements: List[Dict[str, Any]] = []
    relationships: List[Dict[str, Any]] = []
    for path, content in _read_blobs(repo, sha, paths).items():
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            continue
        kind = _local_name(root.tag)
        if path == "model/folder.xml":
            info.update(name=root.get("name") or "", id=root.get("id"))
            continue
        if kind == "ArchimateDiagramModel":
            info["viewCount"] += 1
        if kind in NON_CONCEPTS or kind.startswith(("Canvas", "Sketch", "DiagramModel")) or not root.get("id"):
            continue
        properties = [{"key": p.get("key") or "", "value": p.get("value") or ""} for p in root.findall("properties")]
        if kind.endswith("Relationship"):
            relationships.append({"id": root.get("id"), "type": kind, "name": root.get("name") or "",
                                  "sourceId": _ref_id(root.find("source")) or root.get("source") or "",
                                  "targetId": _ref_id(root.find("target")) or root.get("target") or "",
                                  "properties": properties})
        else:
            parts = path.split("/")
            elements.append({"id": root.get("id"), "name": root.get("name") or "", "type": kind,
                             "layer": LAYER_BY_FOLDER.get(parts[1] if len(parts) > 2 else "", ""), "properties": properties})
    if not info["name"]:
        raise CoArchiError(f"Commit {sha[:7]} has no coArchi model (model/folder.xml is missing).")
    return baselines.normalize_snapshot({"info": info, "elements": elements, "relationships": relationships})
