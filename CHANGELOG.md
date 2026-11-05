# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Pre-commit Ruff hook and pull request checks for conventional titles and a changelog line when `src/mt5_mcp` changes.
- Windows CI job so the junction guardrails run on every pull request.
- `history_coverage` reports the earliest and latest M1 bar already stored in the terminal's yearly `.hcc` files, without downloading.
- `extend_history` accepts `kind=m1` and downloads one UTC month of minute bars at a time until a month has no bars inside it.

### Fixed

- `copy_rates` drops bars outside the requested dates, so an older bar returned for a window before the cache is not saved as that window.
- `probe_history` no longer treats an intraday position search as the broker's oldest bar. M1 depth is `history_coverage` for bars already on disk and `extend_history` with `kind=m1` when older months should be downloaded.

## [0.1.0] - 2026-10-29

### Added

- Read-only MCP server for MetaTrader 5 research: terminal registry, broker profiles, bar and tick export, and Strategy Tester backtests.
- Agent resources and prompts for connecting, market data, account review, and backtests.
- Unit tests that mock the MetaTrader API. Coverage stays at or above 67%.
- GitHub Actions workflow for Ruff, pytest, and package build.
- Contributing guide, security policy, code of conduct, and pull request template.
- MIT license.

[Unreleased]: https://github.com/OWNER/mt5-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/OWNER/mt5-mcp/releases/tag/v0.1.0
