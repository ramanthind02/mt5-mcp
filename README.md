# mt5-mcp

[![CI](https://github.com/OWNER/mt5-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/mt5-mcp/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![codecov](https://codecov.io/gh/OWNER/mt5-mcp/branch/main/graph/badge.svg)](https://codecov.io/gh/OWNER/mt5-mcp)

**A research desk for MetaTrader 5, driven in plain language.**

Tell Cursor, Claude, or Codex to attach to the terminal you already have open, save the broker, export the history, and backtest an Expert Advisor that is already on disk. The server runs on Windows, next to MetaTrader, and speaks [Model Context Protocol](https://modelcontextprotocol.io) over stdio.

> "Attach to the demo account that is already open."
>
> "Save every symbol on this broker, then tell me how Darwinex differs from the other profile."
>
> "How far back does EURUSD go, and write the daily bars to a file."
>
> "Smoke-test this EA on one day. If the journal is clean, run 2024 and read the drawdown."

Live orders stay in the MetaTrader window. You log in there. The agent gets the research.

```
You  →  Cursor / Claude / Codex  →  mt5-mcp  →  the terminal that is already open
                                        │
                                        ├─ profiles/<id>.json
                                        ├─ data/.../rates and ticks
                                        └─ Strategy Tester report + source snapshot
```

Badge links use `OWNER/mt5-mcp`. Swap in your GitHub account when the repository is public. The coverage badge fills in after you connect [Codecov](https://about.codecov.io/) and set `CODECOV_TOKEN`. Until then, open the CI job summary.

## Contents

- [What it will do for you](#what-it-will-do-for-you)
- [Who it is for](#who-it-is-for)
- [Guardrails](#guardrails)
- [Prerequisites](#prerequisites)
- [Quick start](#quick-start)
- [A full session](#a-full-session)
- [Tools](#tools)
- [Resources and prompts](#resources-and-prompts)
- [Where your files go](#where-your-files-go)
- [Project layout](#project-layout)
- [Development](#development)
- [Acknowledgements](#acknowledgements)
- [License](#license)

## What it will do for you

| | |
| --- | --- |
| **Several terminals, one registry** | Demo and live, or two brokers, listed by id. The same `terminal64.exe` can appear twice when two logins share an install. |
| **A broker, saved once** | `capture_profile` writes every symbol: the full `symbol_info` record and the weekday sessions. Compare two profiles on spread, swap, stops, contract size, filling mode, and hours. |
| **History you can actually keep** | Bars and ticks land in CSV (or Parquet). `history_coverage` reads the earliest M1 bar already on disk. `extend_history` with `kind=m1` downloads older months until the broker stops. |
| **Headless Strategy Tester** | Compile, smoke-test one day, write a tester ini, run it, parse the HTML report, and diff two runs. Each run keeps a snapshot of the EA sources. |
| **Account, read in place** | Balance, equity, margin, floating profit, open positions, pending orders. |
| **Agents that already know the workflow** | Resources and prompts ship with the server, so the model reads the guide before it improvises. |

## Who it is for

- **Researchers** who want a broker's symbol list and session hours in a file, then a summary in chat.
- **EA authors** who want compile errors, a one-day smoke test, and a headless 2024 run without clicking through the tester.
- **People with more than one terminal** who are tired of telling an agent which `terminal64.exe` is which.
- **Anyone** who wants an agent on their market data and still wants the trade button to require their hands.

## Guardrails

This is research software. A backtest is a simulation on stored ticks and bars. It is not a forecast, and it is not a live fill.

- The tool list has no place, modify, or close order call.
- Login happens in the MetaTrader window. `use_terminal` attaches to that session. A mismatched login stops and asks you to switch accounts in the window.
- Passwords live in `%USERPROFILE%\.mt5-mcp\secrets.env`. They are never tool arguments and never come back in a tool result.
- `run_backtest` accepts an ini whose only sections are `[Tester]` and `[TesterInputs]`, with `ShutdownTerminal=1`. A junction, an already-open terminal, or the same program launched from another path is refused.
- `stop_backtest` stops a tester process this server started.

## Prerequisites

1. **Windows**, with the MetaTrader 5 terminal installed. The server has to run on that machine.
2. **Python 3.11+** and [uv](https://docs.astral.sh/uv/).
3. **An account that is already logged in** in the terminal you want to use. Demo or live.
4. **An MCP client**: Cursor, Claude Code, Claude Desktop, or Codex.

## Quick start

### 1. Install

```bash
git clone https://github.com/OWNER/mt5-mcp.git
cd mt5-mcp
uv sync
uv run mt5-mcp
```

`MetaTrader5` installs automatically on Windows. History files default to CSV. Add Parquet with `uv sync --extra parquet`.

### 2. Name your terminals

Config lives in `%USERPROFILE%\.mt5-mcp\` (override with `MT5_MCP_HOME`). Copy [examples/terminals.toml](examples/terminals.toml) there and edit the paths, logins, and servers.

```toml
[terminals.darwinex-demo]
path = 'C:\Program Files\Darwinex MetaTrader 5\terminal64.exe'
login = 12345678
server = "Darwinex-Demo"
role = "demo"
enabled = true

[terminals.darwinex-live]
path = 'C:\Program Files\Darwinex MetaTrader 5\terminal64.exe'
login = 87654321
server = "Darwinex-Live"
role = "live"
enabled = true
```

The id in brackets is what you say to the agent. Passwords do not go in this file. If you want one available to the server, put it in `secrets.env` using [examples/secrets.env.example](examples/secrets.env.example). The name is the id, uppercased, with a `_PASSWORD` suffix: `darwinex-demo` becomes `DARWINEX_DEMO_PASSWORD`. Leave the line out and the server will simply require the account to be logged in already.

### 3. Log in from the MetaTrader window

Open the terminal, log in, and leave it up. Then ask the agent to attach. `launch_terminal` starts an install that is not running yet, and skips one whose `terminal64.exe` is already up.

### 4. Connect the agent

**Cursor.** `~/.cursor/mcp.json` or `.cursor/mcp.json` in the project. The same JSON works for Claude Code in `.mcp.json`, and for Claude Desktop in `%APPDATA%\Claude\claude_desktop_config.json`.

```json
{
  "mcpServers": {
    "mt5": {
      "command": "uv",
      "args": ["run", "--directory", "C:\\path\\to\\MT5_MCP", "mt5-mcp"],
      "env": { "MT5_MCP_HOME": "C:\\Users\\you\\.mt5-mcp" }
    }
  }
}
```

Restart the client, then try: *"List my terminals and attach to darwinex-demo."*

**Codex.** Add this to `~/.codex/config.toml`. The full snippet is [examples/codex.toml](examples/codex.toml).

```toml
[mcp_servers.mt5]
command = "uv"
args = ["run", "--directory", "C:\\path\\to\\MT5_MCP", "mt5-mcp"]

[mcp_servers.mt5.env]
MT5_MCP_HOME = "C:\\Users\\you\\.mt5-mcp"
```

## A full session

These are the words. The agent calls the tools. You read the files it writes.

### Attach

> List my terminals. If darwinex-demo is not running, launch it. Attach, then tell me the login, server, and whether the account is demo or real.

Behind that: `list_terminals` → `launch_terminal` if needed → `use_terminal` → `terminal_status`.

### Learn the broker, then pull history

> Capture the broker profile. Then probe daily history for EURUSD and the other majors, and summarize how far back each one goes.

`capture_profile` writes `profiles/<id>.json` with every symbol. `probe_history` writes daily bars to `data/<terminal>/rates/D1/<SYMBOL>.csv` and an index at `survey.json`. Ask for one symbol's file when you want the bars themselves:

> Export H1 EURUSD from 2024-01-01 through 2024-12-31 and quote the first and last bars.

`copy_rates` pages until the terminal runs out when you omit the dates. A date window keeps only bars inside that window. It is not a depth probe: a request before cached history comes back as the oldest bar the terminal already has, and that bar is discarded.

Two calls answer how far M1 goes. `history_coverage` reads the earliest minute already in the yearly `.hcc` files and does not download. `extend_history` with `kind=m1` downloads one UTC month at a time until a month contains no bars. That empty month is the broker limit. `max_steps` caps one call. Call it again when `reached_broker_limit` is false.

Ticks cannot be probed with a position index. `CopyTicks` / `copy_ticks_from` start at the date you pass in. A request for one tick from a date before the local cache still makes the terminal download the gap, and the call does not return until that download finishes. `history_coverage` reads the `.tkc` months already on disk and does not discover older months. `extend_history` with `kind=ticks` downloads one UTC month at a time from the oldest cached month backward and stops when a month comes back empty. That empty month is the broker limit. One month of XAUUSD on Darwinex-Demo was about 10 million ticks and about two and a half minutes.

### Backtest an EA you already wrote

> Compile Experts\MyEA.mq5. Smoke-test it on EURUSD H1 for 2024.01.02. If the journal is clean, run 2024.01.01 to 2024.12.31 with the OHLC model and read the report. Then compare it to the report from last week.

The path looks like this:

1. `compile` returns MetaEditor errors and warnings as structured lines.
2. `smoke_test` runs that single day and scans the tester journal. Init failures, missing history, tester rejects, and runtime errors stop the session. A history-sync miss is retried once.
3. `read_inputs` and `write_tester_config` build a headless ini. `ShutdownTerminal` is 1 and `Visual` is 0. Models are `every_tick`, `ohlc`, `open_prices`, or `real_ticks`.
4. `run_backtest` copies the EA sources and the ini into a snapshot, launches `terminal64.exe /config`, and waits.
5. `read_report` turns the HTML into a summary, every closed trade, and a long/short breakdown.
6. `compare_reports` diffs two of those reports. `list_snapshots` and `read_snapshot` show which sources produced a run.

`tail_log` follows the tester journal if a run is still going. `stop_backtest` cancels one this server started.

### Check the account in between

> Read account health, then list open positions and pending orders.

`account_health` returns balance, equity, margin, free margin, margin level, floating profit, and whether trading is allowed. `positions` and `orders` return the lists on their own.

## Tools

### Terminals

| Tool | What you get |
| --- | --- |
| `list_terminals` | The registry. `include_discovered=true` also lists installs that are not registered yet. |
| `add_terminal` | Adds an id, path, login, and server to the registry. It does not copy terminal files. |
| `remove_terminal` | Removes that registry entry. It does not delete the install. |
| `launch_terminal` | Starts any install, registered or not. Does nothing if that `terminal64.exe` is already running. |
| `use_terminal` | Attaches to the account already open in that window. |
| `terminal_status` | Build, company, server clock, timezone offset, login, and demo or real. |
| `disconnect` | Drops the Python API connection. The terminal window stays open. |

### Account

| Tool | What you get |
| --- | --- |
| `account_health` | Balance, equity, margin, free margin, margin level, floating profit, `trade_allowed`, positions, and pending orders. |
| `positions` | Open positions. |
| `orders` | Pending orders. |

### Broker profile

| Tool | What you get |
| --- | --- |
| `capture_profile` | Every symbol from `symbols_get()`, with `symbol_info` and weekday sessions, in `profiles/<id>.json`. |
| `read_profile` | The summary, or one symbol. |
| `list_profiles` | Saved profiles and how many symbols each one holds. |
| `compare_profiles` | A diff of two or more profiles. Unchanged symbols are counted and left out. |

### Market data

| Tool | What you get |
| --- | --- |
| `list_symbols` | Symbol names on the active terminal. `limit=0` returns every name. |
| `symbol_info` | The full `symbol_info` record, the latest tick, and weekday sessions. |
| `symbol_tick` | Latest bid, ask, last, and time. |
| `select_symbol` | Adds or removes a symbol in Market Watch so history can download. |
| `copy_rates` | Bars, paged until the terminal runs out, written to CSV or Parquet. Omit both dates for every bar the terminal still serves. A date window keeps only bars inside it. The result is a path, a count, a range, and a short preview. |
| `probe_history` | How far back daily, weekly, or monthly bars already go. Omit the symbol to survey every symbol, or pass a group mask. Daily bars go to `data/<terminal>/rates/D1/<SYMBOL>.csv` with a `survey.json` index. This does not measure M1. |
| `history_coverage` | Earliest and latest M1 bar already stored in the yearly `.hcc` files, plus cached bar years and tick months. Does not download. |
| `extend_history` | Downloads older history one chunk at a time. `kind=m1` is one UTC month of minute bars and stops when a month has no bars inside it. `kind=d1` is one UTC year. `kind=ticks` is one UTC month of ticks. `max_steps` caps one call. |
| `copy_ticks` | Ticks to a file for a date range. `all_history=true` is required when dates are omitted. A start date before the cached `.tkc` months blocks while the terminal downloads the gap. |

Bar sizes for `copy_rates`, `probe_history`, and the tester: `M1` `M2` `M3` `M4` `M5` `M6` `M10` `M12` `M15` `M20` `M30` `H1` `H2` `H3` `H4` `H6` `H8` `H12` `D1` `W1` `MN1`. The integer codes the terminal stores are in the `mt5://timeframes` resource.

### Backtests

| Tool | What you get |
| --- | --- |
| `list_experts` | `.mq5` sources and `.ex5` binaries under the active terminal's Experts folder. |
| `compile` | MetaEditor errors and warnings for one `.mq5` file. |
| `smoke_test` | Compile, run one day, scan the journal. `date` is both FromDate and ToDate. Timeout defaults to 180 seconds. |
| `read_inputs` | `input` declarations parsed from an EA source file. |
| `write_tester_config` | A headless `tester.ini`. Pass `source` to seed inputs from the EA, or pass an `inputs` map. |
| `run_backtest` | `terminal64.exe /config` on that ini. Stores a source snapshot first. Default timeout is 1800 seconds. |
| `read_report` | Summary, every closed trade, and a long/short breakdown. Omit the path to use the newest report. |
| `list_snapshots` | Snapshots written by `run_backtest`, newest first. |
| `read_snapshot` | One manifest: EA sources, tester inputs, and the report path. |
| `compare_reports` | Two HTML reports, field by field. |
| `tail_log` | Tester journal or terminal journal. `mode` is `tester` or `journal`. |
| `stop_backtest` | Stops a Strategy Tester process this server started. |

## Resources and prompts

Agents can read a resource without calling a tool. A prompt is a ready-made task.

| Resource | What it covers |
| --- | --- |
| `mt5://getting_started` | Find a terminal, attach, then read data or run a backtest |
| `mt5://terminals` | Registry, launch, and attach |
| `mt5://market_data_guide` | Symbols, profiles, bars, the history probe, and ticks |
| `mt5://account_guide` | Balance, positions, and pending orders |
| `mt5://backtest_guide` | Compile an existing EA and run the Strategy Tester |
| `mt5://timeframes` | Bar-size names and the integer codes the terminal stores |
| `mt5://tester_models` | Tick models for `write_tester_config` |

| Prompt | What it asks the agent to do |
| --- | --- |
| `connect_terminal` | List, launch if needed, and `use_terminal`. It does not ask for a password. |
| `analyze_market_data` | Export bars for one symbol and summarize the file, the range, and the latest tick. |
| `review_account` | Read health, positions, and orders. |
| `backtest_expert` | Smoke-test, write a headless ini, run it, and read the report. |
| `compare_backtests` | Diff two HTML reports, leading with net profit, profit factor, drawdown, and trade count. |

## Where your files go

Everything the server writes stays under the home directory. None of it belongs in git.

```
%USERPROFILE%\.mt5-mcp\
├── terminals.toml
├── secrets.env
├── profiles\
│   └── darwinex-demo.json
└── data\
    ├── <terminal>\rates\D1\
    │   ├── EURUSD.csv
    │   └── survey.json
    └── snapshots\<terminal>\<stamp>\
```

Set `MT5_MCP_HOME` when you want that tree somewhere else.

## Project layout

```
mt5-mcp/
├── src/mt5_mcp/          # server, tools, guides
│   ├── server.py         # tool registration
│   ├── guides.py         # resources and prompts
│   ├── registry.py       # terminal discovery and the registry
│   ├── connection.py     # attach to an open terminal
│   ├── account.py
│   ├── market.py         # symbols, bars, ticks, history probe
│   ├── profile.py
│   ├── tester.py         # compile, smoke test, Strategy Tester
│   ├── snapshot.py
│   └── parsers.py        # compile log and HTML report
├── tests/                # mocked unit tests
├── examples/             # mcp.json, codex.toml, terminals.toml
├── .github/              # CI, Dependabot, issue templates
├── AGENTS.md             # how later changes keep this repo in shape
├── CONTRIBUTING.md
├── CHANGELOG.md
└── pyproject.toml
```

## Development

```bash
uv sync --extra dev
uv run pre-commit install
uv run pytest
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
```

The tests mock the MetaTrader API. They do not open a terminal. Coverage of `src/mt5_mcp` stays at or above 67%. CI on GitHub runs Ruff, pytest on Python 3.11 and 3.13, and a package build. Pull requests also check the title and, for a `feat` or `fix` under `src/mt5_mcp/`, a changelog line.

Branches, commit messages, and the pull request checklist are in [CONTRIBUTING.md](CONTRIBUTING.md). Notable changes go in [CHANGELOG.md](CHANGELOG.md). Vulnerability reports follow [SECURITY.md](SECURITY.md).

## Acknowledgements

Terminal discovery, the compile-log parser, and the report parser are adapted from [PHUICMT/mcp-mt5](https://github.com/PHUICMT/mcp-mt5) (MIT). See [NOTICE](NOTICE).

MetaTrader 5 is a product of [MetaQuotes](https://www.metaquotes.net/).

## License

[MIT](LICENSE). Use it, fork it, and if it saves you an afternoon in the Strategy Tester, star the repository so the next person can find it.
