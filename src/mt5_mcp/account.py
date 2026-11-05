"""Read-only account health, open positions, and pending orders."""

from __future__ import annotations

from .connection import _TRADE_MODE, import_mt5, require_ready
from .serialize import to_jsonable


def _rows(items) -> list[dict]:
    return [to_jsonable(item) for item in (items or [])]


def account_health() -> dict:
    """One ping: account metrics, whether trading is allowed, positions, and pending orders."""
    error = require_ready()
    if error:
        return error
    mt5 = import_mt5()
    info = mt5.account_info()
    if info is None:
        code, message = mt5.last_error()
        return {"error": f"account_info failed: {code} {message}", "connected": True}
    terminal = mt5.terminal_info()
    positions = _rows(mt5.positions_get())
    orders = _rows(mt5.orders_get())
    mode = getattr(info, "trade_mode", None)
    return {
        "connected": True,
        "ready": True,
        "login": getattr(info, "login", None),
        "server": getattr(info, "server", None),
        "trade_mode": _TRADE_MODE.get(mode, mode),
        "currency": getattr(info, "currency", None),
        "leverage": getattr(info, "leverage", None),
        "balance": getattr(info, "balance", None),
        "equity": getattr(info, "equity", None),
        "margin": getattr(info, "margin", None),
        "margin_free": getattr(info, "margin_free", None),
        "margin_level": getattr(info, "margin_level", None),
        "profit": getattr(info, "profit", None),
        "trade_allowed": getattr(terminal, "trade_allowed", None) if terminal else None,
        "position_count": len(positions),
        "order_count": len(orders),
        "positions": positions,
        "orders": orders,
    }


def positions() -> dict:
    error = require_ready()
    if error:
        return error
    rows = _rows(import_mt5().positions_get())
    return {"count": len(rows), "positions": rows}


def orders() -> dict:
    error = require_ready()
    if error:
        return error
    rows = _rows(import_mt5().orders_get())
    return {"count": len(rows), "orders": rows}
