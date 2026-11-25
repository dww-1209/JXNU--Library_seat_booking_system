import React, { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { AlertCircle, CheckCircle2, Info } from "lucide-react";
import { api, normalizeSeatCode } from "./api";
import type {
  AuthSummary,
  LogEntry,
  ReservationConfig,
  Room,
  RoomSeatMap,
  RuntimeStatus,
  ScheduleConfig,
} from "./types";
import { LeftSidebar } from "./components/LeftSidebar";
import { SeatCanvas } from "./components/SeatCanvas";
import { RightInspector } from "./components/RightInspector";
import { LogDrawer } from "./components/LogDrawer";
import { LoginModal } from "./components/LoginModal";

interface ToastMessage {
  id: number;
  kind: "success" | "error" | "info";
  text: string;
}

const DEFAULT_RESERVATION: ReservationConfig = {
  deptIdEnc: "df23c42633fa45e1",
  roomId: "2552",
  roomName: "瑶湖图书馆 · 1F · 期刊阅览室",
  targetSeats: [],
  startTime: "08:00",
  endTime: "22:00",
  reserveDaysAhead: 1,
};

const DEFAULT_SCHEDULE: ScheduleConfig = {
  enabled: true,
  targetTime: "22:00:00",
  prepareSecondsAhead: 60,
  winTaskEnabled: false,
  winTaskTime: "21:59",
  skipDate: null,
};

const DEFAULT_AUTH: AuthSummary = {
  phoneMasked: "",
  phone: "",
  hasPassword: false,
  loggedIn: false,
};

const DEFAULT_RUNTIME: RuntimeStatus = {
  state: "IDLE",
  targetTime: "22:00:00",
  targetDay: "",
  countdownSec: null,
  clockOffsetMs: null,
  lastResult: null,
  verification: null,
  winTask: {
    enabled: false,
    runTime: "21:59",
    nextRunTime: "",
    taskName: "ChaoxingSeatReserverApp",
    message: "",
  },
};

function getLocalTodayIso(): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export const App: React.FC = () => {
  const [rooms, setRooms] = useState<Room[]>([]);
  const [roomsLoading, setRoomsLoading] = useState(true);

  const [reservation, setReservation] =
    useState<ReservationConfig>(DEFAULT_RESERVATION);
  const [roomDraftSeats, setRoomDraftSeats] = useState<
    Record<string, string[]>
  >({});
  const [schedule, setSchedule] = useState<ScheduleConfig>(DEFAULT_SCHEDULE);
  const [auth, setAuth] = useState<AuthSummary>(DEFAULT_AUTH);
  const [runtime, setRuntime] = useState<RuntimeStatus>(DEFAULT_RUNTIME);

  const [seatMap, setSeatMap] = useState<RoomSeatMap | null>(null);
  const [seatMapLoading, setSeatMapLoading] = useState(false);
  const [seatMapError, setSeatMapError] = useState<string | null>(null);

  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedJustNow, setSavedJustNow] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [winTaskBusy, setWinTaskBusy] = useState(false);
  const [skipBusy, setSkipBusy] = useState(false);

  const [loginModalOpen, setLoginModalOpen] = useState(false);
  const [logDrawerOpen, setLogDrawerOpen] = useState(false);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [logFile, setLogFile] = useState("logs/chaoxing_reserver.log");
  const [streamConnected, setStreamConnected] = useState(false);

  const [toast, setToast] = useState<ToastMessage | null>(null);
  const toastTimerRef = useRef<number | null>(null);
  const seenLogIdsRef = useRef<Set<number>>(new Set());

  const showToast = useCallback(
    (kind: ToastMessage["kind"], text: string) => {
      if (toastTimerRef.current !== null) {
        window.clearTimeout(toastTimerRef.current);
      }
      setToast({ id: Date.now(), kind, text });
      toastTimerRef.current = window.setTimeout(() => {
        setToast(null);
      }, 3200);
    },
    [],
  );

  const loadSeatMap = useCallback(async (roomId: string) => {
    if (!roomId) return;
    setSeatMapLoading(true);
    setSeatMapError(null);
    try {
      const mapData = await api.getRoomSeatMap(roomId);
      setSeatMap(mapData);
    } catch (err) {
      setSeatMapError(
        err instanceof Error ? err.message : "无法读取该阅览室平面图数据",
      );
    } finally {
      setSeatMapLoading(false);
    }
  }, []);

  const refreshLogs = useCallback(async () => {
    try {
      const res = await api.getLogs();
      setLogFile(res.logFile);
      seenLogIdsRef.current = new Set(res.entries.map((e) => e.id));
      setLogs(res.entries.slice(-400));
    } catch {
      // Non-fatal during startup
    }
  }, []);

  // Initial bootstrap: load config, rooms, runtime status, auth status, and logs
  useEffect(() => {
    let cancelled = false;

    const bootstrap = async () => {
      setRoomsLoading(true);
      try {
        const [cfgRes, roomsRes, statusRes, logsRes] = await Promise.allSettled([
          api.getConfig(),
          api.getRooms(),
          api.getRuntimeStatus(),
          api.getLogs(),
        ]);

        if (cancelled) return;

        let initialRoomId = "2552";
        if (cfgRes.status === "fulfilled") {
          setReservation(cfgRes.value.reservation);
          setSchedule(cfgRes.value.schedule);
          setAuth(cfgRes.value.auth);
          if (cfgRes.value.reservation.roomId) {
            initialRoomId = cfgRes.value.reservation.roomId;
            setRoomDraftSeats({
              [cfgRes.value.reservation.roomId]:
                cfgRes.value.reservation.targetSeats,
            });
          }
        }

        if (roomsRes.status === "fulfilled") {
          setRooms(roomsRes.value);
          if (
            roomsRes.value.length > 0 &&
            !roomsRes.value.some((r) => r.id === initialRoomId)
          ) {
            initialRoomId = roomsRes.value[0].id;
          }
        }

        if (statusRes.status === "fulfilled") {
          setRuntime(statusRes.value);
        }

        if (logsRes.status === "fulfilled") {
          setLogFile(logsRes.value.logFile);
          seenLogIdsRef.current = new Set(logsRes.value.entries.map((e) => e.id));
          setLogs(logsRes.value.entries.slice(-400));
        }

        await loadSeatMap(initialRoomId);

        // Refresh live auth status quietly in background
        api
          .getAuthStatus()
          .then((liveAuth) => {
            if (!cancelled) {
              setAuth((prev) => ({
                ...prev,
                loggedIn: liveAuth.loggedIn,
                phoneMasked: liveAuth.phoneMasked || prev.phoneMasked,
                message: liveAuth.message || prev.message,
              }));
            }
          })
          .catch(() => {});
      } finally {
        if (!cancelled) {
          setRoomsLoading(false);
        }
      }
    };

    bootstrap();
    return () => {
      cancelled = true;
    };
  }, [loadSeatMap]);

  // Connect SSE stream for real-time logs & runtime status updates
  useEffect(() => {
    const disconnect = api.connectLogStream({
      onLog: (entry) => {
        if (seenLogIdsRef.current.has(entry.id)) return;
        seenLogIdsRef.current.add(entry.id);
        setLogs((prev) => {
          const next = [...prev, entry];
          return next.length > 500 ? next.slice(next.length - 500) : next;
        });
      },
      onStatus: (nextStatus) => {
        setRuntime((prev) => ({
          ...nextStatus,
          verification: nextStatus.verification ?? prev.verification,
        }));
      },
      onConnectionChange: setStreamConnected,
    });

    // Periodic status sync fallback every 4s
    const pollId = window.setInterval(() => {
      api
        .getRuntimeStatus()
        .then((nextStatus) => {
          setRuntime((prev) => ({
            ...nextStatus,
            verification: nextStatus.verification ?? prev.verification,
          }));
        })
        .catch(() => {});
    }, 4000);

    return () => {
      disconnect();
      window.clearInterval(pollId);
    };
  }, []);

  const persistConfig = useCallback(
    async (
      nextRes: ReservationConfig,
      nextSch: ScheduleConfig,
      silent = false,
    ) => {
      setSaving(true);
      try {
        await api.saveConfig({
          reservation: nextRes,
          schedule: nextSch,
        });
        setDirty(false);
        setSavedJustNow(true);
        window.setTimeout(() => setSavedJustNow(false), 1800);
        if (!silent) {
          showToast("success", "预约选座与定时配置已保存");
        }
      } catch (err) {
        if (!silent) {
          showToast(
            "error",
            err instanceof Error ? err.message : "保存配置失败",
          );
        }
        throw err;
      } finally {
        setSaving(false);
      }
    },
    [showToast],
  );

  // Switch active reading room: preserves per-room selections in roomDraftSeats
  const handleSelectRoom = useCallback(
    async (room: Room) => {
      if (room.id === reservation.roomId && seatMap?.room.id === room.id) {
        return;
      }
      const nextDrafts: Record<string, string[]> = {
        ...roomDraftSeats,
        [reservation.roomId]: reservation.targetSeats,
      };
      setRoomDraftSeats(nextDrafts);
      const restoredSeats = nextDrafts[room.id] ?? [];
      const nextRes: ReservationConfig = {
        ...reservation,
        roomId: room.id,
        roomName: room.display_name,
        targetSeats: restoredSeats,
        reserveDaysAhead: 1,
      };
      setReservation(nextRes);
      setDirty(true);
      await loadSeatMap(room.id);
    },
    [loadSeatMap, reservation, roomDraftSeats, seatMap?.room.id],
  );

  const handleToggleSeat = useCallback((seatNum: string) => {
    const code = normalizeSeatCode(seatNum);
    setReservation((prev) => {
      const exists = prev.targetSeats.includes(code);
      const nextSeats = exists
        ? prev.targetSeats.filter((s) => s !== code)
        : [...prev.targetSeats, code];
      setRoomDraftSeats((drafts) => ({
        ...drafts,
        [prev.roomId]: nextSeats,
      }));
      return {
        ...prev,
        targetSeats: nextSeats,
      };
    });
    setDirty(true);
  }, []);

  const handleReorderSeats = useCallback((newOrder: string[]) => {
    setReservation((prev) => {
      setRoomDraftSeats((drafts) => ({
        ...drafts,
        [prev.roomId]: newOrder,
      }));
      return {
        ...prev,
        targetSeats: newOrder,
      };
    });
    setDirty(true);
  }, []);

  const handleRemoveSeat = useCallback((seatNum: string) => {
    setReservation((prev) => {
      const nextSeats = prev.targetSeats.filter((s) => s !== seatNum);
      setRoomDraftSeats((drafts) => ({
        ...drafts,
        [prev.roomId]: nextSeats,
      }));
      return {
        ...prev,
        targetSeats: nextSeats,
      };
    });
    setDirty(true);
  }, []);

  const handleClearSeats = useCallback(() => {
    setReservation((prev) => {
      setRoomDraftSeats((drafts) => ({
        ...drafts,
        [prev.roomId]: [],
      }));
      return {
        ...prev,
        targetSeats: [],
      };
    });
    setDirty(true);
  }, []);

  const handleAddBatchRange = useCallback(
    (fromCode: string, toCode: string): { added: number; message: string } => {
      const startNum = parseInt(fromCode, 10);
      const endNum = parseInt(toCode, 10);
      if (!Number.isFinite(startNum) || !Number.isFinite(endNum)) {
        return { added: 0, message: "请输入有效的数字座位区间（如 007 至 012）" };
      }
      const lo = Math.min(startNum, endNum);
      const hi = Math.max(startNum, endNum);
      if (hi - lo > 30) {
        return { added: 0, message: "单次连选区间建议不超过 30 个席位" };
      }

      const validRoomCodes = seatMap
        ? new Set(seatMap.seats.map((s) => s.seat_num))
        : null;
      const candidates: string[] = [];
      for (let n = lo; n <= hi; n++) {
        const code = String(n).padStart(3, "0");
        if (!validRoomCodes || validRoomCodes.has(code)) {
          candidates.push(code);
        }
      }

      if (candidates.length === 0) {
        return { added: 0, message: "该编号区间内未找到当前阅览室的有效席位" };
      }

      let addedCount = 0;
      setReservation((prev) => {
        const existing = new Set(prev.targetSeats);
        const merged = [...prev.targetSeats];
        for (const c of candidates) {
          if (!existing.has(c)) {
            existing.add(c);
            merged.push(c);
            addedCount++;
          }
        }
        setRoomDraftSeats((drafts) => ({
          ...drafts,
          [prev.roomId]: merged,
        }));
        return { ...prev, targetSeats: merged };
      });

      if (addedCount > 0) {
        setDirty(true);
      }
      return {
        added: addedCount,
        message:
          addedCount > 0
            ? `已按顺序追加 ${addedCount} 个备选席位`
            : "区间内席位已在当前备选队列中",
      };
    },
    [seatMap],
  );

  const handleUpdateReservation = useCallback(
    (patch: Partial<ReservationConfig>) => {
      setReservation((prev) => ({ ...prev, ...patch }));
      setDirty(true);
    },
    [],
  );

  const handleUpdateSchedule = useCallback((patch: Partial<ScheduleConfig>) => {
    setSchedule((prev) => ({ ...prev, ...patch }));
    setDirty(true);
  }, []);

  const handleEnableDailyAutoReserve = async (winTaskTime: string) => {
    if (reservation.targetSeats.length === 0) {
      showToast("info", "请先在平面图上点选至少 1 个备选座位");
      return;
    }
    const cleanTime = winTaskTime || schedule.winTaskTime || "21:59";
    setWinTaskBusy(true);
    try {
      const nextRes: ReservationConfig = {
        ...reservation,
        reserveDaysAhead: 1,
      };
      const nextSch: ScheduleConfig = {
        ...schedule,
        enabled: true,
        targetTime: schedule.targetTime || "22:00:00",
        winTaskEnabled: true,
        winTaskTime: cleanTime,
      };
      await persistConfig(nextRes, nextSch, true);
      setReservation(nextRes);
      setSchedule(nextSch);

      const res = await api.toggleWindowsScheduler({
        enabled: true,
        runTime: cleanTime,
      });
      setRuntime((prev) => ({
        ...prev,
        winTask: res.status,
      }));
      setSchedule((prev) => ({
        ...prev,
        winTaskEnabled: res.status.enabled,
        winTaskTime: res.status.runTime || cleanTime,
      }));
      if (res.success) {
        showToast(
          "success",
          "已托管至 Windows 后台，现在可直接关闭窗口",
        );
      } else {
        showToast("error", res.message || "开启 Windows 后台计划任务失败");
      }
    } catch (err) {
      showToast(
        "error",
        err instanceof Error ? err.message : "开启每日自动抢座失败",
      );
    } finally {
      setWinTaskBusy(false);
    }
  };

  const handleDisableDailyAutoReserve = async () => {
    setWinTaskBusy(true);
    try {
      const res = await api.toggleWindowsScheduler({
        enabled: false,
        runTime: schedule.winTaskTime || "21:59",
      });
      setRuntime((prev) => ({
        ...prev,
        winTask: res.status,
      }));
      setSchedule((prev) => ({
        ...prev,
        winTaskEnabled: res.status.enabled,
      }));
      showToast(
        res.success ? "info" : "error",
        res.message || "已取消每日 Windows 后台自动抢座",
      );
    } catch (err) {
      showToast(
        "error",
        err instanceof Error ? err.message : "取消自动抢座失败",
      );
    } finally {
      setWinTaskBusy(false);
    }
  };

  const handleToggleSkipTonight = async () => {
    const todayIso = getLocalTodayIso();
    const currentlySkipping = schedule.skipDate === todayIso;
    const nextSkipDate = currentlySkipping ? null : todayIso;
    setSkipBusy(true);
    try {
      const nextSch: ScheduleConfig = {
        ...schedule,
        skipDate: nextSkipDate,
      };
      await api.saveConfig({
        schedule: { skipDate: nextSkipDate },
      });
      setSchedule(nextSch);
      showToast(
        "info",
        nextSkipDate
          ? `已设置仅跳过今晚 (${todayIso})，明晚将自动恢复`
          : "已取消跳过标记，今晚将照常自动抢座",
      );
    } catch (err) {
      showToast(
        "error",
        err instanceof Error ? err.message : "更新跳过状态失败",
      );
    } finally {
      setSkipBusy(false);
    }
  };

  const handleVerifyReservation = async () => {
    setVerifying(true);
    try {
      const v = await api.verifyReservation();
      setRuntime((prev) => ({
        ...prev,
        verification: v,
      }));
      await refreshLogs();
      if (v.verified || v.matched_record || v.active_records.length > 0) {
        showToast("success", v.message || "已核验到服务器生效预约记录");
      } else {
        showToast("info", v.message || "已完成服务器查询，当前暂无匹配预约记录");
      }
    } catch (err) {
      showToast(
        "error",
        err instanceof Error ? err.message : "服务器预约状态核验失败",
      );
    } finally {
      setVerifying(false);
    }
  };

  const handleLogin = async (params: {
    phone: string;
    password: string;
    deptIdEnc?: string;
  }) => {
    const res = await api.login(params);
    if (res.success) {
      setAuth((prev) => ({
        ...prev,
        loggedIn: true,
        hasPassword: true,
        phone: params.phone,
        phoneMasked: res.phoneMasked || prev.phoneMasked,
        message: res.message,
      }));
      if (params.deptIdEnc && params.deptIdEnc !== reservation.deptIdEnc) {
        setReservation((prev) => ({
          ...prev,
          deptIdEnc: params.deptIdEnc || prev.deptIdEnc,
        }));
      }
      showToast("success", "超星学习通会话登录成功");
    }
    await refreshLogs();
    return { success: res.success, message: res.message };
  };

  const handleCheckSession = async () => {
    const status = await api.getAuthStatus();
    setAuth((prev) => ({
      ...prev,
      loggedIn: status.loggedIn,
      phoneMasked: status.phoneMasked || prev.phoneMasked,
      message: status.message,
    }));
    await refreshLogs();
  };

  return (
    <div className="relative flex h-screen w-screen flex-col overflow-hidden bg-[#F5F5F7] text-[#1D1D1F]">
      {/* Floating Apple Notification Capsule */}
      <AnimatePresence>
        {toast && (
          <motion.div
            key={toast.id}
            initial={{ opacity: 0, y: -16, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -12, scale: 0.96 }}
            transition={{ type: "spring", stiffness: 480, damping: 32 }}
            className="apple-frosted pointer-events-none fixed top-3.5 left-1/2 z-50 flex -translate-x-1/2 items-center gap-2 rounded-full px-4 py-2 text-[12.5px] font-medium text-[#1D1D1F]"
          >
            {toast.kind === "success" && (
              <CheckCircle2 className="h-4 w-4 shrink-0 stroke-[1.8] text-[#34C759]" />
            )}
            {toast.kind === "error" && (
              <AlertCircle className="h-4 w-4 shrink-0 stroke-[1.8] text-[#FF3B30]" />
            )}
            {toast.kind === "info" && (
              <Info className="h-4 w-4 shrink-0 stroke-[1.8] text-[#0071E3]" />
            )}
            <span>{toast.text}</span>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Main 3-Column Desktop Workbench */}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <LeftSidebar
          rooms={rooms}
          roomsLoading={roomsLoading}
          selectedRoomId={reservation.roomId}
          onSelectRoom={handleSelectRoom}
          auth={auth}
          onOpenLogin={() => setLoginModalOpen(true)}
          verification={
            runtime.verification ?? runtime.lastResult?.verification ?? null
          }
          verifying={verifying}
          onVerify={handleVerifyReservation}
        />

        <SeatCanvas
          seatMap={seatMap}
          loading={seatMapLoading}
          error={seatMapError}
          targetSeats={reservation.targetSeats}
          onToggleSeat={handleToggleSeat}
          onRetry={() => loadSeatMap(reservation.roomId)}
        />

        <RightInspector
          availableSeats={seatMap?.seats ?? []}
          reservation={reservation}
          schedule={schedule}
          dirty={dirty}
          saving={saving}
          savedJustNow={savedJustNow}
          runtime={runtime}
          winTaskBusy={winTaskBusy}
          skipBusy={skipBusy}
          verifying={verifying}
          onReorderSeats={handleReorderSeats}
          onRemoveSeat={handleRemoveSeat}
          onClearSeats={handleClearSeats}
          onAddBatchRange={handleAddBatchRange}
          onUpdateReservation={handleUpdateReservation}
          onUpdateSchedule={handleUpdateSchedule}
          onSaveConfig={() => persistConfig(reservation, schedule, false)}
          onEnableDailyAutoReserve={handleEnableDailyAutoReserve}
          onDisableDailyAutoReserve={handleDisableDailyAutoReserve}
          onToggleSkipTonight={handleToggleSkipTonight}
          onVerify={handleVerifyReservation}
        />
      </div>

      {/* Bottom Real-Time Diagnostic Log Console Drawer */}
      <LogDrawer
        open={logDrawerOpen}
        onToggleOpen={() => setLogDrawerOpen((v) => !v)}
        entries={logs}
        logFile={logFile}
        streamConnected={streamConnected}
        onRefreshLogs={refreshLogs}
      />

      {/* Frosted Apple Login Modal */}
      <LoginModal
        open={loginModalOpen}
        auth={auth}
        deptIdEnc={reservation.deptIdEnc}
        onClose={() => setLoginModalOpen(false)}
        onLogin={handleLogin}
        onCheckSession={handleCheckSession}
      />
    </div>
  );
};

export default App;
