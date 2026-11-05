"""Snapshot every symbol on the connected broker into a JSON profile."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime

from .config import ensure_home, profiles_dir
from .connection import (
    _account_view,
    broker_time,
    import_mt5,
    require_ready,
    session,
    symbol_sessions,
)
from .serialize import to_jsonable


def _write(terminal_id: str, document: dict) -> tuple[str, str]:
    ensure_home()
    root = profiles_dir()
    dest = root / f"{terminal_id}.json"
    text = json.dumps(document, indent=2)
    dest.write_text(text, encoding="utf-8")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    history_dir = root / terminal_id
    history_dir.mkdir(parents=True, exist_ok=True)
    history = history_dir / f"{stamp}.json"
    shutil.copy2(dest, history)
    return str(dest), str(history)


def capture_profile() -> dict:
    error = require_ready()
    if error:
        return error
    record = session.record
    assert record is not None
    mt5 = import_mt5()
    symbols = list(mt5.symbols_get() or [])
    specs: dict[str, dict] = {}
    for item in symbols:
        name = getattr(item, "name", None)
        if not name:
            continue
        info = mt5.symbol_info(name)
        if info is None:
            mt5.symbol_select(name, True)
            info = mt5.symbol_info(name)
        if info is None:
            code, message = mt5.last_error()
            specs[name] = {"error": "symbol_info returned nothing", "last_error": [code, message]}
            continue
        payload = to_jsonable(info)
        payload["sessions"] = symbol_sessions(mt5, name)
        specs[name] = payload

    terminal = mt5.terminal_info()
    account = _account_view(mt5.account_info())
    sample = next(iter(specs), None)
    clock = broker_time(mt5, sample if sample and "error" not in specs.get(sample, {}) else None)
    document = {
        "captured_at": datetime.now(UTC).isoformat(),
        "terminal_id": record.id,
        "terminal": {
            "company": getattr(terminal, "company", None) if terminal else None,
            "name": getattr(terminal, "name", None) if terminal else None,
            "build": getattr(terminal, "build", None) if terminal else None,
            "path": getattr(terminal, "path", None) if terminal else None,
            "data_path": getattr(terminal, "data_path", None) if terminal else None,
            "exe": str(record.exe) if record.exe else None,
        },
        "time": clock,
        "account": account,
        "symbol_count": len(specs),
        "symbols": specs,
    }
    path, history = _write(record.id, document)
    return {
        "path": path,
        "history_path": history,
        "symbol_count": len(specs),
        "timezone_offset_min": clock.get("timezone_offset_min"),
        "trade_mode": account.get("trade_mode"),
        "login": account.get("login"),
        "server": account.get("server"),
    }


def _load(terminal_id: str) -> tuple[dict, str] | dict:
    path = profiles_dir() / f"{terminal_id}.json"
    if not path.exists():
        return {"error": f"no profile for {terminal_id}", "path": str(path)}
    return json.loads(path.read_text(encoding="utf-8")), str(path)


def read_profile(terminal_id: str | None = None, symbol: str | None = None) -> dict:
    resolved = terminal_id or (session.record.id if session.record else None)
    if not resolved:
        return {"error": "pass terminal_id or call use_terminal"}
    loaded = _load(resolved)
    if isinstance(loaded, dict) and "error" in loaded and "symbols" not in loaded:
        return loaded
    document, path = loaded
    specs = document.get("symbols", {})
    if symbol:
        if symbol not in specs:
            return {
                "error": f"{symbol} is not in the profile",
                "symbol_count": document.get("symbol_count", 0),
            }
        return {"terminal_id": resolved, "path": path, "symbol": specs[symbol]}
    names = list(specs)
    return {
        "terminal_id": resolved,
        "path": path,
        "captured_at": document.get("captured_at"),
        "symbol_count": document.get("symbol_count", len(names)),
        "symbol_names_preview": names[:20],
        "time": document.get("time"),
        "account": document.get("account"),
        "terminal": document.get("terminal"),
    }


def list_profiles() -> dict:
    ensure_home()
    found = []
    for path in sorted(profiles_dir().glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        found.append(
            {
                "id": path.stem,
                "path": str(path),
                "captured_at": document.get("captured_at"),
                "symbol_count": document.get("symbol_count"),
            }
        )
    return {"count": len(found), "profiles": found}


_COMPARE_FIELDS = (
    ("spread", "spread", "number"),
    ("spread_float", "spread_float", "bool"),
    ("swap_long", "swap_long", "number"),
    ("swap_short", "swap_short", "number"),
    ("swap_mode", "swap_mode", "swap"),
    ("stops_level", "trade_stops_level", "number"),
    ("contract_size", "trade_contract_size", "number"),
    ("filling", "filling_mode", "filling"),
    ("sessions", "sessions", "sessions"),
)
_FILLING_BITS = ((1, "fok"), (2, "ioc"), (4, "boc"))
_SWAP_MODES = {
    0: "disabled",
    1: "points",
    2: "currency_symbol",
    3: "currency_margin",
    4: "currency_deposit",
    5: "interest_current",
    6: "interest_open",
    7: "reopen_current",
    8: "reopen_bid",
}
_DAY_NAMES = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
_DIFF_LIMIT = 80


def _names(value: str | list[str] | None) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        parts = [item.strip() for item in value.split(",") if item.strip()]
    else:
        parts = [str(item).strip() for item in value if str(item).strip()]
    return parts or None


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _equal(left, right) -> bool:
    if _is_number(left) and _is_number(right):
        return abs(float(left) - float(right)) < 1e-9
    return left == right


def _clock(seconds) -> str:
    minutes = int(float(seconds)) // 60
    hour, minute = divmod(minutes, 60)
    return f"{hour:02d}:{minute:02d}"


def _slot_text(slot) -> str:
    if isinstance(slot, dict):
        start = slot.get("from", slot.get("from_"))
        end = slot.get("to")
        if start is None or end is None:
            return json.dumps(slot, sort_keys=True)
        return f"{_clock(start)}-{_clock(end)}"
    if isinstance(slot, (list, tuple)) and len(slot) >= 2:
        return f"{_clock(slot[0])}-{_clock(slot[1])}"
    return str(slot)


def _session_text(payload) -> str:
    if not isinstance(payload, dict):
        return "" if payload in (None, "") else str(payload)
    days = payload.get("sessions") if isinstance(payload.get("sessions"), dict) else payload
    if not isinstance(days, dict):
        return ""
    parts = []
    for index, name in enumerate(_DAY_NAMES):
        slots = days.get(str(index), days.get(name, []))
        if not slots:
            continue
        parts.append(f"{name} {', '.join(_slot_text(slot) for slot in slots)}")
    return "; ".join(parts)


def _filling(value):
    if isinstance(value, bool) or value is None:
        return value
    try:
        bits = int(value)
    except (TypeError, ValueError):
        return {"raw": value, "modes": []}
    modes = [name for bit, name in _FILLING_BITS if bits & bit]
    return {"raw": bits, "modes": modes}


def _swap_mode(value):
    if isinstance(value, bool) or value is None:
        return value
    try:
        code = int(value)
    except (TypeError, ValueError):
        return {"raw": value, "name": None}
    return {"raw": code, "name": _SWAP_MODES.get(code)}


def _field_value(spec: dict, source_key: str, kind: str):
    if source_key not in spec and kind != "sessions":
        return None
    raw = spec.get(source_key)
    if kind == "sessions":
        return _session_text(raw or {})
    if kind == "filling":
        return _filling(raw)
    if kind == "swap":
        return _swap_mode(raw)
    if kind == "number" and isinstance(raw, str):
        try:
            return float(raw) if "." in raw else int(raw)
        except ValueError:
            return raw
    return raw


def compare_profiles(terminal_ids: list[str], symbols: list[str] | None = None) -> dict:
    """Diff broker profiles on the fields that decide whether a backtest can transfer."""
    ids: list[str] = []
    seen: set[str] = set()
    for raw in _names(terminal_ids) or []:
        if raw in seen:
            continue
        seen.add(raw)
        ids.append(raw)
    if len(ids) < 2:
        return {"error": "pass at least two profile ids from list_profiles"}
    loaded = []
    missing = []
    for terminal_id in ids:
        item = _load(terminal_id)
        if isinstance(item, dict) and "error" in item and "symbols" not in item:
            missing.append(terminal_id)
        else:
            loaded.append((terminal_id, item))
    if missing:
        return {"error": "no profile for " + ", ".join(missing)}
    specs = {terminal_id: document.get("symbols", {}) for terminal_id, (document, _path) in loaded}
    profiles = []
    for terminal_id, (document, path) in loaded:
        account = document.get("account") or {}
        profiles.append(
            {
                "id": terminal_id,
                "path": path,
                "captured_at": document.get("captured_at"),
                "server": account.get("server"),
                "symbol_count": document.get("symbol_count"),
            }
        )
    wanted = set(_names(symbols) or [])
    if wanted:
        names = sorted(wanted)
    else:
        names = sorted(set().union(*(set(table) for table in specs.values())))
    diffs = []
    unchanged = 0
    changed = 0
    absent = 0
    for name in names:
        present = [terminal_id for terminal_id in ids if name in specs[terminal_id]]
        if len(present) < len(ids):
            absent += 1
            diffs.append(
                {
                    "symbol": name,
                    "missing_from": [
                        terminal_id for terminal_id in ids if terminal_id not in present
                    ],
                }
            )
            continue
        fields = {}
        for label, source_key, kind in _COMPARE_FIELDS:
            values = {
                terminal_id: _field_value(specs[terminal_id][name], source_key, kind)
                for terminal_id in ids
            }
            sample = next(iter(values.values()))
            if all(_equal(sample, other) for other in values.values()):
                continue
            if len(ids) == 2 and _is_number(values[ids[0]]) and _is_number(values[ids[1]]):
                values = dict(values)
                values["delta"] = round(float(values[ids[1]]) - float(values[ids[0]]), 6)
            fields[label] = values
        if fields:
            changed += 1
            diffs.append({"symbol": name, "fields": fields})
        else:
            unchanged += 1
    return {
        "profiles": profiles,
        "compared_fields": [label for label, _source, _kind in _COMPARE_FIELDS],
        "common_symbols": unchanged + changed,
        "unchanged_symbols": unchanged,
        "changed_symbols": changed,
        "missing_symbols": absent,
        "diffs": diffs[:_DIFF_LIMIT],
        "diffs_omitted": max(0, len(diffs) - _DIFF_LIMIT),
    }
