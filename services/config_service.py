from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from domain.models import (
    AppConfig,
    SanitizedAuthDTO,
    SanitizedConfigDTO,
)
from domain.ports import ConfigStorePort
from services.log_bus import LogBus, get_log_bus, mask_phone_number

_HHMM_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_HHMMSS_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d):([0-5]\d)$")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ConfigService:
    """Application configuration loader, validator, updater, and credential sanitizer."""

    def __init__(self, store: ConfigStorePort, bus: LogBus | None = None) -> None:
        self._store = store
        self._bus = bus or get_log_bus()

    @staticmethod
    def mask_phone(phone: str) -> str:
        return mask_phone_number(phone)

    def get_config(self) -> AppConfig:
        """Load AppConfig from store and register sensitive values for log redaction."""
        config = self._store.load()
        self._register_secrets(config)
        return config

    def get_sanitized_config(self, logged_in: bool = False) -> SanitizedConfigDTO:
        """Return UI-safe configuration DTO with masked credentials and normalized prewarm lead time."""
        config = self.get_config()
        schedule_view = config.schedule.model_copy(deep=True)
        if schedule_view.prepareSecondsAhead < 15:
            schedule_view.prepareSecondsAhead = 60

        phone = config.auth.credentials.phone.strip()
        password = config.auth.credentials.password
        auth_dto = SanitizedAuthDTO(
            phoneMasked=self.mask_phone(phone),
            phone=phone,
            hasPassword=bool(password),
            loggedIn=logged_in,
            loginMode=config.auth.loginMode,
        )
        return SanitizedConfigDTO(
            reservation=config.reservation.model_copy(deep=True),
            schedule=schedule_view,
            auth=auth_dto,
        )

    def update_config(self, patch: dict[str, Any] | AppConfig) -> AppConfig:
        """Validate and persist partial or full updates while preserving credentials and extra keys."""
        current = self.get_config()
        merged_dict = current.model_dump(by_alias=True, exclude_none=True)

        if isinstance(patch, AppConfig):
            patch_dict = patch.model_dump(by_alias=True, exclude_none=True)
            if "schedule" in patch_dict and isinstance(patch_dict["schedule"], dict):
                patch_dict["schedule"]["skipDate"] = patch.schedule.skipDate
        else:
            patch_dict = dict(patch)

        if "reservation" in patch_dict and isinstance(patch_dict["reservation"], dict):
            res_merged = dict(merged_dict.get("reservation", {}))
            for key, val in patch_dict["reservation"].items():
                if val is not None:
                    res_merged[key] = val
            merged_dict["reservation"] = res_merged

        if "schedule" in patch_dict and isinstance(patch_dict["schedule"], dict):
            sched_merged = dict(merged_dict.get("schedule", {}))
            for key, val in patch_dict["schedule"].items():
                if key == "skipDate":
                    if val is None or (isinstance(val, str) and not val.strip()):
                        sched_merged["skipDate"] = None
                    else:
                        sched_merged["skipDate"] = str(val).strip()
                elif val is not None:
                    sched_merged[key] = val
            merged_dict["schedule"] = sched_merged

        if "auth" in patch_dict and isinstance(patch_dict["auth"], dict):
            auth_merged = dict(merged_dict.get("auth", {}))
            incoming_auth = patch_dict["auth"]
            if "loginMode" in incoming_auth and incoming_auth["loginMode"]:
                auth_merged["loginMode"] = incoming_auth["loginMode"]
            if "rawCookieStr" in incoming_auth and incoming_auth["rawCookieStr"] is not None:
                auth_merged["rawCookieStr"] = incoming_auth["rawCookieStr"]
            if "credentials" in incoming_auth and isinstance(incoming_auth["credentials"], dict):
                creds_merged = dict(auth_merged.get("credentials", {}))
                inc_creds = incoming_auth["credentials"]
                if "phone" in inc_creds and inc_creds["phone"] is not None:
                    creds_merged["phone"] = str(inc_creds["phone"]).strip()
                if "password" in inc_creds and inc_creds["password"] not in (None, "", "******"):
                    creds_merged["password"] = str(inc_creds["password"])
                auth_merged["credentials"] = creds_merged
            merged_dict["auth"] = auth_merged

        updated = AppConfig.model_validate(merged_dict)
        self._validate_business_rules(updated)
        self._register_secrets(updated)
        self._store.save(updated)

        self._bus.info(
            "CONFIG",
            "配置已保存更新",
            detail={
                "roomId": updated.reservation.roomId,
                "roomName": updated.reservation.roomName,
                "targetSeats": updated.reservation.targetSeats,
                "timeRange": f"{updated.reservation.startTime}-{updated.reservation.endTime}",
                "reserveDaysAhead": updated.reservation.reserveDaysAhead,
                "scheduleEnabled": updated.schedule.enabled,
                "targetTime": updated.schedule.targetTime,
                "prepareSecondsAhead": updated.schedule.prepareSecondsAhead,
                "winTaskEnabled": updated.schedule.winTaskEnabled,
                "skipDate": updated.schedule.skipDate,
            },
        )
        return updated

    def update_credentials(
        self,
        phone: str,
        password: str | None = None,
        dept_id_enc: str | None = None,
        raw_cookie_str: str | None = None,
    ) -> AppConfig:
        """Update stored login credentials and optional department/cookie after authentication."""
        current = self.get_config()
        current.auth.credentials.phone = phone.strip()
        if password is not None and password != "******":
            current.auth.credentials.password = password
        if dept_id_enc:
            current.reservation.deptIdEnc = dept_id_enc.strip()
        if raw_cookie_str is not None:
            current.auth.rawCookieStr = raw_cookie_str

        self._register_secrets(current)
        self._store.save(current)
        self._bus.info(
            "AUTH",
            "账号凭据已更新保存",
            detail={"phone": mask_phone_number(current.auth.credentials.phone)},
        )
        return current

    @staticmethod
    def _validate_business_rules(config: AppConfig) -> None:
        start_t = config.reservation.startTime.strip()
        end_t = config.reservation.endTime.strip()
        if not _HHMM_PATTERN.match(start_t):
            raise ValueError(f"无效的开始时间格式: {start_t}，应为 HH:MM")
        if not _HHMM_PATTERN.match(end_t):
            raise ValueError(f"无效的结束时间格式: {end_t}，应为 HH:MM")
        if start_t >= end_t:
            raise ValueError(f"开始时间 ({start_t}) 必须早于结束时间 ({end_t})")
        if config.reservation.reserveDaysAhead < 0 or config.reservation.reserveDaysAhead > 7:
            raise ValueError("预约提前天数必须在 0 到 7 天之间")
        if not _HHMMSS_PATTERN.match(config.schedule.targetTime):
            raise ValueError(f"无效的定时开抢时间格式: {config.schedule.targetTime}，应为 HH:MM:SS")
        if config.schedule.prepareSecondsAhead < 0 or config.schedule.prepareSecondsAhead > 600:
            raise ValueError("预热提前秒数必须在 0 到 600 秒之间")
        if config.schedule.skipDate is not None:
            if not _DATE_PATTERN.match(config.schedule.skipDate):
                raise ValueError(f"无效的跳过日期格式: {config.schedule.skipDate}，应为 YYYY-MM-DD")
            try:
                datetime.strptime(config.schedule.skipDate, "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError(f"无效的跳过日期: {config.schedule.skipDate}") from exc

    def _register_secrets(self, config: AppConfig) -> None:
        self._bus.register_secret(config.auth.credentials.password)
        self._bus.register_secret(config.auth.rawCookieStr)
