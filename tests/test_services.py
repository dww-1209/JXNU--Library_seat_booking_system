from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from domain.models import (
    AppConfig,
    AttemptOutcome,
    AttemptResult,
    AuthConfig,
    CredentialsConfig,
    EngineState,
    ReservationConfig,
    ReservationRecord,
    Room,
    RoomSeatMap,
    ScheduleConfig,
    SeatNode,
    VerificationResult,
)
from domain.ports import (
    ConfigStorePort,
    NotifierPort,
    ReservationExecutorPort,
    SeatCatalogPort,
    SystemSchedulerPort,
)
from services.config_service import ConfigService
from services.library_service import LibraryService
from services.log_bus import LogBus
from services.reserve_service import ReserveService


class InMemoryConfigStore(ConfigStorePort):
    def __init__(self, initial: AppConfig | None = None) -> None:
        self.config = initial or AppConfig(
            _comment="unit-test-config",
            reservation=ReservationConfig(
                deptIdEnc="df23c42633fa45e1",
                roomId="2552",
                roomName="二楼东阅览室",
                targetSeats=["007", "008", "009"],
                startTime="08:00",
                endTime="21:00",
                reserveDaysAhead=1,
            ),
            schedule=ScheduleConfig(
                enabled=True,
                targetTime="22:00:00",
                prepareSecondsAhead=2,
            ),
            auth=AuthConfig(
                loginMode="password",
                credentials=CredentialsConfig(
                    phone="13812345678",
                    password="MySuperSecretPassword123",
                ),
                rawCookieStr="JSESSIONID=secret_cookie_token_abc",
            ),
        )
        self.save_calls = 0

    def load(self) -> AppConfig:
        return self.config.model_copy(deep=True)

    def save(self, config: AppConfig) -> None:
        self.save_calls += 1
        self.config = config.model_copy(deep=True)


class MockCatalog(SeatCatalogPort):
    def __init__(self, server_ts_ms: int | None = None) -> None:
        self.server_ts_ms = server_ts_ms
        self.list_rooms_calls = 0
        self.seat_map_calls = 0
        self.bg_calls = 0
        self.rooms = [
            Room(
                id="2552",
                first_level="瑶湖图书馆",
                second_level="二楼",
                third_level="东阅览室",
                display_name="二楼东阅览室",
                capacity=129,
                pic_seat_bg="floor2552.png",
                bg_width=2300,
                bg_height=1050,
                pic_seat_mode=0,
            ),
            Room(
                id="13542",
                first_level="瑶湖西区自习室",
                second_level="一楼",
                third_level="101自习室",
                display_name="西区101自习室",
                capacity=443,
                start_seat_num=1,
                pic_seat_bg=None,
                bg_width=0,
                bg_height=0,
                pic_seat_mode=2,
            ),
        ]

    async def list_rooms(self, dept_id_enc: str, day: str) -> list[Room]:
        self.list_rooms_calls += 1
        return list(self.rooms)

    async def get_room_seat_map(self, dept_id_enc: str, room: Room) -> RoomSeatMap:
        self.seat_map_calls += 1
        if room.id == "2552":
            seats = [
                SeatNode(
                    seat_num=f"{i:03d}",
                    x=float(50 + i * 10),
                    y=120.0,
                    width=40.0,
                    height=36.0,
                    rotate=90.0,
                )
                for i in range(1, 130)
            ]
            return RoomSeatMap(room=room, has_background=True, seats=seats)
        # Grid room returns empty seat list from Chaoxing getdrawseat endpoint
        return RoomSeatMap(room=room, has_background=False, seats=[])

    async def fetch_background_image(self, pic_seat_bg: str) -> tuple[bytes, str]:
        self.bg_calls += 1
        return b"\x89PNG\r\n\x1a\nFAKE_FLOOR_PLAN", "image/png"

    async def get_server_timestamp_ms(
        self,
        dept_id_enc: str,
        room_id: str,
        day: str,
    ) -> int | None:
        return self.server_ts_ms


class ScriptedExecutor(ReservationExecutorPort):
    def __init__(
        self,
        scripted_responses: dict[str, list[AttemptResult | BaseException]] | None = None,
        verification: VerificationResult | None = None,
        on_submit_advance_ms: int = 0,
        clock: FakeClock | None = None,
    ) -> None:
        self.scripted = {k: list(v) for k, v in (scripted_responses or {}).items()}
        self.verification = verification or VerificationResult(
            verified=False,
            message="未查询到有效预约",
        )
        self.on_submit_advance_ms = on_submit_advance_ms
        self.clock = clock

        self.prepare_calls: list[tuple[str, str]] = []
        self.open_page_calls: list[tuple[str, str, str]] = []
        self.submitted_seats: list[str] = []
        self.verify_calls: list[tuple[str, str | None]] = []
        self.closed_count = 0

    async def prepare_session(self, phone: str, password: str, dept_id_enc: str) -> None:
        self.prepare_calls.append((phone, dept_id_enc))

    async def open_select_page(self, dept_id_enc: str, room_id: str, day: str) -> bool:
        self.open_page_calls.append((dept_id_enc, room_id, day))
        return True

    async def submit_seat(
        self,
        dept_id_enc: str,
        room_id: str,
        seat_num: str,
        day: str,
        start_time: str,
        end_time: str,
    ) -> AttemptResult:
        self.submitted_seats.append(seat_num)
        if self.clock is not None and self.on_submit_advance_ms > 0:
            self.clock.advance_ms(self.on_submit_advance_ms)

        queue = self.scripted.get(seat_num, [])
        if queue:
            item = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(item, BaseException):
                raise item
            return item
        return AttemptResult(
            seat_num=seat_num,
            outcome=AttemptOutcome.OCCUPIED,
            raw_msg="该座位已被预约",
            latency_ms=42.0,
        )

    async def verify_reservation(
        self,
        dept_id_enc: str,
        target_day: str | None = None,
    ) -> VerificationResult:
        self.verify_calls.append((dept_id_enc, target_day))
        return self.verification

    async def close(self) -> None:
        self.closed_count += 1


class RecordingNotifier(NotifierPort):
    def __init__(self) -> None:
        self.events: list[tuple[bool, str, str]] = []

    def notify(self, success: bool, title: str, message: str) -> None:
        self.events.append((success, title, message))


class MockSystemScheduler(SystemSchedulerPort):
    def get_status(self) -> dict[str, Any]:
        return {"enabled": True, "taskName": "ChaoxingSeatReserverApp", "runTime": "21:59"}

    def set_enabled(self, enabled: bool, run_time: str = "21:59") -> tuple[bool, str]:
        return True, "ok"


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.current = start
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.current

    def epoch_ms(self) -> int:
        return int(self.current.timestamp() * 1000)

    def advance_ms(self, delta_ms: int) -> None:
        self.current += timedelta(milliseconds=delta_ms)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=max(0.001, seconds))
        await asyncio.sleep(0)


async def test_log_bus_redacts_secrets_and_streams_sse(tmp_path: Path) -> None:
    log_path = tmp_path / "chaoxing_reserver.log"
    bus = LogBus(log_file=log_path, max_entries=5)
    try:
        bus.register_secret("MySuperSecretPassword123")
        sub_id, queue = bus.subscribe()

        bus.info(
            "AUTH",
            "正在登录账号 13812345678，pwd=MySuperSecretPassword123",
            detail={"phone": "13812345678", "password": "MySuperSecretPassword123"},
        )

        event_type, entry = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event_type == "log"
        assert "138****5678" in entry.message
        assert "13812345678" not in entry.message
        assert "MySuperSecretPassword123" not in entry.message
        assert entry.detail is not None
        assert entry.detail["phone"] == "138****5678"
        assert entry.detail["password"] == "******"

        for idx in range(6):
            bus.debug("POLL", f"tick-{idx}")

        buffered = bus.get_entries()
        assert len(buffered) == 5
        bus.unsubscribe(sub_id)
    finally:
        bus.close()

    file_text = log_path.read_text(encoding="utf-8")
    assert "138****5678" in file_text
    assert "MySuperSecretPassword123" not in file_text


def test_config_service_sanitizes_and_preserves_credentials(tmp_path: Path) -> None:
    bus = LogBus(log_file=tmp_path / "cfg.log", configure_file_sink=False)
    store = InMemoryConfigStore()
    svc = ConfigService(store=store, bus=bus)

    sanitized = svc.get_sanitized_config(logged_in=True)
    assert sanitized.auth.phoneMasked == "138****5678"
    assert sanitized.auth.hasPassword is True
    assert sanitized.auth.loggedIn is True
    # Legacy prepareSecondsAhead=2 is normalized to 60 in UI DTO
    assert sanitized.schedule.prepareSecondsAhead == 60

    updated = svc.update_config(
        {
            "reservation": {
                "targetSeats": ["9", "007", "9", "012"],
                "startTime": "09:00",
                "endTime": "21:00",
            },
            "schedule": {
                "targetTime": "22:00",
                "prepareSecondsAhead": 45,
            },
            "auth": {
                "credentials": {"password": "******"},
            },
        }
    )
    assert updated.reservation.targetSeats == ["009", "007", "012"]
    assert updated.schedule.targetTime == "22:00:00"
    assert updated.schedule.prepareSecondsAhead == 45
    # Password and rawCookieStr are preserved intact
    assert updated.auth.credentials.password == "MySuperSecretPassword123"
    assert updated.auth.rawCookieStr == "JSESSIONID=secret_cookie_token_abc"

    with pytest.raises(ValueError, match="必须早于结束时间"):
        svc.update_config({"reservation": {"startTime": "21:00", "endTime": "08:00"}})

    with pytest.raises(ValueError, match="最晚为 21:00"):
        svc.update_config({"reservation": {"startTime": "08:00", "endTime": "22:00"}})

    with pytest.raises(ValueError, match="不得少于 2 小时"):
        svc.update_config({"reservation": {"startTime": "08:00", "endTime": "09:00"}})

    with pytest.raises(ValueError, match="不得少于 2 小时"):
        svc.update_config({"reservation": {"startTime": "20:00", "endTime": "21:00"}})

    boundary = svc.update_config(
        {"reservation": {"startTime": "19:00", "endTime": "21:00"}}
    )
    assert boundary.reservation.startTime == "19:00"
    assert boundary.reservation.endTime == "21:00"


async def test_library_service_floor_plan_and_grid_generation(tmp_path: Path) -> None:
    bus = LogBus(log_file=tmp_path / "lib.log", configure_file_sink=False)
    store = InMemoryConfigStore()
    cfg_svc = ConfigService(store=store, bus=bus)
    catalog = MockCatalog()
    lib = LibraryService(catalog=catalog, config_service=cfg_svc, bus=bus)

    rooms = await lib.list_rooms(day="2026-11-25")
    assert len(rooms) == 2
    # Cached on second call
    await lib.list_rooms(day="2026-11-25")
    assert catalog.list_rooms_calls == 1

    floor_map = await lib.get_room_seat_map("2552", day="2026-11-25")
    assert floor_map.has_background is True
    assert len(floor_map.seats) == 129
    assert floor_map.seats[0].seat_num == "001"

    grid_map = await lib.get_room_seat_map("13542", day="2026-11-25")
    assert grid_map.has_background is False
    assert len(grid_map.seats) == 443
    assert grid_map.seats[0].seat_num == "001"
    assert grid_map.seats[-1].seat_num == "443"
    assert len({(s.x, s.y) for s in grid_map.seats}) == 443

    bg_bytes, content_type = await lib.get_background_image("2552")
    assert content_type == "image/png"
    assert bg_bytes.startswith(b"\x89PNG")
    await lib.get_background_image("2552")
    assert catalog.bg_calls == 1


async def test_reserve_priority_ordering_and_dropping_occupied_seats(tmp_path: Path) -> None:
    bus = LogBus(log_file=tmp_path / "res1.log", configure_file_sink=False)
    store = InMemoryConfigStore()
    cfg_svc = ConfigService(store=store, bus=bus)
    notifier = RecordingNotifier()

    # Priority queue is ["007", "008", "009"].
    # Round 1:
    #   "007" -> OCCUPIED (must be permanently dropped!)
    #   "008" -> NETWORK_ERROR (kept for Round 2)
    #   "009" -> OCCUPIED (must be permanently dropped!)
    # Round 2:
    #   Only "008" should be attempted -> SUCCESS -> verified!
    executor = ScriptedExecutor(
        scripted_responses={
            "007": [AttemptResult(seat_num="007", outcome=AttemptOutcome.OCCUPIED, raw_msg="已被预约")],
            "008": [
                AttemptResult(
                    seat_num="008",
                    outcome=AttemptOutcome.NETWORK_ERROR,
                    raw_msg="前端请求超时熔断 (7s)",
                    latency_ms=7000.0,
                ),
                AttemptResult(seat_num="008", outcome=AttemptOutcome.SUCCESS, raw_msg="预约成功"),
            ],
            "009": [AttemptResult(seat_num="009", outcome=AttemptOutcome.OCCUPIED, raw_msg="已被预约")],
        },
        verification=VerificationResult(
            verified=True,
            matched_record=ReservationRecord(
                id="rec-1",
                room_id="2552",
                room_name="二楼东阅览室",
                seat_num="008",
                day="2026-11-25",
                start_time="08:00",
                end_time="21:00",
                status_text="待履约",
            ),
            message="核验通过",
        ),
    )
    svc = ReserveService(
        executor=executor,
        config_service=cfg_svc,
        notifier=notifier,
        bus=bus,
        retry_delay_sec=0.0,
    )

    result = await svc.run_once(target_day="2026-11-25")

    assert executor.submitted_seats == ["007", "008", "009", "008"]
    assert result.success is True
    assert result.verified is True
    assert result.locked_seat == "008"
    assert result.attempts == 4
    assert svc.state == EngineState.COMPLETED
    assert len(executor.verify_calls) == 1
    assert len(notifier.events) == 1
    assert notifier.events[0][0] is True


async def test_reserve_already_reserved_stops_immediately_and_verifies(tmp_path: Path) -> None:
    bus = LogBus(log_file=tmp_path / "res2.log", configure_file_sink=False)
    store = InMemoryConfigStore()
    cfg_svc = ConfigService(store=store, bus=bus)
    notifier = RecordingNotifier()

    executor = ScriptedExecutor(
        scripted_responses={
            "007": [
                AttemptResult(
                    seat_num="007",
                    outcome=AttemptOutcome.ALREADY_RESERVED,
                    raw_msg="该时间段您已有预约",
                )
            ],
        },
        verification=VerificationResult(
            verified=True,
            matched_record=ReservationRecord(
                id="rec-2",
                room_id="2552",
                room_name="二楼东阅览室",
                seat_num="007",
                day="2026-11-25",
                start_time="08:00",
                end_time="21:00",
                status_text="已预约",
            ),
        ),
    )
    svc = ReserveService(
        executor=executor,
        config_service=cfg_svc,
        notifier=notifier,
        bus=bus,
        retry_delay_sec=0.0,
    )

    result = await svc.run_once(target_day="2026-11-25")

    assert executor.submitted_seats == ["007"]
    assert result.success is True
    assert result.verified is True
    assert result.locked_seat == "007"
    assert svc.state == EngineState.COMPLETED
    assert notifier.events and notifier.events[0][0] is True


async def test_reserve_timeout_recovery_via_mandatory_server_verification(tmp_path: Path) -> None:
    bus = LogBus(log_file=tmp_path / "res3.log", configure_file_sink=False)
    store = InMemoryConfigStore()
    store.config.reservation.targetSeats = ["007", "008"]
    cfg_svc = ConfigService(store=store, bus=bus)
    notifier = RecordingNotifier()
    clock = FakeClock(datetime(2026, 11, 24, 22, 0, 0))

    # Every submit attempt either times out with NETWORK_ERROR or raises TimeoutError,
    # advancing fake clock 7000ms each call until max_duration_sec=12s expires.
    # Yet Chaoxing processed seat "007" in the background, confirmed by verify_reservation!
    executor = ScriptedExecutor(
        scripted_responses={
            "007": [
                AttemptResult(
                    seat_num="007",
                    outcome=AttemptOutcome.NETWORK_ERROR,
                    raw_msg="前端请求超时熔断 (7s)",
                    latency_ms=7000.0,
                )
            ],
            "008": [TimeoutError("Playwright evaluate timed out")],
        },
        verification=VerificationResult(
            verified=True,
            matched_record=ReservationRecord(
                id="rec-bg-007",
                room_id="2552",
                room_name="二楼东阅览室",
                seat_num="007",
                day="2026-11-25",
                start_time="08:00",
                end_time="21:00",
                status_text="待签到",
            ),
            message="后台慢请求已在服务端生效",
        ),
        on_submit_advance_ms=7000,
        clock=clock,
    )
    svc = ReserveService(
        executor=executor,
        config_service=cfg_svc,
        notifier=notifier,
        bus=bus,
        now_fn=clock.now,
        epoch_ms_fn=clock.epoch_ms,
        sleep_fn=clock.sleep,
        retry_delay_sec=0.0,
    )

    result = await svc.run_once(target_day="2026-11-25", max_duration_sec=12.0)

    assert len(executor.verify_calls) == 1
    assert result.success is True
    assert result.verified is True
    assert result.locked_seat == "007"
    assert svc.state == EngineState.COMPLETED
    assert len(notifier.events) == 1
    assert notifier.events[0][0] is True


async def test_not_open_reopens_select_page_and_preserves_first_priority(tmp_path: Path) -> None:
    bus = LogBus(log_file=tmp_path / "res4.log", configure_file_sink=False)
    store = InMemoryConfigStore()
    store.config.reservation.targetSeats = ["007", "008"]
    cfg_svc = ConfigService(store=store, bus=bus)

    executor = ScriptedExecutor(
        scripted_responses={
            "007": [
                AttemptResult(
                    seat_num="007",
                    outcome=AttemptOutcome.NOT_OPEN_OR_EXPIRED,
                    raw_msg="未到开放预约时间",
                ),
                AttemptResult(
                    seat_num="007",
                    outcome=AttemptOutcome.SUCCESS,
                    raw_msg="预约成功",
                ),
            ],
        },
        verification=VerificationResult(
            verified=True,
            matched_record=ReservationRecord(seat_num="007", room_name="二楼东阅览室", day="2026-11-25"),
        ),
    )
    svc = ReserveService(
        executor=executor,
        config_service=cfg_svc,
        bus=bus,
        retry_delay_sec=0.0,
    )

    result = await svc.run_once(target_day="2026-11-25")

    assert len(executor.open_page_calls) == 2
    assert executor.submitted_seats == ["007", "007"]
    assert result.success is True
    assert result.locked_seat == "007"


async def test_clock_offset_calculation_and_scheduled_prewarm_execution(tmp_path: Path) -> None:
    # Direct unit checks on RTT-compensated offset math
    assert ReserveService.calculate_clock_offset_ms(10_500, 10_000, 10_200) == 400
    assert ReserveService.calculate_clock_offset_ms(9_500, 10_000, 10_100) == -550

    bus = LogBus(log_file=tmp_path / "sched.log", configure_file_sink=False)
    store = InMemoryConfigStore()
    store.config.reservation.targetSeats = ["007"]
    store.config.schedule.targetTime = "22:00:00"
    store.config.schedule.prepareSecondsAhead = 2  # Should auto-upgrade to 60s
    cfg_svc = ConfigService(store=store, bus=bus)

    # Start local clock at 21:58:50 (70s before 22:00:00).
    # Server clock is +2000ms ahead of local clock, so server hits 22:00:00 when local is 21:59:58.000!
    clock = FakeClock(datetime(2026, 11, 24, 21, 58, 50))

    class DynamicServerCatalog(MockCatalog):
        async def get_server_timestamp_ms(
            self,
            dept_id_enc: str,
            room_id: str,
            day: str,
        ) -> int | None:
            return clock.epoch_ms() + 2000

    catalog = DynamicServerCatalog()
    snipe_local_times: list[datetime] = []

    class TimingExecutor(ScriptedExecutor):
        async def open_select_page(self, dept_id_enc: str, room_id: str, day: str) -> bool:
            snipe_local_times.append(clock.now())
            return await super().open_select_page(dept_id_enc, room_id, day)

    executor = TimingExecutor(
        scripted_responses={
            "007": [AttemptResult(seat_num="007", outcome=AttemptOutcome.SUCCESS, raw_msg="成功")],
        },
        verification=VerificationResult(
            verified=True,
            matched_record=ReservationRecord(seat_num="007", room_name="二楼东阅览室", day="2026-11-25"),
        ),
    )
    scheduler_port = MockSystemScheduler()
    svc = ReserveService(
        executor=executor,
        config_service=cfg_svc,
        catalog=catalog,
        scheduler=scheduler_port,
        bus=bus,
        now_fn=clock.now,
        epoch_ms_fn=clock.epoch_ms,
        sleep_fn=clock.sleep,
        retry_delay_sec=0.0,
    )

    result = await svc.run_scheduled()

    assert svc.clock_offset_ms == 2000
    assert snipe_local_times, "open_select_page must have been called"
    # Because server is +2.0s ahead, sniping starts at ~21:59:58 local time (not 22:00:00 local)!
    trigger_local = snipe_local_times[0]
    expected_local = datetime(2026, 11, 24, 21, 59, 58)
    assert abs((trigger_local - expected_local).total_seconds()) < 0.05
    assert result.success is True
    assert result.verified is True
    assert result.locked_seat == "007"
    status = svc.get_status()
    assert status.clockOffsetMs == 2000
    assert status.winTask["taskName"] == "ChaoxingSeatReserverApp"


async def test_all_seats_occupied_marks_failed_and_cancel_resets_idle(tmp_path: Path) -> None:
    bus = LogBus(log_file=tmp_path / "fail_cancel.log", configure_file_sink=False)
    store = InMemoryConfigStore()
    store.config.reservation.targetSeats = ["007", "008"]
    cfg_svc = ConfigService(store=store, bus=bus)
    notifier = RecordingNotifier()

    executor = ScriptedExecutor(
        scripted_responses={
            "007": [AttemptResult(seat_num="007", outcome=AttemptOutcome.OCCUPIED, raw_msg="已被占用")],
            "008": [AttemptResult(seat_num="008", outcome=AttemptOutcome.OCCUPIED, raw_msg="已被占用")],
        },
        verification=VerificationResult(verified=False, message="无预约"),
    )
    svc = ReserveService(
        executor=executor,
        config_service=cfg_svc,
        notifier=notifier,
        bus=bus,
        retry_delay_sec=0.0,
    )

    fail_res = await svc.run_once(target_day="2026-11-25")
    assert fail_res.success is False
    assert fail_res.verified is False
    assert fail_res.locked_seat is None
    assert svc.state == EngineState.FAILED
    assert notifier.events[-1][0] is False

    # Test start_scheduled + cancel()
    task = svc.start_scheduled()
    await asyncio.sleep(0.02)
    await svc.cancel()
    assert task.done()
    assert svc.state == EngineState.IDLE


async def test_verify_now_resets_stale_completed_state_and_supports_none_target_day(
    tmp_path: Path,
) -> None:
    bus = LogBus(log_file=tmp_path / "verify_state.log", configure_file_sink=False)
    store = InMemoryConfigStore()
    cfg_svc = ConfigService(store=store, bus=bus)

    executor = ScriptedExecutor(
        verification=VerificationResult(
            verified=True,
            matched_record=ReservationRecord(
                id="rec-active",
                room_id="2552",
                room_name="二楼东阅览室",
                seat_num="007",
                day="2026-11-25",
                start_time="08:00",
                end_time="21:00",
                status_text="已预约",
            ),
            message="存在有效预约",
        )
    )
    svc = ReserveService(executor=executor, config_service=cfg_svc, bus=bus, retry_delay_sec=0.0)

    # Positive verify with target_day=None passes None to executor and transitions IDLE -> COMPLETED
    pos = await svc.verify_now(target_day=None)
    assert pos.verified is True
    assert executor.verify_calls[-1] == ("df23c42633fa45e1", None)
    assert svc.state == EngineState.COMPLETED

    # Subsequent negative verify MUST reset COMPLETED -> IDLE (never stay stale COMPLETED)
    executor.verification = VerificationResult(
        verified=False,
        matched_record=None,
        active_records=[],
        message="当前账号暂无生效中的座位预约记录",
    )
    neg = await svc.verify_now(target_day="2026-11-26")
    assert neg.verified is False
    assert executor.verify_calls[-1] == ("df23c42633fa45e1", "2026-11-26")
    assert svc.state == EngineState.IDLE
    assert svc.get_status().state == EngineState.IDLE


async def test_schedule_skip_date_matching_tonight_skips_and_auto_clears(tmp_path: Path) -> None:
    bus = LogBus(log_file=tmp_path / "skip_date.log", configure_file_sink=False)
    store = InMemoryConfigStore()
    store.config.reservation.targetSeats = ["007"]
    store.config.schedule.targetTime = "22:00:00"
    cfg_svc = ConfigService(store=store, bus=bus)

    # Set skipDate via update_config and confirm sanitized DTO exposes it
    cfg_svc.update_config({"schedule": {"skipDate": "2026-11-25"}})
    assert cfg_svc.get_sanitized_config().schedule.skipDate == "2026-11-25"

    # Case 1: skipDate matches tonight ("2026-11-25") -> skips silently, auto-clears skipDate, zero Playwright calls
    clock_match = FakeClock(datetime(2026, 11, 25, 21, 59, 0))
    executor_match = ScriptedExecutor(
        scripted_responses={
            "007": [AttemptResult(seat_num="007", outcome=AttemptOutcome.SUCCESS, raw_msg="成功")],
        },
    )
    svc_match = ReserveService(
        executor=executor_match,
        config_service=cfg_svc,
        bus=bus,
        now_fn=clock_match.now,
        epoch_ms_fn=clock_match.epoch_ms,
        sleep_fn=clock_match.sleep,
        retry_delay_sec=0.0,
    )

    res_skip = await svc_match.run_scheduled()
    assert res_skip.success is True
    assert res_skip.verified is False
    assert "2026-11-25" in res_skip.message
    assert svc_match.state == EngineState.IDLE
    assert executor_match.prepare_calls == []
    assert executor_match.open_page_calls == []
    assert executor_match.submitted_seats == []
    assert executor_match.verify_calls == []
    # skipDate must be auto-cleared back to None
    assert cfg_svc.get_config().schedule.skipDate is None
    assert cfg_svc.get_sanitized_config().schedule.skipDate is None

    # Verify explicit clearing with "" also normalizes to None
    cfg_svc.update_config({"schedule": {"skipDate": "2026-11-26"}})
    cfg_svc.update_config({"schedule": {"skipDate": ""}})
    assert cfg_svc.get_config().schedule.skipDate is None

    # Case 2: skipDate is a non-matching future date ("2026-11-26") while tonight is "2026-11-25" -> runs normally
    cfg_svc.update_config({"schedule": {"skipDate": "2026-11-26"}})
    clock_run = FakeClock(datetime(2026, 11, 25, 21, 59, 58))
    executor_run = ScriptedExecutor(
        scripted_responses={
            "007": [AttemptResult(seat_num="007", outcome=AttemptOutcome.SUCCESS, raw_msg="成功")],
        },
        verification=VerificationResult(
            verified=True,
            matched_record=ReservationRecord(seat_num="007", room_name="二楼东阅览室", day="2026-11-26"),
        ),
    )
    svc_run = ReserveService(
        executor=executor_run,
        config_service=cfg_svc,
        bus=bus,
        now_fn=clock_run.now,
        epoch_ms_fn=clock_run.epoch_ms,
        sleep_fn=clock_run.sleep,
        retry_delay_sec=0.0,
    )

    res_run = await svc_run.run_scheduled()
    assert res_run.success is True
    assert res_run.verified is True
    assert res_run.locked_seat == "007"
    assert executor_run.submitted_seats == ["007"]
    # Non-matching future skipDate stays preserved for tomorrow night
    assert cfg_svc.get_config().schedule.skipDate == "2026-11-26"


def test_domain_paths_source_and_frozen_modes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from domain.paths import get_app_dir, get_resource_dir, is_frozen

    # Source mode (not frozen)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    expected_root = Path(__file__).resolve().parent.parent
    assert is_frozen() is False
    assert get_app_dir() == expected_root
    assert get_resource_dir() == expected_root

    # Frozen mode with LOCALAPPDATA set and _MEIPASS present (PyInstaller onefile temp dir)
    fake_exe_dir = tmp_path / "Desktop"
    fake_exe_dir.mkdir(parents=True)
    fake_exe = fake_exe_dir / "YanxiSeatReserver.exe"
    fake_meipass = tmp_path / "temp" / "_MEI12345"
    fake_meipass.mkdir(parents=True)
    fake_local_appdata = tmp_path / "LocalAppData"

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_meipass), raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(fake_local_appdata))

    assert is_frozen() is True
    expected_appdata_dir = (fake_local_appdata / "YanxiSeatReserver").resolve()
    assert get_app_dir() == expected_appdata_dir
    assert expected_appdata_dir.is_dir()
    assert get_resource_dir() == fake_meipass.resolve()

    # Frozen mode with LOCALAPPDATA absent falls back to Path.home()/AppData/Local/YanxiSeatReserver
    fake_home = tmp_path / "FakeHome"
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    expected_fallback_dir = (fake_home / "AppData" / "Local" / "YanxiSeatReserver").resolve()
    assert get_app_dir() == expected_fallback_dir
    assert expected_fallback_dir.is_dir()

    # Frozen mode without _MEIPASS falls back to executable directory
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert get_resource_dir() == fake_exe_dir.resolve()




