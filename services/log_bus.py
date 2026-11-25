from __future__ import annotations

import asyncio
import json
import re
import threading
import traceback
from collections import deque
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger as _loguru_logger

from domain.models import LogEntry
from domain.paths import get_app_dir

DEFAULT_LOG_FILE = get_app_dir() / "logs" / "chaoxing_reserver.log"

_PHONE_PATTERN = re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)")
_KV_SECRET_PATTERN = re.compile(
    r"""(?i)(["']?(?:password|pwd|rawCookieStr|cookie)["']?\s*[:=]\s*["']?)([^"',;\s}&]+)(["']?)"""
)


def mask_phone_number(phone: str) -> str:
    """Mask an 11-digit phone number as 138****0000 while keeping short inputs safe."""
    cleaned = (phone or "").strip()
    if not cleaned:
        return ""
    if len(cleaned) >= 7:
        return f"{cleaned[:3]}****{cleaned[-4:]}"
    return "***"


class LogBus:
    """Central diagnostic logging bus backed by loguru file rotation, ring buffer, and SSE pub/sub."""

    def __init__(
        self,
        log_file: Path | str = DEFAULT_LOG_FILE,
        max_entries: int = 500,
        configure_file_sink: bool = True,
    ) -> None:
        self._log_file = Path(log_file)
        self._max_entries = max_entries
        self._entries: deque[LogEntry] = deque(maxlen=max_entries)
        self._seq: int = 0
        self._lock = threading.RLock()
        self._secrets: set[str] = set()
        self._subscribers: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Queue[tuple[str, Any]]]] = {}
        self._next_sub_id: int = 1
        self._sink_id: int | None = None

        if configure_file_sink:
            self._setup_file_sink()

    @property
    def log_file(self) -> Path:
        return self._log_file

    def _setup_file_sink(self) -> None:
        self._log_file.parent.mkdir(parents=True, exist_ok=True)
        # Remove default stderr handler only once if desired? Keep console clean or file-focused:
        # Add dedicated bound file sink filtered to records emitted by this LogBus instance.
        bus_tag = id(self)
        self._sink_id = _loguru_logger.add(
            str(self._log_file),
            rotation="10 MB",
            retention=5,
            encoding="utf-8",
            backtrace=False,
            diagnose=False,
            filter=lambda record: record["extra"].get("_bus_id") == bus_tag,
            format="{message}",
        )

    def close(self) -> None:
        """Detach the loguru file sink owned by this LogBus instance."""
        if self._sink_id is not None:
            try:
                _loguru_logger.remove(self._sink_id)
            except ValueError:
                pass
            self._sink_id = None

    def register_secret(self, secret: str | None) -> None:
        """Register a sensitive string (such as a password or cookie) to be redacted from all logs."""
        if secret:
            trimmed = secret.strip()
            if len(trimmed) >= 4:
                with self._lock:
                    self._secrets.add(trimmed)

    def mask_text(self, text: str) -> str:
        """Mask phone numbers, secret key-values, and registered sensitive strings."""
        if not text:
            return ""
        masked = text
        with self._lock:
            secrets_snapshot = sorted(self._secrets, key=len, reverse=True)
        for secret in secrets_snapshot:
            if secret in masked:
                masked = masked.replace(secret, "******")
        masked = _KV_SECRET_PATTERN.sub(r"\1******\3", masked)
        masked = _PHONE_PATTERN.sub(r"\1****\3", masked)
        return masked

    def _sanitize_value(self, value: Any, key_hint: str = "") -> Any:
        lower_key = key_hint.lower()
        if any(k in lower_key for k in ("password", "pwd", "cookie", "secret", "token")):
            if value:
                return "******"
            return ""
        if "phone" in lower_key and isinstance(value, str):
            return mask_phone_number(value)
        if isinstance(value, str):
            return self.mask_text(value)
        if isinstance(value, dict):
            return {str(k): self._sanitize_value(v, key_hint=str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._sanitize_value(item, key_hint=key_hint) for item in value]
        return value

    def log(
        self,
        level: str,
        stage: str,
        message: str,
        detail: dict[str, Any] | None = None,
        exc: BaseException | None = None,
    ) -> LogEntry:
        """Record a structured log entry to rotating file, memory ring buffer, and SSE subscribers."""
        norm_level = (level or "INFO").upper()
        norm_stage = (stage or "APP").upper()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        safe_message = self.mask_text(str(message))
        safe_detail: dict[str, Any] | None = None
        if detail:
            safe_detail = self._sanitize_value(detail)

        if exc is not None:
            tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            safe_tb = self.mask_text(tb_text)
            safe_detail = dict(safe_detail or {})
            safe_detail["exception_type"] = type(exc).__name__
            safe_detail["exception"] = self.mask_text(str(exc))
            safe_detail["traceback"] = safe_tb

        with self._lock:
            self._seq += 1
            entry = LogEntry(
                id=self._seq,
                timestamp=now_str,
                level=norm_level,
                stage=norm_stage,
                message=safe_message,
                detail=safe_detail,
            )
            self._entries.append(entry)

        file_line = f"{now_str} | {norm_level:<7} | [{norm_stage}] {safe_message}"
        if safe_detail:
            compact_detail = {k: v for k, v in safe_detail.items() if k != "traceback"}
            if compact_detail:
                try:
                    detail_json = json.dumps(compact_detail, ensure_ascii=False, default=str)
                except Exception:
                    detail_json = self.mask_text(str(compact_detail))
                file_line = f"{file_line} | detail={detail_json}"
            if "traceback" in safe_detail:
                file_line = f"{file_line}\n{safe_detail['traceback'].rstrip()}"

        if self._sink_id is not None:
            loguru_level = norm_level if norm_level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} else "INFO"
            _loguru_logger.bind(_bus_id=id(self)).log(loguru_level, file_line)

        self._broadcast("log", entry)
        return entry

    def debug(self, stage: str, message: str, detail: dict[str, Any] | None = None) -> LogEntry:
        return self.log("DEBUG", stage, message, detail=detail)

    def info(self, stage: str, message: str, detail: dict[str, Any] | None = None) -> LogEntry:
        return self.log("INFO", stage, message, detail=detail)

    def warning(self, stage: str, message: str, detail: dict[str, Any] | None = None) -> LogEntry:
        return self.log("WARNING", stage, message, detail=detail)

    def error(
        self,
        stage: str,
        message: str,
        detail: dict[str, Any] | None = None,
        exc: BaseException | None = None,
    ) -> LogEntry:
        return self.log("ERROR", stage, message, detail=detail, exc=exc)

    def exception(
        self,
        stage: str,
        message: str,
        exc: BaseException,
        detail: dict[str, Any] | None = None,
    ) -> LogEntry:
        return self.log("ERROR", stage, message, detail=detail, exc=exc)

    def publish_status(self, status_payload: Any) -> None:
        """Broadcast a runtime status snapshot ('status' SSE event) to all connected clients."""
        self._broadcast("status", status_payload)

    def get_entries(
        self,
        limit: int | None = None,
        level_filter: str | None = None,
    ) -> list[LogEntry]:
        """Return buffered log entries in chronological order."""
        with self._lock:
            items = list(self._entries)
        if level_filter:
            filt = level_filter.upper()
            if filt == "ERROR":
                items = [e for e in items if e.level in {"ERROR", "CRITICAL"}]
            elif filt in {"KEY", "IMPORTANT", "WARN", "WARNING"}:
                items = [e for e in items if e.level in {"INFO", "WARNING", "ERROR", "CRITICAL"} and e.stage != "POLL"]
            elif filt in {"DEBUG", "INFO"}:
                items = [e for e in items if e.level == filt]
        if limit is not None and limit > 0:
            items = items[-limit:]
        return items

    def clear(self) -> None:
        """Clear in-memory ring buffer entries (primarily for tests)."""
        with self._lock:
            self._entries.clear()

    def subscribe(self, max_queue_size: int = 256) -> tuple[int, asyncio.Queue[tuple[str, Any]]]:
        """Subscribe to live ('log', LogEntry) and ('status', payload) events on the active event loop."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=max_queue_size)
        with self._lock:
            sub_id = self._next_sub_id
            self._next_sub_id += 1
            self._subscribers[sub_id] = (loop, queue)
        return sub_id, queue

    def unsubscribe(self, sub_id: int) -> None:
        """Remove an SSE subscriber queue."""
        with self._lock:
            self._subscribers.pop(sub_id, None)

    async def subscribe_events(self) -> AsyncIterator[tuple[str, Any]]:
        """Async generator yielding ('log', LogEntry) and ('status', RuntimeStatus/dict) tuples."""
        sub_id, queue = self.subscribe()
        try:
            while True:
                item = await queue.get()
                yield item
        finally:
            self.unsubscribe(sub_id)

    def _broadcast(self, event_type: str, payload: Any) -> None:
        with self._lock:
            subscribers = list(self._subscribers.items())
        for sub_id, (loop, queue) in subscribers:
            if loop.is_closed():
                self.unsubscribe(sub_id)
                continue
            try:
                loop.call_soon_threadsafe(self._push_to_queue, queue, (event_type, payload))
            except RuntimeError:
                self.unsubscribe(sub_id)

    @staticmethod
    def _push_to_queue(queue: asyncio.Queue[tuple[str, Any]], item: tuple[str, Any]) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            pass


# Shared singleton LogBus for upstream services and downstream adapters
log_bus = LogBus()


def get_log_bus() -> LogBus:
    """Return the shared application LogBus instance."""
    return log_bus
