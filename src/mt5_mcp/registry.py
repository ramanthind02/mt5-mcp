"""Discover MetaTrader data folders and merge them with terminals.toml.

Adapted from the origin.txt scan in PHUICMT/mcp-mt5 (MIT). See NOTICE.
"""

from __future__ import annotations

import csv
import os
import re
import subprocess
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from .config import load_terminal_config, password_configured, save_terminal_config
from .paths import install_dir, junction_launch_error, launch_key

_HASH_LEN = 32


@dataclass
class TerminalRecord:
    id: str
    name: str
    exe: Path | None
    install: Path | None
    data_dir: Path | None
    login: int | None
    server: str | None
    enabled: bool
    source: str
    password_env: str | None
    password_configured: bool
    running: bool
    role: str | None = None
    path_warning: str | None = None

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "exe": str(self.exe) if self.exe else None,
            "install": str(self.install) if self.install else None,
            "data_dir": str(self.data_dir) if self.data_dir else None,
            "login": self.login,
            "server": self.server,
            "enabled": self.enabled,
            "source": self.source,
            "password_configured": self.password_configured,
            "running": self.running,
            "path_warning": self.path_warning,
        }


def read_origin(terminal_dir: Path) -> str | None:
    origin = terminal_dir / "origin.txt"
    if not origin.exists():
        return None
    raw = origin.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    cleaned = text.strip().strip("\x00")
    return cleaned or None


def _exe_from_install(install: Path) -> Path | None:
    if install.name.lower() == "terminal64.exe":
        return install
    candidate = install / "terminal64.exe"
    if candidate.exists():
        return candidate
    return candidate


def discover(appdata: Path | None = None) -> list[dict]:
    root = appdata
    if root is None:
        env = os.environ.get("APPDATA")
        if not env:
            return []
        root = Path(env)
    base = root / "MetaQuotes" / "Terminal"
    if not base.exists():
        return []
    found: list[dict] = []
    for child in base.iterdir():
        if not child.is_dir() or len(child.name) != _HASH_LEN:
            continue
        origin = read_origin(child)
        install = Path(origin) if origin else None
        exe = _exe_from_install(install) if install else None
        found.append(
            {
                "hash": child.name,
                "origin": origin,
                "install": install,
                "exe": exe,
                "data_dir": child,
            }
        )
    return found


def _exe_key(path: Path | None) -> str | None:
    if path is None:
        return None
    exe = path if path.name.lower() == "terminal64.exe" else path / "terminal64.exe"
    return launch_key(exe)


def _install_key(path: Path | None) -> str | None:
    if path is None:
        return None
    return launch_key(install_dir(path))


def _path_warning(exe: Path | None) -> str | None:
    if exe is None:
        return None
    blocked = junction_launch_error(exe)
    if blocked is None:
        return None
    return blocked["error"]


def running_terminals() -> list[dict]:
    """terminal64.exe processes, keyed by the path Windows reports.

    That path is the launch path. It is not resolved, because a junction and
    its target are different MetaTrader data folders.
    """
    if os.name != "nt":
        return []
    command = (
        "Get-CimInstance Win32_Process -Filter \"Name = 'terminal64.exe'\" "
        "| Select-Object ProcessId,ExecutablePath | ConvertTo-Csv -NoTypeInformation"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    found: list[dict] = []
    for row in csv.DictReader(StringIO(proc.stdout or "")):
        exe = (row.get("ExecutablePath") or "").strip()
        pid = (row.get("ProcessId") or "").strip()
        if not exe or not pid:
            continue
        try:
            found.append({"pid": int(pid), "exe": exe})
        except ValueError:
            continue
    return found


def running_terminal_paths() -> set[str]:
    keys: set[str] = set()
    for item in running_terminals():
        key = launch_key(item["exe"])
        if key:
            keys.add(key)
    return keys


def _as_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def list_terminals(
    appdata: Path | None = None,
    config: dict[str, dict] | None = None,
    running_paths: set[str] | None = None,
    include_discovered: bool = False,
) -> dict:
    discovered = discover(appdata)
    configured = load_terminal_config() if config is None else config
    running = running_terminal_paths() if running_paths is None else running_paths
    by_install: dict[str, dict] = {}
    for item in discovered:
        key = _install_key(item["install"])
        if key and key not in by_install:
            by_install[key] = item

    claimed: set[str] = set()
    records: list[TerminalRecord] = []
    for terminal_id, entry in configured.items():
        raw_path = entry.get("path")
        install = Path(raw_path) if raw_path else None
        exe = _exe_from_install(install) if install else None
        match = by_install.get(_install_key(install))
        if match is None and exe is not None:
            match = by_install.get(_install_key(exe.parent))
        data_dir = match["data_dir"] if match else None
        if match:
            claimed.add(match["hash"])
        env_name = entry.get("password_env")
        records.append(
            TerminalRecord(
                id=terminal_id,
                name=str(entry.get("name") or terminal_id),
                exe=exe,
                install=exe.parent if exe else install,
                data_dir=data_dir,
                login=_as_int(entry.get("login")),
                server=entry.get("server") or None,
                enabled=bool(entry.get("enabled", True)),
                source="registry",
                password_env=str(env_name) if env_name else None,
                password_configured=password_configured(
                    terminal_id, str(env_name) if env_name else None
                ),
                running=_exe_key(exe) in running if exe else False,
                role=entry.get("role") or None,
                path_warning=_path_warning(exe),
            )
        )

    if include_discovered:
        _append_discovered(discovered, claimed, running, records)

    registered = sum(1 for record in records if record.source == "registry")
    return {
        "count": len(records),
        "registered": registered,
        "terminals": [record.public() for record in records],
    }


def _append_discovered(discovered, claimed, running, records) -> None:
    for item in discovered:
        if item["hash"] in claimed:
            continue
        exe = item["exe"]
        records.append(
            TerminalRecord(
                id=item["hash"],
                name=item["hash"],
                exe=exe,
                install=item["install"],
                data_dir=item["data_dir"],
                login=None,
                server=None,
                enabled=True,
                source="discovered",
                password_env=None,
                password_configured=False,
                running=_exe_key(exe) in running if exe else False,
                role=None,
                path_warning=_path_warning(exe),
            )
        )


_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def add_terminal(
    terminal_id: str | None = None,
    path: str | None = None,
    name: str | None = None,
    role: str | None = None,
    login: int | None = None,
    server: str | None = None,
    enabled: bool = True,
    appdata: Path | None = None,
) -> dict:
    """Add or update a registry entry. Does not copy or create a terminal install."""
    if not terminal_id and not path:
        return {"error": "pass terminal_id or path"}
    registry_id = name or terminal_id
    if registry_id is None and path:
        folder = Path(path)
        registry_id = folder.parent.name if folder.name.lower() == "terminal64.exe" else folder.name
    if not registry_id or not _ID_RE.fullmatch(registry_id):
        return {"error": "registry id must be letters, numbers, hyphen, or underscore"}

    discovered = {item["hash"]: item for item in discover(appdata)}
    config = load_terminal_config()
    previous = config.get(registry_id, {})
    exe: Path | None = None
    data_dir = None
    if terminal_id and terminal_id in discovered:
        exe = discovered[terminal_id]["exe"]
        data_dir = discovered[terminal_id]["data_dir"]
    elif path:
        exe = _exe_from_install(Path(path))
    elif terminal_id and terminal_id in config and config[terminal_id].get("path"):
        exe = _exe_from_install(Path(config[terminal_id]["path"]))
        previous = config[terminal_id]
    else:
        return {"error": f"unknown terminal: {terminal_id}"}
    if exe is None:
        return {"error": "could not resolve terminal64.exe"}
    blocked = junction_launch_error(exe)
    if blocked:
        return blocked

    entry: dict = {"path": str(exe), "name": name or registry_id, "enabled": enabled}
    chosen_role = role if role is not None else previous.get("role")
    if chosen_role:
        entry["role"] = chosen_role
    if login is not None:
        entry["login"] = int(login)
    elif previous.get("login") is not None:
        entry["login"] = previous["login"]
    chosen_server = server if server else previous.get("server")
    if chosen_server:
        entry["server"] = chosen_server
    if previous.get("password_env"):
        entry["password_env"] = previous["password_env"]
    config[registry_id] = entry
    saved = save_terminal_config(config)
    return {
        "added": registry_id,
        "path": str(exe),
        "data_dir": str(data_dir) if data_dir else None,
        "registry": str(saved),
        "files_deleted": False,
    }


def remove_terminal(terminal_id: str) -> dict:
    """Drop a registry entry. The install and its data folder stay on disk."""
    config = load_terminal_config()
    if terminal_id not in config:
        return {"error": f"{terminal_id} is not in the registry"}
    removed = config.pop(terminal_id)
    saved = save_terminal_config(config)
    return {
        "removed": terminal_id,
        "path": removed.get("path"),
        "registry": str(saved),
        "files_deleted": False,
    }


def _resolve_exe(
    terminal_id: str | None, path: str | None, appdata: Path | None
) -> tuple[Path | None, dict | None]:
    if path:
        return _exe_from_install(Path(path)), None
    if not terminal_id:
        return None, {"error": "pass terminal_id or path"}
    if any(sep in terminal_id for sep in ("\\", "/")) or terminal_id.lower().endswith(".exe"):
        return _exe_from_install(Path(terminal_id)), None
    record = get_terminal(terminal_id, appdata=appdata)
    if record is None or record.exe is None:
        return None, {"error": f"unknown terminal: {terminal_id}"}
    return record.exe, None


def _start_exe(exe: Path) -> None:
    if os.name != "nt":
        raise OSError("launching a MetaTrader terminal requires Windows")
    env = os.environ.copy()
    env["MT5_LAUNCH_EXE"] = str(exe)
    proc = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Start-Process -FilePath $env:MT5_LAUNCH_EXE",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise OSError(detail or f"Start-Process failed ({proc.returncode})")


def launch_terminal(
    terminal_id: str | None = None,
    path: str | None = None,
    appdata: Path | None = None,
    runner=None,
    running_paths: set[str] | None = None,
) -> dict:
    """Start terminal64.exe. A registry id or any install path is accepted."""
    exe, error = _resolve_exe(terminal_id, path, appdata)
    if error:
        return error
    assert exe is not None
    blocked = junction_launch_error(exe)
    if blocked:
        return blocked
    if not exe.exists():
        return {"error": f"terminal64.exe not found: {exe}"}
    running = running_terminal_paths() if running_paths is None else running_paths
    if _exe_key(exe) in running:
        return {"exe": str(exe), "launched": False, "already_running": True}
    start = runner or _start_exe
    try:
        start(exe)
    except OSError as exc:
        return {"error": str(exc), "exe": str(exe)}
    return {"exe": str(exe), "launched": True, "already_running": False}


def get_terminal(terminal_id: str, appdata: Path | None = None) -> TerminalRecord | None:
    payload = list_terminals(appdata=appdata, include_discovered=True)
    for item in payload["terminals"]:
        if item["id"] == terminal_id:
            return TerminalRecord(
                id=item["id"],
                name=item["name"],
                exe=Path(item["exe"]) if item["exe"] else None,
                install=Path(item["install"]) if item["install"] else None,
                data_dir=Path(item["data_dir"]) if item["data_dir"] else None,
                login=item["login"],
                server=item["server"],
                enabled=item["enabled"],
                source=item["source"],
                password_env=None,
                password_configured=item["password_configured"],
                running=item["running"],
                role=item.get("role"),
                path_warning=item.get("path_warning"),
            )
    return None
