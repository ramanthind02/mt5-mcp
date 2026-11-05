"""MetaEditor compile logs, tester HTML reports, and journal lines.

Adapted from PHUICMT/mcp-mt5 (MIT). See NOTICE.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterator
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path


def read_text_auto(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    return raw.decode("utf-8", errors="replace")


_DIAG_RE = re.compile(
    r"^(?P<file>.*?)\((?P<line>\d+),(?P<col>\d+)\)\s*:\s*(?P<sev>error|warning)\s+(?P<code>\d+):\s*(?P<msg>.*)$"
)
_RESULT_RE = re.compile(r"Result:\s*(\d+)\s*errors?,\s*(\d+)\s*warnings?", re.IGNORECASE)


def parse_compile_log(text: str) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    result_errors = None
    result_warnings = None

    for line in text.splitlines():
        match = _DIAG_RE.match(line.strip())
        if match:
            item = {
                "file": match.group("file").strip(),
                "line": int(match.group("line")),
                "col": int(match.group("col")),
                "code": int(match.group("code")),
                "message": match.group("msg").strip(),
            }
            if match.group("sev") == "error":
                errors.append(item)
            else:
                warnings.append(item)
            continue
        result = _RESULT_RE.search(line)
        if result:
            result_errors = int(result.group(1))
            result_warnings = int(result.group(2))

    return {
        "errors": errors,
        "warnings": warnings,
        "result_errors": result_errors if result_errors is not None else len(errors),
        "result_warnings": result_warnings if result_warnings is not None else len(warnings),
        "ok": (result_errors == 0) if result_errors is not None else len(errors) == 0,
    }


class _ReportParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_cell = False
        self.row: list[str] = []
        self.rows: list[list[str]] = []
        self.cell_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        name = tag.lower()
        if name == "tr":
            self.row = []
        elif name in ("td", "th"):
            self.cell_buf = []
            self.in_cell = True

    def handle_endtag(self, tag: str):
        name = tag.lower()
        if name in ("td", "th"):
            self.row.append("".join(self.cell_buf).strip())
            self.in_cell = False
        elif name == "tr":
            if self.row:
                self.rows.append(self.row)
            self.row = []

    def handle_data(self, data: str):
        if self.in_cell:
            self.cell_buf.append(data)


_KV_KEYS = {
    "Total Net Profit": "net_profit",
    "Gross Profit": "gross_profit",
    "Gross Loss": "gross_loss",
    "Profit Factor": "profit_factor",
    "Expected Payoff": "expected_payoff",
    "Recovery Factor": "recovery_factor",
    "Sharpe Ratio": "sharpe_ratio",
    "Total Trades": "total_trades",
    "Short Trades (won %)": "short_trades_won_pct",
    "Long Trades (won %)": "long_trades_won_pct",
    "Profit Trades (% of total)": "profit_trades_pct",
    "Loss trades (% of total)": "loss_trades_pct",
    "Maximal Drawdown": "max_drawdown",
    "Balance Drawdown Maximal": "balance_drawdown_max",
    "Equity Drawdown Maximal": "equity_drawdown_max",
    "Initial Deposit": "initial_deposit",
    "Symbol": "symbol",
    "Period": "period",
    "Expert": "expert",
}


def parse_tester_report(html: str) -> dict:
    parser = _ReportParser()
    parser.feed(html)
    summary: dict = {}
    for row in parser.rows:
        if len(row) >= 2 and row[0].rstrip(":").strip() in _KV_KEYS:
            key = _KV_KEYS[row[0].rstrip(":").strip()]
            summary[key] = row[1].strip()
        elif len(row) >= 4:
            for index in range(0, len(row) - 1, 2):
                label = row[index].rstrip(":").strip()
                if label in _KV_KEYS:
                    summary[_KV_KEYS[label]] = row[index + 1].strip()

    trades = _closed_trades(_extract_deals(parser.rows))
    return {
        "summary": summary,
        "trades": trades,
        "breakdown": _trade_breakdown(trades),
    }


def iter_journal_lines(text: str) -> Iterator[dict]:
    full = re.compile(
        r"^(?P<ts>\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+(?P<src>[^\t]+?)\t(?P<msg>.*)$"
    )
    simple = re.compile(
        r"^(?P<ts>\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+(?P<msg>.*)$"
    )
    tester = re.compile(
        r"^(?P<code>[A-Z]{2})\t\d+\t(?P<ts>\d{2}:\d{2}:\d{2}(?:\.\d+)?)\t(?P<src>[^\t]*)\t(?P<msg>.*)$"
    )
    for line in text.splitlines():
        match = full.match(line)
        if match:
            yield {
                "ts": match.group("ts"),
                "source": match.group("src").strip(),
                "message": match.group("msg"),
            }
            continue
        match = tester.match(line)
        if match:
            yield {
                "ts": match.group("ts"),
                "source": match.group("src").strip(),
                "message": match.group("msg"),
            }
            continue
        match = simple.match(line)
        if match:
            yield {"ts": match.group("ts"), "source": "", "message": match.group("msg")}


_INPUT_RE = re.compile(
    r"^\s*(input|sinput|extern)\s+([A-Za-z_][\w:]*)\s+([A-Za-z_]\w*)\s*=\s*([^;]+?)\s*;\s*(?://\s*(.*))?$",
    re.MULTILINE,
)

_TF_ENUM_TO_CODE = {
    f"PERIOD_{name}": code
    for name, code in {
        "M1": 1,
        "M2": 2,
        "M3": 3,
        "M4": 4,
        "M5": 5,
        "M6": 6,
        "M10": 10,
        "M12": 12,
        "M15": 15,
        "M20": 20,
        "M30": 30,
        "H1": 16385,
        "H2": 16386,
        "H3": 16387,
        "H4": 16388,
        "H6": 16390,
        "H8": 16392,
        "H12": 16396,
        "D1": 16408,
        "W1": 32769,
        "MN1": 49153,
    }.items()
}


def extract_inputs(source: str | Path) -> list[dict]:
    path = Path(source)
    if not path.exists():
        return []
    text = read_text_auto(path)
    found: list[dict] = []
    for match in _INPUT_RE.finditer(text):
        found.append(
            {
                "kind": match.group(1),
                "type": match.group(2),
                "name": match.group(3),
                "default": match.group(4).strip(),
                "comment": (match.group(5) or "").strip(),
            }
        )
    return found


def gen_tester_inputs(source: str | Path) -> str:
    inputs = extract_inputs(source)
    if not inputs:
        return ""
    lines = ["[TesterInputs]"]
    for item in inputs:
        default = item["default"]
        if default in _TF_ENUM_TO_CODE:
            default = str(_TF_ENUM_TO_CODE[default])
        if default.lower() in ("true", "false"):
            default = default.lower()
        clean = default.strip().strip('"')
        lines.append(f"{item['name']}={clean}||{clean}||0||{clean}||N")
    return "\n".join(lines) + "\n"


_DEAL_FIELDS = (
    "time",
    "symbol",
    "type",
    "direction",
    "volume",
    "price",
    "commission",
    "swap",
    "profit",
    "comment",
)
_DIRECTIONS = {"in", "out", "in/out", "out by"}
_MT5_TIME = re.compile(r"(\d{4})[.\-/](\d{2})[.\-/](\d{2})[ T](\d{2}):(\d{2}):(\d{2})")


def _norm_cell(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.replace("\xa0", " ").replace(" ", "").strip()
    if text in {"", "-"}:
        return None
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif text.count(",") == 1 and "." not in text:
        text = text.replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _mt5_time(value: str) -> datetime | None:
    match = _MT5_TIME.search(value.strip())
    if not match:
        return None
    return datetime(*(int(part) for part in match.groups()))


def _side(type_name: str) -> str | None:
    name = type_name.strip().lower()
    if name.startswith("buy"):
        return "long"
    if name.startswith("sell"):
        return "short"
    return None


def _header_indexes(row: list[str]) -> dict[str, int] | None:
    labels = [_norm_cell(cell) for cell in row]
    if not {"time", "type", "direction"} <= set(labels):
        return None
    return {name: labels.index(name) for name in _DEAL_FIELDS if name in labels}


def _cells(row: list[str], indexes: dict[str, int]) -> dict[str, str]:
    return {name: row[index].strip() if index < len(row) else "" for name, index in indexes.items()}


def _direction(value: str) -> str | None:
    name = value.strip().lower()
    if name == "inout":
        return "in/out"
    if name in _DIRECTIONS:
        return name
    return None


def _deal_from_cells(cells: dict[str, str]) -> dict | None:
    type_name = cells.get("type", "")
    direction = _direction(cells.get("direction", ""))
    if _side(type_name) is None or direction is None:
        return None
    when = _mt5_time(cells.get("time", ""))
    volume_text = cells.get("volume", "").split("/")[0]
    return {
        "time": cells.get("time", "").strip(),
        "when": when,
        "symbol": cells.get("symbol", "").strip(),
        "type": type_name.strip().lower(),
        "direction": direction,
        "volume": _number(volume_text),
        "price": _number(cells.get("price", "")),
        "commission": _number(cells.get("commission", "")),
        "swap": _number(cells.get("swap", "")),
        "profit": _number(cells.get("profit", "")),
        "comment": cells.get("comment", "").strip(),
    }


def _extract_deals(rows: list[list[str]]) -> list[dict]:
    deals: list[dict] = []
    indexes: dict[str, int] | None = None
    saw_header = False
    for row in rows:
        if not row:
            continue
        header = _header_indexes(row)
        if header:
            saw_header = True
            indexes = header
            continue
        if indexes is None:
            continue
        if len(row) < 4:
            indexes = None
            continue
        deal = _deal_from_cells(_cells(row, indexes))
        if deal:
            deals.append(deal)
    if saw_header:
        return deals
    columns = (
        "time",
        "deal",
        "symbol",
        "type",
        "direction",
        "volume",
        "price",
        "order",
        "commission",
        "swap",
        "profit",
        "balance",
        "comment",
    )
    positional = {name: index for index, name in enumerate(columns)}
    for row in rows:
        if len(row) < 11:
            continue
        deal = _deal_from_cells(_cells(row, positional))
        if deal:
            deals.append(deal)
    return deals


def _money(left: float | None, right: float | None) -> float | None:
    if left is None and right is None:
        return None
    return round((left or 0) + (right or 0), 2)


def _as_trade(entry: dict | None, exit_deal: dict) -> dict:
    if entry:
        side = _side(entry["type"])
        entry_time = entry["time"]
        entry_when = entry["when"]
        entry_price = entry["price"]
        comment = exit_deal["comment"] or entry["comment"]
        commission = _money(entry["commission"], exit_deal["commission"])
        swap = _money(entry["swap"], exit_deal["swap"])
        symbol = exit_deal["symbol"] or entry["symbol"]
    else:
        exit_side = _side(exit_deal["type"])
        side = {"long": "short", "short": "long"}.get(exit_side or "")
        entry_time = None
        entry_when = None
        entry_price = None
        comment = exit_deal["comment"]
        commission = exit_deal["commission"]
        swap = exit_deal["swap"]
        symbol = exit_deal["symbol"]
    exit_when = exit_deal["when"]
    holding = None
    if entry_when and exit_when:
        seconds = int((exit_when - entry_when).total_seconds())
        if seconds >= 0:
            holding = seconds
    return {
        "entry_time": entry_time,
        "exit_time": exit_deal["time"],
        "symbol": symbol,
        "side": side,
        "volume": exit_deal["volume"],
        "entry_price": entry_price,
        "exit_price": exit_deal["price"],
        "profit": exit_deal["profit"],
        "commission": commission,
        "swap": swap,
        "comment": comment,
        "holding_seconds": holding,
        "entry_hour": entry_when.hour if entry_when else None,
    }


def _closed_trades(deals: list[dict]) -> list[dict]:
    """Pair deal rows into round trips. Profit stays on the closing deal."""
    open_lots: dict[str, deque] = {}
    trades: list[dict] = []
    for deal in deals:
        queue = open_lots.setdefault(deal["symbol"] or "*", deque())
        direction = deal["direction"]
        if direction in {"out", "out by", "in/out"}:
            entry = queue[0] if queue else None
            if (
                entry is not None
                and deal["volume"] is not None
                and entry.get("remaining") is not None
            ):
                entry["remaining"] = round(entry["remaining"] - deal["volume"], 8)
                if entry["remaining"] <= 1e-8:
                    queue.popleft()
            elif entry is not None:
                queue.popleft()
            trades.append(_as_trade(entry, deal))
        if direction in {"in", "in/out"}:
            lot = dict(deal)
            lot["remaining"] = deal["volume"] if deal["volume"] is not None else 0.0
            queue.append(lot)
    return trades


def _side_stats(rows: list[dict]) -> dict:
    profits = [row["profit"] for row in rows if isinstance(row.get("profit"), (int, float))]
    holds = [
        row["holding_seconds"]
        for row in rows
        if isinstance(row.get("holding_seconds"), (int, float))
    ]
    net = round(sum(profits), 2) if profits else 0.0
    return {
        "count": len(rows),
        "wins": sum(1 for value in profits if value > 0),
        "losses": sum(1 for value in profits if value < 0),
        "net_profit": net,
        "expectancy": round(net / len(profits), 2) if profits else None,
        "avg_holding_seconds": round(sum(holds) / len(holds)) if holds else None,
    }


def _trade_breakdown(trades: list[dict]) -> dict:
    """Entry hour is the hour on the report clock, which is the broker session clock."""
    by_hour = []
    for hour in range(24):
        rows = [row for row in trades if row.get("entry_hour") == hour]
        if not rows:
            continue
        profits = [row["profit"] for row in rows if isinstance(row.get("profit"), (int, float))]
        net = round(sum(profits), 2) if profits else 0.0
        by_hour.append(
            {
                "hour": hour,
                "count": len(rows),
                "net_profit": net,
                "expectancy": round(net / len(profits), 2) if profits else None,
            }
        )
    holds = sorted(
        row["holding_seconds"]
        for row in trades
        if isinstance(row.get("holding_seconds"), (int, float))
    )
    if not holds:
        median = None
    elif len(holds) % 2:
        median = holds[len(holds) // 2]
    else:
        mid = len(holds) // 2
        median = round((holds[mid - 1] + holds[mid]) / 2)
    return {
        "closed_trades": len(trades),
        "long": _side_stats([row for row in trades if row.get("side") == "long"]),
        "short": _side_stats([row for row in trades if row.get("side") == "short"]),
        "by_entry_hour": by_hour,
        "holding_seconds": {
            "min": holds[0] if holds else None,
            "max": holds[-1] if holds else None,
            "avg": round(sum(holds) / len(holds)) if holds else None,
            "median": median,
        },
    }
