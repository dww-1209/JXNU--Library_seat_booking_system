from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Room(BaseModel):
    """Library reading room metadata and floor-plan dimensions."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    first_level: str
    second_level: str = ""
    third_level: str = ""
    display_name: str
    capacity: int
    start_seat_num: int = 1
    open_time: str = "08:00"
    close_time: str = "22:00"
    pic_seat_bg: str | None = None
    bg_width: int = 0
    bg_height: int = 0
    pic_seat_mode: int = 0


class SeatNode(BaseModel):
    """Pure layout geometry for a single seat on a room map."""

    model_config = ConfigDict(populate_by_name=True)

    seat_num: str
    x: float
    y: float
    width: float
    height: float
    rotate: float = 0.0


class RoomSeatMap(BaseModel):
    """Pure architectural seat map for next-day target selection (no occupancy flags)."""

    model_config = ConfigDict(populate_by_name=True)

    room: Room
    has_background: bool
    seats: list[SeatNode] = Field(default_factory=list)


class CredentialsConfig(BaseModel):
    """User login credentials stored in config.json."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    phone: str = ""
    password: str = ""


class AuthConfig(BaseModel):
    """Authentication configuration block backward-compatible with config.json."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    loginMode: str = "password"
    credentials: CredentialsConfig = Field(default_factory=CredentialsConfig)
    rawCookieStr: str = ""

    @property
    def login_mode(self) -> str:
        return self.loginMode

    @property
    def raw_cookie_str(self) -> str:
        return self.rawCookieStr


class ReservationConfig(BaseModel):
    """Target room, priority seat queue, and time window configuration."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    deptIdEnc: str = "df23c42633fa45e1"
    roomId: str = "2552"
    roomName: str = ""
    targetSeats: list[str] = Field(default_factory=lambda: ["007"])
    startTime: str = "08:00"
    endTime: str = "21:00"
    reserveDaysAhead: int = 1

    @field_validator("targetSeats", mode="before")
    @classmethod
    def _normalize_target_seats(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        seen: set[str] = set()
        normalized: list[str] = []
        for raw in value:
            s = str(raw).strip()
            if not s:
                continue
            if s.isdigit() and len(s) < 3:
                s = f"{int(s):03d}"
            if s not in seen:
                seen.add(s)
                normalized.append(s)
        return normalized

    @property
    def dept_id_enc(self) -> str:
        return self.deptIdEnc

    @property
    def room_id(self) -> str:
        return self.roomId

    @property
    def room_name(self) -> str:
        return self.roomName

    @property
    def target_seats(self) -> list[str]:
        return self.targetSeats

    @property
    def start_time(self) -> str:
        return self.startTime

    @property
    def end_time(self) -> str:
        return self.endTime

    @property
    def reserve_days_ahead(self) -> int:
        return self.reserveDaysAhead


class ScheduleConfig(BaseModel):
    """Scheduled sniping and Windows background task settings."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    enabled: bool = True
    targetTime: str = "22:00:00"
    prepareSecondsAhead: int = 60
    winTaskEnabled: bool = False
    winTaskTime: str = "21:59"
    skipDate: str | None = None

    @field_validator("skipDate", mode="before")
    @classmethod
    def _normalize_skip_date(cls, value: Any) -> str | None:
        if value is None:
            return None
        s = str(value).strip()
        return s or None

    @field_validator("targetTime", mode="before")
    @classmethod
    def _normalize_target_time(cls, value: Any) -> str:
        s = str(value or "22:00:00").strip()
        parts = s.split(":")
        if len(parts) == 2:
            return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:00"
        if len(parts) == 3:
            return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:{parts[2].zfill(2)}"
        return "22:00:00"

    @property
    def target_time(self) -> str:
        return self.targetTime

    @property
    def prepare_seconds_ahead(self) -> int:
        return self.prepareSecondsAhead

    @property
    def win_task_enabled(self) -> bool:
        return self.winTaskEnabled

    @property
    def win_task_time(self) -> str:
        return self.winTaskTime

    @property
    def skip_date(self) -> str | None:
        return self.skipDate


class AppConfig(BaseModel):
    """Root application configuration fully backward-compatible with config.json."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    comment: str | None = Field(
        default=None,
        alias="_comment",
        serialization_alias="_comment",
    )
    reservation: ReservationConfig = Field(default_factory=ReservationConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)


class SanitizedAuthDTO(BaseModel):
    """Redacted authentication summary safe for UI responses."""

    phoneMasked: str = ""
    phone: str = ""
    hasPassword: bool = False
    loggedIn: bool = False
    loginMode: str = "password"


class SanitizedConfigDTO(BaseModel):
    """Sanitized configuration payload returned by GET /api/config."""

    reservation: ReservationConfig
    schedule: ScheduleConfig
    auth: SanitizedAuthDTO


class AttemptOutcome(StrEnum):
    """Normalized outcome of a single seat reservation submission."""

    SUCCESS = "SUCCESS"
    ALREADY_RESERVED = "ALREADY_RESERVED"
    OCCUPIED = "OCCUPIED"
    NOT_OPEN_OR_EXPIRED = "NOT_OPEN_OR_EXPIRED"
    NETWORK_ERROR = "NETWORK_ERROR"
    FATAL_ERROR = "FATAL_ERROR"


class AttemptResult(BaseModel):
    """Result of submitting a single seat reservation request."""

    seat_num: str
    outcome: AttemptOutcome
    raw_msg: str
    latency_ms: float = 0.0


class ReservationRecord(BaseModel):
    """Server-side reservation record returned by verification checks."""

    id: str | None = None
    room_id: str | None = None
    room_name: str = ""
    seat_num: str = ""
    day: str = ""
    start_time: str = ""
    end_time: str = ""
    status_text: str = ""


class VerificationResult(BaseModel):
    """Server-side verification status confirming whether a reservation is active."""

    verified: bool
    matched_record: ReservationRecord | None = None
    active_records: list[ReservationRecord] = Field(default_factory=list)
    message: str = ""
    checked_at: str = ""


class EngineState(StrEnum):
    """Lifecycle states of the upstream reservation engine."""

    IDLE = "IDLE"
    WAITING = "WAITING"
    PREWARMING = "PREWARMING"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RunResult(BaseModel):
    """Final outcome summary of a reservation execution run."""

    success: bool
    locked_seat: str | None = None
    verified: bool = False
    verification: VerificationResult | None = None
    attempts: int = 0
    elapsed_sec: float = 0.0
    message: str = ""


class RuntimeStatus(BaseModel):
    """Live engine status snapshot for REST and SSE updates."""

    state: EngineState = EngineState.IDLE
    targetTime: str = ""
    targetDay: str = ""
    countdownSec: float | None = None
    clockOffsetMs: int | None = None
    lastResult: RunResult | None = None
    verification: VerificationResult | None = None
    winTask: dict[str, Any] = Field(default_factory=dict)


class LogEntry(BaseModel):
    """Structured diagnostic log record for ring buffer and SSE streaming."""

    id: int
    timestamp: str
    level: str
    stage: str
    message: str
    detail: dict[str, Any] | None = None
