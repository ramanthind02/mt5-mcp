"""Home directory, terminal toml, and secrets file.

Passwords stay in this module. Tool results must never include them.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path


def home() -> Path:
    override = os.environ.get("MT5_MCP_HOME")
    if override:
        return Path(override)
    return Path.home() / ".mt5-mcp"


def ensure_home() -> Path:
    root = home()
    (root / "profiles").mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(parents=True, exist_ok=True)
    return root


def terminals_path() -> Path:
    return home() / "terminals.toml"


def secrets_path() -> Path:
    return home() / "secrets.env"


def profiles_dir() -> Path:
    return home() / "profiles"


def data_dir() -> Path:
    return home() / "data"


def password_env_name(terminal_id: str) -> str:
    """darwinex-demo -> DARWINEX_DEMO_PASSWORD."""
    chars = [(ch if ch.isalnum() else "_") for ch in terminal_id.upper()]
    name = "".join(chars).strip("_")
    return f"{name}_PASSWORD"


def parse_env_file(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


def load_secrets() -> dict[str, str]:
    path = secrets_path()
    file_values: dict[str, str] = {}
    if path.exists():
        file_values = parse_env_file(path.read_text(encoding="utf-8"))
    # Process environment wins so an MCP client env block can override the file.
    merged = dict(file_values)
    for key in set(file_values) | {k for k in os.environ if k.endswith("_PASSWORD")}:
        if key in os.environ:
            merged[key] = os.environ[key]
    return merged


def _toml_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def save_terminal_config(terminals: dict[str, dict]) -> Path:
    """Write the registry. Passwords are never included."""
    ensure_home()
    lines = [
        "# Terminal registry. Passwords belong in secrets.env, not here.",
        "",
    ]
    for terminal_id, entry in terminals.items():
        lines.append(f"[terminals.{terminal_id}]")
        for key in ("path", "name", "role", "login", "server", "enabled", "password_env"):
            if key not in entry or entry[key] is None or entry[key] == "":
                continue
            value = entry[key]
            if isinstance(value, bool):
                lines.append(f"{key} = {'true' if value else 'false'}")
            elif key == "login":
                lines.append(f"{key} = {int(value)}")
            else:
                lines.append(f"{key} = {_toml_literal(str(value))}")
        lines.append("")
    path = terminals_path()
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def load_terminal_config() -> dict[str, dict]:
    path = terminals_path()
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    terminals = data.get("terminals", {})
    if not isinstance(terminals, dict):
        return {}
    return {str(key): dict(value) for key, value in terminals.items() if isinstance(value, dict)}


def password_for(terminal_id: str, password_env: str | None = None) -> str | None:
    name = password_env or password_env_name(terminal_id)
    value = load_secrets().get(name)
    if value:
        return value
    return None


def password_configured(terminal_id: str, password_env: str | None = None) -> bool:
    return password_for(terminal_id, password_env) is not None
