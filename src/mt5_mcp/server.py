"""MCP tools for MetaTrader 5 research. No live order tools are registered."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from . import account, market, profile, registry, snapshot, tester
from .connection import disconnect as disconnect_session
from .connection import terminal_status as terminal_status_session
from .connection import use_terminal as use_terminal_session
from .guides import register as register_guides

mcp = MCPServer(
    "mt5",
    instructions=(
        "Research and backtesting only. This server cannot place, modify, or close live orders. "
        "list_terminals returns the registry. Pass include_discovered=true to see every install. "
        "add_terminal and remove_terminal change the registry only, not the files on disk. "
        "launch_terminal starts any install, registered or not, and skips one that is already running. "
        "Call use_terminal before market data, account_health, or a backtest. "
        "account_health, positions, and orders are read-only. "
        "capture_profile writes every symbol on the broker to a JSON file. "
        "copy_rates and copy_ticks write history to files and return a short preview. "
        "history_coverage reads the earliest M1 bar already on disk and does not download. "
        "extend_history with kind=m1 downloads older M1 one UTC month at a time until a month has no bars. "
        "The server never logs in. Log in from the terminal window. "
        "An existing Expert Advisor is compiled and tested as-is. "
        "compare_profiles diffs saved broker profiles. "
        "smoke_test compiles an EA and runs one day, then scans the journal. "
        "run_backtest stores a snapshot of the sources, the ini, and the report path. "
        "read_report returns every closed trade. "
        "Read the mt5:// resources before a new kind of task."
    ),
)

register_guides(mcp)

_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
_STOP = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)


@mcp.tool(annotations=_READ)
def list_terminals(include_discovered: bool = False) -> dict:
    """List terminals in the registry.

    Pass include_discovered=true to also list installs found under
    %APPDATA%\\MetaQuotes\\Terminal that are not in the registry yet.
    Password values are not included.
    """
    return registry.list_terminals(include_discovered=include_discovered)


@mcp.tool(annotations=_WRITE)
def add_terminal(
    terminal_id: str | None = None,
    path: str | None = None,
    name: str | None = None,
    role: str | None = None,
    login: int | None = None,
    server: str | None = None,
    enabled: bool = True,
) -> dict:
    """Add a discovered terminal, or a path, to the registry.

    This writes terminals.toml. It does not copy or create an install.
    name becomes the registry id when it is set.
    A junction to an install is refused. Register the real directory.
    """
    return registry.add_terminal(terminal_id, path, name, role, login, server, enabled)


@mcp.tool(annotations=_WRITE)
def remove_terminal(terminal_id: str) -> dict:
    """Remove a terminal from the registry. The install and data folder stay on disk."""
    return registry.remove_terminal(terminal_id)


@mcp.tool(annotations=_WRITE)
def launch_terminal(terminal_id: str | None = None, path: str | None = None) -> dict:
    """Start a MetaTrader terminal. Pass a registry id, a discovered id, or any terminal path.

    If that terminal64.exe is already running, this leaves it alone.
    A junction is refused, because MetaTrader would open a second data folder.
    """
    return registry.launch_terminal(terminal_id, path)


@mcp.tool(annotations=_WRITE)
def use_terminal(terminal_id: str) -> dict:
    """Attach the API to a terminal that is already logged in.

    This never sends a login or password. If the open account is not the one
    named in the registry, log in from the terminal window.
    """
    return use_terminal_session(terminal_id)


@mcp.tool(annotations=_READ)
def terminal_status() -> dict:
    """Report the active terminal: build, company, server clock, timezone offset, login, and demo or real."""
    return terminal_status_session()


@mcp.tool(name="disconnect", annotations=_WRITE)
def disconnect_terminal() -> dict:
    """Drop the Python API connection. The terminal window stays open."""
    return disconnect_session()


@mcp.tool(annotations=_READ)
def account_health() -> dict:
    """Read account health for the active terminal.

    Returns balance, equity, margin, free margin, margin level, floating profit,
    whether trading is allowed, open positions, and pending orders.
    This does not place, modify, or close anything.
    """
    return account.account_health()


@mcp.tool(annotations=_READ)
def positions() -> dict:
    """List open positions on the active account. Read-only."""
    return account.positions()


@mcp.tool(annotations=_READ)
def orders() -> dict:
    """List pending orders on the active account. Read-only."""
    return account.orders()


@mcp.tool(annotations=_WRITE)
def capture_profile() -> dict:
    """Write a JSON profile of the active broker: clock, account mode, and every symbol.

    Each symbol stores the full symbol_info record plus weekday sessions.
    The tool result is a summary and a file path, not the whole symbol list.
    """
    return profile.capture_profile()


@mcp.tool(annotations=_READ)
def read_profile(terminal_id: str | None = None, symbol: str | None = None) -> dict:
    """Read a saved broker profile. Without symbol, return the summary. With symbol, return that instrument."""
    return profile.read_profile(terminal_id, symbol)


@mcp.tool(annotations=_READ)
def list_profiles() -> dict:
    """List saved broker profiles and how many symbols each one contains."""
    return profile.list_profiles()


@mcp.tool(annotations=_READ)
def compare_profiles(terminal_ids: list[str], symbols: list[str] | None = None) -> dict:
    """Diff saved broker profiles on the fields that decide whether a backtest can transfer.

    Pass at least two ids from list_profiles. Symbol names must match exactly.
    Compared fields are spread, the floating-spread flag, swap, swap mode, stops level,
    contract size, filling mode, and session hours. Unchanged symbols are counted and omitted.
    """
    return profile.compare_profiles(terminal_ids, symbols)


@mcp.tool(annotations=_READ)
def list_symbols(group: str | None = None, limit: int = 500) -> dict:
    """List symbol names on the active terminal. limit=0 returns every name."""
    return market.list_symbols(group, limit)


@mcp.tool(annotations=_READ)
def symbol_info(symbol: str) -> dict:
    """Return the full symbol_info record, the latest tick, and weekday sessions."""
    return market.symbol_info(symbol)


@mcp.tool(annotations=_READ)
def symbol_tick(symbol: str) -> dict:
    """Return the latest bid, ask, last, and time for one symbol."""
    return market.symbol_tick(symbol)


@mcp.tool(annotations=_WRITE)
def select_symbol(symbol: str, visible: bool = True) -> dict:
    """Add or remove a symbol in Market Watch so history can download."""
    return market.select_symbol(symbol, visible)


@mcp.tool(annotations=_WRITE)
def copy_rates(
    symbol: str,
    timeframe: str = "H1",
    date_from: str | None = None,
    date_to: str | None = None,
    save_to: str | None = None,
    format: str = "csv",
) -> dict:
    """Download bars, paging until the terminal runs out, and write CSV or Parquet.

    Omit both dates to request every bar the terminal still serves. Pass both
    dates for one window. Bars outside that window are dropped, including the
    older bar the terminal returns when the window is before cached history.
    This is not how far back M1 goes. Use history_coverage for bars already on
    disk and extend_history with kind=m1 to download older months.
    The result is a file path, a count, a time range, and a short preview.
    """
    return market.copy_rates(symbol, timeframe, date_from, date_to, save_to, format)


@mcp.tool(annotations=_WRITE)
def probe_history(
    timeframe: str = "D1",
    symbol: str | None = None,
    group: str | None = None,
    limit: int = 0,
    save: bool = True,
) -> dict:
    """Probe how far back daily, weekly, or monthly bars already go, and save that series by default.

    Omit symbol to survey every symbol, or pass a group mask. Daily bars are
    stored as data/<terminal>/rates/D1/<SYMBOL>.csv with a survey.json index.
    The tool result is the summary, not the bars. This does not measure M1.
    history_coverage reads the earliest M1 bar already on disk.
    extend_history with kind=m1 downloads older months until one comes back empty.
    """
    return market.probe_history(timeframe, symbol, group, limit, save)


@mcp.tool(annotations=_READ)
def history_coverage(symbol: str, verify: bool = True) -> dict:
    """Report stored bar and tick depth for one symbol without downloading a gap.

    m1.time_from is the earliest minute bar already in the yearly .hcc files.
    A missing earlier year has not been fetched. A year file the terminal has
    open is read back with one request bounded to that year. This call does
    not request an earlier year. Bar years and tick months come from the same
    cache. When verify is true, one bounded UTC request is made inside that
    cache for daily bars and for ticks.
    """
    return market.history_coverage(symbol, verify)


@mcp.tool(annotations=_WRITE)
def extend_history(symbol: str, kind: str = "d1", max_steps: int = 80) -> dict:
    """Download older history one chunk at a time and stop at the first empty chunk.

    kind=d1 walks one UTC year backward from the oldest cached year.
    kind=m1 walks one UTC month backward from the month before the earliest
    cached M1 bar. A bar outside that month does not count. The first month
    with no bars inside it is the broker limit. max_steps is the number of
    months in one call. Call again when reached_broker_limit is false.
    kind=ticks walks one UTC month backward from the oldest cached tick month.
    """
    return market.extend_history(symbol, kind, max_steps)


@mcp.tool(annotations=_WRITE)
def copy_ticks(
    symbol: str,
    date_from: str | None = None,
    date_to: str | None = None,
    all_history: bool = False,
    save_to: str | None = None,
    format: str = "csv",
) -> dict:
    """Download ticks to a file. Set all_history=true when dates are omitted.

    Unbounded tick history is large, so all_history is required for that case.
    """
    return market.copy_ticks(symbol, date_from, date_to, all_history, save_to, format)


@mcp.tool(annotations=_READ)
def list_experts() -> dict:
    """List .mq5 sources and .ex5 binaries under the active terminal Experts folder."""
    return tester.list_experts()


@mcp.tool(annotations=_WRITE)
def compile(source: str, timeout_sec: int = 300) -> dict:
    """Compile one .mq5 file with MetaEditor and return structured errors and warnings."""
    return tester.compile_source(source, timeout_sec)


@mcp.tool(annotations=_WRITE)
def smoke_test(
    source: str,
    symbol: str,
    period: str = "H1",
    date: str = "2024.01.02",
    model: str = "ohlc",
    timeout_sec: int = 180,
) -> dict:
    """Compile an EA, run that one day, and scan the tester journal.

    date is both FromDate and ToDate. timeout_sec defaults to 180.
    Blocking lines are init failures, history not synchronized, tester rejects,
    and runtime errors. A history-sync miss is retried once.
    This does not place a live order.
    """
    return tester.smoke_test(source, symbol, period, date, model, timeout_sec)


@mcp.tool(annotations=_READ)
def read_inputs(source: str) -> dict:
    """Parse input declarations from an Expert Advisor source file."""
    return tester.read_inputs(source)


@mcp.tool(annotations=_WRITE)
def write_tester_config(
    expert: str,
    symbol: str,
    period: str = "H1",
    model: str = "ohlc",
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
    """Write a headless tester.ini. ShutdownTerminal is always 1 and Visual is always 0.

    model is every_tick, ohlc, open_prices, or real_ticks. Pass source to seed
    TesterInputs from the EA, or pass inputs as a name-to-value map.
    """
    return tester.write_tester_config(
        expert,
        symbol,
        period,
        model,
        date_from,
        date_to,
        deposit,
        currency,
        leverage,
        optimization,
        inputs,
        source,
        path,
    )


@mcp.tool(annotations=_WRITE)
def run_backtest(config: str, timeout_sec: int = 1800) -> dict:
    """Run terminal64.exe /config on a tester ini and wait until it exits.

    The ini must contain only [Tester] and [TesterInputs], with ShutdownTerminal=1.
    The launch path is used as written. A junction, an already-open terminal, or
    the same program running from another path is refused. The report is the
    file named by Report in the ini. Before the launch, the EA sources and the
    ini are copied into a snapshot. The manifest is updated with the report path.
    """
    return tester.run_backtest(config, timeout_sec)


@mcp.tool(annotations=_READ)
def read_report(path: str | None = None) -> dict:
    """Parse a tester HTML report into a summary, every closed trade, and a breakdown.

    Omit path to use the newest report for the active terminal. Each trade has
    entry and exit time, side, volume, prices, profit, and comment. The breakdown
    splits long and short expectancy, holding time, and entry hour on the report clock.
    """
    return tester.read_report(path)


@mcp.tool(annotations=_READ)
def list_snapshots(limit: int = 20) -> dict:
    """List source snapshots written by run_backtest, newest first."""
    return snapshot.list_snapshots(limit)


@mcp.tool(annotations=_READ)
def read_snapshot(path: str) -> dict:
    """Read one snapshot manifest: EA sources, tester inputs, and the report path."""
    return snapshot.read_snapshot(path)


@mcp.tool(annotations=_READ)
def compare_reports(baseline: str, candidate: str) -> dict:
    """Diff two tester reports field by field."""
    return tester.compare_reports(baseline, candidate)


@mcp.tool(annotations=_READ)
def tail_log(
    mode: str = "tester", lines: int = 100, date: str | None = None, structured: bool = True
) -> dict:
    """Tail the tester journal or the terminal journal. mode is tester or journal."""
    return tester.tail_log(mode, lines, date, structured)


@mcp.tool(annotations=_STOP)
def stop_backtest() -> dict:
    """Stop a Strategy Tester process this server started."""
    return tester.stop_backtest()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
