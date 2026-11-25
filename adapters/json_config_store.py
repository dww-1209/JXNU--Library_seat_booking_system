from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from domain.models import AppConfig
from domain.paths import get_app_dir, get_resource_dir
from domain.ports import ConfigStorePort
from services.log_bus import LogBus, get_log_bus

DEFAULT_CONFIG_PATH = get_app_dir() / "config.json"
DEFAULT_EXAMPLE_PATH = get_resource_dir() / "config.example.json"


class JsonConfigStore(ConfigStorePort):
    """Thread-safe JSON file adapter for AppConfig preserving backward compatibility."""

    def __init__(
        self,
        config_path: Path | str | None = None,
        example_path: Path | str | None = None,
        bus: LogBus | None = None,
    ) -> None:
        self._config_path = Path(config_path) if config_path is not None else (get_app_dir() / "config.json")
        if example_path is not None:
            self._example_path = Path(example_path)
        else:
            app_example = get_app_dir() / "config.example.json"
            self._example_path = (
                app_example if app_example.exists() else (get_resource_dir() / "config.example.json")
            )
        self._bus = bus or get_log_bus()
        self._io_lock = threading.RLock()

    @property
    def config_path(self) -> Path:
        return self._config_path

    def _read_raw_dict(self, path: Path) -> dict[str, Any]:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"配置文件根节点必须为 JSON 对象: {path}")
        return data

    def load(self) -> AppConfig:
        """Load AppConfig from config.json (falling back to config.example.json or defaults)."""
        with self._io_lock:
            target_path: Path | None = None
            if self._config_path.exists():
                target_path = self._config_path
            elif self._example_path.exists():
                target_path = self._example_path

            if target_path is None:
                self._bus.warning(
                    "CONFIG",
                    "未找到 config.json，使用内置默认配置",
                    detail={"path": str(self._config_path)},
                )
                return AppConfig()

            try:
                raw = self._read_raw_dict(target_path)
                cfg = AppConfig.model_validate(raw)
                return cfg
            except Exception as exc:
                self._bus.exception(
                    "CONFIG",
                    f"读取配置文件失败: {target_path}",
                    exc,
                    detail={"path": str(target_path)},
                )
                raise

    def save(self, config: AppConfig) -> None:
        """Atomically persist AppConfig while preserving existing comments, extra keys, and credentials."""
        with self._io_lock:
            existing: dict[str, Any] = {}
            if self._config_path.exists():
                try:
                    existing = self._read_raw_dict(self._config_path)
                except Exception:
                    existing = {}

            dumped = config.model_dump(by_alias=True, exclude_none=True)

            # Preserve legacy _comment if not explicitly set on model
            if "_comment" not in dumped and "_comment" in existing:
                merged: dict[str, Any] = {"_comment": existing["_comment"]}
            else:
                merged = {}

            for key, val in existing.items():
                if key not in dumped and key != "_comment":
                    merged[key] = val

            for key, val in dumped.items():
                merged[key] = val

            # Safety guard: never wipe an existing stored password/phone with an empty string unintentionally
            existing_creds = (
                existing.get("auth", {}).get("credentials", {})
                if isinstance(existing.get("auth"), dict)
                else {}
            )
            new_auth = merged.get("auth")
            if isinstance(new_auth, dict) and isinstance(existing_creds, dict):
                new_creds = new_auth.get("credentials")
                if isinstance(new_creds, dict):
                    if not new_creds.get("password") and existing_creds.get("password"):
                        new_creds["password"] = existing_creds["password"]
                    if not new_creds.get("phone") and existing_creds.get("phone"):
                        new_creds["phone"] = existing_creds["phone"]
                if not new_auth.get("rawCookieStr") and isinstance(existing.get("auth"), dict):
                    old_cookie = existing["auth"].get("rawCookieStr")
                    if old_cookie:
                        new_auth["rawCookieStr"] = old_cookie

            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._config_path.with_name(f".{self._config_path.name}.tmp")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(merged, f, ensure_ascii=False, indent=2)
                    f.write("\n")
                os.replace(tmp_path, self._config_path)
            except Exception as exc:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                self._bus.exception(
                    "CONFIG",
                    f"写入配置文件失败: {self._config_path}",
                    exc,
                    detail={"path": str(self._config_path)},
                )
                raise
