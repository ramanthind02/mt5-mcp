# Contributing to mt5-mcp

Thanks for helping keep this project accurate and safe to run next to a live terminal.

## Prerequisites

- Python 3.11 or higher
- [uv](https://docs.astral.sh/uv/)
- Git
- Windows and a MetaTrader 5 terminal, only if you are checking a live attach or a real backtest

Unit tests mock the terminal. They run without MetaTrader installed.

## Setup

```bash
git clone https://github.com/ramanthind02/mt5-mcp.git
cd mt5-mcp
uv sync --extra dev
uv run pytest
```

Copy [examples/terminals.toml](examples/terminals.toml) to `%USERPROFILE%\.mt5-mcp\` when you need a real terminal. Passwords belong in `%USERPROFILE%\.mt5-mcp\secrets.env`, using [examples/secrets.env.example](examples/secrets.env.example) as the shape. Leave those files out of git.

## Workflow

```bash
git checkout -b feat/short-name
```

Branch prefixes: `feat/`, `fix/`, `docs/`, `test/`, `ci/`.

Install the git hook once, after `uv sync`:

```bash
uv run pre-commit install
```

The hook runs Ruff on the files you commit. Before you open a pull request:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest
```

`uv run ruff format src tests` rewrites formatting. `uv run ruff check --fix src tests` applies the lint fixes Ruff can do on its own.

## What a change includes

A behavior change updates:

- Tests in `tests/`. New tests mock MetaTrader. They do not open a terminal.
- The tool, resource, or prompt section of [README.md](README.md) when that surface changes.
- The `## [Unreleased]` section of [CHANGELOG.md](CHANGELOG.md).
- [src/mt5_mcp/guides.py](src/mt5_mcp/guides.py) when the text an agent reads should change with the behavior.

This server stays read-only toward the trade server. A pull request that adds a tool to place, modify, or close a live order needs an explicit product decision in the pull request description. The default is to leave that capability out.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` a new capability
- `fix:` a bug fix
- `docs:` documentation only
- `test:` tests only
- `refactor:` a code change that keeps behavior
- `ci:` GitHub Actions or other automation
- `chore:` maintenance that is none of the above

```bash
git commit -m "fix: stop the history probe on an empty page"
```

## Pull requests

Push the branch and open a pull request. The title is a conventional commit, such as `fix: stop the history probe on an empty page`. Fill in the template: summary, type of change, tests, a changelog line, and the confirmation that no live-order tool was added.

A `feat` or `fix` that changes `src/mt5_mcp/` includes an `## [Unreleased]` line in [CHANGELOG.md](CHANGELOG.md). The maintenance workflow rejects the pull request without it.

CI runs Ruff, pytest with coverage, and a package build on every pull request to `main`. Coverage may not drop below the threshold in `pyproject.toml`.

## Reports

- Bugs and feature ideas use the issue templates.
- Account numbers, passwords, and profile JSON stay out of issues. See [SECURITY.md](SECURITY.md).
- Community expectations are in [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
