from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DATA_DIR_NAME = "YanxiSeatReserver"


def is_frozen() -> bool:
    """Return True when running inside a PyInstaller frozen executable."""
    return bool(getattr(sys, "frozen", False))


def get_app_dir() -> Path:
    """Return the writable application directory for runtime data (config, logs, cache, storage_state).

    In frozen (.exe) mode, uses Windows per-user AppData (%LOCALAPPDATA%\\YanxiSeatReserver)
    so zero stray files are created beside the standalone executable.
    In source mode, returns the repository root directory.
    """
    if is_frozen():
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local_app_data) if local_app_data else (Path.home() / "AppData" / "Local")
        app_dir = (base / APP_DATA_DIR_NAME).resolve()
        app_dir.mkdir(parents=True, exist_ok=True)
        return app_dir
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
