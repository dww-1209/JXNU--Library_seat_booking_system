from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing
import os
import socket
import sys
import threading
import time
from typing import Any

# Guard against pythonw.exe and PyInstaller console=False where sys.stdout / sys.stderr are None
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import httpx
import uvicorn

from adapters.beep_notifier import BeepNotifier
from domain.paths import get_app_dir, is_frozen
from services.log_bus import get_log_bus
from ui.server import AppContainer, create_app

WINDOW_TITLE = "砚席 · 图书馆智能选座"


def find_free_port(host: str = "127.0.0.1") -> int:
    """Bind an ephemeral localhost socket to discover an available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def wait_for_server_ready(url: str, timeout_sec: float = 12.0) -> bool:
    """Poll /api/health until the background FastAPI server is ready to accept connections."""
    deadline = time.monotonic() + timeout_sec
    health_url = f"{url.rstrip('/')}/api/health"
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(health_url, timeout=1.0)
            if resp.status_code == 200 and resp.json().get("ok") is True:
                return True
        except Exception:
            pass
        time.sleep(0.08)
    return False


def run_server_mode(host: str = "127.0.0.1", port: int = 18765) -> int:
    """Run FastAPI server directly in foreground (development & integration mode)."""
    bus = get_log_bus()
    container = AppContainer(bus=bus)
    app = create_app(container)
    bus.info("MAIN", f"以独立服务模式启动: http://{host}:{port}", detail={"host": host, "port": port})

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="warning",
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(config)
    try:
        server.run()
        return 0
    finally:
        bus.close()


def run_desktop_mode(host: str = "127.0.0.1", port: int = 0) -> int:
    """Launch background FastAPI server + native pywebview desktop window."""
    bus = get_log_bus()
    resolved_port = port if port > 0 else find_free_port(host)
    base_url = f"http://{host}:{resolved_port}"

    container = AppContainer(bus=bus)
    app = create_app(container)

    config = uvicorn.Config(
        app=app,
        host=host,
        port=resolved_port,
        log_level="warning",
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(config)
    loop_holder: dict[str, asyncio.AbstractEventLoop] = {}

    def _server_thread_entry() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop_holder["loop"] = loop
        try:
            loop.run_until_complete(server.serve())
        except Exception as exc:
            bus.exception("SERVER", "后台服务线程异常退出", exc)
        finally:
            try:
                loop.run_until_complete(container.shutdown())
            except Exception:
                pass
            loop.close()

    server_thread = threading.Thread(
        target=_server_thread_entry,
        name="chaoxing-fastapi-bg",
        daemon=True,
    )
    server_thread.start()

    if not wait_for_server_ready(base_url, timeout_sec=12.0):
        bus.error("MAIN", f"等待本地后端就绪超时: {base_url}")
        server.should_exit = True
        bus.close()
        return 1

    bus.info("MAIN", f"正在打开原生桌面窗口: {base_url}", detail={"url": base_url})

    try:
        import webview

        webview.create_window(
            WINDOW_TITLE,
            base_url,
            width=1360,
            height=860,
            min_size=(1100, 720),
        )
        webview.start()
    finally:
        bus.info("MAIN", "桌面窗口已关闭，正在清理后台服务与浏览器资源")
        bg_loop = loop_holder.get("loop")
        if bg_loop is not None and not bg_loop.is_closed():
            try:
                bg_loop.call_soon_threadsafe(container.shutdown_event.set)
            except RuntimeError:
                pass
        server.should_exit = True
        server_thread.join(timeout=5.0)
        bus.close()

    return 0


async def _headless_async() -> int:
    bus = get_log_bus()
    notifier = BeepNotifier(enabled=True, blocking=True, bus=bus)
    container = AppContainer(bus=bus, notifier=notifier)
    try:
        cfg = container.config_service.get_config()
        bus.info(
            "HEADLESS",
            "启动无人值守后台抢座流程",
            detail={
                "scheduleEnabled": cfg.schedule.enabled,
                "targetTime": cfg.schedule.targetTime,
                "roomId": cfg.reservation.roomId,
                "targetSeats": cfg.reservation.targetSeats,
            },
        )
        if cfg.schedule.enabled:
            result = await container.reserve_service.run_scheduled()
        else:
            result = await container.reserve_service.run_once()

        summary: dict[str, Any] = result.model_dump(mode="json")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if result.success else 1
    finally:
        await container.shutdown()
        bus.close()


async def _verify_async(target_day: str | None = None) -> int:
    bus = get_log_bus()
    container = AppContainer(bus=bus)
    try:
        cfg = container.config_service.get_config()
        clean_day = target_day.strip() if target_day and target_day.strip() else None
        result = await container.chaoxing.verify_reservation(
            dept_id_enc=cfg.reservation.deptIdEnc,
            target_day=clean_day,
        )
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0
    finally:
        await container.shutdown()
        bus.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="砚席 · 超星图书馆智能选座桌面端与无人值守引擎",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--server",
        action="store_true",
        help="直接在前台运行本地 FastAPI 服务（开发与调试模式）",
    )
    mode_group.add_argument(
        "--headless",
        action="store_true",
        help="以无人值守无头模式执行定时等待、预热、抢座、核验与蜂鸣提醒",
    )
    mode_group.add_argument(
        "--verify",
        action="store_true",
        help="以只读模式向超星服务器查询并核验当前有效预约记录",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="本地服务监听地址（默认 127.0.0.1）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=18765,
        help="--server 模式监听端口（默认 18765）",
    )
    parser.add_argument(
        "--day",
        default=None,
        help="--verify 模式可选指定核验日期（YYYY-MM-DD）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    if is_frozen():
        os.chdir(get_app_dir())
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.server:
        return run_server_mode(host=args.host, port=args.port)
    if args.headless:
        return asyncio.run(_headless_async())
    if args.verify:
        return asyncio.run(_verify_async(target_day=args.day))
    return run_desktop_mode(host=args.host, port=0)


if __name__ == "__main__":
    raise SystemExit(main())
