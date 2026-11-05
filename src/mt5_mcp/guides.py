"""Resources and prompts that tell an agent how to use this server.

The texts describe the tools that exist. This server does not place,
modify, or close live orders, so the guides stay on terminals, history,
account reads, and the Strategy Tester.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from .timeframes import TIMEFRAMES

_GETTING_STARTED = """\
# Getting started

This server is for research on a MetaTrader 5 terminal that is already installed on this Windows machine. It discovers terminals, reads the account, exports bars and ticks, and backtests an Expert Advisor that already exists.

It does not place, modify, or close live orders. There is no login tool. Log in from the terminal window.

## First session

1. `list_terminals`. Pass `include_discovered=true` when the registry is empty or you need an install that is not registered yet.
2. `launch_terminal` with a registry id, a discovered id, or a path to `terminal64.exe`. If that executable is already running, the tool leaves it alone.
3. Log in from the MetaTrader window if the chart is not already on the account you want.
4. `use_terminal` with the registry id. This attaches the Python API. It never sends a login or a password. If the open account is not the one named in the registry, switch accounts in the window and call `use_terminal` again.
5. `terminal_status` to confirm build, company, server clock, and whether the account is demo or real.

Market data, `account_health`, and the tester all require that attachment. `disconnect` drops the API connection and leaves the window open.

## Where to read next

- `mt5://terminals` — registry, launch, and attach
- `mt5://market_data_guide` — symbols, bars, ticks
- `mt5://account_guide` — balance, positions, and pending orders, read-only
- `mt5://backtest_guide` — compile and run the Strategy Tester
- `mt5://timeframes` — bar-size names
- `mt5://tester_models` — tick model names

Config and exported files live under `%USERPROFILE%\\.mt5-mcp`, or `MT5_MCP_HOME` when that variable is set. They are not part of the repository.
"""

_TERMINALS = """\
# Terminals

The registry is `terminals.toml` in the server home directory. `add_terminal` and `remove_terminal` edit that file only. They do not copy, create, or delete a MetaTrader install.

`list_terminals` returns the registry. Password values are never included. `include_discovered=true` also lists installs found under `%APPDATA%\\MetaQuotes\\Terminal` that are not registered yet.

`launch_terminal` starts any install, registered or not. Pass a registry id, a discovered id, or a full path. A `terminal64.exe` that is already running is left alone.

The registry path is the launch path. MetaTrader picks the data folder from that path, not from the directory a junction points at. A junction and its target are two data folders and one program: starting the junction while the real terminal is open only focuses that window. `add_terminal`, `launch_terminal`, and `use_terminal` refuse a junction. Register `terminal64.exe` inside the real install directory. A separate tester copy has to be a real directory, not a junction to that install.

`use_terminal` attaches to the account that is already open. The server never logs in. If the open login does not match the registry, the tool stops and tells you to switch accounts in the window.

`terminal_status` reports the active terminal: build, company, server clock, timezone offset, login, and demo or real. Bar and tick timestamps use the broker chart clock stored as a Unix time. The offset is that clock minus UTC, rounded to 15 minutes. A UTC broker reports 0.

`disconnect` drops the Python API connection. The terminal window stays open.

Call `use_terminal` before market data, account reads, or a backtest.
"""

_MARKET_DATA = """\
# Market data

Call `use_terminal` first. History tools write files and return a short preview, not the whole series.

## Symbols

- `list_symbols` lists names. `group` is a mask such as `*USD*`. `limit=0` returns every name. The default limit is 500.
- `symbol_info` returns the full symbol record, the latest tick, and weekday sessions.
- `symbol_tick` returns bid, ask, last, and time.
- `select_symbol` adds or removes a symbol in Market Watch so the terminal can download history. `copy_rates` and `copy_ticks` select the symbol themselves.

`capture_profile` writes every symbol on the broker, with the full symbol record and weekday sessions, to `profiles/<id>.json`. The tool result is a summary and a path. `read_profile` returns that summary, or one symbol when `symbol` is set. `list_profiles` lists saved files.

`compare_profiles` diffs two or more of those files. Pass ids from `list_profiles`. It compares spread, the floating-spread flag, swap long and short, swap mode, stops level, contract size, filling mode, and session hours. Symbol names must match exactly. Unchanged symbols are counted and left out of the diff. Pass `symbols` when you only care about the instruments in a backtest.

## Bars

`copy_rates(symbol, timeframe="H1", date_from, date_to, save_to, format="csv")`.

Pass timeframe as a name from `mt5://timeframes`, such as `M15`, `H1`, or `D1`. Omit both dates to page until the terminal stops returning bars. Dates accept `2024.01.01`, `2024.01.01 00:00:00`, `2024-01-01`, and `2024-01-01T00:00:00`.

The default file is `data/<terminal>/rates/<TIMEFRAME>/<SYMBOL>.csv` with columns `time,open,high,low,close,tick_volume,spread,real_volume`. `format` is `csv` or `parquet`. Parquet needs the optional `parquet` extra.

`probe_history` finds the oldest bar the terminal has. Omit `symbol` to survey every symbol, or pass a `group` mask. Daily files use the same rates path, and `survey.json` in that folder records the bar count and date range. The tool result is the summary, not the bars. `save` defaults to true.

## Ticks

`copy_ticks` writes ticks to `data/ticks/<terminal>/<SYMBOL>.csv` unless `save_to` is set. Pass `date_from` and `date_to`, or set `all_history=true` for an unbounded download. Unbounded tick history is large, so `all_history` is required when both dates are omitted. The download uses every stored tick flag.

Daily bars can be probed with `copy_rates_from_pos`. Ticks cannot. `copy_ticks_from` and `CopyTicks` have no position index. The first tick in the array is only the first tick at or after the start time you passed. A request for one tick from a date before the cached `.tkc` months still makes the terminal download that gap, and the call blocks until the download finishes. `history_coverage` reports the months already stored and does not find older ones. `extend_history` with `kind=ticks` downloads one UTC month at a time, oldest cached month first, and stops when a month comes back empty. That empty month is the broker limit.
"""

_ACCOUNT = """\
# Account

`account_health`, `positions`, and `orders` are read-only. None of them can send, modify, or close a trade.

Call `use_terminal` first.

`account_health` returns balance, equity, margin, free margin, margin level, floating profit, whether trading is allowed, the open positions, and the pending orders.

`positions` lists open positions. `orders` lists pending orders. Each call returns a count and the records.

These reads describe the account that is open in the terminal. They are not a substitute for a Strategy Tester report. Use `mt5://backtest_guide` for historical EA results.
"""

_BACKTEST = """\
# Strategy Tester

The tester runs an Expert Advisor that already exists under the active terminal's `MQL5/Experts` folder. `run_backtest` accepts only an ini whose sections are `[Tester]` and `[TesterInputs]`, with `ShutdownTerminal=1`. The launcher will not start a terminal onto a live chart. `Visual` is always 0.

Call `use_terminal` first. The terminal needs a data folder (`origin.txt`). Open it once if that folder is missing.

## Smoke test

`smoke_test(source, symbol, period="H1", date="2024.01.02", model="ohlc", timeout_sec=180)` compiles the `.mq5`, runs that single day, and reads the tester journal.

Blocking lines are init failures, `history not synchronized` (also `no history`), tester rejects (`rejected`, invalid volume, expert not found), and runtime errors such as zero divide. Notes include `test passed` and `final balance`. If the only hit is unsynced history, it runs that same day once more. `timeout_sec` defaults to 180, not the long backtest timeout.

Use this before a long `run_backtest`. Stop when `ok` is false and quote the blocking lines.

## One pass

1. `list_experts` lists `.mq5` sources and `.ex5` binaries. Use the `rel` path as `Expert`, for example `Examples\\Moving Average\\Moving Average.ex5`.
2. `compile` runs MetaEditor on one `.mq5` file and returns errors and warnings. `timeout_sec` defaults to 300. `smoke_test` compiles on its own, so a separate `compile` is optional when you are about to smoke-test.
3. `read_inputs` parses `input` declarations from a source file.
4. `write_tester_config` writes a headless ini. `model` is `every_tick`, `ohlc` (the default), `open_prices`, or `real_ticks`. See `mt5://tester_models`. `period` is a timeframe name. Dates look like `2024.01.01`. Pass `source` to seed `[TesterInputs]` from the EA, or pass `inputs` as a name-to-value map. Leave `optimization` at 0.
5. `run_backtest` runs `terminal64.exe /config` on that ini and waits until it exits. `timeout_sec` defaults to 1800. Before the launch it copies the expert `.mq5`, the `.mqh` files that expert includes, the matching `.ex5`, and the ini into `data/snapshots/<terminal>/<time>/`. The manifest records the tester inputs and, after the run, the report path. `list_snapshots` and `read_snapshot` open that manifest later.
6. `read_report` parses the HTML report. Omit `path` to use the newest report. The result is the summary, every closed trade (entry time, exit time, side, volume, prices, profit, comment, holding time), and a breakdown of long vs short expectancy, holding time, and entry hour. Entry hour uses the report clock. The same trades are written under `data/reports/<terminal>/`.
7. `compare_reports` diffs two reports field by field.
8. `tail_log` reads the tester journal (`mode="tester"`) or the terminal journal (`mode="journal"`).

`stop_backtest` kills only a tester process this server started.
"""

_TESTER_MODELS = """\
# Tester models

`write_tester_config` accepts these `model` names. The integer is what the ini stores.

| name | code | meaning |
| --- | --- | --- |
| every_tick | 0 | every tick generated from the bars |
| ohlc | 1 | OHLC prices on the selected timeframe. This is the default |
| open_prices | 2 | open prices only |
| real_ticks | 4 | real ticks from the broker history |

Leave `optimization` at 0. That is one backtest. Call `run_backtest`.

`run_backtest` refuses an ini that is not limited to `[Tester]` and `[TesterInputs]`, and it refuses `ShutdownTerminal` other than 1. `write_tester_config` always sets `ShutdownTerminal=1` and `Visual=0`.

`run_backtest` launches the registry path unchanged. It does not follow junctions. It refuses to start when that `terminal64.exe` is already running, or when the same program is already running from a different path, because MetaTrader would only focus the open window. The report path is the `Report` name from the ini. A missing file is an error. The newest HTML already in the data folder is not used.
"""


def _timeframes_text() -> str:
    lines = [
        "# Timeframes",
        "",
        "Pass these names to `copy_rates`, `probe_history`, and `write_tester_config`. "
        "The tools convert the name to the integer the terminal expects. "
        "Do not pass minute counts such as 60 for H1.",
        "",
        "| name | code |",
        "| --- | --- |",
    ]
    for name, code in TIMEFRAMES.items():
        lines.append(f"| {name} | {code} |")
    lines.append("")
    lines.append(
        "`copy_rates` stores daily and higher bars with a date, and intraday bars with a date and time. "
        "Both use the broker chart clock."
    )
    return "\n".join(lines) + "\n"


def register(mcp) -> None:
    """Attach the research guides and prompts to the server."""

    @mcp.resource(
        "mt5://getting_started",
        name="getting_started",
        title="Getting started",
        description="Basic workflow: find a terminal, attach, then read data or run a backtest.",
        mime_type="text/markdown",
    )
    def getting_started() -> str:
        return _GETTING_STARTED

    @mcp.resource(
        "mt5://terminals",
        name="terminals",
        title="Terminals",
        description="Registry, launch, and attach. The server never logs in.",
        mime_type="text/markdown",
    )
    def terminals() -> str:
        return _TERMINALS

    @mcp.resource(
        "mt5://market_data_guide",
        name="market_data_guide",
        title="Market data",
        description="Symbols, broker profiles, bar export, history probe, and tick export.",
        mime_type="text/markdown",
    )
    def market_data_guide() -> str:
        return _MARKET_DATA

    @mcp.resource(
        "mt5://account_guide",
        name="account_guide",
        title="Account",
        description="Read-only account health, open positions, and pending orders.",
        mime_type="text/markdown",
    )
    def account_guide() -> str:
        return _ACCOUNT

    @mcp.resource(
        "mt5://backtest_guide",
        name="backtest_guide",
        title="Strategy Tester",
        description="Compile an existing Expert Advisor and run a headless backtest.",
        mime_type="text/markdown",
    )
    def backtest_guide() -> str:
        return _BACKTEST

    @mcp.resource(
        "mt5://timeframes",
        name="timeframes",
        title="Timeframes",
        description="Bar-size names and the integer codes the terminal stores.",
        mime_type="text/markdown",
    )
    def timeframes() -> str:
        return _timeframes_text()

    @mcp.resource(
        "mt5://tester_models",
        name="tester_models",
        title="Tester models",
        description="Tick-model names for write_tester_config.",
        mime_type="text/markdown",
    )
    def tester_models() -> str:
        return _TESTER_MODELS

    @mcp.prompt(
        title="Connect a terminal",
        description="Attach to a MetaTrader terminal that is already logged in. Do not ask for a password.",
    )
    def connect_terminal(
        terminal_id: Annotated[
            str, Field(description="Registry id. Leave empty to list terminals first.")
        ] = "",
    ) -> str:
        target = terminal_id.strip() or "the terminal the user names"
        return (
            f"Connect this research server to {target}.\n\n"
            "Read mt5://getting_started and mt5://terminals.\n"
            "Call list_terminals. Use include_discovered=true if the id is not in the registry.\n"
            "Call launch_terminal if that terminal64.exe is not already running.\n"
            "Call use_terminal with the registry id. Never ask for a password and never invent a login call. "
            "If the open account does not match, tell the user to switch accounts in the MetaTrader window, "
            "then call use_terminal again.\n"
            "Finish with terminal_status and report the login, server, and whether the account is demo or real."
        )

    @mcp.prompt(
        title="Analyze market data",
        description="Export bars for one symbol and summarize the file, the range, and the latest tick.",
    )
    def analyze_market_data(
        symbol: Annotated[str, Field(description="Symbol name, for example EURUSD.")],
        timeframe: Annotated[str, Field(description="Bar size such as M15, H1, or D1.")] = "H1",
    ) -> str:
        return (
            f"Analyze {symbol} on {timeframe}.\n\n"
            "Read mt5://market_data_guide and mt5://timeframes. Call use_terminal first if terminal_status is not ready.\n"
            f"Call symbol_info and symbol_tick for {symbol}. "
            f"Call copy_rates with symbol={symbol!r} and timeframe={timeframe!r}. "
            "Omit the dates when the user wants the full stored history.\n"
            "Summarize digits, spread, contract size, session hours, the bid and ask, "
            "the file path, the bar count, and the time range. Quote the preview. Do not paste the whole file."
        )

    @mcp.prompt(
        title="Review the account",
        description="Read balance, margin, open positions, and pending orders. Do not send or change an order.",
    )
    def review_account() -> str:
        return (
            "Review the account that is open on the active terminal.\n\n"
            "Read mt5://account_guide. Call use_terminal first if needed.\n"
            "Call account_health, then positions and orders if the lists were truncated.\n"
            "Report balance, equity, free margin, margin level, floating profit, trade_allowed, "
            "and each open position and pending order.\n"
            "Do not try to close, modify, or send anything. This server cannot do that."
        )

    @mcp.prompt(
        title="Backtest an Expert Advisor",
        description="Compile an existing EA if needed, write a headless tester ini, run it, and read the report.",
    )
    def backtest_expert(
        expert: Annotated[
            str,
            Field(
                description="Expert path relative to the Experts folder, or a .mq5 path to compile."
            ),
        ],
        symbol: Annotated[str, Field(description="Symbol to test, for example EURUSD.")],
        period: Annotated[str, Field(description="Timeframe name such as H1.")] = "H1",
        date_from: Annotated[
            str, Field(description="Tester FromDate, for example 2024.01.01.")
        ] = "2024.01.01",
        date_to: Annotated[
            str, Field(description="Tester ToDate, for example 2024.12.31.")
        ] = "2024.12.31",
    ) -> str:
        return (
            f"Backtest {expert} on {symbol} {period} from {date_from} to {date_to}.\n\n"
            "Read mt5://backtest_guide and mt5://tester_models. Call use_terminal first if needed.\n"
            "Call list_experts. If the user gave a .mq5 source, call smoke_test with that source, "
            f"symbol={symbol!r}, period={period!r}, and a one-day date inside {date_from} to {date_to}. "
            "If smoke_test is not ok, stop and quote the blocking journal lines. Do not start the long test.\n"
            "Call read_inputs when a source path is available, then write_tester_config with that source "
            f"or an inputs map. Use symbol={symbol!r}, period={period!r}, "
            f"date_from={date_from!r}, date_to={date_to!r}, and model ohlc unless the user asked for another model. "
            "Leave optimization at 0.\n"
            "Call run_backtest on the ini path. If it times out, say so and offer tail_log. "
            "Do not call stop_backtest unless the user asks to cancel. "
            "Keep the snapshot_path from the result.\n"
            "Call read_report and summarize net profit, profit factor, drawdown, trade count, "
            "and the long and short expectancy from the breakdown. "
            "This is a Strategy Tester run. It is not a live order."
        )

    @mcp.prompt(
        title="Compare backtests",
        description="Diff two tester reports.",
    )
    def compare_backtests(
        baseline: Annotated[
            str,
            Field(description="Path to the baseline HTML report. Empty to use the newest report."),
        ] = "",
        candidate: Annotated[str, Field(description="Path to the candidate HTML report.")] = "",
    ) -> str:
        base = baseline.strip() or "the newest report from read_report"
        cand = candidate.strip() or "the report path the user provides"
        return (
            f"Compare tester reports. Baseline: {base}. Candidate: {cand}.\n\n"
            "Read mt5://backtest_guide. Call compare_reports with both paths. "
            "If a path was omitted, call read_report first and use the path it returns.\n"
            "Lead with the fields that changed: net profit, profit factor, drawdown, and trade count."
        )
