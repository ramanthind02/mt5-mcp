"""Freeze the sources and tester ini that a backtest ran.

Paths are compared with abspath, not Path.resolve(). resolve() follows a
junction, and this server treats a junction as a different terminal.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from .config import data_dir
from .connection import active_record
from .parsers import read_text_auto

_INCLUDE_RE = re.compile(r'(?m)^[ \t]*#include[ \t]+(?:<([^>\r\n]+)>|"([^"\r\n]+)")')
_MAX_SOURCE_FILES = 200
_SOURCE_SUFFIXES = {".mq5", ".mqh", ".ex5"}


def _same_tree(root: Path, path: Path) -> bool:
    root_key = os.path.normcase(os.path.abspath(root))
    path_key = os.path.normcase(os.path.abspath(path))
    return path_key == root_key or path_key.startswith(root_key + os.sep)


def _ini_settings(text: str) -> dict:
    section = None
    tester: dict[str, str] = {}
    inputs: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if "=" not in line or section not in {"tester", "testerinputs"}:
            continue
        key, value = line.split("=", 1)
        if section == "tester":
            tester[key.strip().lower()] = value.strip()
        else:
            inputs[key.strip()] = value.split("||", 1)[0].strip()
    return {"tester": tester, "inputs": inputs}


def _expert_files(experts: Path, expert: str) -> list[Path]:
    rel = expert.strip().replace("/", "\\").strip("\\")
    if not rel:
        return []
    path = Path(rel)
    if path.suffix.lower() in {".mq5", ".ex5"}:
        names = [path, path.with_suffix(".mq5"), path.with_suffix(".ex5")]
    else:
        names = [Path(str(path) + ".mq5"), Path(str(path) + ".ex5")]
    found: list[Path] = []
    seen: set[str] = set()
    for name in names:
        candidate = experts / name
        key = os.path.normcase(os.path.abspath(candidate))
        if key in seen or not candidate.is_file():
            continue
        seen.add(key)
        found.append(candidate)
    return found


def _resolve_include(
    mql: Path, source: Path, angled: str | None, quoted: str | None
) -> Path | None:
    spec = (quoted or angled or "").strip().replace("\\", "/")
    if not spec:
        return None
    rel = Path(spec)
    candidates = []
    if quoted:
        candidates.append(source.parent / rel)
    candidates.append(mql / "Include" / rel)
    candidates.append(source.parent / rel)
    candidates.append(mql / "Experts" / rel)
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(os.path.abspath(candidate))
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file() and _same_tree(mql, candidate):
            return candidate
    return None


def _walk_sources(mql: Path, roots: list[Path]) -> dict:
    files: list[Path] = []
    missing: list[str] = []
    seen: set[str] = set()
    truncated = False
    queue = list(roots)
    while queue:
        current = queue.pop(0)
        if not current.is_file() or not _same_tree(mql, current):
            continue
        key = os.path.normcase(os.path.abspath(current))
        if key in seen:
            continue
        if len(seen) >= _MAX_SOURCE_FILES:
            truncated = True
            break
        if current.suffix.lower() not in _SOURCE_SUFFIXES:
            continue
        seen.add(key)
        files.append(current)
        if current.suffix.lower() == ".ex5":
            continue
        for match in _INCLUDE_RE.finditer(read_text_auto(current)):
            spec = (match.group(1) or match.group(2) or "").strip()
            resolved = _resolve_include(mql, current, match.group(1), match.group(2))
            if resolved is None:
                if spec and spec not in missing:
                    missing.append(spec)
                continue
            queue.append(resolved)
    return {"files": files, "missing_includes": missing, "files_truncated": truncated}


def begin_snapshot(layout: dict, config: Path, text: str) -> dict:
    """Copy the ini and the expert's .mq5/.mqh tree before the terminal starts."""
    try:
        return _write_snapshot(layout, config, text)
    except OSError as exc:
        return {"error": str(exc)}


def _write_snapshot(layout: dict, config: Path, text: str) -> dict:
    settings = _ini_settings(text)
    tester = settings["tester"]
    record = active_record()
    terminal_id = record.id if record else "terminal"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    root = data_dir() / "snapshots" / terminal_id / stamp
    sources = root / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config, root / "tester.ini")
    mql = layout["mql"]
    roots = _expert_files(layout["experts"], tester.get("expert", "")) if mql.exists() else []
    collected = (
        _walk_sources(mql, roots)
        if roots
        else {
            "files": [],
            "missing_includes": [],
            "files_truncated": False,
        }
    )
    listed = []
    for path in collected["files"]:
        rel = Path(os.path.relpath(path, mql)).as_posix()
        dest = sources / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        listed.append(
            {
                "path": rel,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
        )
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "terminal_id": terminal_id,
        "expert": tester.get("expert"),
        "symbol": tester.get("symbol"),
        "period": tester.get("period"),
        "from_date": tester.get("fromdate"),
        "to_date": tester.get("todate"),
        "model": tester.get("model"),
        "inputs": settings["inputs"],
        "ini": "tester.ini",
        "ini_source": str(config),
        "report_name": tester.get("report"),
        "report_path": None,
        "directory": str(root),
        "files": listed,
        "missing_includes": collected["missing_includes"],
        "files_truncated": collected["files_truncated"],
    }
    if tester.get("expert") and not any(item["path"].lower().endswith(".mq5") for item in listed):
        manifest["source_note"] = "no .mq5 matched Expert under MQL5/Experts"
    dest = root / "manifest.json"
    dest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"manifest": str(dest), "directory": str(root)}


def finish_snapshot(manifest_path: str | None, report_path: str | None) -> None:
    if not manifest_path or not report_path:
        return
    path = Path(manifest_path)
    if not path.is_file():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    data["report_path"] = report_path
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def snapshot_for_report(report: Path) -> str | None:
    root = data_dir() / "snapshots"
    if not root.exists():
        return None
    target = os.path.normcase(os.path.abspath(report))
    for manifest in root.rglob("manifest.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        stored = data.get("report_path")
        if stored and os.path.normcase(os.path.abspath(stored)) == target:
            return str(manifest)
    return None


def list_snapshots(limit: int = 20) -> dict:
    root = data_dir() / "snapshots"
    found = []
    if root.exists():
        for manifest in root.rglob("manifest.json"):
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            found.append(
                {
                    "path": str(manifest),
                    "created_at": data.get("created_at"),
                    "terminal_id": data.get("terminal_id"),
                    "expert": data.get("expert"),
                    "symbol": data.get("symbol"),
                    "report_path": data.get("report_path"),
                    "file_count": len(data.get("files") or []),
                }
            )
    found.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {"count": len(found), "snapshots": found[:limit]}


def read_snapshot(path: str) -> dict:
    target = Path(path)
    if target.is_dir():
        target = target / "manifest.json"
    if not target.is_file():
        return {"error": f"snapshot not found: {target}"}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"error": f"snapshot is not valid JSON: {exc}", "path": str(target)}
    data["path"] = str(target)
    data["directory"] = str(target.parent)
    return data
