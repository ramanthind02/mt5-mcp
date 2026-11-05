"""Read-only market data. History is paged and written to disk."""

from __future__ import annotations

import csv
import json
import struct
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import data_dir, ensure_home
from .connection import import_mt5, require_ready, session, symbol_sessions
from .serialize import to_jsonable
from .timeframes import TIMEFRAME_SECONDS, parse_timeframe

RATES_CHUNK = 50_000
TICKS_CHUNK = 100_000
RATE_COLUMNS = ("time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume")
_DAILY_TIMEFRAMES = {"D1", "W1", "MN1"}
_DATE_FORMATS = (
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d %H:%M",
    "%Y.%m.%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
)


def parse_when(value: str) -> datetime:
    text = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"could not parse date {value!r}")


def _rows(block: Any) -> list[dict]:
    if block is None:
        return []
    names = getattr(getattr(block, "dtype", None), "names", None)
    if names:
        return [{name: to_jsonable(row[name]) for name in names} for row in block]
    rows: list[dict] = []
    for item in block:
        if isinstance(item, dict):
            rows.append({key: to_jsonable(value) for key, value in item.items()})
        elif hasattr(item, "_asdict"):
            rows.append(to_jsonable(item))
        else:
            rows.append({"value": to_jsonable(item)})
    return rows


def _fetch(fn, pause: float, attempts: int = 3):
    last = None
    for attempt in range(attempts):
        last = fn()
        if last is not None and len(last) > 0:
            return last
        if attempt + 1 < attempts and pause:
            time.sleep(pause)
    return last


def _row_time(row: dict) -> datetime | None:
    raw = row.get("time")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(int(raw), UTC).replace(tzinfo=None)
    if isinstance(raw, str):
        try:
            return parse_when(raw)
        except ValueError:
            return None
    return None


class _TableWriter:
    def __init__(self, path: Path, fmt: str):
        self.path = path
        self.fmt = fmt
        self._csv = None
        self._handle = None
        self._writer = None
        self._parquet = None
        self._fields: list[str] | None = None
        self.count = 0
        if fmt == "parquet":
            try:
                import pyarrow as pa
                import pyarrow.parquet as pq
            except ImportError as exc:
                raise RuntimeError(
                    "parquet output needs the pyarrow extra: uv sync --extra parquet"
                ) from exc
            self._pa = pa
            self._pq = pq
        elif fmt != "csv":
            raise RuntimeError(f"unsupported format {fmt!r}; use csv or parquet")

    def write(self, rows: list[dict]) -> None:
        if not rows:
            return
        if self.fmt == "csv":
            if self._handle is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._fields = list(rows[0].keys())
                self._handle = self.path.open("w", newline="", encoding="utf-8")
                self._writer = csv.DictWriter(
                    self._handle, fieldnames=self._fields, extrasaction="ignore"
                )
                self._writer.writeheader()
            assert self._writer is not None
            self._writer.writerows(rows)
        else:
            table = self._pa.Table.from_pylist(rows)
            if self._parquet is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._parquet = self._pq.ParquetWriter(self.path, table.schema)
            self._parquet.write_table(table)
        self.count += len(rows)

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
        if self._parquet is not None:
            self._parquet.close()


def list_symbols(group: str | None = None, limit: int = 500) -> dict:
    error = require_ready()
    if error:
        return error
    mt5 = import_mt5()
    found = mt5.symbols_get(group) if group else mt5.symbols_get()
    found = list(found or [])
    view = found if limit <= 0 else found[:limit]
    return {
        "total": len(found),
        "returned": len(view),
        "symbols": [
            {"name": getattr(item, "name", None), "path": getattr(item, "path", None)}
            for item in view
        ],
    }


def symbol_info(symbol: str) -> dict:
    error = require_ready()
    if error:
        return error
    mt5 = import_mt5()
    info = mt5.symbol_info(symbol)
    if info is None:
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
    if info is None:
        code, message = mt5.last_error()
        return {"error": f"symbol not found: {symbol}", "last_error": [code, message]}
    return {
        "symbol": to_jsonable(info),
        "tick": to_jsonable(mt5.symbol_info_tick(symbol)),
        "sessions": symbol_sessions(mt5, symbol),
    }


def symbol_tick(symbol: str) -> dict:
    error = require_ready()
    if error:
        return error
    mt5 = import_mt5()
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        code, message = mt5.last_error()
        return {"error": f"no tick for {symbol}", "last_error": [code, message]}
    return {"symbol": symbol, "tick": to_jsonable(tick)}


def select_symbol(symbol: str, visible: bool = True) -> dict:
    error = require_ready()
    if error:
        return error
    mt5 = import_mt5()
    ok = bool(mt5.symbol_select(symbol, visible))
    if not ok:
        code, message = mt5.last_error()
        return {"error": f"symbol_select failed for {symbol}", "last_error": [code, message]}
    return {"symbol": symbol, "visible": visible}


def _default_path(kind: str, symbol: str, suffix: str, timeframe: str | None = None) -> Path:
    ensure_home()
    terminal_id = session.record.id if session.record else "unknown"
    stem = f"{symbol}_{timeframe}" if timeframe else symbol
    return data_dir() / kind / terminal_id / f"{stem}.{suffix}"


def _safe_symbol(symbol: str) -> str:
    return "".join(ch if ch not in '<>:"/\\|?*' else "_" for ch in symbol)


def rates_file(symbol: str, timeframe: str, fmt: str = "csv") -> Path:
    """data/<terminal>/rates/<TIMEFRAME>/<SYMBOL>.csv"""
    ensure_home()
    terminal_id = session.record.id if session.record else "unknown"
    return data_dir() / terminal_id / "rates" / timeframe.upper() / f"{_safe_symbol(symbol)}.{fmt}"


def _format_bar_time(row: dict, timeframe: str) -> str | None:
    moment = _row_time(row)
    if moment is None:
        return None
    if timeframe.upper() in _DAILY_TIMEFRAMES:
        return moment.date().isoformat()
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _standard_rate_rows(rows: list[dict], timeframe: str) -> list[dict]:
    standard = []
    for row in rows:
        item = {column: row.get(column) for column in RATE_COLUMNS}
        item["time"] = _format_bar_time(row, timeframe)
        standard.append(item)
    return standard


def copy_rates(
    symbol: str,
    timeframe: str = "H1",
    date_from: str | None = None,
    date_to: str | None = None,
    save_to: str | None = None,
    format: str = "csv",
    pause: float = 0.5,
    chunk: int = RATES_CHUNK,
) -> dict:
    """Page bars until the terminal runs out, and write them oldest-first."""
    error = require_ready()
    if error:
        return error
    try:
        tf = parse_timeframe(timeframe)
        start = parse_when(date_from) if date_from else None
        end = parse_when(date_to) if date_to else None
    except ValueError as exc:
        return {"error": str(exc)}

    mt5 = import_mt5()
    mt5.symbol_select(symbol, True)
    dest = Path(save_to) if save_to else rates_file(symbol, timeframe, format)
    writer = _TableWriter(dest, format)
    preview_head: list[dict] = []
    preview_tail: list[dict] = []
    first_time = None
    last_time = None
    try:
        if start is None and end is None:
            pos = 0
            windows: list[list[dict]] = []
            while True:
                block = _fetch(
                    lambda pos=pos: mt5.copy_rates_from_pos(symbol, tf, pos, chunk),
                    pause,
                )
                rows = _standard_rate_rows(_rows(block), timeframe)
                if not rows:
                    break
                windows.append(rows)
                if len(rows) < chunk:
                    break
                pos += len(rows)
            for rows in reversed(windows):
                writer.write(rows)
                if not preview_head:
                    preview_head = rows[:3]
                    first_time = _row_time(rows[0])
                preview_tail = rows[-3:]
                last_time = _row_time(rows[-1])
        else:
            cursor = start or datetime(1970, 1, 1)
            step = timedelta(seconds=TIMEFRAME_SECONDS.get(timeframe.upper(), 60))
            while True:
                block = _fetch(
                    lambda cursor=cursor: mt5.copy_rates_from(symbol, tf, cursor, chunk),
                    pause,
                )
                rows = _standard_rate_rows(_rows(block), timeframe)
                rows = [
                    row
                    for row in rows
                    if (moment := _row_time(row)) is not None
                    and (start is None or moment >= start)
                    and (end is None or moment <= end)
                ]
                if not rows:
                    break
                writer.write(rows)
                if not preview_head:
                    preview_head = rows[:3]
                    first_time = _row_time(rows[0])
                preview_tail = rows[-3:]
                last_time = _row_time(rows[-1])
                if len(rows) < chunk or last_time is None:
                    break
                nxt = last_time + step
                if nxt <= cursor:
                    break
                if end is not None and nxt > end:
                    break
                cursor = nxt
    except RuntimeError as exc:
        return {"error": str(exc)}
    finally:
        writer.close()

    if writer.count == 0:
        code, message = mt5.last_error()
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "count": 0,
            "path": str(dest),
            "last_error": [code, message],
            "error": "no bars returned",
        }
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "count": writer.count,
        "path": str(dest),
        "time_from": first_time.isoformat() if first_time else None,
        "time_to": last_time.isoformat() if last_time else None,
        "preview": preview_head + preview_tail,
    }


def _probe_daily(mt5, symbol: str, timeframe: str) -> tuple[list[dict], dict | None]:
    """Read every cached daily bar from the current bar backward.

    copy_rates_from(1970) makes the terminal try to download the entire
    archive and can block, so the survey uses the bars already available.
    """
    code = parse_timeframe(timeframe)
    rows: list[dict] = []
    pos = 0
    while pos < 200_000:
        block = mt5.copy_rates_from_pos(symbol, code, pos, 20_000)
        more = _standard_rate_rows(_rows(block), timeframe)
        if not more:
            break
        rows = more + rows
        if len(more) < 20_000:
            break
        pos += len(more)
    return rows, None


def probe_history(
    timeframe: str = "D1",
    symbol: str | None = None,
    group: str | None = None,
    limit: int = 0,
    save: bool = True,
) -> dict:
    """Find the oldest stored bar for each symbol and, by default, save that series.

    Daily, weekly, and monthly bars already available to the terminal are read
    with copy_rates_from_pos. This does not search M1. history_coverage reads
    the earliest M1 bar already on disk. extend_history kind=m1 downloads older
    months until the broker returns none inside the month.
    Files land in data/<terminal>/rates/<TIMEFRAME>/<SYMBOL>.csv.
    A survey.json next to them records bars, time_from, and time_to.
    """
    error = require_ready()
    if error:
        return error
    try:
        parse_timeframe(timeframe)
    except ValueError as exc:
        return {"error": str(exc)}
    if timeframe.upper() not in _DAILY_TIMEFRAMES:
        return {
            "error": (
                "probe_history reads cached daily, weekly, or monthly bars. "
                "history_coverage reads the earliest M1 bar already on disk and does not download. "
                "extend_history with kind=m1 downloads older M1 months until one comes back empty."
            )
        }

    mt5 = import_mt5()
    if symbol:
        names = [symbol]
    else:
        found = mt5.symbols_get(group) if group else mt5.symbols_get()
        names = [getattr(item, "name", None) for item in (found or [])]
        names = [name for name in names if name]
        if limit > 0:
            names = names[:limit]

    surveyed = []
    for name in names:
        rows, _ = _probe_daily(mt5, name, timeframe)
        if not rows:
            mt5.symbol_select(name, True)
            rows, _ = _probe_daily(mt5, name, timeframe)
        if not rows:
            err_code, message = mt5.last_error()
            entry = {
                "symbol": name,
                "timeframe": timeframe.upper(),
                "bars": 0,
                "error": "no bars",
                "last_error": [err_code, message],
            }
        else:
            entry = {
                "symbol": name,
                "timeframe": timeframe.upper(),
                "bars": len(rows),
                "time_from": rows[0]["time"],
                "time_to": rows[-1]["time"],
            }
            if save:
                dest = rates_file(name, timeframe, "csv")
                writer = _TableWriter(dest, "csv")
                try:
                    writer.write(rows)
                finally:
                    writer.close()
                entry["path"] = str(dest)
        surveyed.append(entry)

    with_bars = [item for item in surveyed if item.get("bars")]
    summary = {
        "terminal_id": session.record.id if session.record else None,
        "timeframe": timeframe.upper(),
        "symbols": len(surveyed),
        "with_bars": len(with_bars),
        "empty": len(surveyed) - len(with_bars),
        "probed_at": datetime.now(UTC).isoformat(),
    }
    if save or len(surveyed) != 1:
        ensure_home()
        survey_path = rates_file("_survey", timeframe, "json").with_name("survey.json")
        survey_path.parent.mkdir(parents=True, exist_ok=True)
        survey_path.write_text(
            json.dumps({**summary, "symbols": surveyed}, indent=2),
            encoding="utf-8",
        )
        summary["survey_path"] = str(survey_path)
    summary["preview"] = surveyed[:20]
    if len(surveyed) == 1:
        summary.update(surveyed[0])
    return summary


_HCC_INDEX_AT = 228
_HCC_CHUNK_HEADER = 129
_HCC_RECORD = 60
_HCC_INDEX = struct.Struct("<IIHII")
_HCC_TIME_MIN = 631_152_000
_HCC_TIME_MAX = 4_102_444_800


def _format_unix(stamp: int) -> str:
    return datetime.fromtimestamp(stamp, UTC).strftime("%Y-%m-%d %H:%M:%S")


def _valid_hcc_chunk(data: bytes, offset: int, size: int) -> bool:
    if size < _HCC_CHUNK_HEADER or (size - _HCC_CHUNK_HEADER) % _HCC_RECORD != 0:
        return False
    if offset < _HCC_INDEX_AT or offset + size > len(data):
        return False
    return struct.unpack_from("<H", data, offset)[0] == _HCC_CHUNK_HEADER


def _chunk_m1_bounds(data: bytes, offset: int, size: int) -> tuple[int | None, int | None, int]:
    """Earliest time, latest time, and M1 count in one day chunk.

    The first record of a chunk is often a daily summary, not an M1 bar.
    """
    count = (size - _HCC_CHUNK_HEADER) // _HCC_RECORD
    base = offset + _HCC_CHUNK_HEADER

    def time_at(index: int) -> int:
        return struct.unpack_from("<q", data, base + index * _HCC_RECORD)[0]

    def volume_at(index: int) -> int:
        return struct.unpack_from("<q", data, base + index * _HCC_RECORD + 40)[0]

    start = 0
    if count > 1:
        rest = sum(volume_at(index) for index in range(1, count))
        if time_at(0) >= time_at(1) or (rest > 0 and volume_at(0) >= 0.9 * rest):
            start = 1
    times = [
        stamp
        for index in range(start, count)
        if _HCC_TIME_MIN < (stamp := time_at(index)) < _HCC_TIME_MAX
    ]
    if not times:
        return None, None, 0
    return min(times), max(times), len(times)


def _hcc_file_span(path: Path) -> dict | None:
    try:
        data = path.read_bytes()
    except PermissionError:
        return {
            "file": path.name,
            "error": f"{path.name} is open in the terminal and could not be read",
        }
    except OSError as exc:
        return {"file": path.name, "error": str(exc)}
    earliest: int | None = None
    latest: int | None = None
    bars = 0
    days = 0
    earliest_offset: int | None = None
    for index in range(366):
        pos = _HCC_INDEX_AT + 18 * index
        if earliest_offset is not None and pos >= earliest_offset:
            break
        if pos + 18 > len(data):
            break
        _idx, _updated, _unknown, size, offset = _HCC_INDEX.unpack_from(data, pos)
        if not _valid_hcc_chunk(data, offset, size):
            if days:
                break
            continue
        if earliest_offset is None or offset < earliest_offset:
            earliest_offset = offset
        first, last, count = _chunk_m1_bounds(data, offset, size)
        days += 1
        bars += count
        if first is not None and (earliest is None or first < earliest):
            earliest = first
        if last is not None and (latest is None or last > latest):
            latest = last
    if not days:
        return None
    return {
        "bars": bars,
        "time_from": _format_unix(earliest) if earliest is not None else None,
        "time_to": _format_unix(latest) if latest is not None else None,
    }


def _cached_m1_span(bar_dir: Path) -> dict:
    """Earliest and latest M1 bar stored in yearly .hcc files. Does not download."""
    files = []
    if bar_dir.exists():
        files = sorted(path for path in bar_dir.glob("*.hcc") if path.stem.isdigit())
    span: dict = {"bars": 0, "time_from": None, "time_to": None, "files": len(files)}
    if not files:
        return span
    unread: list[str] = []
    parsed = 0
    for path in files:
        one = _hcc_file_span(path)
        if one is None or one.get("error"):
            unread.append(one.get("error") if one else path.name)
            continue
        parsed += 1
        span["bars"] += one["bars"]
        if one["time_from"] and (span["time_from"] is None or one["time_from"] < span["time_from"]):
            span["time_from"] = one["time_from"]
        if one["time_to"] and (span["time_to"] is None or one["time_to"] > span["time_to"]):
            span["time_to"] = one["time_to"]
    if unread:
        span["unread"] = unread
    if parsed == 0:
        span["error"] = "could not read M1 bars from the .hcc files"
    return span


def _m1_year_from_terminal(mt5, symbol: str, year: int) -> dict | None:
    """Read one cached year through the terminal when the .hcc file is locked.

    The request stays inside that year. It does not ask for the previous year.
    """
    mt5.symbol_select(symbol, True)
    start = datetime(year, 1, 1, tzinfo=UTC)
    end = datetime(year + 1, 1, 1, tzinfo=UTC)
    bars = mt5.copy_rates_range(symbol, parse_timeframe("M1"), start, end)
    rows = [
        row
        for row in _rows(bars)
        if (moment := _row_time(row)) is not None
        and start.replace(tzinfo=None) <= moment < end.replace(tzinfo=None)
    ]
    if not rows:
        return None
    rows.sort(key=lambda row: _row_time(row) or datetime.min)
    return {
        "bars": len(rows),
        "time_from": _format_bar_time(rows[0], "M1"),
        "time_to": _format_bar_time(rows[-1], "M1"),
    }


def _fill_locked_m1_years(span: dict, mt5, symbol: str) -> None:
    still_unread = []
    via_terminal: list[int] = []
    for item in span.get("unread") or []:
        text = str(item)
        if "open in the terminal" not in text:
            still_unread.append(item)
            continue
        year_text = text.split(".", 1)[0]
        if not year_text.isdigit():
            still_unread.append(item)
            continue
        extra = _m1_year_from_terminal(mt5, symbol, int(year_text))
        if not extra:
            still_unread.append(item)
            continue
        span["bars"] += extra["bars"]
        if extra["time_from"] and (
            span["time_from"] is None or extra["time_from"] < span["time_from"]
        ):
            span["time_from"] = extra["time_from"]
        if extra["time_to"] and (span["time_to"] is None or extra["time_to"] > span["time_to"]):
            span["time_to"] = extra["time_to"]
        via_terminal.append(int(year_text))
    if still_unread:
        span["unread"] = still_unread
    else:
        span.pop("unread", None)
    if via_terminal:
        span["read_via_terminal"] = via_terminal
    if span.get("bars"):
        span.pop("error", None)


def history_coverage(symbol: str, verify: bool = True) -> dict:
    """How far back stored bars and ticks go, without starting a history download.

    Depth comes from the terminal's own cache: yearly `.hcc` bar files and
    monthly `.tkc` tick files under `bases/<server>/`. The `m1` field is the earliest
    and latest minute bar already in those `.hcc` files. A missing earlier
    year has not been fetched, and this call does not request it. A year file
    the terminal has open is read back with one request bounded to that year.
    `extend_history` with `kind=m1` downloads older months.

    The official Python calls use a UTC window. `copy_ticks_from` on a date
    before the first `.tkc` file downloads the gap and does not return, so a
    live check is only made inside the cached span when verify is true: one
    month of daily bars at the start of the bar cache, and one day of ticks
    at the start of the tick cache.
    See https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksrange_py
    """
    error = require_ready()
    if error:
        return error
    mt5 = import_mt5()
    account = mt5.account_info()
    server = getattr(account, "server", None)
    data_dir = session.record.data_dir if session.record else None
    if not server or data_dir is None:
        return {"error": "connected terminal has no server or data folder"}
    base = Path(data_dir) / "bases" / str(server)
    tick_dir = base / "ticks" / symbol
    bar_dir = base / "history" / symbol
    tick_files = sorted(path.stem for path in tick_dir.glob("*.tkc")) if tick_dir.exists() else []
    bar_files = sorted(path.stem for path in bar_dir.glob("*.hcc")) if bar_dir.exists() else []
    result: dict = {
        "symbol": symbol,
        "server": server,
        "bar_years": [bar_files[0], bar_files[-1]] if bar_files else [],
        "bar_files": len(bar_files),
        "tick_months": [tick_files[0], tick_files[-1]] if tick_files else [],
        "tick_files": len(tick_files),
        "m1": _cached_m1_span(bar_dir),
    }
    _fill_locked_m1_years(result["m1"], mt5, symbol)
    if not verify:
        return result
    mt5.symbol_select(symbol, True)
    if bar_files:
        year = int(bar_files[0])
        start = datetime(year, 1, 1, tzinfo=UTC)
        end = datetime(year, 4, 1, tzinfo=UTC)
        bars = mt5.copy_rates_range(symbol, parse_timeframe("D1"), start, end)
        rows = _rows(bars)
        result["d1_sample"] = {
            "requested": [start.date().isoformat(), end.date().isoformat()],
            "bars": len(rows),
            "first": _format_bar_time(rows[0], "D1") if rows else None,
            "last": _format_bar_time(rows[-1], "D1") if rows else None,
        }
    if tick_files:
        month = tick_files[0]
        year, mon = int(month[:4]), int(month[4:6])
        start = datetime(year, mon, 1, tzinfo=UTC)
        if mon == 12:
            end = datetime(year + 1, 1, 1, tzinfo=UTC)
        else:
            end = datetime(year, mon + 1, 1, tzinfo=UTC)
        # One day, not the whole month. A month of EURUSD ticks is tens of thousands.
        day_end = start + timedelta(days=1)
        ticks = mt5.copy_ticks_range(symbol, start, day_end, mt5.COPY_TICKS_ALL)
        tick_rows = _rows(ticks)
        result["tick_sample"] = {
            "requested": [start.isoformat(), day_end.isoformat()],
            "ticks": len(tick_rows),
            "first": _format_bar_time(tick_rows[0], "M1") if tick_rows else None,
            "last_error": list(mt5.last_error()) if not tick_rows else None,
        }
    return result


def _month_window(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=UTC)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(year, month + 1, 1, tzinfo=UTC)
    return start, end


def _cache_edges(symbol: str) -> dict:
    covered = history_coverage(symbol, verify=False)
    if covered.get("error"):
        return covered
    return covered


def fetch_d1_year(symbol: str, year: int) -> dict:
    """Download one UTC calendar year of daily bars. An empty year is the broker limit."""
    error = require_ready()
    if error:
        return error
    mt5 = import_mt5()
    mt5.symbol_select(symbol, True)
    start = datetime(year, 1, 1, tzinfo=UTC)
    end = datetime(year + 1, 1, 1, tzinfo=UTC)
    bars = mt5.copy_rates_range(symbol, parse_timeframe("D1"), start, end)
    rows = [
        row
        for row in _rows(bars)
        if (moment := _row_time(row)) is not None
        and start.replace(tzinfo=None) <= moment < end.replace(tzinfo=None)
    ]
    count = len(rows)
    first = _format_bar_time(rows[0], "D1") if rows else None
    last = _format_bar_time(rows[-1], "D1") if rows else None
    return {
        "symbol": symbol,
        "year": year,
        "bars": count,
        "first": first,
        "last": last,
        "last_error": None if count else list(mt5.last_error()),
    }


def fetch_m1_month(symbol: str, year: int, month: int) -> dict:
    """Download one UTC month of M1 bars.

    Bars outside that month are dropped. The terminal returns the oldest bar
    it already has when the month is before cached history, and that bar is
    not an empty result unless it is filtered out.
    """
    error = require_ready()
    if error:
        return error
    mt5 = import_mt5()
    mt5.symbol_select(symbol, True)
    start, end = _month_window(year, month)
    bars = mt5.copy_rates_range(symbol, parse_timeframe("M1"), start, end)
    rows = [
        row
        for row in _rows(bars)
        if (moment := _row_time(row)) is not None
        and start.replace(tzinfo=None) <= moment < end.replace(tzinfo=None)
    ]
    count = len(rows)
    return {
        "symbol": symbol,
        "month": f"{year:04d}-{month:02d}",
        "bars": count,
        "first": _format_bar_time(rows[0], "M1") if rows else None,
        "last": _format_bar_time(rows[-1], "M1") if rows else None,
        "last_error": None if count else list(mt5.last_error()),
    }


def fetch_tick_month(symbol: str, year: int, month: int) -> dict:
    """Download one UTC month of ticks. An empty month is the broker limit."""
    error = require_ready()
    if error:
        return error
    mt5 = import_mt5()
    mt5.symbol_select(symbol, True)
    start, end = _month_window(year, month)
    ticks = mt5.copy_ticks_range(symbol, start, end, mt5.COPY_TICKS_ALL)
    rows = [
        row
        for row in _rows(ticks)
        if (moment := _row_time(row)) is not None
        and start.replace(tzinfo=None) <= moment < end.replace(tzinfo=None)
    ]
    count = len(rows)
    return {
        "symbol": symbol,
        "month": f"{year:04d}{month:02d}",
        "ticks": count,
        "last_error": None if count else list(mt5.last_error()),
    }


def _previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def extend_history(symbol: str, kind: str = "d1", max_steps: int = 80) -> dict:
    """Walk backward from the oldest cached chunk until one chunk is empty.

    kind is "d1" (one UTC year), "m1" (one UTC month of minute bars), or
    "ticks" (one UTC month). For m1, a bar returned from outside the requested
    month does not count. The first empty month is the broker limit.
    max_steps bounds one call. When the limit was not reached, call again.
    """
    edges = _cache_edges(symbol)
    if edges.get("error"):
        return edges
    kind = kind.lower()
    steps: list[dict] = []
    if kind == "d1":
        if edges["bar_years"]:
            year = int(edges["bar_years"][0]) - 1
        else:
            year = datetime.now(UTC).year
        for _ in range(max_steps):
            step = fetch_d1_year(symbol, year)
            steps.append(step)
            if not step.get("bars"):
                break
            year -= 1
    elif kind == "m1":
        cached = edges.get("m1") or {}
        if cached.get("error"):
            return {"error": cached["error"], "symbol": symbol}
        if cached.get("time_from"):
            moment = parse_when(cached["time_from"])
            year, month = _previous_month(moment.year, moment.month)
        else:
            now = datetime.now(UTC)
            year, month = now.year, now.month
        for _ in range(max_steps):
            step = fetch_m1_month(symbol, year, month)
            steps.append(step)
            if not step.get("bars"):
                break
            year, month = _previous_month(year, month)
    elif kind == "ticks":
        if edges["tick_months"]:
            oldest = edges["tick_months"][0]
            year, month = int(oldest[:4]), int(oldest[4:6])
            year, month = _previous_month(year, month)
        else:
            now = datetime.now(UTC)
            year, month = now.year, now.month
        for _ in range(max_steps):
            step = fetch_tick_month(symbol, year, month)
            steps.append(step)
            if not step.get("ticks"):
                break
            year, month = _previous_month(year, month)
    else:
        return {"error": "kind must be d1, m1, or ticks"}
    after = history_coverage(symbol, verify=False)
    downloaded = [step for step in steps if step.get("bars") or step.get("ticks")]
    reached = bool(steps) and not (steps[-1].get("bars") or steps[-1].get("ticks"))
    oldest_bar = downloaded[-1].get("first") if downloaded else None
    if oldest_bar is None and reached and kind == "m1":
        oldest_bar = (edges.get("m1") or {}).get("time_from")
    return {
        "symbol": symbol,
        "kind": kind,
        "before": {
            "bar_years": edges.get("bar_years"),
            "tick_months": edges.get("tick_months"),
            "m1": edges.get("m1"),
        },
        "after": {
            "bar_years": after.get("bar_years"),
            "tick_months": after.get("tick_months"),
            "m1": after.get("m1"),
        },
        "steps": len(steps),
        "reached_broker_limit": reached,
        "oldest_bar": oldest_bar,
        "stopped": steps[-1] if steps else None,
        "downloaded": downloaded,
    }


def copy_ticks(
    symbol: str,
    date_from: str | None = None,
    date_to: str | None = None,
    all_history: bool = False,
    save_to: str | None = None,
    format: str = "csv",
    pause: float = 0.5,
    chunk: int = TICKS_CHUNK,
) -> dict:
    """Page ticks forward from date_from. Unbounded pulls require all_history."""
    error = require_ready()
    if error:
        return error
    if date_from is None and date_to is None and not all_history:
        return {
            "error": "pass date_from and date_to, or set all_history=true to download every stored tick"
        }
    try:
        start = parse_when(date_from) if date_from else None
        end = parse_when(date_to) if date_to else None
    except ValueError as exc:
        return {"error": str(exc)}

    mt5 = import_mt5()
    mt5.symbol_select(symbol, True)
    if start is None:
        info = mt5.symbol_info(symbol)
        raw_start = getattr(info, "start_time", 0) if info else 0
        start = (
            datetime.fromtimestamp(int(raw_start), UTC).replace(tzinfo=None)
            if raw_start
            else datetime(2000, 1, 1)
        )

    flags = getattr(mt5, "COPY_TICKS_ALL", 0)
    dest = Path(save_to) if save_to else _default_path("ticks", symbol, format)
    writer = _TableWriter(dest, format)
    preview_head: list[dict] = []
    preview_tail: list[dict] = []
    first_time = None
    last_time = None
    cursor = start
    try:
        while True:
            block = _fetch(
                lambda cursor=cursor: mt5.copy_ticks_from(symbol, cursor, chunk, flags),
                pause,
            )
            rows = _rows(block)
            if end is not None:
                rows = [row for row in rows if (_row_time(row) or end) <= end]
            if not rows:
                break
            writer.write(rows)
            if not preview_head:
                preview_head = rows[:3]
                first_time = _row_time(rows[0])
            preview_tail = rows[-3:]
            last_time = _row_time(rows[-1])
            if last_time is None or len(rows) < chunk:
                break
            nxt = last_time + timedelta(milliseconds=1)
            if nxt <= cursor:
                break
            if end is not None and nxt > end:
                break
            cursor = nxt
    except RuntimeError as exc:
        return {"error": str(exc)}
    finally:
        writer.close()

    if writer.count == 0:
        code, message = mt5.last_error()
        return {
            "symbol": symbol,
            "count": 0,
            "path": str(dest),
            "last_error": [code, message],
            "error": "no ticks returned",
        }
    return {
        "symbol": symbol,
        "count": writer.count,
        "path": str(dest),
        "time_from": first_time.isoformat() if first_time else None,
        "time_to": last_time.isoformat() if last_time else None,
        "preview": preview_head + preview_tail,
    }
