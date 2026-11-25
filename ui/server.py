from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from adapters.beep_notifier import BeepNotifier
from adapters.chaoxing_adapter import ChaoxingAdapter
from adapters.json_config_store import JsonConfigStore
from adapters.win_task_scheduler import WinTaskSchedulerAdapter
from domain.models import (
    RoomSeatMap,
    RunResult,
    RuntimeStatus,
    SanitizedConfigDTO,
    VerificationResult,
)
from domain.paths import get_app_dir, get_resource_dir
from services.config_service import ConfigService
from services.library_service import LibraryService
from services.log_bus import LogBus, get_log_bus, mask_phone_number
from services.reserve_service import ReserveService


def _resolve_frontend_dist_dir() -> Path:
    resource_dist = get_resource_dir() / "frontend" / "dist"
    if (resource_dist / "index.html").exists():
        return resource_dist
    return get_app_dir() / "frontend" / "dist"


FRONTEND_DIST_DIR = _resolve_frontend_dist_dir()


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    phone: str
    password: str = ""
    deptIdEnc: str | None = None


class ConfigPatchRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    reservation: dict[str, Any] | None = None
    schedule: dict[str, Any] | None = None


class RunNowRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dryVerifyOnly: bool = False
    targetDay: str | None = None


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    targetDay: str | None = None


class WindowsSchedulerRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool
    runTime: str | None = None


class AppContainer:
    """Dependency container wiring upstream services to concrete downstream adapters."""

    def __init__(
        self,
        *,
        bus: LogBus | None = None,
        config_store: JsonConfigStore | None = None,
        chaoxing: ChaoxingAdapter | None = None,
        notifier: BeepNotifier | None = None,
        win_scheduler: WinTaskSchedulerAdapter | None = None,
    ) -> None:
        self.bus = bus or get_log_bus()
        self.config_store = config_store or JsonConfigStore(bus=self.bus)
        self.config_service = ConfigService(store=self.config_store, bus=self.bus)
        self.chaoxing = chaoxing or ChaoxingAdapter(
            config_store=self.config_store,
            bus=self.bus,
        )
        self.notifier = notifier or BeepNotifier(enabled=True, blocking=False, bus=self.bus)
        self.win_scheduler = win_scheduler or WinTaskSchedulerAdapter(bus=self.bus)
        self.library_service = LibraryService(
            catalog=self.chaoxing,
            config_service=self.config_service,
            bus=self.bus,
        )
        self.reserve_service = ReserveService(
            executor=self.chaoxing,
            config_service=self.config_service,
            catalog=self.chaoxing,
            notifier=self.notifier,
            scheduler=self.win_scheduler,
            bus=self.bus,
        )
        self.shutdown_event = asyncio.Event()

    async def shutdown(self) -> None:
        """Signal SSE streams to close and release active engine/Playwright resources."""
        self.shutdown_event.set()
        try:
            await self.reserve_service.cancel()
        except Exception:
            pass
        try:
            await self.chaoxing.close()
        except Exception:
            pass


def create_app(container: AppContainer | None = None) -> FastAPI:
    """Create the local FastAPI application serving REST, SSE, and built frontend assets."""
    ctx = container or AppContainer()
    dist_dir = _resolve_frontend_dist_dir()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        ctx.shutdown_event.clear()
        cfg = ctx.config_service.get_config()
        ctx.bus.info(
            "SERVER",
            "砚席本地服务已启动就绪",
            detail={
                "roomId": cfg.reservation.roomId,
                "targetSeatsCount": len(cfg.reservation.targetSeats),
                "frontendBuilt": (dist_dir / "index.html").exists(),
                "frontendDir": str(dist_dir),
            },
        )
        try:
            yield
        finally:
            await ctx.shutdown()

    app = FastAPI(
        title="砚席 · 图书馆智能选座服务",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.container = ctx

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:18765",
            "http://localhost:18765",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def get_health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/config", response_model=SanitizedConfigDTO)
    async def get_config() -> SanitizedConfigDTO:
        dto = ctx.config_service.get_sanitized_config(logged_in=ctx.chaoxing.logged_in_cached)
        win_status = ctx.win_scheduler.get_status()
        if win_status.get("exists"):
            dto.schedule.winTaskEnabled = bool(win_status.get("enabled", False))
            if win_status.get("runTime"):
                dto.schedule.winTaskTime = str(win_status["runTime"])
        return dto

    @app.put("/api/config", response_model=SanitizedConfigDTO)
    async def put_config(req: ConfigPatchRequest) -> SanitizedConfigDTO:
        patch_dict = req.model_dump(exclude_none=True)
        sched_patch = patch_dict.get("schedule")
        if isinstance(sched_patch, dict) and "skipDate" in sched_patch:
            raw_skip = sched_patch["skipDate"]
            if raw_skip is None or (isinstance(raw_skip, str) and not raw_skip.strip()):
                sched_patch["skipDate"] = None
            elif isinstance(raw_skip, str):
                sched_patch["skipDate"] = raw_skip.strip()
        try:
            ctx.config_service.update_config(patch_dict)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return await get_config()

    @app.post("/api/auth/login")
    async def auth_login(req: LoginRequest) -> dict[str, Any]:
        current_cfg = ctx.config_service.get_config()
        phone = req.phone.strip()
        password = req.password
        if password in ("", "******") and phone == current_cfg.auth.credentials.phone.strip():
            password = current_cfg.auth.credentials.password

        dept_id = (req.deptIdEnc or current_cfg.reservation.deptIdEnc or "df23c42633fa45e1").strip()
        prev_dept = current_cfg.reservation.deptIdEnc

        ok, message = await ctx.chaoxing.login(
            phone=phone,
            password=password,
            dept_id_enc=dept_id,
        )
        if ok:
            ctx.config_service.update_credentials(
                phone=phone,
                password=password,
                dept_id_enc=dept_id,
            )
            if dept_id != prev_dept:
                ctx.library_service.clear_cache()

        return {
            "success": ok,
            "message": message,
            "phoneMasked": mask_phone_number(phone),
        }

    @app.get("/api/auth/status")
    async def auth_status() -> dict[str, Any]:
        cfg = ctx.config_service.get_config()
        phone = cfg.auth.credentials.phone.strip()
        has_pwd = bool(cfg.auth.credentials.password)
        ok, msg = await ctx.chaoxing.check_session(cfg.reservation.deptIdEnc)
        return {
            "loggedIn": ok,
            "message": msg,
            "phoneMasked": mask_phone_number(phone),
            "phone": phone,
            "hasPassword": has_pwd,
        }

    @app.get("/api/rooms")
    async def list_rooms(
        day: str | None = Query(default=None),
        refresh: bool = Query(default=False),
    ) -> dict[str, Any]:
        try:
            rooms = await ctx.library_service.list_rooms(day=day, force_refresh=refresh)
            return {"rooms": rooms}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/rooms/{room_id}/seats", response_model=RoomSeatMap)
    async def get_room_seats(
        room_id: str,
        refresh: bool = Query(default=False),
    ) -> RoomSeatMap:
        try:
            return await ctx.library_service.get_room_seat_map(room_id, force_refresh=refresh)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/rooms/{room_id}/background")
    async def get_room_background(room_id: str) -> Response:
        try:
            img_bytes, content_type = await ctx.library_service.get_background_image(room_id)
            return Response(
                content=img_bytes,
                media_type=content_type,
                headers={"Cache-Control": "public, max-age=86400"},
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/runtime/status", response_model=RuntimeStatus)
    async def get_runtime_status() -> RuntimeStatus:
        return ctx.reserve_service.get_status()

    @app.post("/api/runtime/start-schedule", response_model=RuntimeStatus)
    async def start_schedule() -> RuntimeStatus:
        ctx.reserve_service.start_scheduled()
        await asyncio.sleep(0.02)
        return ctx.reserve_service.get_status()

    @app.post("/api/runtime/stop", response_model=RuntimeStatus)
    async def stop_runtime() -> RuntimeStatus:
        await ctx.reserve_service.cancel()
        return ctx.reserve_service.get_status()

    @app.post("/api/runtime/run-now", response_model=RunResult)
    async def run_now(req: RunNowRequest | None = None) -> RunResult:
        opts = req or RunNowRequest()
        return await ctx.reserve_service.run_once(
            target_day=opts.targetDay,
            dry_verify_only=opts.dryVerifyOnly,
        )

    @app.post("/api/runtime/verify", response_model=VerificationResult)
    async def verify_runtime(req: VerifyRequest | None = None) -> VerificationResult:
        opts = req or VerifyRequest()
        clean_day = opts.targetDay.strip() if opts.targetDay and opts.targetDay.strip() else None
        return await ctx.reserve_service.verify_now(target_day=clean_day)

    @app.post("/api/scheduler/windows")
    async def configure_windows_scheduler(req: WindowsSchedulerRequest) -> dict[str, Any]:
        cfg = ctx.config_service.get_config()
        run_time = (req.runTime or cfg.schedule.winTaskTime or "21:59").strip()
        try:
            ok, msg = ctx.win_scheduler.set_enabled(enabled=req.enabled, run_time=run_time)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        status_dict = ctx.win_scheduler.get_status(force_refresh=True)
        if ok:
            try:
                ctx.config_service.update_config(
                    {
                        "schedule": {
                            "winTaskEnabled": req.enabled,
                            "winTaskTime": status_dict.get("runTime", run_time),
                        }
                    }
                )
            except Exception:
                pass
        ctx.bus.publish_status(ctx.reserve_service.get_status())
        return {
            "success": ok,
            "message": msg,
            "status": status_dict,
        }

    @app.get("/api/logs")
    async def get_logs(
        limit: int = Query(default=300, ge=1, le=1000),
        level: str | None = Query(default=None),
    ) -> dict[str, Any]:
        entries = ctx.bus.get_entries(limit=limit, level_filter=level)
        return {
            "logFile": str(ctx.bus.log_file),
            "entries": entries,
        }

    @app.get("/api/logs/stream")
    async def stream_logs(request: Request) -> StreamingResponse:
        async def event_generator() -> AsyncIterator[str]:
            sub_id, queue = ctx.bus.subscribe(max_queue_size=256)
            idle_ticks = 0
            try:
                initial_status = ctx.reserve_service.get_status().model_dump(mode="json")
                yield f"event: status\ndata: {json.dumps(initial_status, ensure_ascii=False)}\n\n"

                while not ctx.shutdown_event.is_set():
                    if await request.is_disconnected():
                        break
                    try:
                        event_type, payload = await asyncio.wait_for(queue.get(), timeout=1.5)
                        idle_ticks = 0
                        if hasattr(payload, "model_dump"):
                            serialized = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False)
                        else:
                            serialized = json.dumps(payload, ensure_ascii=False, default=str)
                        yield f"event: {event_type}\ndata: {serialized}\n\n"
                    except TimeoutError:
                        idle_ticks += 1
                        if idle_ticks >= 10:
                            idle_ticks = 0
                            yield ": keepalive\n\n"
            finally:
                ctx.bus.unsubscribe(sub_id)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Serve static SPA frontend when frontend/dist/index.html is built
    dist_index = dist_dir / "index.html"
    if dist_index.exists():
        assets_dir = dist_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend-assets")

        @app.get("/", include_in_schema=False)
        async def serve_spa_index() -> FileResponse:
            return FileResponse(dist_index)

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa_fallback(full_path: str) -> FileResponse:
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="API endpoint not found")
            resolved_root = dist_dir.resolve()
            candidate = (dist_dir / full_path).resolve()
            if (
                candidate != resolved_root
                and str(candidate).startswith(str(resolved_root))
                and candidate.is_file()
            ):
                return FileResponse(candidate)
            return FileResponse(dist_index)

    return app
