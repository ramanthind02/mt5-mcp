"""Convert MetaTrader named tuples and numpy values into JSON-safe objects."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    module = type(value).__module__
    if module == "numpy":
        if hasattr(value, "item"):
            try:
                return to_jsonable(value.item())
            except (ValueError, TypeError):
                pass
        if hasattr(value, "tolist"):
            return to_jsonable(value.tolist())
    if hasattr(value, "_asdict") and callable(value._asdict):
        return {str(key): to_jsonable(item) for key, item in value._asdict().items()}
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return str(value)
