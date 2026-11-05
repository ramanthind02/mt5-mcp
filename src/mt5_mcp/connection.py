"""Attach to one MetaTrader 5 terminal and optionally log in.

The password is read from the secrets file inside this process. It is never
returned to the agent and is never a tool argument.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .paths import junction_launch_error
from .registry import TerminalRecord, get_terminal
from .serialize import to_jsonable

_TRADE_MODE = {0: "demo", 1: "contest", 2: "real"}


@dataclass
class Session:
    record: TerminalRecord | None = None
    ready: bool = False
    connected: bool = False


session = Session()


def import_mt5():
    import MetaTrader5 as mt5

    return mt5


def _account_view(info) -> dict:
    if info is None:
        return {}
    mode = getattr(info, "trade_mode", None)
    return {
        "login": getattr(info, "login", None),
        "server": getattr(info, "server", None),
        "currency": getattr(info, "currency", None),
        "leverage": getattr(info, "leverage", None),
        "trade_mode": _TRADE_MODE.get(mode, mode),
        "company": getattr(info, "company", None),
        "name": getattr(info, "name", None),
    }


def broker_time(mt5, symbol: str | None = None) -> dict:
    """Estimate the broker clock offset.

    MetaTrader's Python timestamps match the chart clock, which is broker
    server time packed as a Unix timestamp. The offset is that clock minus UTC,
    rounded to 15 minutes. A UTC broker reports 0.
    """
    utc = int(time.time())
    result: dict = {
        "utc_unix": utc,
        "quote_time_unix": None,
        "timezone_offset_min": None,
        "time_convention": "broker_clock_as_unix",
    }
    tick = None
    if symbol:
        tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        symbols = mt5.symbols_get() or []
        for item in symbols[:20]:
            name = getattr(item, "name", None)
            if not name:
                continue
            tick = mt5.symbol_info_tick(name)
            if tick is not None and getattr(tick, "time", None):
                break
    if tick is None or not getattr(tick, "time", None):
        return result
    quote_time = int(tick.time)
    result["quote_time_unix"] = quote_time
    rounded = int(round((quote_time - utc) / 60.0 / 15.0) * 15)
    if -14 * 60 <= rounded <= 14 * 60:
        result["timezone_offset_min"] = rounded
    return result


def symbol_sessions(mt5, symbol: str) -> dict:
    fn = getattr(mt5, "symbol_info_session", None)
    if fn is None:
        return {"day_index": "0=Sunday", "sessions": {}}
    days: dict[str, list] = {}
    for day in range(7):
        slots = []
        for index in range(8):
            try:
                row = fn(symbol, day, index)
            except TypeError:
                return {"error": "symbol_info_session signature is not supported"}
            if row is None:
                break
            slots.append(to_jsonable(row))
        days[str(day)] = slots
    return {"day_index": "0=Sunday", "sessions": days}


def _mismatch(record: TerminalRecord, info) -> dict | None:
    if record.login is None or info is None:
        return None
    current = getattr(info, "login", None)
    try:
        same_login = int(current) == int(record.login)
    except (TypeError, ValueError):
        same_login = current == record.login
    if same_login:
        return None
    return {
        "error": (
            f"Terminal is logged into {current}, but {record.id} expects {record.login}. "
            "Log in to the expected account in the terminal window."
        ),
        "account_login": current,
        "expected_login": record.login,
    }


def _result(record: TerminalRecord, mt5, logged_in_now: bool, error: str | None = None) -> dict:
    info = mt5.account_info() if session.connected else None
    view = _account_view(info)
    payload = {
        "id": record.id,
        "exe": str(record.exe) if record.exe else None,
        "data_dir": str(record.data_dir) if record.data_dir else None,
        "connected": session.connected,
        "ready": session.ready,
        "logged_in_now": logged_in_now,
        **view,
    }
    if error:
        payload["error"] = error
    return payload


def use_terminal(terminal_id: str, appdata: Path | None = None) -> dict:
    record = get_terminal(terminal_id, appdata=appdata)
    if record is None:
        return {"error": f"unknown terminal: {terminal_id}"}
    if not record.enabled:
        return {"error": f"terminal {terminal_id} is disabled in terminals.toml"}
    if record.exe is None:
        return {"error": f"terminal {terminal_id} has no terminal64.exe path"}
    blocked = junction_launch_error(record.exe)
    if blocked:
        return blocked

    mt5 = import_mt5()
    if session.connected:
        mt5.shutdown()
        session.connected = False
        session.ready = False

    session.record = record
    if not mt5.initialize(path=str(record.exe), timeout=60_000):
        code, message = mt5.last_error()
        return {
            "error": f"initialize failed: {code} {message}",
            "id": record.id,
            "exe": str(record.exe),
            "connected": False,
            "ready": False,
        }
    session.connected = True
    info = mt5.account_info()
    if info is None:
        session.ready = False
        payload = _result(record, mt5, False)
        payload["error"] = "No account is logged in. Log in from the terminal window."
        return payload
    mismatch = _mismatch(record, info)
    if mismatch:
        session.ready = False
        payload = _result(record, mt5, False)
        payload.update(mismatch)
        return payload
    session.ready = True
    return _result(record, mt5, False)


def disconnect() -> dict:
    if session.connected:
        try:
            import_mt5().shutdown()
        except Exception:
            pass
    previous = session.record.id if session.record else None
    session.record = None
    session.connected = False
    session.ready = False
    return {"disconnected": True, "id": previous}


def terminal_status() -> dict:
    record = session.record
    if record is None:
        return {
            "error": "no terminal selected; call use_terminal first",
            "connected": False,
            "ready": False,
        }
    if not session.connected:
        return {
            "error": "API is not connected",
            "id": record.id,
            "connected": False,
            "ready": False,
        }
    mt5 = import_mt5()
    info = mt5.terminal_info()
    account = mt5.account_info()
    version = mt5.version()
    clock = broker_time(mt5)
    return {
        "id": record.id,
        "connected": session.connected,
        "ready": session.ready,
        "exe": str(record.exe) if record.exe else None,
        "data_dir": str(record.data_dir) if record.data_dir else None,
        "build": getattr(info, "build", None) if info else None,
        "company": getattr(info, "company", None) if info else None,
        "terminal_name": getattr(info, "name", None) if info else None,
        "terminal_path": getattr(info, "path", None) if info else None,
        "version": to_jsonable(version),
        "server_time": datetime.fromtimestamp(clock["utc_unix"], UTC).isoformat(),
        **clock,
        **_account_view(account),
    }


def require_ready() -> dict | None:
    if session.record is None:
        return {"error": "no terminal selected; call use_terminal first"}
    if not session.connected or not session.ready:
        return {
            "error": f"terminal {session.record.id} is not ready; login does not match or the API is disconnected"
        }
    return None


def active_record() -> TerminalRecord | None:
    return session.record
