from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from domain.paths import get_app_dir, is_frozen
from domain.ports import SystemSchedulerPort
from services.log_bus import LogBus, get_log_bus

DEDICATED_TASK_NAME = "ChaoxingSeatReserverApp"
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?$")
_PS_UTF8_PREAMBLE = (
    "$OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
    "try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}"
)


class WinTaskSchedulerAdapter(SystemSchedulerPort):
    """Windows Scheduled Task adapter strictly scoped to '\\ChaoxingSeatReserverApp'.

    Never touches or modifies any legacy user tasks such as '\\ChaoxingSeatReservation'.
    """

    CACHE_TTL_SEC = 30.0
    DEFAULT_TIMEOUT_SEC = 25.0

    def __init__(
        self,
        task_name: str = DEDICATED_TASK_NAME,
        workdir: Path | str | None = None,
        bus: LogBus | None = None,
    ) -> None:
        if task_name == "ChaoxingSeatReservation":
            raise ValueError("禁止操作遗留计划任务 ChaoxingSeatReservation")
        self._task_name = task_name
        self._workdir = Path(workdir).resolve() if workdir is not None else get_app_dir()
        self._bus = bus or get_log_bus()
        self._lock = threading.RLock()
        self._ps_lock = threading.Lock()
        self._cached_status: dict[str, Any] | None = None
        self._cached_at: float = 0.0

    @property
    def task_name(self) -> str:
        return self._task_name

    def invalidate_cache(self) -> None:
        with self._lock:
            self._cached_status = None
            self._cached_at = 0.0

    def _resolve_pythonw(self) -> Path:
        venv_pythonw = self._workdir / ".venv" / "Scripts" / "pythonw.exe"
        if venv_pythonw.exists():
            return venv_pythonw
        venv_python = self._workdir / ".venv" / "Scripts" / "python.exe"
        if venv_python.exists():
            return venv_python
        return Path(sys.executable).resolve()

    def _resolve_launch_command(self) -> tuple[str, str, str]:
        """Return (executable, arguments, working_dir) for both frozen .exe and source mode."""
        if is_frozen():
            return (
                str(Path(sys.executable).resolve()),
                "--headless",
                str(get_app_dir()),
            )
        return (
            str(self._resolve_pythonw()),
            "main.py --headless",
            str(self._workdir),
        )

    @staticmethod
    def _normalize_hhmm(run_time: str) -> str:
        cleaned = (run_time or "21:59").strip()
        match = _TIME_RE.match(cleaned)
        if not match:
            raise ValueError(f"无效的计划任务运行时间格式: {run_time}（应为 HH:MM）")
        return f"{match.group(1)}:{match.group(2)}"

    @staticmethod
    def _format_proc_error(proc: subprocess.CompletedProcess[str]) -> str:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        parts: list[str] = []
        if stderr:
            parts.append(stderr)
        if stdout and stdout not in stderr:
            parts.append(f"stdout={stdout}")
        if parts:
            return f"{' | '.join(parts)} (exit={proc.returncode})"
        return f"PowerShell 退出码 {proc.returncode}（无错误输出，可能为计划任务服务冷启动或权限限制）"

    def _run_powershell(
        self,
        script: str,
        timeout: float = DEFAULT_TIMEOUT_SEC,
    ) -> subprocess.CompletedProcess[str]:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        wrapped_script = (
            f"{_PS_UTF8_PREAMBLE}\n"
            "$ErrorActionPreference = 'Stop'\n"
            "try {\n"
            f"{script}\n"
            "} catch {\n"
            "  [Console]::Error.WriteLine($_.Exception.Message)\n"
            "  exit 1\n"
            "}"
        )
        with self._ps_lock:
            return subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    wrapped_script,
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=creationflags,
            )

    def get_status(self, force_refresh: bool = False) -> dict[str, Any]:
        """Query current registration state of ChaoxingSeatReserverApp (with TTL cache)."""
        now_mono = time.monotonic()
        with self._lock:
            if (
                not force_refresh
                and self._cached_status is not None
                and (now_mono - self._cached_at) < self.CACHE_TTL_SEC
            ):
                return dict(self._cached_status)

        fresh = self._query_status_uncached()
        with self._lock:
            self._cached_status = dict(fresh)
            self._cached_at = time.monotonic()
        return fresh

    def _query_status_uncached(self) -> dict[str, Any]:
        launch_exe, launch_args, _ = self._resolve_launch_command()
        expected_cmd = f'"{launch_exe}" {launch_args}'.strip()

        if sys.platform != "win32":
            return {
                "supported": False,
                "taskName": self._task_name,
                "exists": False,
                "enabled": False,
                "state": "UnsupportedOS",
                "runTime": "21:59",
                "nextRunTime": "",
                "lastRunTime": None,
                "lastTaskResult": None,
                "command": expected_cmd,
                "message": "当前操作系统不支持 Windows 计划任务",
            }

        ps_query = f"""
$t = Get-ScheduledTask -TaskName '{self._task_name}' -ErrorAction SilentlyContinue
if (-not $t) {{
    @{{ exists = $false }} | ConvertTo-Json -Compress
}} else {{
    $info = Get-ScheduledTaskInfo -TaskName '{self._task_name}' -ErrorAction SilentlyContinue
    $startBoundary = ''
    if ($t.Triggers -and $t.Triggers.Count -gt 0) {{
        $startBoundary = [string]$t.Triggers[0].StartBoundary
    }}
    $exe = ''
    $args = ''
    if ($t.Actions -and $t.Actions.Count -gt 0) {{
        $exe = [string]$t.Actions[0].Execute
        $args = [string]$t.Actions[0].Arguments
    }}
    @{{
        exists = $true
        state = [string]$t.State
        enabled = ($t.State -ne 'Disabled')
        hidden = [bool]$t.Settings.Hidden
        wakeToRun = [bool]$t.Settings.WakeToRun
        startBoundary = $startBoundary
        nextRunTime = if ($info -and $info.NextRunTime) {{ $info.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss') }} else {{ '' }}
        lastRunTime = if ($info -and $info.LastRunTime) {{ $info.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss') }} else {{ $null }}
        lastTaskResult = if ($info) {{ $info.LastTaskResult }} else {{ $null }}
        execute = $exe
        arguments = $args
    }} | ConvertTo-Json -Compress
}}
"""
        try:
            proc = self._run_powershell(ps_query)
            stdout = (proc.stdout or "").strip()
            if proc.returncode != 0 or not stdout:
                err_detail = self._format_proc_error(proc)
                self._bus.warning(
                    "SCHEDULER",
                    f"查询 Windows 计划任务状态异常: {err_detail}",
                    detail={
                        "returncode": proc.returncode,
                        "stderr": (proc.stderr or "").strip(),
                        "stdout": stdout,
                    },
                )
                return {
                    "supported": True,
                    "taskName": self._task_name,
                    "exists": False,
                    "enabled": False,
                    "state": "Unknown",
                    "runTime": "21:59",
                    "nextRunTime": "",
                    "lastRunTime": None,
                    "lastTaskResult": None,
                    "command": expected_cmd,
                    "message": f"无法读取系统计划任务状态: {err_detail}",
                }

            parsed = json.loads(stdout)
            if not parsed.get("exists"):
                return {
                    "supported": True,
                    "taskName": self._task_name,
                    "exists": False,
                    "enabled": False,
                    "state": "NotRegistered",
                    "runTime": "21:59",
                    "nextRunTime": "",
                    "lastRunTime": None,
                    "lastTaskResult": None,
                    "command": expected_cmd,
                    "message": "每日后台静默任务未启用",
                }

            run_hhmm = "21:59"
            sb = str(parsed.get("startBoundary") or "")
            if "T" in sb:
                time_part = sb.split("T", 1)[1]
                if len(time_part) >= 5:
                    run_hhmm = time_part[:5]

            is_enabled = bool(parsed.get("enabled", True))
            exe = parsed.get("execute") or launch_exe
            args = parsed.get("arguments") or launch_args
            next_run = str(parsed.get("nextRunTime") or "")
            return {
                "supported": True,
                "taskName": self._task_name,
                "exists": True,
                "enabled": is_enabled,
                "state": str(parsed.get("state") or "Ready"),
                "hidden": bool(parsed.get("hidden", True)),
                "wakeToRun": bool(parsed.get("wakeToRun", True)),
                "runTime": run_hhmm,
                "nextRunTime": next_run,
                "lastRunTime": parsed.get("lastRunTime"),
                "lastTaskResult": parsed.get("lastTaskResult"),
                "command": f'"{exe}" {args}'.strip(),
                "message": (
                    f"已启用每日 {run_hhmm} 静默唤醒执行"
                    if is_enabled
                    else "计划任务当前处于禁用状态"
                ),
            }
        except subprocess.TimeoutExpired as exc:
            self._bus.error(
                "SCHEDULER",
                f"查询 Windows 计划任务超时 ({self.DEFAULT_TIMEOUT_SEC:.0f}s)",
                detail={"taskName": self._task_name},
                exc=exc,
            )
            return {
                "supported": True,
                "taskName": self._task_name,
                "exists": False,
                "enabled": False,
                "state": "Timeout",
                "runTime": "21:59",
                "nextRunTime": "",
                "lastRunTime": None,
                "lastTaskResult": None,
                "command": expected_cmd,
                "message": f"查询计划任务超时 ({self.DEFAULT_TIMEOUT_SEC:.0f}s)",
            }
        except Exception as exc:
            self._bus.exception("SCHEDULER", "查询 Windows 计划任务状态异常", exc)
            return {
                "supported": True,
                "taskName": self._task_name,
                "exists": False,
                "enabled": False,
                "state": "Error",
                "runTime": "21:59",
                "nextRunTime": "",
                "lastRunTime": None,
                "lastTaskResult": None,
                "command": expected_cmd,
                "message": f"状态读取异常: {exc}",
            }

    def set_enabled(self, enabled: bool, run_time: str = "21:59") -> tuple[bool, str]:
        """Register/update or remove the dedicated ChaoxingSeatReserverApp task."""
        if sys.platform != "win32":
            return False, "当前操作系统不支持 Windows 任务计划程序"

        hhmm = self._normalize_hhmm(run_time)
        launch_exe, launch_args, launch_wd = self._resolve_launch_command()

        if enabled:
            exe_escaped = launch_exe.replace("'", "''")
            args_escaped = launch_args.replace("'", "''")
            wd_escaped = launch_wd.replace("'", "''")
            ps_register = f"""
$action = New-ScheduledTaskAction -Execute '{exe_escaped}' -Argument '{args_escaped}' -WorkingDirectory '{wd_escaped}'
$trigger = New-ScheduledTaskTrigger -Daily -At '{hhmm}'
$settings = New-ScheduledTaskSettingsSet -Hidden -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName '{self._task_name}' -Action $action -Trigger $trigger -Settings $settings -Description '砚席 · 图书馆智能选座每日静默后台任务' -Force | Out-Null
Write-Output 'OK'
"""
            last_err = ""
            for attempt in range(1, 3):
                try:
                    proc = self._run_powershell(ps_register)
                    self.invalidate_cache()
                    if proc.returncode == 0:
                        self._bus.info(
                            "SCHEDULER",
                            f"已启用每日静默后台计划任务 ({self._task_name} @ {hhmm})",
                            detail={
                                "taskName": self._task_name,
                                "runTime": hhmm,
                                "executable": launch_exe,
                                "arguments": launch_args,
                                "workingDirectory": launch_wd,
                                "attempt": attempt,
                            },
                        )
                        return True, f"已启用每日 {hhmm} 静默唤醒任务 ({self._task_name})"

                    last_err = self._format_proc_error(proc)
                    self._bus.warning(
                        "SCHEDULER",
                        f"注册 Windows 计划任务失败 (第 {attempt} 次): {last_err}",
                        detail={
                            "taskName": self._task_name,
                            "runTime": hhmm,
                            "attempt": attempt,
                            "returncode": proc.returncode,
                            "stderr": (proc.stderr or "").strip(),
                            "stdout": (proc.stdout or "").strip(),
                        },
                    )
                    if attempt == 1:
                        time.sleep(0.5)
                        continue
                except subprocess.TimeoutExpired as exc:
                    self.invalidate_cache()
                    last_err = f"PowerShell 执行超时 ({self.DEFAULT_TIMEOUT_SEC:.0f}s)"
                    self._bus.error(
                        "SCHEDULER",
                        f"注册 Windows 计划任务超时 (第 {attempt} 次): {last_err}",
                        detail={"taskName": self._task_name, "runTime": hhmm, "attempt": attempt},
                        exc=exc,
                    )
                    if attempt == 1:
                        time.sleep(0.5)
                        continue
                except Exception as exc:
                    self.invalidate_cache()
                    self._bus.exception("SCHEDULER", "注册 Windows 计划任务异常", exc)
                    return False, f"注册计划任务异常: {exc}"

            self._bus.error(
                "SCHEDULER",
                f"注册 Windows 计划任务最终失败: {last_err}",
                detail={"taskName": self._task_name, "runTime": hhmm},
            )
            return False, f"注册计划任务失败: {last_err}"

        ps_unregister = f"""
$t = Get-ScheduledTask -TaskName '{self._task_name}' -ErrorAction SilentlyContinue
if ($t) {{
    Unregister-ScheduledTask -TaskName '{self._task_name}' -Confirm:$false
    Write-Output 'REMOVED'
}} else {{
    Write-Output 'NOT_FOUND'
}}
"""
        last_err = ""
        for attempt in range(1, 3):
            try:
                proc = self._run_powershell(ps_unregister)
                self.invalidate_cache()
                if proc.returncode == 0:
                    self._bus.info(
                        "SCHEDULER",
                        f"已关闭每日静默后台计划任务 ({self._task_name})",
                        detail={"taskName": self._task_name, "attempt": attempt},
                    )
                    return True, f"已关闭后台计划任务 ({self._task_name})"

                last_err = self._format_proc_error(proc)
                self._bus.warning(
                    "SCHEDULER",
                    f"移除 Windows 计划任务失败 (第 {attempt} 次): {last_err}",
                    detail={
                        "taskName": self._task_name,
                        "attempt": attempt,
                        "returncode": proc.returncode,
                        "stderr": (proc.stderr or "").strip(),
                        "stdout": (proc.stdout or "").strip(),
                    },
                )
                if attempt == 1:
                    time.sleep(0.5)
                    continue
            except subprocess.TimeoutExpired as exc:
                self.invalidate_cache()
                last_err = f"PowerShell 执行超时 ({self.DEFAULT_TIMEOUT_SEC:.0f}s)"
                self._bus.error(
                    "SCHEDULER",
                    f"移除 Windows 计划任务超时 (第 {attempt} 次): {last_err}",
                    detail={"taskName": self._task_name, "attempt": attempt},
                    exc=exc,
                )
                if attempt == 1:
                    time.sleep(0.5)
                    continue
            except Exception as exc:
                self.invalidate_cache()
                self._bus.exception("SCHEDULER", "移除 Windows 计划任务异常", exc)
                return False, f"关闭计划任务异常: {exc}"

        self._bus.error(
            "SCHEDULER",
            f"移除 Windows 计划任务最终失败: {last_err}",
            detail={"taskName": self._task_name},
        )
        return False, f"关闭计划任务失败: {last_err}"
