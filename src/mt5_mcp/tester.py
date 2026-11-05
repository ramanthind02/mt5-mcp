"""Compile an existing Expert Advisor and run it in the Strategy Tester.

The launcher only accepts an ini whose sections are Tester and TesterInputs,
with ShutdownTerminal=1. It will not start a terminal onto a live chart.

The compile-log parser, report parser, and input generator are adapted from
PHUICMT/mcp-mt5 (MIT). See NOTICE.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .config import data_dir, ensure_home
from .connection import active_record
from .parsers import (
    extract_inputs,
    gen_tester_inputs,
    iter_journal_lines,
    parse_compile_log,
    parse_tester_report,
    read_text_auto,
)
from .paths import junction_launch_error, launch_conflict
from .registry import running_terminals
from .snapshot import begin_snapshot, finish_snapshot, snapshot_for_report
from .timeframes import parse_timeframe

_ALLOWED_SECTIONS = {"tester", "testerinputs"}
_MODELS = {"every_tick": 0, "ohlc": 1, "open_prices": 2, "real_ticks": 4}
_proc: subprocess.Popen | None = None


def _layout() -> tuple[dict | None, dict | None]:
    record = active_record()
    if record is None or record.exe is None:
        return None, {"error": "call use_terminal first"}
    blocked = junction_launch_error(record.exe)
    if blocked:
        return None, blocked
    if record.data_dir is None:
        return None, {
            "error": "no data folder for this terminal; open it once so origin.txt exists"
        }
    data = record.data_dir
    install = record.exe.parent
    return {
        "exe": record.exe,
        "metaeditor": install / "MetaEditor64.exe",
        "data": data,
        "mql": data / "MQL5",
        "experts": data / "MQL5" / "Experts",
        "journal": data / "logs",
        "tester": data / "Tester",
        "tester_logs": data / "Tester" / "logs",
    }, None


def validate_tester_config(text: str) -> dict:
    """Refuse anything other than a headless Strategy Tester config."""
    sections: list[str] = []
    values: dict[str, dict[str, str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            sections.append(current)
            values.setdefault(current.lower(), {})
            continue
        if current and "=" in line:
            key, value = line.split("=", 1)
            values[current.lower()][key.strip().lower()] = value.strip()
    bad = [name for name in sections if name.lower() not in _ALLOWED_SECTIONS]
    if bad:
        return {"ok": False, "error": f"refusing ini sections that can start a live chart: {bad}"}
    if "tester" not in {name.lower() for name in sections}:
        return {"ok": False, "error": "ini has no [Tester] section"}
    shutdown = values.get("tester", {}).get("shutdownterminal", "")
    if shutdown not in {"1", "true", "yes"}:
        return {"ok": False, "error": "ShutdownTerminal must be 1"}
    return {"ok": True, "values": values}


def _normalize_date(value: str) -> str:
    text = value.strip().replace("-", ".")
    if "T" in text:
        text = text.replace("T", " ")
    return text


def _model_code(model: int | str) -> int:
    if isinstance(model, int):
        return model
    key = str(model).strip().lower()
    if key in _MODELS:
        return _MODELS[key]
    return int(key)


def write_tester_config(
    expert: str,
    symbol: str,
    period: str = "H1",
    model: int | str = 1,
    date_from: str = "2024.01.01",
    date_to: str = "2024.12.31",
    deposit: float = 10000,
    currency: str = "USD",
    leverage: int = 100,
    optimization: int = 0,
    inputs: dict | None = None,
    source: str | None = None,
    path: str | None = None,
) -> dict:
    try:
        period_text = str(period).strip()
        if period_text.isdigit():
            period_value = period_text
        else:
            period_value = period_text.upper()
            parse_timeframe(period_value)
        model_code = _model_code(model)
    except ValueError as exc:
        return {"error": str(exc)}

    lines_by_name: dict[str, str] = {}
    if source:
        generated = gen_tester_inputs(source)
        for line in generated.splitlines():
            if not line or line.startswith("["):
                continue
            lines_by_name[line.split("=", 1)[0]] = line
    if inputs:
        for key, value in inputs.items():
            lines_by_name[str(key)] = f"{key}={value}||{value}||0||{value}||N"

    record = active_record()
    terminal_id = record.id if record else "terminal"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    ensure_home()
    dest = Path(path) if path else data_dir() / "tester" / terminal_id / f"{stamp}.ini"
    dest.parent.mkdir(parents=True, exist_ok=True)
    report = f"mt5mcp_{stamp}"
    body = [
        "[Tester]",
        f"Expert={expert}",
        f"Symbol={symbol}",
        f"Period={period_value}",
        f"Model={model_code}",
        f"FromDate={_normalize_date(date_from)}",
        f"ToDate={_normalize_date(date_to)}",
        f"Deposit={deposit}",
        f"Currency={currency}",
        f"Leverage={leverage}",
        "Visual=0",
        "ShutdownTerminal=1",
        f"Optimization={int(optimization)}",
        f"Report={report}",
        "",
        "[TesterInputs]",
    ]
    body.extend(lines_by_name.values())
    text = "\n".join(body) + "\n"
    check = validate_tester_config(text)
    if not check["ok"]:
        return check
    dest.write_text(text, encoding="utf-16")
    return {"path": str(dest), "report": report, "ok": True}


def list_experts() -> dict:
    layout, error = _layout()
    if error:
        return error
    experts: Path = layout["experts"]
    if not experts.exists():
        return {"error": f"Experts dir missing: {experts}"}
    files = []
    for pattern in ("*.mq5", "*.ex5"):
        for path in experts.rglob(pattern):
            stat = path.stat()
            files.append(
                {
                    "name": path.name,
                    "rel": str(path.relative_to(experts)),
                    "size": stat.st_size,
                    "mtime": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                }
            )
    files.sort(key=lambda item: item["rel"])
    return {"count": len(files), "files": files}


def _metaeditor_switch(name: str, value: str) -> str:
    text = str(value)
    if any(ch in text for ch in ' "'):
        return f'/{name}:"{text}"'
    return f"/{name}:{text}"


def compile_source(source: str, timeout_sec: int = 300) -> dict:
    layout, error = _layout()
    if error:
        return error
    src = Path(source)
    if not src.exists():
        return {"error": f"source not found: {src}"}
    editor: Path = layout["metaeditor"]
    if not editor.exists():
        return {"error": f"MetaEditor missing: {editor}"}
    ensure_home()
    log_path = data_dir() / "logs" / f"{src.stem}.compile.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # MetaEditor splits its own command line on spaces. The quotes have to
    # appear in the raw command line, not as extra quoting around the switch.
    command = f'"{editor}" /compile:"{src}" /include:"{layout["mql"]}" /log:"{log_path}"'
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout_sec, check=False
        )
        returncode = proc.returncode
        stderr = (proc.stderr or "")[-2000:]
    except subprocess.TimeoutExpired:
        returncode = -1
        stderr = f"timeout after {timeout_sec}s"
    parsed = {
        "errors": [],
        "warnings": [],
        "result_errors": None,
        "result_warnings": None,
        "ok": False,
    }
    excerpt = ""
    if log_path.exists():
        text = read_text_auto(log_path)
        parsed = parse_compile_log(text)
        excerpt = "\n".join(text.splitlines()[-80:])
    return {
        "returncode": returncode,
        "cmd": command,
        "log_path": str(log_path),
        "ok": parsed["ok"],
        "result_errors": parsed["result_errors"],
        "result_warnings": parsed["result_warnings"],
        "errors": parsed["errors"][:50],
        "warnings": parsed["warnings"][:50],
        "log_excerpt": excerpt,
        "stderr_tail": stderr,
    }


def read_inputs(source: str) -> dict:
    path = Path(source)
    if not path.exists():
        return {"error": f"source not found: {path}"}
    inputs = extract_inputs(path)
    return {"source": str(path), "count": len(inputs), "inputs": inputs}


def _named_report(data: Path, tester_dir: Path, name: str) -> str | None:
    """Find the HTML report named in the ini. Do not fall back to an older file."""
    stem = Path(name).name
    if stem.lower().endswith((".htm", ".html")):
        names = [stem]
    else:
        names = [stem + ".htm", stem + ".html"]
    found: list[Path] = []
    for root in (data, tester_dir):
        for filename in names:
            candidate = root / filename
            if candidate.is_file():
                found.append(candidate)
    if not found:
        return None
    found.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return str(found[0])


def _newest(root: Path, pattern: str) -> str | None:
    if not root.exists():
        return None
    files = sorted(root.rglob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    return str(files[0]) if files else None


def run_backtest(config: str, timeout_sec: int = 1800, popen=None, processes=None) -> dict:
    global _proc
    layout, error = _layout()
    if error:
        return error
    cfg = Path(config)
    if not cfg.exists():
        return {"error": f"config not found: {cfg}"}
    text = read_text_auto(cfg)
    check = validate_tester_config(text)
    if not check["ok"]:
        return check
    exe: Path = layout["exe"]
    running = running_terminals() if processes is None else processes
    conflict = launch_conflict(exe, running)
    if conflict:
        return conflict
    if not exe.exists():
        return {"error": f"terminal missing: {exe}"}
    snapshot = begin_snapshot(layout, cfg, text)
    cmd = [str(exe), _metaeditor_switch("config", str(cfg))]
    launcher = popen or subprocess.Popen
    proc = launcher(cmd)
    _proc = proc
    try:
        returncode = proc.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        stop_backtest()
        payload = _close_run(layout, check, snapshot)
        payload["error"] = f"timeout after {timeout_sec}s"
        payload["cmd"] = cmd
        return payload
    finally:
        if proc.poll() is not None and _proc is proc:
            _proc = None
    payload = _close_run(layout, check, snapshot)
    payload["returncode"] = returncode
    payload["cmd"] = cmd
    return payload


def _close_run(layout: dict, check: dict, snapshot: dict) -> dict:
    report_name = check["values"].get("tester", {}).get("report", "")
    report_path = (
        _named_report(layout["data"], layout["tester"], report_name) if report_name else None
    )
    finish_snapshot(snapshot.get("manifest"), report_path)
    payload = {
        "report_path": report_path,
        "tester_log": _newest(layout["tester_logs"], "*.log"),
        "snapshot_path": snapshot.get("manifest"),
    }
    if snapshot.get("error"):
        payload["snapshot_error"] = snapshot["error"]
    if report_name and report_path is None:
        payload["error"] = f"tester exited without writing {Path(report_name).name}.htm"
    return payload


def stop_backtest() -> dict:
    global _proc
    proc = _proc
    if proc is None or proc.poll() is not None:
        _proc = None
        return {"error": "no tester process started by this server is running"}
    pid = proc.pid
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid), "/T"], capture_output=True, text=True, check=False
        )
    else:
        proc.kill()
    _proc = None
    return {"stopped": True, "pid": pid}


def read_report(path: str | None = None) -> dict:
    if path:
        report = Path(path)
    else:
        layout, error = _layout()
        if error:
            return error
        found = _newest(layout["tester"], "*.htm*") or _newest(layout["data"], "*.htm")
        if not found:
            return {"error": "no tester report found"}
        report = Path(found)
    if not report.exists():
        return {"error": f"report not found: {report}"}
    parsed = parse_tester_report(read_text_auto(report))
    record = active_record()
    terminal_id = record.id if record else "terminal"
    trades_path = data_dir() / "reports" / terminal_id / f"{report.stem}.trades.json"
    trades_path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "report": str(report),
        "summary": parsed["summary"],
        "trade_count": len(parsed["trades"]),
        "trades": parsed["trades"],
        "breakdown": parsed["breakdown"],
    }
    trades_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    payload = {
        "path": str(report),
        "summary": parsed["summary"],
        "trade_count": document["trade_count"],
        "trades": parsed["trades"],
        "breakdown": parsed["breakdown"],
        "trades_path": str(trades_path),
    }
    found = snapshot_for_report(report)
    if found:
        payload["snapshot_path"] = found
    return payload


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.replace(" ", "").replace(",", "").replace("%", "").strip())
    except (ValueError, AttributeError):
        return None


def compare_reports(baseline: str, candidate: str) -> dict:
    base_path = Path(baseline)
    cand_path = Path(candidate)
    if not base_path.exists():
        return {"error": f"baseline missing: {base_path}"}
    if not cand_path.exists():
        return {"error": f"candidate missing: {cand_path}"}
    base = parse_tester_report(read_text_auto(base_path))["summary"]
    cand = parse_tester_report(read_text_auto(cand_path))["summary"]
    diffs = []
    for key in sorted(set(base) | set(cand)):
        before = base.get(key)
        after = cand.get(key)
        before_n = _to_float(before)
        after_n = _to_float(after)
        delta = None
        pct = None
        if before_n is not None and after_n is not None:
            delta = round(after_n - before_n, 6)
            pct = round((delta / before_n) * 100, 4) if before_n != 0 else None
        diffs.append(
            {"key": key, "baseline": before, "candidate": after, "delta": delta, "pct": pct}
        )
    return {"baseline": str(base_path), "candidate": str(cand_path), "diffs": diffs}


def tail_log(
    mode: str = "tester", lines: int = 100, date: str | None = None, structured: bool = True
) -> dict:
    layout, error = _layout()
    if error:
        return error
    if mode == "tester":
        found = _newest(layout["tester_logs"], "*.log")
        if not found:
            return {"error": f"no tester logs in {layout['tester_logs']}"}
        path = Path(found)
    elif mode == "journal":
        stamp = date or datetime.now().strftime("%Y%m%d")
        path = layout["journal"] / f"{stamp}.log"
    else:
        return {"error": f"unknown mode {mode!r}; use tester or journal"}
    if not path.exists():
        return {"error": f"log not found: {path}", "path": str(path)}
    text = read_text_auto(path)
    tail = text.splitlines()[-lines:]
    payload: dict = {"path": str(path), "line_count": len(tail)}
    if structured:
        payload["records"] = list(iter_journal_lines("\n".join(tail)))
    else:
        payload["content"] = "\n".join(tail)
    return payload


_HISTORY_PHRASES = (
    "history not synchronized",
    "no history",
    "history synchronization failed",
    "synchronization error",
)
_INIT_PHRASES = (
    "initialization failed",
    "init failed",
    "cannot initialize",
    "failed to initialize",
    "initialization function",
)
_REJECT_PHRASES = (
    "rejected",
    "invalid volume",
    "invalid stops",
    "invalid price",
    "expert not found",
    "cannot load",
    "tester didn't start",
    "test failed",
    "not found",
)
_RUNTIME_PHRASES = (
    "array out of range",
    "zero divide",
    "critical error",
)
_NOTE_PHRASES = (
    ("passed", "test passed"),
    ("balance", "final balance"),
    ("finished", "testing finished"),
    ("history_ok", "history synchronized"),
)


def _journal_kind(message: str) -> str | None:
    folded = message.lower()
    if any(phrase in folded for phrase in _HISTORY_PHRASES):
        return "history"
    if any(phrase in folded for phrase in _INIT_PHRASES) or (
        "oninit" in folded and "fail" in folded
    ):
        return "init"
    if any(phrase in folded for phrase in _REJECT_PHRASES):
        return "reject"
    if any(phrase in folded for phrase in _RUNTIME_PHRASES):
        return "runtime"
    if any(phrase in folded for phrase in (text for _label, text in _NOTE_PHRASES)):
        return "note"
    return None


def _note_label(message: str) -> str:
    folded = message.lower()
    for label, phrase in _NOTE_PHRASES:
        if phrase in folded:
            return label
    return "note"


def scan_journal(records: list[dict]) -> dict:
    """Classify tester journal lines. Blocking kinds are init, history, reject, and runtime."""
    blocking = []
    notes = []
    for row in records:
        message = str(row.get("message") or "").strip()
        if not message:
            continue
        kind = _journal_kind(message)
        if kind is None:
            continue
        item = {"kind": kind, "message": message, "ts": row.get("ts")}
        if kind == "note":
            item["kind"] = _note_label(message)
            notes.append(item)
        else:
            blocking.append(item)
    return {"ok": not blocking, "blocking": blocking, "notes": notes}


def _journal_records(text: str) -> list[dict]:
    records = list(iter_journal_lines(text))
    if records:
        return records
    return [{"message": line.strip()} for line in text.splitlines() if line.strip()]


def _scan_log_file(path: str | None) -> dict:
    if not path or not Path(path).is_file():
        return {"ok": False, "blocking": [], "notes": [], "error": "no tester log"}
    return scan_journal(_journal_records(read_text_auto(Path(path))))


def _history_only(scanned: dict) -> bool:
    blocking = scanned.get("blocking") or []
    return bool(blocking) and all(item.get("kind") == "history" for item in blocking)


def _under(root: Path, path: Path) -> bool:
    root_key = os.path.normcase(os.path.abspath(root))
    path_key = os.path.normcase(os.path.abspath(path))
    return path_key == root_key or path_key.startswith(root_key + os.sep)


def _place_expert(experts: Path, source: Path, binary: Path) -> dict:
    if _under(experts, source):
        rel = Path(os.path.relpath(source, experts)).with_suffix(".ex5")
        return {"expert": str(rel), "copied": False}
    experts.mkdir(parents=True, exist_ok=True)
    target = experts / binary.name
    shutil.copy2(binary, target)
    mq5_target = experts / source.name
    if os.path.normcase(os.path.abspath(source)) != os.path.normcase(os.path.abspath(mq5_target)):
        shutil.copy2(source, mq5_target)
    return {"expert": binary.name, "copied": True, "deployed_to": str(target)}


def _compile_brief(compiled: dict) -> dict:
    brief = {
        "ok": bool(compiled.get("ok")),
        "result_errors": compiled.get("result_errors"),
        "result_warnings": compiled.get("result_warnings"),
        "errors": (compiled.get("errors") or [])[:20],
        "warnings": (compiled.get("warnings") or [])[:20],
    }
    for key in ("error", "log_path"):
        if compiled.get(key):
            brief[key] = compiled[key]
    return brief


def _smoke_runs(config: str, timeout_sec: int) -> tuple[dict, dict, int]:
    run = run_backtest(config, timeout_sec=timeout_sec)
    scanned = _scan_log_file(run.get("tester_log"))
    attempts = 1
    timed_out = "timeout" in str(run.get("error", ""))
    if _history_only(scanned) and not timed_out:
        run = run_backtest(config, timeout_sec=timeout_sec)
        scanned = _scan_log_file(run.get("tester_log"))
        attempts = 2
    return run, scanned, attempts


def smoke_test(
    source: str,
    symbol: str,
    period: str = "H1",
    date: str = "2024.01.02",
    model: str = "ohlc",
    timeout_sec: int = 180,
) -> dict:
    """Compile, run one day, and scan the tester journal.

    FromDate and ToDate are both `date`. A history-sync miss is retried once.
    Init failures, tester rejects, and runtime errors are not retried.
    """
    layout, error = _layout()
    if error:
        return {**error, "ok": False, "stage": "terminal"}
    src = Path(source)
    day = _normalize_date(date)
    compiled = compile_source(str(src))
    brief = _compile_brief(compiled)
    common = {
        "source": str(src),
        "symbol": symbol,
        "period": period,
        "date": day,
        "model": model,
        "compile": brief,
    }
    if not compiled.get("ok"):
        return {**common, "ok": False, "stage": "compile"}
    binary = src.with_suffix(".ex5")
    if not binary.is_file():
        return {
            **common,
            "ok": False,
            "stage": "compile",
            "error": f"no binary next to the source: {binary.name}",
        }
    placed = _place_expert(layout["experts"], src, binary)
    written = write_tester_config(
        expert=placed["expert"],
        symbol=symbol,
        period=period,
        model=model,
        date_from=day,
        date_to=day,
        source=str(src),
        optimization=0,
    )
    if not written.get("ok"):
        return {**common, **placed, "ok": False, "stage": "config", "error": written.get("error")}
    run, scanned, attempts = _smoke_runs(written["path"], timeout_sec)
    if run.get("error") or run.get("returncode") != 0:
        stage = "backtest"
    elif not scanned.get("ok"):
        stage = "journal"
    else:
        stage = "done"
    result = {
        **common,
        **placed,
        "ok": stage == "done",
        "stage": stage,
        "config": written["path"],
        "attempts": attempts,
        "backtest": {
            "returncode": run.get("returncode"),
            "error": run.get("error"),
            "report_path": run.get("report_path"),
            "tester_log": run.get("tester_log"),
            "snapshot_path": run.get("snapshot_path"),
        },
        "journal": scanned,
    }
    report_path = run.get("report_path")
    if report_path and Path(report_path).is_file():
        report = read_report(report_path)
        if "error" not in report:
            result["summary"] = report["summary"]
            result["trade_count"] = report["trade_count"]
            result["breakdown"] = report["breakdown"]
            result["trades_path"] = report["trades_path"]
    return result
