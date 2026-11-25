from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

from domain.models import (
    AttemptOutcome,
    AttemptResult,
    ReservationRecord,
    Room,
    RoomSeatMap,
    SeatNode,
    VerificationResult,
)
from domain.paths import get_app_dir
from domain.ports import (
    AuthPort,
    ConfigStorePort,
    ReservationExecutorPort,
    SeatCatalogPort,
)
from services.log_bus import LogBus, get_log_bus, mask_phone_number

# Ensure Playwright / child processes never flash console windows on Windows
if sys.platform == "win32" and not getattr(asyncio, "_chaoxing_no_window_patched", False):
    _orig_create_subprocess_exec = asyncio.create_subprocess_exec

    async def _no_window_create_subprocess_exec(*args: Any, **kwargs: Any) -> Any:
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        return await _orig_create_subprocess_exec(*args, **kwargs)

    asyncio.create_subprocess_exec = _no_window_create_subprocess_exec  # type: ignore[assignment]
    asyncio._chaoxing_no_window_patched = True  # type: ignore[attr-defined]

DEFAULT_STORAGE_STATE_PATH = get_app_dir() / "storage_state.json"
DEFAULT_BG_CACHE_DIR = get_app_dir() / "cache" / "backgrounds"
CN_TZ = timezone(timedelta(hours=8))

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)

_STATUS_LABELS: dict[int, str] = {
    0: "待履约",
    1: "使用中",
    2: "已履约",
    3: "暂离中",
    4: "被监督中",
    5: "已取消",
    6: "未签到违约",
    7: "已结束",
    8: "未履约/已失效",
}

_SUBMIT_JS = """
(params) => {
    return new Promise((resolve) => {
        const timer = setTimeout(() => {
            resolve({ ok: false, timeout: true, msg: '前端请求超时熔断 (7s)' });
        }, 7000);

        if (typeof window.require === 'function') {
            window.require(['submitVerify', 'jquery'], function(submitVerify, $) {
                const payload = {
                    deptIdEnc: params.deptIdEnc,
                    roomId: params.roomId,
                    startTime: params.startTime,
                    endTime: params.endTime,
                    day: params.day,
                    seatNum: params.seatNum,
                    captcha: '',
                    wyToken: ''
                };
                try {
                    if (submitVerify && typeof submitVerify.verifyParam === 'function') {
                        payload.enc = submitVerify.verifyParam(payload);
                    }
                } catch (e) {}
                $.ajax({
                    url: '/data/apps/seat/submit',
                    type: 'POST',
                    data: payload,
                    timeout: 6200,
                    dataType: 'json',
                    success: function(data) {
                        clearTimeout(timer);
                        resolve({ ok: true, data: data });
                    },
                    error: function(xhr) {
                        clearTimeout(timer);
                        try {
                            resolve({ ok: false, status: xhr.status, data: JSON.parse(xhr.responseText) });
                        } catch (e) {
                            resolve({
                                ok: false,
                                status: xhr.status,
                                msg: (xhr && xhr.responseText) ? String(xhr.responseText).slice(0, 300) : '网络请求错误'
                            });
                        }
                    }
                });
            }, function(err) {
                clearTimeout(timer);
                resolve({ ok: false, msg: '模块加载失败: ' + String(err) });
            });
        } else {
            clearTimeout(timer);
            resolve({ ok: false, msg: '当前页面无 require 模块' });
        }
    });
}
"""


class ChaoxingAdapter(AuthPort, SeatCatalogPort, ReservationExecutorPort):
    """Concrete Chaoxing library seat adapter combining Playwright session & domain-aware httpx client."""

    def __init__(
        self,
        config_store: ConfigStorePort | None = None,
        storage_state_path: Path | str | None = None,
        bg_cache_dir: Path | str | None = None,
        bus: LogBus | None = None,
    ) -> None:
        self._config_store = config_store
        self._storage_state_path = (
            Path(storage_state_path)
            if storage_state_path is not None
            else (get_app_dir() / "storage_state.json")
        )
        self._bg_cache_dir = (
            Path(bg_cache_dir)
            if bg_cache_dir is not None
            else (get_app_dir() / "cache" / "backgrounds")
        )
        self._bus = bus or get_log_bus()

        self._auth_lock = asyncio.Lock()
        self._browser_lock = asyncio.Lock()
        self._cookies: httpx.Cookies = httpx.Cookies()
        self._logged_in_cached: bool = False
        self._last_login_epoch: float = 0.0

        # Persistent Playwright runtime handles for pre-warmed sniping
        self._pw_cm: Any = None
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

        self._load_storage_state_into_jar()

    @property
    def logged_in_cached(self) -> bool:
        return self._logged_in_cached

    def _load_storage_state_into_jar(self) -> bool:
        """Populate httpx.Cookies preserving domain & path from Playwright storage_state.json."""
        if not self._storage_state_path.exists():
            return False
        try:
            with open(self._storage_state_path, encoding="utf-8") as f:
                state = json.load(f)
            cookies_list = state.get("cookies", [])
            if not isinstance(cookies_list, list) or not cookies_list:
                return False

            new_jar = httpx.Cookies()
            now_epoch = time.time()
            valid_count = 0
            for item in cookies_list:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                value = str(item.get("value") or "")
                domain = str(item.get("domain") or "")
                path = str(item.get("path") or "/")
                expires = item.get("expires", -1)
                if not name:
                    continue
                if isinstance(expires, (int, float)) and expires > 0 and expires < now_epoch:
                    continue
                new_jar.set(name, value, domain=domain, path=path)
                valid_count += 1

            if valid_count > 0:
                self._cookies = new_jar
                self._logged_in_cached = True
                return True
            return False
        except Exception as exc:
            self._bus.warning(
                "AUTH",
                f"读取会话缓存文件异常: {self._storage_state_path.name}",
                detail={"error": str(exc)},
            )
            return False

    def _persist_raw_cookie_compat(self, cookies_list: list[dict[str, Any]]) -> str:
        """Keep config.json auth.rawCookieStr updated for backward compatibility with seat_reserve.py."""
        raw_cookie_str = "; ".join(
            f"{c['name']}={c['value']}"
            for c in cookies_list
            if isinstance(c, dict) and c.get("name") is not None and c.get("value") is not None
        )
        if raw_cookie_str:
            self._bus.register_secret(raw_cookie_str)
        if self._config_store is not None and raw_cookie_str:
            try:
                cfg = self._config_store.load()
                cfg.auth.rawCookieStr = raw_cookie_str
                self._config_store.save(cfg)
            except Exception as exc:
                self._bus.warning(
                    "AUTH",
                    "回写兼容 Cookie 至 config.json 时出现非致命异常",
                    detail={"error": str(exc)},
                )
        return raw_cookie_str

    def _get_stored_credentials(self) -> tuple[str, str, str]:
        """Read (phone, password, dept_id_enc) from ConfigStorePort if available."""
        if self._config_store is None:
            return "", "", "df23c42633fa45e1"
        try:
            cfg = self._config_store.load()
            phone = cfg.auth.credentials.phone.strip()
            pwd = cfg.auth.credentials.password.strip()
            dept = cfg.reservation.deptIdEnc.strip() or "df23c42633fa45e1"
            if phone == "13800000000" or pwd == "your_password_here":
                return "", "", dept
            self._bus.register_secret(pwd)
            return phone, pwd, dept
        except Exception:
            return "", "", "df23c42633fa45e1"

    async def _launch_headless_browser(self, playwright: Any) -> Any:
        """Launch headless Microsoft Edge first, falling back to bundled Chromium."""
        try:
            return await playwright.chromium.launch(channel="msedge", headless=True)
        except Exception as edge_exc:
            self._bus.info(
                "BROWSER",
                "未启用系统 Edge 无头通道，回退至内置 Chromium 无头内核",
                detail={"reason": str(edge_exc)},
            )
            return await playwright.chromium.launch(headless=True)

    async def _perform_playwright_login(
        self,
        phone: str,
        password: str,
        dept_id_enc: str,
        keep_open_for_executor: bool = False,
    ) -> tuple[bool, str]:
        """Authenticate via Playwright headless browser and persist storage_state.json."""
        clean_phone = (phone or "").strip()
        clean_pwd = (password or "").strip()
        clean_dept = (dept_id_enc or "df23c42633fa45e1").strip()

        if not clean_phone or not clean_pwd:
            return False, "账号手机号或密码不能为空"

        self._bus.register_secret(clean_pwd)
        masked = mask_phone_number(clean_phone)
        login_url = (
            "https://passport2.chaoxing.com/login?"
            f"refer=https%3A%2F%2Foffice.chaoxing.com%2Ffront%2Fthird%2Fapps%2Fseat%2Flist%3FdeptIdEnc%3D{clean_dept}"
        )

        t0 = time.perf_counter()
        self._bus.info(
            "AUTH",
            f"正在通过无头浏览器登录超星账号 ({masked})",
            detail={"deptIdEnc": clean_dept, "keepOpen": keep_open_for_executor},
        )

        from playwright.async_api import async_playwright

        if keep_open_for_executor:
            async with self._browser_lock:
                await self._close_browser_unlocked()
                self._pw_cm = async_playwright()
                self._playwright = await self._pw_cm.__aenter__()
                self._browser = await self._launch_headless_browser(self._playwright)
                self._context = await self._browser.new_context(user_agent=_USER_AGENT)
                self._page = await self._context.new_page()
                ok, msg = await self._drive_login_page(
                    self._page,
                    self._context,
                    login_url,
                    clean_phone,
                    clean_pwd,
                    t0,
                )
                if not ok:
                    await self._close_browser_unlocked()
                return ok, msg

        async with async_playwright() as p:
            browser = await self._launch_headless_browser(p)
            try:
                context = await browser.new_context(user_agent=_USER_AGENT)
                page = await context.new_page()
                return await self._drive_login_page(
                    page,
                    context,
                    login_url,
                    clean_phone,
                    clean_pwd,
                    t0,
                )
            finally:
                await browser.close()

    async def _drive_login_page(
        self,
        page: Any,
        context: Any,
        login_url: str,
        phone: str,
        password: str,
        t0: float,
    ) -> tuple[bool, str]:
        try:
            await page.goto(login_url, wait_until="domcontentloaded", timeout=20000)
            if "passport2.chaoxing.com" in page.url:
                await page.wait_for_selector("#phone", timeout=10000)
                await page.fill("#phone", phone)
                await page.fill("#pwd", password)
                await page.click("#loginBtn")
                try:
                    await page.wait_for_url("**/office.chaoxing.com/**", timeout=12000)
                except Exception:
                    await page.wait_for_timeout(1500)

            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            cur_url = page.url

            if "passport2.chaoxing.com" in cur_url:
                err_tip = await page.evaluate(
                    """() => {
                        const selectors = ['#err-txt', '.err-tip', '.error-tip', '#show_error'];
                        for (const sel of selectors) {
                            const el = document.querySelector(sel);
                            if (el && el.innerText && el.innerText.trim()) {
                                return el.innerText.trim();
                            }
                        }
                        return '';
                    }"""
                )
                reason = err_tip or "登录未跳转至预约系统，请核对手机号与密码"
                self._logged_in_cached = False
                self._bus.error(
                    "AUTH",
                    f"超星账号登录失败: {reason}",
                    detail={"url": cur_url, "latencyMs": elapsed_ms},
                )
                return False, reason

            try:
                await page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass

            self._storage_state_path.parent.mkdir(parents=True, exist_ok=True)
            state_dict = await context.storage_state(path=str(self._storage_state_path))
            cookies_list = state_dict.get("cookies", []) if isinstance(state_dict, dict) else []
            self._load_storage_state_into_jar()
            self._persist_raw_cookie_compat(cookies_list)
            self._logged_in_cached = True
            self._last_login_epoch = time.time()

            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            self._bus.info(
                "AUTH",
                f"超星账号登录成功，已保存多域会话状态 ({len(cookies_list)} 枚 Cookie)",
                detail={
                    "url": cur_url,
                    "cookieCount": len(cookies_list),
                    "storageState": str(self._storage_state_path),
                    "latencyMs": elapsed_ms,
                },
            )
            return True, "登录成功，会话状态已保存"
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            self._logged_in_cached = False
            self._bus.exception(
                "AUTH",
                "超星登录过程发生异常",
                exc,
                detail={"latencyMs": elapsed_ms},
            )
            return False, f"登录异常: {exc}"

    def _build_http_headers(self, dept_id_enc: str) -> dict[str, str]:
        return {
            "User-Agent": _USER_AGENT,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": f"https://office.chaoxing.com/front/third/apps/seat/list?deptIdEnc={dept_id_enc}",
            "X-Requested-With": "XMLHttpRequest",
        }

    async def _request_chaoxing_json(
        self,
        method: str,
        url: str,
        dept_id_enc: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        stage: str = "HTTP",
        allow_auto_relogin: bool = True,
    ) -> tuple[dict[str, Any], httpx.Headers, float]:
        """Execute an authenticated Chaoxing JSON API call with automatic session bootstrap/recovery."""
        if not self._cookies and self._storage_state_path.exists():
            self._load_storage_state_into_jar()

        # Bootstrap login if no cookies exist yet and credentials are saved
        if not self._cookies and allow_auto_relogin:
            await self._try_auto_relogin(dept_id_enc, reason="本地尚无 storage_state 会话缓存")

        for attempt in range(2):
            t0 = time.perf_counter()
            try:
                async with httpx.AsyncClient(
                    cookies=self._cookies,
                    headers=self._build_http_headers(dept_id_enc),
                    follow_redirects=False,
                    timeout=12.0,
                ) as client:
                    resp = await client.request(method, url, params=params, data=data)
                latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            except Exception as exc:
                latency_ms = round((time.perf_counter() - t0) * 1000, 1)
                self._bus.exception(
                    stage,
                    f"请求超星接口网络异常: {method} {url}",
                    exc,
                    detail={"params": params, "latencyMs": latency_ms},
                )
                raise

            location = resp.headers.get("location", "")
            is_redirect_login = resp.status_code in {301, 302, 303, 307, 308} and (
                "passport2" in location or "login" in location
            )

            parsed_json: dict[str, Any] | None = None
            body_snippet = ""
            if resp.status_code == 200:
                try:
                    raw_obj = resp.json()
                    if isinstance(raw_obj, dict):
                        parsed_json = raw_obj
                except Exception:
                    body_snippet = resp.text[:240]
            else:
                body_snippet = resp.text[:240]

            session_invalid = (
                is_redirect_login
                or resp.status_code in {401, 403}
                or (parsed_json is None and ("passport2" in body_snippet or "<html" in body_snippet.lower()))
            )

            if session_invalid:
                self._logged_in_cached = False
                self._bus.warning(
                    stage,
                    f"超星接口会话未登录或已过期 (HTTP {resp.status_code})",
                    detail={
                        "url": url,
                        "statusCode": resp.status_code,
                        "location": location,
                        "latencyMs": latency_ms,
                        "attempt": attempt + 1,
                    },
                )
                if attempt == 0 and allow_auto_relogin:
                    relogged = await self._try_auto_relogin(dept_id_enc, reason="接口返回登录重定向或会话失效")
                    if relogged:
                        continue
                raise RuntimeError("超星登录会话已失效，请在界面点击账号重新登录")

            if parsed_json is None:
                self._bus.error(
                    stage,
                    f"超星接口返回非预期响应 (HTTP {resp.status_code})",
                    detail={
                        "url": url,
                        "statusCode": resp.status_code,
                        "snippet": body_snippet,
                        "latencyMs": latency_ms,
                    },
                )
                raise RuntimeError(f"超星接口响应解析失败 (HTTP {resp.status_code})")

            if parsed_json.get("success") is True and self._has_auth_cookies():
                self._logged_in_cached = True
            self._bus.debug(
                stage,
                f"{method} {url.replace('https://office.chaoxing.com', '')} -> {resp.status_code} ({latency_ms}ms)",
                detail={
                    "statusCode": resp.status_code,
                    "success": parsed_json.get("success"),
                    "latencyMs": latency_ms,
                },
            )
            return parsed_json, resp.headers, latency_ms

        raise RuntimeError("超星接口会话重试失败")

    def _has_auth_cookies(self) -> bool:
        """Return True only when real Chaoxing authentication cookies are present."""
        if not self._cookies and self._storage_state_path.exists():
            self._load_storage_state_into_jar()
        auth_cookie_names = {"UID", "_uid", "vc3", "oa_uid", "p_auth_token"}
        for cookie in self._cookies.jar:
            if cookie.name in auth_cookie_names and cookie.value:
                return True
        if self._config_store is not None:
            try:
                raw_cookie = self._config_store.load().auth.rawCookieStr or ""
                if any(f"{name}=" in raw_cookie for name in ("UID", "_uid", "vc3")):
                    return True
            except Exception:
                pass
        return False

    async def _try_auto_relogin(self, dept_id_enc: str, reason: str) -> bool:
        async with self._auth_lock:
            if time.time() - self._last_login_epoch < 5.0 and self._logged_in_cached:
                return True
            phone, pwd, stored_dept = self._get_stored_credentials()
            if not phone or not pwd:
                return False
            self._bus.info(
                "AUTH",
                f"触发静默会话续期 ({reason})",
                detail={"phone": mask_phone_number(phone)},
            )
            ok, _ = await self._perform_playwright_login(
                phone,
                pwd,
                dept_id_enc or stored_dept,
                keep_open_for_executor=False,
            )
            return ok

    # -------------------------------------------------------------------------
    # AuthPort
    # -------------------------------------------------------------------------

    async def login(self, phone: str, password: str, dept_id_enc: str) -> tuple[bool, str]:
        """Perform explicit user login, save storage_state.json, and refresh httpx cookie jar."""
        async with self._auth_lock:
            return await self._perform_playwright_login(
                phone=phone,
                password=password,
                dept_id_enc=dept_id_enc,
                keep_open_for_executor=False,
            )

    async def check_session(self, dept_id_enc: str) -> tuple[bool, str]:
        """Verify stored session against an auth-enforced Chaoxing endpoint (/room/list)."""
        clean_dept = (dept_id_enc or "df23c42633fa45e1").strip()
        if not self._has_auth_cookies():
            await self._try_auto_relogin(clean_dept, reason="本地尚无有效登录凭据缓存")
        if not self._has_auth_cookies():
            self._logged_in_cached = False
            return False, "尚未登录，请点击左上角登录学习通账号"

        today_str = datetime.now(CN_TZ).strftime("%Y-%m-%d")
        try:
            payload, _, latency_ms = await self._request_chaoxing_json(
                "GET",
                "https://office.chaoxing.com/data/apps/seat/room/list",
                clean_dept,
                params={
                    "time": "",
                    "cpage": 1,
                    "pageSize": 1,
                    "firstLevelName": "",
                    "secondLevelName": "",
                    "thirdLevelName": "",
                    "day": today_str,
                    "deptIdEnc": clean_dept,
                },
                stage="AUTH",
                allow_auto_relogin=True,
            )
            if payload.get("success") is True and isinstance(payload.get("data"), dict):
                self._logged_in_cached = True
                return True, f"超星会话在线 ({latency_ms}ms)"
            self._logged_in_cached = False
            return False, str(payload.get("msg") or "超星会话未登录或已过期，请重新登录")
        except Exception as exc:
            self._logged_in_cached = False
            return False, str(exc)

    # -------------------------------------------------------------------------
    # SeatCatalogPort
    # -------------------------------------------------------------------------

    async def list_rooms(self, dept_id_enc: str, day: str) -> list[Room]:
        """Fetch all library reading rooms from /data/apps/seat/room/list."""
        clean_dept = (dept_id_enc or "df23c42633fa45e1").strip()
        query_day = (day or datetime.now(CN_TZ).strftime("%Y-%m-%d")).strip()

        if not self._has_auth_cookies():
            await self._try_auto_relogin(clean_dept, reason="拉取自习室前本地尚无会话缓存")
        if not self._has_auth_cookies():
            self._logged_in_cached = False
            self._bus.warning("CATALOG", "会话未登录或已过期，无法拉取自习室列表")
            return []

        payload, _, latency_ms = await self._request_chaoxing_json(
            "GET",
            "https://office.chaoxing.com/data/apps/seat/room/list",
            clean_dept,
            params={
                "time": "",
                "cpage": 1,
                "pageSize": 100,
                "firstLevelName": "",
                "secondLevelName": "",
                "thirdLevelName": "",
                "day": query_day,
                "deptIdEnc": clean_dept,
            },
            stage="CATALOG",
        )

        if payload.get("success") is not True or not isinstance(payload.get("data"), dict):
            self._logged_in_cached = False
            self._bus.warning(
                "CATALOG",
                "会话未登录或已过期，无法拉取自习室列表",
                detail={
                    "success": payload.get("success"),
                    "msg": payload.get("msg"),
                    "latencyMs": latency_ms,
                },
            )
            return []

        data_block = payload.get("data")
        raw_rooms = data_block.get("seatRoomList", []) if isinstance(data_block, dict) else []
        rooms: list[Room] = []

        for item in raw_rooms:
            if not isinstance(item, dict) or item.get("id") is None:
                continue
            rid = str(item["id"]).strip()
            first = str(item.get("firstLevelName") or "").strip()
            second = str(item.get("secondLevelName") or "").strip()
            third = str(item.get("thirdLevelName") or "").strip()
            display = "-".join(part for part in (first, second, third) if part) or f"自习室-{rid}"

            special_time = item.get("seatSpecialTime")
            open_t = "08:00"
            close_t = "22:00"
            if isinstance(special_time, dict):
                open_t = str(special_time.get("monStartTime") or "08:00").strip()
                close_t = str(special_time.get("monEndTime") or "22:00").strip()

            pic_mode = int(item.get("picSeatMode") or 0)
            raw_bg = str(item.get("picSeatBg") or "").strip()
            pic_bg = raw_bg if (raw_bg and pic_mode != 2) else None

            try:
                bg_w = int(float(item.get("picSeatBgWidth") or 0))
            except (TypeError, ValueError):
                bg_w = 0
            try:
                bg_h = int(float(item.get("picSeatBgHeight") or 0))
            except (TypeError, ValueError):
                bg_h = 0

            rooms.append(
                Room(
                    id=rid,
                    first_level=first or "默认场馆",
                    second_level=second,
                    third_level=third,
                    display_name=display,
                    capacity=int(item.get("capacity") or 0),
                    start_seat_num=int(item.get("startSeatNum") or 1),
                    open_time=open_t,
                    close_time=close_t,
                    pic_seat_bg=pic_bg,
                    bg_width=bg_w,
                    bg_height=bg_h,
                    pic_seat_mode=pic_mode,
                )
            )

        self._bus.info(
            "CATALOG",
            f"已同步馆藏阅览室列表: 共 {len(rooms)} 个区域 ({latency_ms}ms)",
            detail={"count": len(rooms), "day": query_day, "latencyMs": latency_ms},
        )
        return rooms

    @staticmethod
    def build_deterministic_grid_seats(
        capacity: int,
        start_seat_num: int = 1,
        cols: int = 20,
    ) -> tuple[list[SeatNode], int, int]:
        """Generate crisp architectural multi-column grid coordinates when getdrawseat is empty."""
        total = max(0, int(capacity))
        if total == 0:
            return [], 1200, 800

        effective_cols = min(cols, total)
        seat_w = 46.0
        seat_h = 40.0
        step_x = 60.0
        step_y = 56.0
        aisle_x = 24.0
        aisle_y = 22.0
        pad_x = 56.0
        pad_y = 56.0

        seats: list[SeatNode] = []
        for idx in range(total):
            num = start_seat_num + idx
            seat_num = f"{num:03d}"
            row = idx // effective_cols
            col = idx % effective_cols
            x = pad_x + col * step_x + (col // 5) * aisle_x
            y = pad_y + row * step_y + (row // 4) * aisle_y
            seats.append(
                SeatNode(
                    seat_num=seat_num,
                    x=round(x, 1),
                    y=round(y, 1),
                    width=seat_w,
                    height=seat_h,
                    rotate=0.0,
                )
            )

        total_rows = (total + effective_cols - 1) // effective_cols
        canvas_w = int(
            pad_x * 2
            + (effective_cols - 1) * step_x
            + ((effective_cols - 1) // 5) * aisle_x
            + seat_w
        )
        canvas_h = int(
            pad_y * 2
            + max(0, total_rows - 1) * step_y
            + (max(0, total_rows - 1) // 4) * aisle_y
            + seat_h
        )
        return seats, canvas_w, canvas_h

    async def get_room_seat_map(self, dept_id_enc: str, room: Room) -> RoomSeatMap:
        """Fetch floor-plan seat coordinates from getdrawseat or synthesize clean grid coordinates."""
        clean_dept = (dept_id_enc or "df23c42633fa45e1").strip()
        raw_nodes: list[dict[str, Any]] = []
        latency_ms = 0.0

        if room.pic_seat_mode != 2:
            payload, _, latency_ms = await self._request_chaoxing_json(
                "GET",
                "https://office.chaoxing.com/data/apps/seat/getdrawseat",
                clean_dept,
                params={"id": room.id},
                stage="CATALOG",
            )
            data_list = payload.get("data")
            if isinstance(data_list, list):
                raw_nodes = [x for x in data_list if isinstance(x, dict)]

        if raw_nodes:
            seats: list[SeatNode] = []
            max_right = float(room.bg_width or 0)
            max_bottom = float(room.bg_height or 0)

            for item in raw_nodes:
                raw_num = str(item.get("seatNum") or "").strip()
                if not raw_num:
                    continue
                seat_num = f"{int(raw_num):03d}" if raw_num.isdigit() and len(raw_num) < 3 else raw_num
                x = float(item.get("xleft") or 0.0)
                y = float(item.get("ytop") or 0.0)
                w = float(item.get("width") or 40.0)
                h = float(item.get("height") or 40.0)
                rot = float(item.get("rotate") or 0.0) % 360.0

                max_right = max(max_right, x + w + 40.0)
                max_bottom = max(max_bottom, y + h + 40.0)
                seats.append(
                    SeatNode(
                        seat_num=seat_num,
                        x=x,
                        y=y,
                        width=w,
                        height=h,
                        rotate=rot,
                    )
                )

            room_out = room.model_copy(
                update={
                    "bg_width": room.bg_width if room.bg_width > 0 else int(max_right),
                    "bg_height": room.bg_height if room.bg_height > 0 else int(max_bottom),
                }
            )
            has_bg = bool(room_out.pic_seat_bg and room_out.pic_seat_mode != 2)
            self._bus.info(
                "CATALOG",
                f"已加载平面图座位坐标: {room.display_name} ({len(seats)} 座, {latency_ms}ms)",
                detail={"roomId": room.id, "seatCount": len(seats), "hasBackground": has_bg},
            )
            return RoomSeatMap(room=room_out, has_background=has_bg, seats=seats)

        # Grid mode room (picSeatMode == 2 or empty getdrawseat)
        grid_seats, grid_w, grid_h = self.build_deterministic_grid_seats(
            capacity=room.capacity,
            start_seat_num=room.start_seat_num,
            cols=20,
        )
        room_out = room.model_copy(
            update={
                "bg_width": room.bg_width if room.bg_width > 0 else grid_w,
                "bg_height": room.bg_height if room.bg_height > 0 else grid_h,
            }
        )
        self._bus.info(
            "CATALOG",
            f"已生成标准矩阵座位图: {room.display_name} ({len(grid_seats)} 座)",
            detail={"roomId": room.id, "seatCount": len(grid_seats), "canvas": f"{grid_w}x{grid_h}"},
        )
        return RoomSeatMap(room=room_out, has_background=False, seats=grid_seats)

    @staticmethod
    def _sniff_image_mime(content: bytes, fallback: str = "image/png") -> str:
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            return "image/webp"
        if content.startswith(b"GIF8"):
            return "image/gif"
        return fallback

    async def fetch_background_image(self, pic_seat_bg: str) -> tuple[bytes, str]:
        """Download and disk-cache Chaoxing floor-plan image with mandatory Referer header."""
        token = (pic_seat_bg or "").strip()
        if not _SAFE_TOKEN_RE.match(token):
            raise ValueError(f"非法的平面底图标识: {pic_seat_bg}")

        self._bg_cache_dir.mkdir(parents=True, exist_ok=True)
        img_path = self._bg_cache_dir / token
        ct_path = self._bg_cache_dir / f"{token}.ct"

        if img_path.exists() and img_path.stat().st_size > 0:
            cached_bytes = img_path.read_bytes()
            cached_ct = ""
            if ct_path.exists():
                try:
                    cached_ct = ct_path.read_text(encoding="utf-8").strip()
                except Exception:
                    cached_ct = ""
            mime = cached_ct or self._sniff_image_mime(cached_bytes)
            return cached_bytes, mime

        url = f"https://pan-yz.chaoxing.com/thumbnail/origin/{token}"
        t0 = time.perf_counter()
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Referer": "https://office.chaoxing.com/",
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                },
            )
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)

        if resp.status_code != 200 or not resp.content:
            self._bus.error(
                "CATALOG",
                f"拉取阅览室平面底图失败 (HTTP {resp.status_code})",
                detail={"picSeatBg": token, "statusCode": resp.status_code, "latencyMs": latency_ms},
            )
            raise RuntimeError(f"拉取平面底图失败 (HTTP {resp.status_code})")

        raw_ct = (resp.headers.get("content-type") or "").split(";")[0].strip()
        mime = raw_ct if raw_ct.startswith("image/") else self._sniff_image_mime(resp.content)

        tmp_img = self._bg_cache_dir / f".{token}.tmp"
        tmp_img.write_bytes(resp.content)
        tmp_img.replace(img_path)
        ct_path.write_text(mime, encoding="utf-8")

        self._bus.info(
            "CATALOG",
            f"已缓存阅览室平面底图 ({len(resp.content)} bytes, {latency_ms}ms)",
            detail={"picSeatBg": token, "contentType": mime, "bytes": len(resp.content)},
        )
        return resp.content, mime

    async def get_server_timestamp_ms(
        self,
        dept_id_enc: str,
        room_id: str,
        day: str,
    ) -> int | None:
        """Query server epoch milliseconds via POST /data/apps/seat/room/info (data.serverNow)."""
        clean_dept = (dept_id_enc or "df23c42633fa45e1").strip()
        clean_room = (room_id or "2552").strip()
        clean_day = (day or datetime.now(CN_TZ).strftime("%Y-%m-%d")).strip()

        try:
            t_req = time.time() * 1000.0
            payload, headers, latency_ms = await self._request_chaoxing_json(
                "POST",
                "https://office.chaoxing.com/data/apps/seat/room/info",
                clean_dept,
                data={
                    "id": clean_room,
                    "toDay": clean_day,
                    "fidEnc": clean_dept,
                    "queryReserve": "false",
                },
                stage="CLOCK",
            )
            t_resp = time.time() * 1000.0
            rtt_ms = max(0.0, t_resp - t_req)

            data_obj = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            server_now = data_obj.get("serverNow") if isinstance(data_obj, dict) else None
            if server_now is not None:
                raw_server_ms = int(float(server_now))
                midpoint_ms = (t_req + t_resp) * 0.5
                offset_ms = int(raw_server_ms - midpoint_ms)
                self._bus.info(
                    "CLOCK",
                    f"获取服务器时间戳完成: 原始偏差 {offset_ms:+d}ms (RTT {round(rtt_ms, 1)}ms)",
                    detail={
                        "serverNow": raw_server_ms,
                        "offsetMs": offset_ms,
                        "rttMs": round(rtt_ms, 1),
                        "latencyMs": latency_ms,
                    },
                )
                return raw_server_ms

            date_hdr = headers.get("date")
            if date_hdr:
                dt = parsedate_to_datetime(date_hdr)
                return int(dt.timestamp() * 1000)
            return None
        except Exception as exc:
            self._bus.warning(
                "CLOCK",
                "获取服务器时钟失败，将回退使用本地系统时间",
                detail={"error": str(exc)},
            )
            return None

    # -------------------------------------------------------------------------
    # ReservationExecutorPort
    # -------------------------------------------------------------------------

    async def prepare_session(self, phone: str, password: str, dept_id_enc: str) -> None:
        """Pre-warm persistent headless Playwright session ahead of target sniping time."""
        async with self._auth_lock:
            ok, msg = await self._perform_playwright_login(
                phone=phone,
                password=password,
                dept_id_enc=dept_id_enc,
                keep_open_for_executor=True,
            )
            if not ok:
                raise RuntimeError(f"预热登录失败: {msg}")

    async def _ensure_executor_page(self, dept_id_enc: str) -> Any:
        """Ensure an active Playwright page is open and authenticated for seat submission."""
        async with self._browser_lock:
            if self._page is not None and not self._page.is_closed():
                return self._page

            from playwright.async_api import async_playwright

            await self._close_browser_unlocked()
            self._pw_cm = async_playwright()
            self._playwright = await self._pw_cm.__aenter__()
            self._browser = await self._launch_headless_browser(self._playwright)

            if self._storage_state_path.exists():
                self._context = await self._browser.new_context(
                    storage_state=str(self._storage_state_path),
                    user_agent=_USER_AGENT,
                )
            else:
                self._context = await self._browser.new_context(user_agent=_USER_AGENT)

            self._page = await self._context.new_page()

        # Verify if storage_state was absent; if so, login using stored credentials
        if not self._storage_state_path.exists():
            phone, pwd, stored_dept = self._get_stored_credentials()
            if phone and pwd:
                await self.prepare_session(phone, pwd, dept_id_enc or stored_dept)

        return self._page

    async def open_select_page(self, dept_id_enc: str, room_id: str, day: str) -> bool:
        """Open or reload the Chaoxing seat selection page so RequireJS submitVerify is ready."""
        clean_dept = (dept_id_enc or "df23c42633fa45e1").strip()
        clean_room = str(room_id).strip()
        clean_day = str(day).strip()
        select_url = (
            "https://office.chaoxing.com/front/third/apps/seat/select"
            f"?deptIdEnc={clean_dept}&id={clean_room}&day={clean_day}&backLevel=2&fidEnc={clean_dept}"
        )

        page = await self._ensure_executor_page(clean_dept)
        for attempt in range(1, 4):
            t0 = time.perf_counter()
            try:
                await page.goto(select_url, wait_until="domcontentloaded", timeout=12000)
                cur_url = page.url

                if "passport2.chaoxing.com" in cur_url:
                    phone, pwd, _ = self._get_stored_credentials()
                    if phone and pwd:
                        self._bus.warning("SNIPE", "选座页触发登录挑战，正在自动补登...")
                        await self.prepare_session(phone, pwd, clean_dept)
                        page = await self._ensure_executor_page(clean_dept)
                        await page.goto(select_url, wait_until="domcontentloaded", timeout=12000)
                        cur_url = page.url

                if "error" in cur_url or "code/500" in cur_url:
                    self._bus.warning(
                        "SNIPE",
                        f"选座页跳转至异常页 (第 {attempt} 次): {cur_url}",
                        detail={"attempt": attempt, "url": cur_url},
                    )
                    await asyncio.sleep(0.25)
                    continue

                try:
                    await page.wait_for_function(
                        "() => typeof window.require === 'function'",
                        timeout=3500,
                    )
                except Exception:
                    pass

                latency_ms = round((time.perf_counter() - t0) * 1000, 1)
                self._bus.info(
                    "SNIPE",
                    f"选座页面就绪 (roomId={clean_room}, day={clean_day}, {latency_ms}ms)",
                    detail={"roomId": clean_room, "day": clean_day, "latencyMs": latency_ms},
                )
                return True
            except Exception as exc:
                latency_ms = round((time.perf_counter() - t0) * 1000, 1)
                self._bus.warning(
                    "SNIPE",
                    f"加载选座页面异常 (第 {attempt} 次, {latency_ms}ms): {exc}",
                    detail={"attempt": attempt, "roomId": clean_room, "day": clean_day},
                )
                await asyncio.sleep(0.25)

        return False

    @staticmethod
    def classify_submit_response(res: Any) -> tuple[AttemptOutcome, str]:
        """Map Chaoxing /data/apps/seat/submit response dictionary to AttemptOutcome."""
        if not isinstance(res, dict):
            return AttemptOutcome.NETWORK_ERROR, f"异常响应格式: {res}"

        data = res.get("data") if isinstance(res.get("data"), dict) else {}
        if data.get("success") is True or (data and data.get("code") == 0 and data.get("success") is not False):
            msg = str(data.get("msg") or data.get("message") or "预约成功")
            return AttemptOutcome.SUCCESS, msg

        raw_msg = str(
            data.get("msg")
            or data.get("message")
            or res.get("msg")
            or "未知预约响应"
        ).strip()

        if any(k in raw_msg for k in ("已有预约", "已经预约", "已存在预约", "预约数已达上限", "不能重复预约")):
            return AttemptOutcome.ALREADY_RESERVED, raw_msg

        if any(
            k in raw_msg
            for k in ("请求超时熔断", "网络请求错误", "模块加载失败", "系统繁忙", "频繁", "稍后再试")
        ) or res.get("timeout") is True:
            return AttemptOutcome.NETWORK_ERROR, raw_msg

        http_status = res.get("status")
        if isinstance(http_status, int) and http_status >= 500:
            return AttemptOutcome.NETWORK_ERROR, f"HTTP {http_status}: {raw_msg}"

        if any(
            k in raw_msg
            for k in (
                "未到开放",
                "未到预约",
                "尚未开放",
                "不在预约",
                "不在可预约",
                "安全验证",
                "已超时",
                "页面已过期",
                "无 require 模块",
                "非法请求",
            )
        ) or "enc" in raw_msg.lower():
            return AttemptOutcome.NOT_OPEN_OR_EXPIRED, raw_msg

        if any(k in raw_msg for k in ("违约", "冻结", "黑名单", "暂停预约", "限制预约", "无权限", "未登录")):
            return AttemptOutcome.FATAL_ERROR, raw_msg

        if any(
            k in raw_msg
            for k in ("已被", "占用", "已占", "已满", "不可预约", "不可选", "冲突", "预约失败", "不存在该座位")
        ):
            return AttemptOutcome.OCCUPIED, raw_msg

        if data and data.get("success") is False:
            return AttemptOutcome.OCCUPIED, raw_msg

        return AttemptOutcome.NETWORK_ERROR, raw_msg

    async def submit_seat(
        self,
        dept_id_enc: str,
        room_id: str,
        seat_num: str,
        day: str,
        start_time: str,
        end_time: str,
    ) -> AttemptResult:
        """Execute signed seat reservation POST inside Playwright page with 7s timeout fuse."""
        clean_dept = (dept_id_enc or "df23c42633fa45e1").strip()
        norm_seat = f"{int(seat_num):03d}" if seat_num.isdigit() and len(seat_num) < 3 else seat_num.strip()

        page = await self._ensure_executor_page(clean_dept)
        if "error" in page.url or "code/500" in page.url or "passport2" in page.url:
            return AttemptResult(
                seat_num=norm_seat,
                outcome=AttemptOutcome.NOT_OPEN_OR_EXPIRED,
                raw_msg=f"当前选座页状态异常 ({page.url})",
                latency_ms=0.0,
            )

        t0 = time.perf_counter()
        try:
            res = await page.evaluate(
                _SUBMIT_JS,
                {
                    "deptIdEnc": clean_dept,
                    "roomId": str(room_id).strip(),
                    "startTime": str(start_time).strip(),
                    "endTime": str(end_time).strip(),
                    "day": str(day).strip(),
                    "seatNum": norm_seat,
                },
            )
            latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        except Exception as exc:
            latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            self._bus.warning(
                "SNIPE",
                f"提交座位 {norm_seat} 执行异常 ({latency_ms}ms): {exc}",
                detail={"seatNum": norm_seat, "latencyMs": latency_ms},
            )
            return AttemptResult(
                seat_num=norm_seat,
                outcome=AttemptOutcome.NETWORK_ERROR,
                raw_msg=f"JS 执行异常: {exc}",
                latency_ms=latency_ms,
            )

        outcome, raw_msg = self.classify_submit_response(res)
        log_fn = self._bus.info if outcome in {AttemptOutcome.SUCCESS, AttemptOutcome.ALREADY_RESERVED} else self._bus.warning
        log_fn(
            "SNIPE",
            f"座位 {norm_seat} 提交反馈 -> [{outcome.value}] {raw_msg} ({latency_ms}ms)",
            detail={
                "seatNum": norm_seat,
                "roomId": room_id,
                "day": day,
                "outcome": outcome.value,
                "rawMsg": raw_msg,
                "latencyMs": latency_ms,
            },
        )
        return AttemptResult(
            seat_num=norm_seat,
            outcome=outcome,
            raw_msg=raw_msg,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _format_epoch_or_hhmm(val: Any) -> str:
        if val is None:
            return ""
        if isinstance(val, (int, float)) and val > 1_000_000_000:
            ts = val / 1000.0 if val > 10_000_000_000 else float(val)
            return datetime.fromtimestamp(ts, tz=CN_TZ).strftime("%H:%M")
        s = str(val).strip()
        if s.isdigit() and len(s) >= 10:
            num = float(s)
            ts = num / 1000.0 if num > 10_000_000_000 else num
            return datetime.fromtimestamp(ts, tz=CN_TZ).strftime("%H:%M")
        return s

    @classmethod
    def _parse_reservation_record(cls, item: dict[str, Any], from_cur_reserves: bool = False) -> tuple[ReservationRecord, int]:
        rec_id = str(item["id"]) if item.get("id") is not None else None
        room_id = str(item["roomId"]) if item.get("roomId") is not None else None

        first = str(item.get("firstLevelName") or "").strip()
        second = str(item.get("secondLevelName") or "").strip()
        third = str(item.get("thirdLevelName") or "").strip()
        room_name = "-".join(p for p in (first, second, third) if p) or str(item.get("roomName") or "")

        raw_seat = str(item.get("seatNum") or "").strip()
        seat_num = f"{int(raw_seat):03d}" if raw_seat.isdigit() and len(raw_seat) < 3 else raw_seat

        day_str = str(item.get("today") or item.get("day") or "").strip()
        if not day_str and isinstance(item.get("startTime"), (int, float)) and item["startTime"] > 10_000_000_000:
            day_str = datetime.fromtimestamp(item["startTime"] / 1000.0, tz=CN_TZ).strftime("%Y-%m-%d")

        start_str = cls._format_epoch_or_hhmm(item.get("startTime"))
        end_str = cls._format_epoch_or_hhmm(item.get("endTime"))

        try:
            status_code = int(item.get("status", 0 if from_cur_reserves else -1))
        except (TypeError, ValueError):
            status_code = 0 if from_cur_reserves else -1

        status_text = _STATUS_LABELS.get(status_code, "生效中" if from_cur_reserves else f"状态({status_code})")
        return (
            ReservationRecord(
                id=rec_id,
                room_id=room_id,
                room_name=room_name,
                seat_num=seat_num,
                day=day_str,
                start_time=start_str,
                end_time=end_str,
                status_text=status_text,
            ),
            status_code,
        )

    async def verify_reservation(
        self,
        dept_id_enc: str,
        target_day: str | None = None,
    ) -> VerificationResult:
        """Query Chaoxing /data/apps/seat/index & /data/apps/seat/reservelist in read-only mode."""
        clean_dept = (dept_id_enc or "df23c42633fa45e1").strip()
        checked_at = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
        norm_target_day = target_day.strip() if target_day and target_day.strip() else None

        # Sync latest browser cookies into httpx jar if Playwright executor just submitted a seat
        if self._context is not None:
            try:
                state_dict = await self._context.storage_state(path=str(self._storage_state_path))
                self._load_storage_state_into_jar()
                if isinstance(state_dict, dict):
                    self._persist_raw_cookie_compat(state_dict.get("cookies", []))
            except Exception:
                pass

        t0 = time.perf_counter()
        try:
            index_payload, _, _ = await self._request_chaoxing_json(
                "GET",
                "https://office.chaoxing.com/data/apps/seat/index",
                clean_dept,
                params={"fidEnc": clean_dept},
                stage="VERIFY",
            )
            list_payload, _, _ = await self._request_chaoxing_json(
                "GET",
                "https://office.chaoxing.com/data/apps/seat/reservelist",
                clean_dept,
                params={
                    "indexId": 0,
                    "pageSize": 20,
                    "type": -1,
                    "fidEnc": clean_dept,
                },
                stage="VERIFY",
            )
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            self._bus.exception(
                "VERIFY",
                "查询服务器预约记录失败",
                exc,
                detail={"targetDay": norm_target_day, "latencyMs": elapsed_ms},
            )
            return VerificationResult(
                verified=False,
                matched_record=None,
                active_records=[],
                message=f"核验请求失败: {exc}",
                checked_at=checked_at,
            )

        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

        index_data = index_payload.get("data") if isinstance(index_payload.get("data"), dict) else {}
        cur_raw = index_data.get("curReserves", []) if isinstance(index_data, dict) else []

        list_data = list_payload.get("data") if isinstance(list_payload.get("data"), dict) else {}
        hist_raw = list_data.get("reserveList", []) if isinstance(list_data, dict) else []

        active_records: list[ReservationRecord] = []
        completed_today_records: list[ReservationRecord] = []
        seen_keys: set[str] = set()

        def _record_key(rec: ReservationRecord) -> str:
            return rec.id or f"{rec.day}:{rec.room_id}:{rec.seat_num}:{rec.start_time}"

        for raw in cur_raw:
            if not isinstance(raw, dict):
                continue
            rec, _ = self._parse_reservation_record(raw, from_cur_reserves=True)
            k = _record_key(rec)
            if k not in seen_keys:
                seen_keys.add(k)
                active_records.append(rec)

        for raw in hist_raw:
            if not isinstance(raw, dict):
                continue
            rec, code = self._parse_reservation_record(raw, from_cur_reserves=False)
            k = _record_key(rec)
            if code in {0, 1, 3, 4} and k not in seen_keys:
                seen_keys.add(k)
                active_records.append(rec)
            elif code == 2 and k not in seen_keys:
                completed_today_records.append(rec)

        matched: ReservationRecord | None = None
        if norm_target_day:
            for rec in active_records:
                if rec.day == norm_target_day:
                    matched = rec
                    break
            if matched is None:
                for rec in completed_today_records:
                    if rec.day == norm_target_day:
                        matched = rec
                        break

            if matched is not None:
                verified = True
                msg = (
                    f"服务器已确认 {matched.day} 预约：{matched.room_name} "
                    f"{matched.seat_num}号 ({matched.start_time}-{matched.end_time} · {matched.status_text})"
                )
            else:
                verified = False
                if active_records:
                    cur = active_records[0]
                    msg = (
                        f"服务器暂无 {norm_target_day} 的预约记录"
                        f"（当前生效预约：{cur.day} {cur.room_name} {cur.seat_num}号 · {cur.status_text}）"
                    )
                else:
                    msg = f"服务器未查到 {norm_target_day} 的有效座位预约记录"
        else:
            if active_records:
                matched = active_records[0]
                verified = True
                msg = (
                    f"检测到生效中预约：{matched.room_name} "
                    f"{matched.seat_num}号 ({matched.day} {matched.start_time}-{matched.end_time} · {matched.status_text})"
                )
            else:
                verified = False
                msg = "当前账号暂无生效中的座位预约记录"

        self._bus.info(
            "VERIFY",
            f"{msg} ({elapsed_ms}ms)",
            detail={
                "verified": verified,
                "targetDay": norm_target_day,
                "activeCount": len(active_records),
                "matchedSeat": matched.seat_num if matched else None,
                "matchedDay": matched.day if matched else None,
                "latencyMs": elapsed_ms,
            },
        )
        return VerificationResult(
            verified=verified,
            matched_record=matched,
            active_records=active_records,
            message=msg,
            checked_at=checked_at,
        )

    async def _close_browser_unlocked(self) -> None:
        if self._page is not None:
            try:
                await self._page.close()
            except Exception:
                pass
            self._page = None
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw_cm is not None:
            try:
                await self._pw_cm.__aexit__(None, None, None)
            except Exception:
                pass
            self._pw_cm = None
            self._playwright = None

    async def close(self) -> None:
        """Cleanly shut down any open Playwright browser/context instances."""
        async with self._browser_lock:
            had_browser = self._browser is not None
            await self._close_browser_unlocked()
            if had_browser:
                self._bus.info("BROWSER", "无头浏览器执行器已安全关闭释放")
