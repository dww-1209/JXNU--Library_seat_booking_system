from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from domain.models import (
    AppConfig,
    AttemptResult,
    Room,
    RoomSeatMap,
    VerificationResult,
)


class AuthPort(ABC):
    """Outbound port for authenticating and checking session validity."""

    @abstractmethod
    async def login(self, phone: str, password: str, dept_id_enc: str) -> tuple[bool, str]:
        """Perform login, persist session state, and return (success, message)."""

    @abstractmethod
    async def check_session(self, dept_id_enc: str) -> tuple[bool, str]:
        """Verify whether the current stored session is authenticated."""


class SeatCatalogPort(ABC):
    """Outbound port for querying rooms, pure layout coordinates, floor plans, and server clock."""

    @abstractmethod
    async def list_rooms(self, dept_id_enc: str, day: str) -> list[Room]:
        """Return available reading rooms for the given department and date (YYYY-MM-DD)."""

    @abstractmethod
    async def get_room_seat_map(self, dept_id_enc: str, room: Room) -> RoomSeatMap:
        """Return pure seat layout coordinates for a room without occupancy state."""

    @abstractmethod
    async def fetch_background_image(self, pic_seat_bg: str) -> tuple[bytes, str]:
        """Fetch floor-plan image bytes and MIME content-type for pic_seat_bg."""

    @abstractmethod
    async def get_server_timestamp_ms(self, dept_id_enc: str, room_id: str, day: str) -> int | None:
        """Return remote server epoch timestamp in milliseconds, or None if unavailable."""


class ReservationExecutorPort(ABC):
    """Outbound port for browser/session pre-warming, seat submission, and server verification."""

    @abstractmethod
    async def prepare_session(self, phone: str, password: str, dept_id_enc: str) -> None:
        """Pre-warm authenticated browser/HTTP session prior to target snipe time."""

    @abstractmethod
    async def open_select_page(self, dept_id_enc: str, room_id: str, day: str) -> bool:
        """Open or refresh the room seat-selection page so anti-tamper tokens are ready."""

    @abstractmethod
    async def submit_seat(
        self,
        dept_id_enc: str,
        room_id: str,
        seat_num: str,
        day: str,
        start_time: str,
        end_time: str,
    ) -> AttemptResult:
        """Submit a single seat reservation attempt and classify its outcome."""

    @abstractmethod
    async def verify_reservation(
        self,
        dept_id_enc: str,
        target_day: str | None = None,
    ) -> VerificationResult:
        """Query server reservation records in read-only mode to confirm reservation status."""

    @abstractmethod
    async def close(self) -> None:
        """Release underlying browser/network resources cleanly."""


class ConfigStorePort(ABC):
    """Outbound port for reading and persisting AppConfig."""

    @abstractmethod
    def load(self) -> AppConfig:
        """Load AppConfig from persistent storage."""

    @abstractmethod
    def save(self, config: AppConfig) -> None:
        """Persist AppConfig to storage while preserving backward compatibility."""


class NotifierPort(ABC):
    """Outbound port for user-audible or desktop completion notifications."""

    @abstractmethod
    def notify(self, success: bool, title: str, message: str) -> None:
        """Notify the user about reservation completion or failure."""


class SystemSchedulerPort(ABC):
    """Outbound port for managing OS-level scheduled wakeup/execution tasks."""

    @abstractmethod
    def get_status(self) -> dict[str, Any]:
        """Return current OS scheduled task status dictionary."""

    @abstractmethod
    def set_enabled(self, enabled: bool, run_time: str = "21:59") -> tuple[bool, str]:
        """Enable/update or disable the OS scheduled task, returning (success, message)."""
