from __future__ import annotations

import inspect
import time
from datetime import datetime, timedelta
from typing import Any

from domain.models import Room, RoomSeatMap, SeatNode
from domain.ports import SeatCatalogPort
from services.config_service import ConfigService
from services.log_bus import LogBus, get_log_bus


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class LibraryService:
    """Upstream service for reading room discovery, deterministic seat-map layout, and floor-plan caching."""

    DEFAULT_DEPT_ID_ENC = "df23c42633fa45e1"

    def __init__(
        self,
        catalog: SeatCatalogPort,
        config_service: ConfigService | None = None,
        bus: LogBus | None = None,
    ) -> None:
        self._catalog = catalog
        self._config_service = config_service
        self._bus = bus or get_log_bus()
        self._rooms_cache: dict[tuple[str, str], list[Room]] = {}
        self._rooms_by_id: dict[str, Room] = {}
        self._seat_map_cache: dict[str, RoomSeatMap] = {}
        self._bg_cache: dict[str, tuple[bytes, str]] = {}

    def _resolve_dept_and_day(
        self,
        dept_id_enc: str | None = None,
        day: str | None = None,
    ) -> tuple[str, str]:
        cfg = self._config_service.get_config() if self._config_service is not None else None
        resolved_dept = (
            (dept_id_enc or "").strip()
            or (cfg.reservation.deptIdEnc if cfg else "")
            or self.DEFAULT_DEPT_ID_ENC
        )
        if day and day.strip():
            resolved_day = day.strip()
        else:
            days_ahead = cfg.reservation.reserveDaysAhead if cfg else 1
            resolved_day = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        return resolved_dept, resolved_day

    async def list_rooms(
        self,
        dept_id_enc: str | None = None,
        day: str | None = None,
        force_refresh: bool = False,
    ) -> list[Room]:
        """Fetch and cache reading rooms for the department and target date."""
        resolved_dept, resolved_day = self._resolve_dept_and_day(dept_id_enc, day)
        cache_key = (resolved_dept, resolved_day)
        if not force_refresh and cache_key in self._rooms_cache:
            return list(self._rooms_cache[cache_key])

        t0 = time.perf_counter()
        try:
            rooms: list[Room] = await _maybe_await(
                self._catalog.list_rooms(resolved_dept, resolved_day)
            )
        except Exception as exc:
            self._bus.exception(
                "CATALOG",
                f"获取阅览室列表失败 (day={resolved_day})",
                exc,
                detail={"deptIdEnc": resolved_dept, "day": resolved_day},
            )
            raise

        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        self._rooms_cache[cache_key] = list(rooms)
        for room in rooms:
            self._rooms_by_id[room.id] = room

        self._bus.info(
            "CATALOG",
            f"已加载 {len(rooms)} 个阅览室列表",
            detail={"day": resolved_day, "count": len(rooms), "latency_ms": latency_ms},
        )
        return list(rooms)

    async def get_room(
        self,
        room_id: str,
        dept_id_enc: str | None = None,
        day: str | None = None,
    ) -> Room:
        """Resolve a single Room metadata model by room_id."""
        clean_id = str(room_id).strip()
        if clean_id in self._rooms_by_id:
            return self._rooms_by_id[clean_id]

        await self.list_rooms(dept_id_enc=dept_id_enc, day=day, force_refresh=True)
        if clean_id in self._rooms_by_id:
            return self._rooms_by_id[clean_id]

        raise LookupError(f"未找到自习室 ID: {clean_id}")

    async def get_room_seat_map(
        self,
        room_id: str,
        dept_id_enc: str | None = None,
        day: str | None = None,
        force_refresh: bool = False,
    ) -> RoomSeatMap:
        """Return pure seat layout map for a room, synthesizing a clean 3-digit grid when absent."""
        clean_id = str(room_id).strip()
        if not force_refresh and clean_id in self._seat_map_cache:
            return self._seat_map_cache[clean_id]

        resolved_dept, resolved_day = self._resolve_dept_and_day(dept_id_enc, day)
        room = await self.get_room(clean_id, dept_id_enc=resolved_dept, day=resolved_day)

        t0 = time.perf_counter()
        try:
            raw_map: RoomSeatMap = await _maybe_await(
                self._catalog.get_room_seat_map(resolved_dept, room)
            )
        except Exception as exc:
            self._bus.exception(
                "CATALOG",
                f"获取自习室座位布局失败 (room={clean_id})",
                exc,
                detail={"roomId": clean_id},
            )
            raise

        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)

        if not raw_map.seats and room.capacity > 0:
            final_map = self.build_grid_seat_map(raw_map.room or room)
            mode_note = "grid-generated"
        else:
            normalized_seats = [
                seat.model_copy(
                    update={
                        "seat_num": (
                            f"{int(seat.seat_num.strip()):03d}"
                            if seat.seat_num.strip().isdigit() and len(seat.seat_num.strip()) < 3
                            else seat.seat_num.strip()
                        )
                    }
                )
                for seat in raw_map.seats
            ]
            effective_room = raw_map.room or room
            has_bg = bool(
                raw_map.has_background
                and effective_room.pic_seat_bg
                and effective_room.pic_seat_mode != 2
            )
            final_map = RoomSeatMap(
                room=effective_room,
                has_background=has_bg,
                seats=normalized_seats,
            )
            mode_note = "floor-plan" if has_bg else "coordinate-grid"

        self._seat_map_cache[clean_id] = final_map
        self._bus.info(
            "CATALOG",
            f"已加载自习室 {final_map.room.display_name} 座位布局 ({len(final_map.seats)} 座)",
            detail={
                "roomId": clean_id,
                "seats": len(final_map.seats),
                "hasBackground": final_map.has_background,
                "layoutMode": mode_note,
                "latency_ms": latency_ms,
            },
        )
        return final_map

    @staticmethod
    def build_grid_seat_map(room: Room, columns: int = 20) -> RoomSeatMap:
        """Build a deterministic 3-digit zero-padded architectural grid layout for grid-mode rooms."""
        cols = max(1, columns)
        start_num = max(1, room.start_seat_num)
        total = max(0, room.capacity)

        seat_w = 52.0
        seat_h = 40.0
        step_x = 68.0
        step_y = 56.0
        aisle_every_cols = 5
        aisle_gap_x = 24.0
        aisle_every_rows = 6
        aisle_gap_y = 20.0
        pad_x = 60.0
        pad_y = 60.0

        seats: list[SeatNode] = []
        max_right = pad_x
        max_bottom = pad_y

        for idx in range(total):
            col = idx % cols
            row = idx // cols
            x = pad_x + col * step_x + (col // aisle_every_cols) * aisle_gap_x
            y = pad_y + row * step_y + (row // aisle_every_rows) * aisle_gap_y
            seat_label = f"{start_num + idx:03d}"
            seats.append(
                SeatNode(
                    seat_num=seat_label,
                    x=x,
                    y=y,
                    width=seat_w,
                    height=seat_h,
                    rotate=0.0,
                )
            )
            max_right = max(max_right, x + seat_w)
            max_bottom = max(max_bottom, y + seat_h)

        canvas_w = int(max_right + pad_x) if seats else 1200
        canvas_h = int(max_bottom + pad_y) if seats else 800
        updated_room = room.model_copy(
            update={
                "bg_width": room.bg_width if room.bg_width > 0 else canvas_w,
                "bg_height": room.bg_height if room.bg_height > 0 else canvas_h,
            }
        )
        return RoomSeatMap(
            room=updated_room,
            has_background=False,
            seats=seats,
        )

    async def get_background_image(
        self,
        room_id: str,
        dept_id_enc: str | None = None,
    ) -> tuple[bytes, str]:
        """Fetch and cache floor-plan image bytes and content-type for a reading room."""
        room = await self.get_room(room_id, dept_id_enc=dept_id_enc)
        if not room.pic_seat_bg:
            raise LookupError(f"自习室 {room_id} ({room.display_name}) 未配置平面底图")

        bg_key = room.pic_seat_bg.strip()
        if bg_key in self._bg_cache:
            return self._bg_cache[bg_key]

        t0 = time.perf_counter()
        try:
            image_bytes, content_type = await _maybe_await(
                self._catalog.fetch_background_image(bg_key)
            )
        except Exception as exc:
            self._bus.exception(
                "CATALOG",
                f"加载平面底图失败 (room={room_id})",
                exc,
                detail={"roomId": room_id, "picSeatBg": bg_key},
            )
            raise

        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        resolved_ct = content_type or "image/png"
        self._bg_cache[bg_key] = (image_bytes, resolved_ct)
        self._bus.info(
            "CATALOG",
            f"已加载自习室 {room.display_name} 平面底图 ({len(image_bytes)} bytes)",
            detail={
                "roomId": room_id,
                "bytes": len(image_bytes),
                "contentType": resolved_ct,
                "latency_ms": latency_ms,
            },
        )
        return image_bytes, resolved_ct

    # Alias for convenience in downstream FastAPI routes
    fetch_room_background = get_background_image

    def clear_cache(self) -> None:
        """Clear in-memory room and seat-map caches."""
        self._rooms_cache.clear()
        self._rooms_by_id.clear()
        self._seat_map_cache.clear()
        self._bg_cache.clear()
