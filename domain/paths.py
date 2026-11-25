from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """Return True when running inside a PyInstaller frozen executable."""
    return bool(getattr(sys, "frozen", False))


def get_app_dir() -> Path:
    """Return the writable application directory for runtime data (config, logs, cache, storage_state).

    In frozen (.exe) mode, returns the directory containing the executable.
    In source mode, returns the repository root directory.
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_resource_dir() -> Path:
    """Return the read-only bundled resource directory (e.g. frontend/dist).

    In frozen (.exe) mode, returns sys._MEIPASS when present, falling back to the executable directory.
    In source mode, returns the repository root directory.
    """
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass).resolve()
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
