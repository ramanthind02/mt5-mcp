"""Launch-path identity for a MetaTrader install.

MetaTrader chooses the data folder from the path used to start terminal64.exe.
A junction and the directory it points at are two data folders and one program.
Path.resolve() follows the junction, so the registry then attaches to the wrong
folder, and starting the shortcut only focuses the terminal that is already open.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


def launch_key(path: Path | str | None) -> str | None:
    """Compare paths as launched. Junctions stay junctions."""
    if path is None or path == "":
        return None
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def install_dir(path: Path | str) -> Path:
    folder = Path(path)
    if folder.name.lower() == "terminal64.exe":
        return folder.parent
    return folder


def image_key(path: Path | str | None) -> str | None:
    """Resolved program image. Used only to detect one process under two paths."""
    if path is None or path == "":
        return None
    try:
        return os.path.normcase(str(Path(path).resolve()))
    except OSError:
        return launch_key(path)


def _is_junction(path: Path) -> bool:
    checker = getattr(os.path, "isjunction", None)
    if checker is not None:
        try:
            return bool(checker(path))
        except (OSError, ValueError):
            return False
    try:
        if path.is_symlink():
            return False
        attrs = os.lstat(path).st_file_attributes
    except (OSError, AttributeError, ValueError):
        return False
    return bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def reparse_point(path: Path | str) -> Path | None:
    """Deepest junction or symlink on this path, if there is one."""
    current = Path(path)
    for candidate in (current, *current.parents):
        if candidate == Path(candidate.anchor):
            break
        try:
            linked = candidate.is_symlink()
        except OSError:
            return None
        if linked or _is_junction(candidate):
            return candidate
    return None


def junction_launch_error(path: Path | str) -> dict | None:
    point = reparse_point(path)
    if point is None:
        return None
    try:
        target = point.resolve()
    except OSError:
        target = point
    if target.name.lower() == "terminal64.exe":
        real_exe = target
    else:
        real_exe = target / "terminal64.exe"
    kind = "symlink" if point.is_symlink() else "junction"
    return {
        "error": (
            f"{point} is a {kind} to {target}. "
            "MetaTrader picks the data folder from the launch path, not the folder the link points at. "
            f"Register {real_exe}. "
            "A separate tester copy has to be a real directory, not a junction to that install."
        ),
        "junction": str(point),
        "target": str(target),
        "real_exe": str(real_exe),
    }


def launch_conflict(exe: Path | str, processes: list[dict]) -> dict | None:
    """Refuse a launch that would focus an open terminal instead of starting the tester.

    A junction is refused before any running process is considered, so the open
    window of the real install is left alone.
    """
    blocked = junction_launch_error(exe)
    if blocked:
        return blocked
    mine = launch_key(exe)
    image: str | None = None
    for item in processes:
        reported = item.get("exe") or ""
        if not reported:
            continue
        if launch_key(reported) == mine:
            return {
                "error": (
                    f"{exe} is already running (pid {item.get('pid')}). "
                    "Starting it again only focuses that window and does not run the tester. "
                    "Close it first. The tester shuts the terminal down when the pass finishes."
                ),
                "pid": item.get("pid"),
                "exe": str(exe),
                "already_running": True,
            }
        if image is None:
            image = image_key(exe)
        if image is not None and image_key(reported) == image:
            return {
                "error": (
                    f"{reported} is already running (pid {item.get('pid')}). "
                    f"It is the same program as {exe} and a different data folder. "
                    "Launching this path would only focus that window. "
                    "Close it, and launch the path whose data folder you want to test."
                ),
                "pid": item.get("pid"),
                "running_exe": reported,
                "exe": str(exe),
            }
    return None
