from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from domain.models import (
    AppConfig,
    AttemptOutcome,
    AttemptResult,
    EngineState,
    RunResult,
    RuntimeStatus,
    VerificationResult,
)
from domain.ports import (
    NotifierPort,
    ReservationExecutorPort,
    SeatCatalogPort,
    SystemSchedulerPort,
)
from services.config_service import ConfigService
from services.log_bus import LogBus, get_log_bus


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class ReserveService:
    """Upstream state machine orchestrating scheduled pre-warming, clock calibration, sniping, and verification."""

    DEFAULT_MAX_DURATION_SEC = 35.0
    MIN_EFFECTIVE_PREPARE_SEC = 15
    DEFAULT_EFFECTIVE_PREPARE_SEC = 60

    def __init__(
        self,
        executor: ReservationExecutorPort,
        config_service: ConfigService,
        catalog: SeatCatalogPort | None = None,
        notifier: NotifierPort | None = None,
        scheduler: SystemSchedulerPort | None = None,
        bus: LogBus | None = None,
        now_fn: Callable[[], datetime] | None = None,
        epoch_ms_fn: Callable[[], int] | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
        retry_delay_sec: float = 0.05,
    ) -> None:
        self._executor = executor
        self._config_service = config_service
        self._catalog = catalog
        self._notifier = notifier
        self._scheduler = scheduler
        self._bus = bus or get_log_bus()

        self._now_fn = now_fn or datetime.now
        self._epoch_ms_fn = epoch_ms_fn or (lambda: int(self._now_fn().timestamp() * 1000))
        self._sleep_fn = sleep_fn or asyncio.sleep
        self._retry_delay_sec = max(0.0, retry_delay_sec)

        self._state: EngineState = EngineState.IDLE
        self._target_time_str: str = ""
        self._target_day_str: str = ""
        self._countdown_sec: float | None = None
        self._clock_offset_ms: int | None = None
        self._last_result: RunResult | None = None
        self._last_verification: VerificationResult | None = None

        self._cancel_event = asyncio.Event()
        self._active_task: asyncio.Task[RunResult] | None = None
        self._run_lock = asyncio.Lock()

    @property
    def state(self) -> EngineState:
        return self._state

    @property
    def clock_offset_ms(self) -> int | None:
        return self._clock_offset_ms

    @property
    def last_result(self) -> RunResult | None:
        return self._last_result

    @property
    def last_verification(self) -> VerificationResult | None:
        return self._last_verification

    @staticmethod
    def calculate_clock_offset_ms(
        server_ts_ms: int,
        local_before_ms: int,
        local_after_ms: int | None = None,
    ) -> int:
        """Compute (server_now_ms - local_midpoint_ms). Positive means server clock is ahead of local clock."""
        midpoint_ms = (
            local_before_ms
            if local_after_ms is None
            else (local_before_ms + local_after_ms) // 2
        )
        return int(server_ts_ms - midpoint_ms)

    @staticmethod
    def compute_next_target_datetime(target_time_str: str, now: datetime) -> datetime:
        """Resolve the next scheduled trigger datetime; rolls to tomorrow if >5s past today's target."""
        parts = target_time_str.strip().split(":")
        if len(parts) == 2:
            norm_time = f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:00"
        else:
            norm_time = target_time_str.strip()
        today_target = datetime.strptime(
            f"{now.strftime('%Y-%m-%d')} {norm_time}",
            "%Y-%m-%d %H:%M:%S",
        )
        if now > today_target and (now - today_target).total_seconds() > 5.0:
            today_target += timedelta(days=1)
        return today_target

    def calibrated_remaining_seconds(self, target_dt: datetime) -> float:
        """Return remaining seconds until server clock reaches target_dt using calibrated offset."""
        target_epoch_ms = int(target_dt.timestamp() * 1000)
        estimated_server_ms = self._epoch_ms_fn() + (self._clock_offset_ms or 0)
        return (target_epoch_ms - estimated_server_ms) / 1000.0

    def get_status(self) -> RuntimeStatus:
        """Return live RuntimeStatus snapshot for GET /api/runtime/status and SSE events."""
        cfg = self._config_service.get_config()
        target_time = self._target_time_str or cfg.schedule.targetTime
        target_day = self._target_day_str or (
            self._now_fn() + timedelta(days=cfg.reservation.reserveDaysAhead)
        ).strftime("%Y-%m-%d")

        win_task_info: dict[str, Any] = {}
        if self._scheduler is not None:
            try:
                win_task_info = self._scheduler.get_status()
            except Exception as exc:
                win_task_info = {"enabled": False, "error": str(exc)}

        return RuntimeStatus(
            state=self._state,
            targetTime=target_time,
            targetDay=target_day,
            countdownSec=self._countdown_sec,
            clockOffsetMs=self._clock_offset_ms,
            lastResult=self._last_result,
            verification=self._last_verification,
            winTask=win_task_info,
        )

    def _set_state(self, new_state: EngineState) -> None:
        self._state = new_state
        self._publish_status()

    def _publish_status(self) -> None:
        try:
            self._bus.publish_status(self.get_status())
        except Exception:
            pass

    async def calibrate_clock(self, dept_id_enc: str, room_id: str, day: str) -> int | None:
        """Query server timestamp and compute RTT-compensated clock offset in milliseconds."""
        if self._catalog is None:
            return None

        t0_ms = self._epoch_ms_fn()
        try:
            server_ms = await _maybe_await(
                self._catalog.get_server_timestamp_ms(dept_id_enc, room_id, day)
            )
        except Exception as exc:
            self._bus.warning(
                "CLOCK",
                f"服务器时钟校准请求异常，将回退使用本机时钟: {exc}",
                detail={"roomId": room_id, "day": day},
            )
            return None
        t1_ms = self._epoch_ms_fn()

        if server_ms is None:
            self._bus.warning(
                "CLOCK",
                "未获取到服务器时间戳，使用本机时钟作为基准",
                detail={"roomId": room_id, "day": day},
            )
            return None

        offset_ms = self.calculate_clock_offset_ms(server_ms, t0_ms, t1_ms)
        rtt_ms = max(0, t1_ms - t0_ms)
        self._clock_offset_ms = offset_ms
        self._bus.info(
            "CLOCK",
            f"已完成超星服务器时钟校准: 偏移 {offset_ms:+d} ms (RTT {rtt_ms} ms)",
            detail={
                "serverTimestampMs": server_ms,
                "clockOffsetMs": offset_ms,
                "rttMs": rtt_ms,
            },
        )
        self._publish_status()
        return offset_ms

    def start_scheduled(
        self,
        max_duration_sec: float = DEFAULT_MAX_DURATION_SEC,
    ) -> asyncio.Task[RunResult]:
        """Launch the scheduled wait -> prewarm -> snipe -> verify pipeline in the background."""
        if self._active_task is not None and not self._active_task.done():
            return self._active_task

        self._cancel_event.clear()
        self._active_task = asyncio.create_task(
            self.run_scheduled(max_duration_sec=max_duration_sec),
            name="chaoxing-scheduled-reserve",
        )
        return self._active_task

    async def cancel(self) -> None:
        """Cancel any active scheduled countdown or running execution and reset engine state to IDLE."""
        self._cancel_event.set()
        task = self._active_task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._active_task = None
        self._countdown_sec = None
        await self._safe_close_executor()
        self._set_state(EngineState.IDLE)
        self._bus.info("ENGINE", "预约引擎已手动停止并重置为空闲状态")

    async def verify_now(self, target_day: str | None = None) -> VerificationResult:
        """Perform a standalone read-only server reservation verification check.

        When ``target_day`` is None or blank, queries all active server reservations
        (pass ``target_day=None`` to ``executor.verify_reservation``).
        """
        cfg = self._config_service.get_config()
        dept_id_enc = cfg.reservation.deptIdEnc
        resolved_day: str | None = (
            target_day.strip() if target_day and target_day.strip() else None
        )
        day_label = resolved_day or "全部有效预约"

        can_update_state = self._state in {EngineState.IDLE, EngineState.FAILED, EngineState.COMPLETED}
        prev_state = self._state
        if can_update_state:
            self._set_state(EngineState.VERIFYING)

        self._bus.info(
            "VERIFY",
            f"正在向超星服务器查询预约状态 (查询范围: {day_label})",
            detail={"deptIdEnc": dept_id_enc, "targetDay": resolved_day},
        )
        verification = await self._safe_verify(dept_id_enc, resolved_day)
        self._last_verification = verification

        if verification.verified:
            if verification.matched_record is not None:
                rec = verification.matched_record
                self._bus.info(
                    "VERIFY",
                    f"服务端核验确认存在有效预约: {rec.room_name} 座位 {rec.seat_num} ({rec.day} {rec.start_time}-{rec.end_time})",
                    detail=rec.model_dump(),
                )
            else:
                self._bus.info(
                    "VERIFY",
                    verification.message or "服务端核验确认存在有效预约",
                    detail={"targetDay": resolved_day, "activeCount": len(verification.active_records)},
                )
            if can_update_state:
                self._set_state(EngineState.COMPLETED)
            else:
                self._publish_status()
        else:
            self._bus.info(
                "VERIFY",
                verification.message or f"服务端未查询到 {day_label} 的有效预约记录",
                detail={
                    "targetDay": resolved_day,
                    "activeCount": len(verification.active_records),
                },
            )
            if can_update_state:
                # Reset COMPLETED -> IDLE on negative verification; preserve FAILED if previously failed.
                next_state = EngineState.FAILED if prev_state == EngineState.FAILED else EngineState.IDLE
                self._set_state(next_state)
            else:
                self._publish_status()

        return verification

    async def run_scheduled(
        self,
        max_duration_sec: float = DEFAULT_MAX_DURATION_SEC,
        prepare_seconds_override: float | None = None,
    ) -> RunResult:
        """Execute scheduled wait -> prewarm & clock calibration -> snipe -> mandatory verification."""
        async with self._run_lock:
            self._cancel_event.clear()
            cfg = self._config_service.get_config()
            now = self._now_fn()
            today_str = now.strftime("%Y-%m-%d")

            if cfg.schedule.skipDate == today_str:
                self._bus.info(
                    "SCHEDULE",
                    f"已设置仅跳过今晚 ({today_str})，本次静默退出并自动清除跳过标记",
                    detail={"skipDate": today_str},
                )
                self._config_service.update_config({"schedule": {"skipDate": None}})
                res = RunResult(
                    success=True,
                    verified=False,
                    message=f"已按设置跳过今晚 ({today_str}) 预约，明晚自动恢复",
                )
                self._last_result = res
                self._countdown_sec = None
                self._set_state(EngineState.IDLE)
                return res

            if not cfg.reservation.targetSeats:
                msg = "未配置候选座位，请先在座位图中选择至少一个目标座位"
                self._bus.error("ENGINE", msg)
                res = RunResult(success=False, message=msg)
                self._last_result = res
                self._set_state(EngineState.FAILED)
                return res

            target_dt = self.compute_next_target_datetime(cfg.schedule.targetTime, now)
            target_day = (
                target_dt.date() + timedelta(days=cfg.reservation.reserveDaysAhead)
            ).strftime("%Y-%m-%d")

            self._target_time_str = cfg.schedule.targetTime
            self._target_day_str = target_day

            if prepare_seconds_override is not None:
                prepare_sec = max(0.0, float(prepare_seconds_override))
            elif cfg.schedule.prepareSecondsAhead < self.MIN_EFFECTIVE_PREPARE_SEC:
                prepare_sec = float(self.DEFAULT_EFFECTIVE_PREPARE_SEC)
            else:
                prepare_sec = float(cfg.schedule.prepareSecondsAhead)

            self._set_state(EngineState.WAITING)
            self._bus.info(
                "SCHEDULE",
                f"已启动定时值守: 目标开抢时间 {target_dt.strftime('%Y-%m-%d %H:%M:%S')}，预约日期 {target_day}，提前 {int(prepare_sec)} 秒预热",
                detail={
                    "targetDatetime": target_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "targetDay": target_day,
                    "roomId": cfg.reservation.roomId,
                    "targetSeats": cfg.reservation.targetSeats,
                    "prepareSecondsAhead": prepare_sec,
                },
            )

            try:
                # Stage 1: Wait until pre-warming window
                while not self._cancel_event.is_set():
                    rem = self.calibrated_remaining_seconds(target_dt)
                    self._countdown_sec = round(max(0.0, rem), 2)
                    self._publish_status()
                    if rem <= prepare_sec:
                        break
                    sleep_step = min(1.0, max(0.01, rem - prepare_sec))
                    await self._sleep_fn(sleep_step)

                if self._cancel_event.is_set():
                    return self._build_cancelled_result()

                # Stage 2: Pre-warm session & calibrate server clock
                cfg = self._config_service.get_config()
                self._set_state(EngineState.PREWARMING)
                self._bus.info(
                    "PREWARM",
                    "进入预热阶段: 正在预热登录会话并校准服务器时钟",
                    detail={"roomId": cfg.reservation.roomId, "targetDay": target_day},
                )
                await self._safe_prepare_session(cfg)
                await self.calibrate_clock(
                    cfg.reservation.deptIdEnc,
                    cfg.reservation.roomId,
                    target_day,
                )

                # Stage 3: Precision countdown using calibrated server clock
                while not self._cancel_event.is_set():
                    rem = self.calibrated_remaining_seconds(target_dt)
                    self._countdown_sec = round(max(0.0, rem), 3)
                    if rem <= 0.0:
                        break
                    if rem > 0.6:
                        self._publish_status()
                        await self._sleep_fn(min(0.25, rem - 0.5))
                    else:
                        await self._sleep_fn(min(0.01, max(0.002, rem)))

                if self._cancel_event.is_set():
                    return self._build_cancelled_result()

                self._countdown_sec = 0.0
                cfg = self._config_service.get_config()
                return await self._execute_snipe_and_verify(
                    cfg,
                    target_day=target_day,
                    prewarmed=True,
                    max_duration_sec=max_duration_sec,
                )
            except asyncio.CancelledError:
                self._countdown_sec = None
                await self._safe_close_executor()
                self._set_state(EngineState.IDLE)
                raise
            finally:
                await self._safe_close_executor()

    async def run_once(
        self,
        target_day: str | None = None,
        max_duration_sec: float = DEFAULT_MAX_DURATION_SEC,
        dry_verify_only: bool = False,
    ) -> RunResult:
        """Execute an immediate reservation run (or dry verification only) with mandatory verification."""
        if dry_verify_only:
            verification = await self.verify_now(target_day=target_day)
            matched_seat = (
                verification.matched_record.seat_num
                if verification.verified and verification.matched_record
                else None
            )
            res = RunResult(
                success=verification.verified,
                locked_seat=matched_seat,
                verified=verification.verified,
                verification=verification,
                attempts=0,
                elapsed_sec=0.0,
                message=verification.message
                or ("服务端核验通过" if verification.verified else "未检测到有效预约记录"),
            )
            self._last_result = res
            self._publish_status()
            return res

        async with self._run_lock:
            self._cancel_event.clear()
            cfg = self._config_service.get_config()
            resolved_day = (
                target_day.strip()
                if target_day and target_day.strip()
                else (
                    self._now_fn() + timedelta(days=cfg.reservation.reserveDaysAhead)
                ).strftime("%Y-%m-%d")
            )
            self._target_time_str = cfg.schedule.targetTime
            self._target_day_str = resolved_day
            self._countdown_sec = 0.0

            if not cfg.reservation.targetSeats:
                msg = "未配置候选座位，请先在座位图中选择至少一个目标座位"
                self._bus.error("ENGINE", msg)
                res = RunResult(success=False, message=msg)
                self._last_result = res
                self._set_state(EngineState.FAILED)
                return res

            try:
                return await self._execute_snipe_and_verify(
                    cfg,
                    target_day=resolved_day,
                    prewarmed=False,
                    max_duration_sec=max_duration_sec,
                )
            finally:
                await self._safe_close_executor()

    async def _execute_snipe_and_verify(
        self,
        cfg: AppConfig,
        target_day: str,
        prewarmed: bool,
        max_duration_sec: float,
    ) -> RunResult:
        dept_id_enc = cfg.reservation.deptIdEnc
        room_id = cfg.reservation.roomId
        start_time = cfg.reservation.startTime
        end_time = cfg.reservation.endTime
        active_seats = list(cfg.reservation.targetSeats)

        if not prewarmed:
            self._set_state(EngineState.PREWARMING)
            self._bus.info(
                "PREWARM",
                "正在初始化登录会话以准备立即抢座",
                detail={"roomId": room_id, "targetDay": target_day},
            )
            await self._safe_prepare_session(cfg)
            await self.calibrate_clock(dept_id_enc, room_id, target_day)

        if self._cancel_event.is_set():
            return self._build_cancelled_result()

        self._set_state(EngineState.RUNNING)
        self._bus.info(
            "SNIPE",
            f"开始执行优先级抢座: 自习室 {room_id}，日期 {target_day} ({start_time}-{end_time})，候选顺位 {active_seats}",
            detail={
                "roomId": room_id,
                "targetDay": target_day,
                "startTime": start_time,
                "endTime": end_time,
                "targetSeats": active_seats,
            },
        )

        loop_start_ms = self._epoch_ms_fn()
        max_duration_ms = int(max(0.1, max_duration_sec) * 1000)

        try:
            page_ok = await _maybe_await(
                self._executor.open_select_page(dept_id_enc, room_id, target_day)
            )
            if not page_ok:
                self._bus.warning(
                    "SNIPE",
                    "首次打开选座页面返回未就绪，将在循环中继续重试",
                    detail={"roomId": room_id, "targetDay": target_day},
                )
        except Exception as exc:
            self._bus.exception("SNIPE", "打开选座页面发生异常，将继续尝试恢复", exc)

        attempts = 0
        round_num = 0
        tentative_seat: str | None = None
        client_reported_success = False
        fatal_aborted = False
        last_raw_msg = ""

        while active_seats and not self._cancel_event.is_set():
            elapsed_ms = self._epoch_ms_fn() - loop_start_ms
            if attempts > 0 and elapsed_ms >= max_duration_ms:
                self._bus.warning(
                    "SNIPE",
                    f"已达到最大抢座持续时长 ({max_duration_sec:.1f}s)，结束提交循环并进入服务端核验",
                    detail={"attempts": attempts, "elapsedMs": elapsed_ms},
                )
                break

            round_num += 1
            reopened_mid_round = False

            for seat_num in list(active_seats):
                if self._cancel_event.is_set():
                    break
                if attempts > 0 and (self._epoch_ms_fn() - loop_start_ms) >= max_duration_ms:
                    break

                attempt = await self._safe_submit_seat(
                    dept_id_enc=dept_id_enc,
                    room_id=room_id,
                    seat_num=seat_num,
                    day=target_day,
                    start_time=start_time,
                    end_time=end_time,
                )
                attempts += 1
                last_raw_msg = attempt.raw_msg

                self._bus.info(
                    "SNIPE",
                    f"[第 {round_num} 轮] 座位 {seat_num} -> {attempt.outcome.value}: {attempt.raw_msg}",
                    detail={
                        "round": round_num,
                        "seatNum": seat_num,
                        "outcome": attempt.outcome.value,
                        "latencyMs": round(attempt.latency_ms, 1),
                        "rawMsg": attempt.raw_msg,
                    },
                )

                if attempt.outcome == AttemptOutcome.SUCCESS:
                    tentative_seat = seat_num
                    client_reported_success = True
                    break

                if attempt.outcome == AttemptOutcome.ALREADY_RESERVED:
                    tentative_seat = seat_num
                    client_reported_success = True
                    self._bus.info(
                        "SNIPE",
                        f"检测到当前时段已有生效预约 ({attempt.raw_msg})，停止提交并转入服务端核验",
                    )
                    break

                if attempt.outcome == AttemptOutcome.OCCUPIED:
                    active_seats.remove(seat_num)
                    self._bus.info(
                        "SNIPE",
                        f"座位 {seat_num} 已被他人占用，已从本轮及后续轮次永久剔除，立即顺延下一顺位",
                        detail={"droppedSeat": seat_num, "remainingSeats": list(active_seats)},
                    )
                    continue

                if attempt.outcome == AttemptOutcome.NOT_OPEN_OR_EXPIRED:
                    self._bus.warning(
                        "SNIPE",
                        f"预约通道未开放或页面令牌过期 ({attempt.raw_msg})，正在刷新选座页面重试",
                    )
                    try:
                        await _maybe_await(
                            self._executor.open_select_page(dept_id_enc, room_id, target_day)
                        )
                    except Exception as exc:
                        self._bus.exception("SNIPE", "刷新选座页面异常", exc)
                    if self._retry_delay_sec > 0:
                        await self._sleep_fn(self._retry_delay_sec)
                    reopened_mid_round = True
                    break

                if attempt.outcome == AttemptOutcome.NETWORK_ERROR:
                    self._bus.warning(
                        "SNIPE",
                        f"座位 {seat_num} 请求网络超时或异常 ({attempt.latency_ms:.0f}ms)，顺延尝试下一候选座位",
                        detail={
                            "seatNum": seat_num,
                            "latencyMs": round(attempt.latency_ms, 1),
                            "rawMsg": attempt.raw_msg,
                        },
                    )
                    continue

                if attempt.outcome == AttemptOutcome.FATAL_ERROR:
                    self._bus.error(
                        "SNIPE",
                        f"遇到不可恢复错误，终止提交循环并执行兜底核验: {attempt.raw_msg}",
                    )
                    fatal_aborted = True
                    break

            if client_reported_success or fatal_aborted or not active_seats:
                break

            if not reopened_mid_round and self._retry_delay_sec > 0:
                await self._sleep_fn(self._retry_delay_sec)

        if self._cancel_event.is_set():
            return self._build_cancelled_result()

        # Mandatory Post-Execution Server Verification
        self._set_state(EngineState.VERIFYING)
        self._bus.info(
            "VERIFY",
            "提交阶段结束，正在执行强制服务端预约结果核验",
            detail={
                "targetDay": target_day,
                "attempts": attempts,
                "clientReportedSuccess": client_reported_success,
            },
        )
        verification = await self._safe_verify(dept_id_enc, target_day)
        if not verification.verified and client_reported_success and self._retry_delay_sec > 0:
            await self._sleep_fn(min(0.3, self._retry_delay_sec * 4))
            verification = await self._safe_verify(dept_id_enc, target_day)

        self._last_verification = verification
        elapsed_sec = round(max(0.0, (self._epoch_ms_fn() - loop_start_ms) / 1000.0), 3)

        if verification.verified:
            locked_seat = (
                verification.matched_record.seat_num
                if verification.matched_record and verification.matched_record.seat_num
                else tentative_seat
            )
            room_display = (
                verification.matched_record.room_name
                if verification.matched_record and verification.matched_record.room_name
                else (cfg.reservation.roomName or room_id)
            )
            summary_msg = (
                verification.message
                or f"服务端已确认预约成功: {room_display} 座位 {locked_seat} ({target_day} {start_time}-{end_time})"
            )
            result = RunResult(
                success=True,
                locked_seat=locked_seat,
                verified=True,
                verification=verification,
                attempts=attempts,
                elapsed_sec=elapsed_sec,
                message=summary_msg,
            )
            self._last_result = result
            self._set_state(EngineState.COMPLETED)
            self._bus.info(
                "VERIFY",
                summary_msg,
                detail={
                    "lockedSeat": locked_seat,
                    "verified": True,
                    "attempts": attempts,
                    "elapsedSec": elapsed_sec,
                },
            )
            await self._safe_notify(True, "图书馆预约核验成功", summary_msg)
            return result

        if client_reported_success and tentative_seat:
            summary_msg = (
                f"客户端提交座位 {tentative_seat} 返回成功，但服务端预约列表暂未检索到对应记录"
            )
            result = RunResult(
                success=True,
                locked_seat=tentative_seat,
                verified=False,
                verification=verification,
                attempts=attempts,
                elapsed_sec=elapsed_sec,
                message=summary_msg,
            )
            self._last_result = result
            self._set_state(EngineState.COMPLETED)
            self._bus.warning("VERIFY", summary_msg, detail=result.model_dump())
            await self._safe_notify(True, "座位提交成功 (待服务端同步)", summary_msg)
            return result

        if not active_seats:
            fail_reason = "所有候选顺位座位均已被占用，且服务端核验未发现成功预约"
        elif fatal_aborted:
            fail_reason = f"抢座因严重错误中止: {last_raw_msg}"
        else:
            fail_reason = f"抢座超时未锁定座位 ({attempts} 次尝试): {last_raw_msg or '无可用座位'}"

        result = RunResult(
            success=False,
            locked_seat=None,
            verified=False,
            verification=verification,
            attempts=attempts,
            elapsed_sec=elapsed_sec,
            message=fail_reason,
        )
        self._last_result = result
        self._set_state(EngineState.FAILED)
        self._bus.error(
            "VERIFY",
            fail_reason,
            detail={"attempts": attempts, "elapsedSec": elapsed_sec},
        )
        await self._safe_notify(False, "图书馆抢座未成功", fail_reason)
        return result

    async def _safe_prepare_session(self, cfg: AppConfig) -> None:
        phone = cfg.auth.credentials.phone.strip()
        password = cfg.auth.credentials.password
        dept_id_enc = cfg.reservation.deptIdEnc
        try:
            await _maybe_await(self._executor.prepare_session(phone, password, dept_id_enc))
            self._bus.info("PREWARM", "会话预热就绪")
        except Exception as exc:
            self._bus.exception("PREWARM", "会话预热出现异常，将在进入选座页时继续尝试", exc)

    async def _safe_submit_seat(
        self,
        dept_id_enc: str,
        room_id: str,
        seat_num: str,
        day: str,
        start_time: str,
        end_time: str,
    ) -> AttemptResult:
        t0_ms = self._epoch_ms_fn()
        try:
            return await _maybe_await(
                self._executor.submit_seat(
                    dept_id_enc,
                    room_id,
                    seat_num,
                    day,
                    start_time,
                    end_time,
                )
            )
        except Exception as exc:
            latency_ms = float(max(0, self._epoch_ms_fn() - t0_ms))
            self._bus.exception(
                "SNIPE",
                f"提交座位 {seat_num} 时抛出未捕获异常",
                exc,
                detail={"seatNum": seat_num, "latencyMs": latency_ms},
            )
            return AttemptResult(
                seat_num=seat_num,
                outcome=AttemptOutcome.NETWORK_ERROR,
                raw_msg=f"请求异常: {exc}",
                latency_ms=latency_ms,
            )

    async def _safe_verify(self, dept_id_enc: str, target_day: str | None) -> VerificationResult:
        checked_at = self._now_fn().strftime("%Y-%m-%d %H:%M:%S")
        try:
            res: VerificationResult = await _maybe_await(
                self._executor.verify_reservation(dept_id_enc, target_day)
            )
            if not res.checked_at:
                res = res.model_copy(update={"checked_at": checked_at})
            return res
        except Exception as exc:
            self._bus.exception(
                "VERIFY",
                f"向服务器核验预约状态异常 (targetDay={target_day or 'ALL'})",
                exc,
            )
            return VerificationResult(
                verified=False,
                matched_record=None,
                active_records=[],
                message=f"核验接口异常: {exc}",
                checked_at=checked_at,
            )

    async def _safe_notify(self, success: bool, title: str, message: str) -> None:
        if self._notifier is None:
            return
        try:
            if inspect.iscoroutinefunction(self._notifier.notify):
                await self._notifier.notify(success, title, message)
            else:
                await asyncio.to_thread(self._notifier.notify, success, title, message)
        except Exception as exc:
            self._bus.exception("NOTIFY", "发送完成通知异常", exc)

    async def _safe_close_executor(self) -> None:
        try:
            await _maybe_await(self._executor.close())
        except Exception as exc:
            self._bus.exception("ENGINE", "关闭执行器资源时出现异常", exc)

    def _build_cancelled_result(self) -> RunResult:
        self._countdown_sec = None
        res = RunResult(success=False, message="任务已手动取消")
        self._last_result = res
        self._set_state(EngineState.IDLE)
        return res
