# Security

## Supported use

mt5-mcp is a local research server. It runs on the same Windows machine as MetaTrader 5 and talks to agents over stdio. It cannot place, modify, or close a live order, and it does not send a login or password to the terminal.

## Reporting a vulnerability

Open a GitHub issue if you find a bug that exposes data or weakens the read-only boundary. Describe the impact and a safe way to reproduce it.

Leave these out of the issue, the pull request, and any attached log:

- Account numbers and investor passwords
- Server passwords and API keys
- Contents of `secrets.env`, `.env`, or a broker profile JSON
- Full paths that include your Windows username, when a shortened path is enough

If the report cannot be made safely in public, say that in the issue title and wait for a maintainer before adding detail.

## What stays off git

- `.env` and `secrets.env`
- `%USERPROFILE%\.mt5-mcp\` (or the directory in `MT5_MCP_HOME`), including profiles and exported history
- Terminal data directories and Strategy Tester reports from a live account

[examples/secrets.env.example](examples/secrets.env.example) shows the variable names only. Real values stay in the home directory.
