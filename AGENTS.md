# Agent notes

mt5-mcp is a read-only MetaTrader 5 research server: terminals, broker profiles, history export, and Strategy Tester backtests. It runs on Windows beside the terminals. Agents connect over stdio. This file is the maintenance contract for later changes.

Human-facing process lives in [CONTRIBUTING.md](CONTRIBUTING.md). Security boundaries live in [SECURITY.md](SECURITY.md). The public walkthrough lives in [README.md](README.md).

## Hard constraints

- Do not add a tool that places, modifies, or closes a live order. Live trading stays in the MetaTrader window.
- Do not add a login tool. The password is read from `%USERPROFILE%\.mt5-mcp\secrets.env` inside the process. It is never a tool argument and never part of a tool result.
- Do not commit `.env`, `secrets.env`, broker profiles, exported history, coverage files, or anything under `MT5_MCP_HOME`.
- Import `MetaTrader5` only inside `import_mt5()` in [src/mt5_mcp/connection.py](src/mt5_mcp/connection.py). A module-level import breaks Ubuntu CI, because the package is Windows-only.
- Compare launch paths with `launch_key()` in [src/mt5_mcp/paths.py](src/mt5_mcp/paths.py). `Path.resolve()` follows junctions, and MetaTrader treats a junction and its target as two data folders.
- Agent-facing failures return a dict with an `error` key. Tool handlers stay thin.
- Large history comes back as a file path, a count, a range, and a short preview. Do not return the whole series in the tool result.
- Tests mock the MetaTrader API. They do not open a terminal.

## Where code goes

| Concern | Module |
| --- | --- |
| Tool names, docstrings, annotations | [src/mt5_mcp/server.py](src/mt5_mcp/server.py) |
| Text the agent reads before a task | [src/mt5_mcp/guides.py](src/mt5_mcp/guides.py) and the `instructions` string on the server |
| Registry, discovery, launch | [src/mt5_mcp/registry.py](src/mt5_mcp/registry.py) |
| Attach, status, disconnect | [src/mt5_mcp/connection.py](src/mt5_mcp/connection.py) |
| Balance, positions, orders | [src/mt5_mcp/account.py](src/mt5_mcp/account.py) |
| Symbols, bars, ticks, history probe | [src/mt5_mcp/market.py](src/mt5_mcp/market.py) |
| Saved broker profiles | [src/mt5_mcp/profile.py](src/mt5_mcp/profile.py) |
| Compile, smoke test, Strategy Tester | [src/mt5_mcp/tester.py](src/mt5_mcp/tester.py) |
| Source snapshots for a run | [src/mt5_mcp/snapshot.py](src/mt5_mcp/snapshot.py) |
| Compile log and HTML report parsing | [src/mt5_mcp/parsers.py](src/mt5_mcp/parsers.py) |
| Home directory and `secrets.env` | [src/mt5_mcp/config.py](src/mt5_mcp/config.py) |

`server.py` registers tools and delegates. Parsing, paging, and tester rules stay in the module that owns them.

`scripts/` is for one-off runs against a live terminal on this machine. CI does not run it. A capability a user can ask for belongs in `src/mt5_mcp/` and a tool, with a mocked test.

## Adding or changing a tool

1. Put the behavior in the module above. Return a dict.
2. Register a thin wrapper in `server.py`. Pick one annotation:
   - `_READ` for a query
   - `_WRITE` for a file, a registry edit, a compile, or a tester launch
   - `_STOP` for `stop_backtest` only
3. The docstring is the tool description the model sees. State the arguments that change behavior (`all_history`, `include_discovered`, ini rules) in that docstring.
4. If the model needs the rule before the first call, add a sentence to the server `instructions` and a section in `guides.py`.
5. Add the name to `test_server_tool_surface` in [tests/test_mt5_mcp.py](tests/test_mt5_mcp.py). That set is the complete public tool list.
6. Update every README section that names the tool: the feature table, the session walkthrough, and the tool table.
7. Add a line under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md).

A new resource or prompt updates `guides.py`, the README tables, and the changelog. It still needs a test when the wording encodes a rule the agent must follow.

## Tests

- New tests live in `tests/` and mock MetaTrader. Pass fakes for `popen`, runners, and the `mt5` object. Do not call `import_mt5()` from a test.
- Junction and launch-path tests that create a real Windows link use `@pytest.mark.skipif(os.name != "nt", ...)`. Ubuntu CI skips those two. The `test-windows` job in `.github/workflows/ci.yml` runs the full suite, including `test_junction_and_target_keep_separate_data_folders` and `test_run_backtest_refuses_a_junction_install`. `test_use_terminal_never_logs_in` runs on both.
- Keep coverage of `src/mt5_mcp` at or above `--cov-fail-under` in [pyproject.toml](pyproject.toml). The floor is 67% today. Raise it when coverage goes up. Do not lower it to make a change pass.

## Docs that drift together

A behavior change touches all of these that mention it:

- [README.md](README.md) feature table, session, and tool table
- [src/mt5_mcp/guides.py](src/mt5_mcp/guides.py)
- the `instructions` string in `server.py`
- [CHANGELOG.md](CHANGELOG.md) `## [Unreleased]`

Examples in `examples/` stay free of real logins, passwords, and profile JSON. The public GitHub path is `ramanthind02/mt5-mcp`. Install instructions use `uvx --from git+https://github.com/ramanthind02/mt5-mcp`, so a client does not need a checkout path.

## Checks

```bash
uv sync --extra dev
uv run pre-commit install
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest
```

`pre-commit install` runs Ruff on each commit, using the Ruff version locked in `uv.lock`. Ruff formats `src/` and `tests/`. The formatter line length is 100. `E501` is ignored so long guide strings stay readable. If `uv sync` cannot replace `mt5-mcp.exe`, a server process is holding that file. Stop it, then sync again.

On a pull request, `.github/workflows/maintenance.yml` checks two things. The title is a conventional commit. A `feat` or `fix` that touches `src/mt5_mcp/` also updates `CHANGELOG.md`. `chore`, `ci`, `docs`, `test`, and `refactor` skip the changelog check. Dependabot is skipped and opens with a `chore(deps):` title.

## Releases

Cut a release only when asked. Move `## [Unreleased]` into a dated version section, and set the same version in [pyproject.toml](pyproject.toml) and `src/mt5_mcp/__init__.py`. Do not publish to PyPI unless asked.

## Commits and pull requests

Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `ci:`, `chore:`.

A pull request fills in `.github/pull_request_template.md`, including the line that no live-order tool was added.

## Layout

- `src/mt5_mcp/` server, tools, and guides
- `tests/test_mt5_mcp.py` mocked unit tests
- `examples/` client config and a sample terminal registry
- `scripts/` local live-terminal helpers, gitignored, so CI does not lint it and the test suite does not run it
- `.github/` CI, Dependabot, and issue templates
