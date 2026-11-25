import React, { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion, Reorder } from "motion/react";
import {
  CalendarClock,
  Check,
  CheckCircle2,
  Clock,
  GripVertical,
  ListOrdered,
  Moon,
  Plus,
  Power,
  PowerOff,
  RefreshCw,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import type {
  ReservationConfig,
  RuntimeStatus,
  ScheduleConfig,
  SeatNode,
  VerificationResult,
} from "../types";
import { normalizeSeatCode } from "../api";

interface RightInspectorProps {
  availableSeats: SeatNode[];
  reservation: ReservationConfig;
  schedule: ScheduleConfig;
  dirty: boolean;
  saving: boolean;
  savedJustNow: boolean;
  runtime: RuntimeStatus;
  winTaskBusy: boolean;
  skipBusy: boolean;
  verifying: boolean;
  onReorderSeats: (newOrder: string[]) => void;
  onRemoveSeat: (seatNum: string) => void;
  onClearSeats: () => void;
  onAddBatchRange: (fromCode: string, toCode: string) => {
    added: number;
    message: string;
  };
  onUpdateReservation: (patch: Partial<ReservationConfig>) => void;
  onUpdateSchedule: (patch: Partial<ScheduleConfig>) => void;
  onSaveConfig: () => void;
  onEnableDailyAutoReserve: (winTaskTime: string) => void;
  onDisableDailyAutoReserve: () => void;
  onToggleSkipTonight: () => void;
  onVerify: () => void;
}

const START_HOURS = Array.from({ length: 14 }, (_, i) => {
  const h = i + 8;
  return `${String(h).padStart(2, "0")}:00`;
});

const END_HOURS = Array.from({ length: 14 }, (_, i) => {
  const h = i + 9;
  return `${String(h).padStart(2, "0")}:00`;
});

function getLocalTodayIso(): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export const RightInspector: React.FC<RightInspectorProps> = ({
  availableSeats,
  reservation,
  schedule,
  dirty,
  saving,
  savedJustNow,
  runtime,
  winTaskBusy,
  skipBusy,
  verifying,
  onReorderSeats,
  onRemoveSeat,
  onClearSeats,
  onAddBatchRange,
  onUpdateReservation,
  onUpdateSchedule,
  onSaveConfig,
  onEnableDailyAutoReserve,
  onDisableDailyAutoReserve,
  onToggleSkipTonight,
  onVerify,
}) => {
  const [rangeOpen, setRangeOpen] = useState(false);
  const [rangeStart, setRangeStart] = useState("007");
  const [rangeEnd, setRangeEnd] = useState("012");
  const [rangeTip, setRangeTip] = useState<string | null>(null);
  const [winTimeInput, setWinTimeInput] = useState(
    runtime.winTask.runTime || schedule.winTaskTime || "21:59",
  );

  useEffect(() => {
    const synced = runtime.winTask.runTime || schedule.winTaskTime;
    if (synced) {
      setWinTimeInput(synced);
    }
  }, [runtime.winTask.runTime, schedule.winTaskTime]);

  const winTaskEnabled = runtime.winTask.enabled;
  const todayIso = getLocalTodayIso();
  const isSkippingTonight = Boolean(
    schedule.skipDate && schedule.skipDate === todayIso,
  );

  const validEndHours = useMemo(() => {
    const startHourNum = parseInt(reservation.startTime.split(":")[0] || "8", 10);
    return END_HOURS.filter(
      (t) => parseInt(t.split(":")[0], 10) > startHourNum,
    );
  }, [reservation.startTime]);

  const handleStartTimeChange = (nextStart: string) => {
    const startH = parseInt(nextStart.split(":")[0] || "8", 10);
    const curEndH = parseInt(reservation.endTime.split(":")[0] || "22", 10);
    if (curEndH <= startH) {
      const adjustedEnd = `${String(Math.min(22, startH + 1)).padStart(2, "0")}:00`;
      onUpdateReservation({ startTime: nextStart, endTime: adjustedEnd });
    } else {
      onUpdateReservation({ startTime: nextStart });
    }
  };

  const handleApplyRange = (e: React.FormEvent) => {
    e.preventDefault();
    const res = onAddBatchRange(
      normalizeSeatCode(rangeStart),
      normalizeSeatCode(rangeEnd),
    );
    setRangeTip(res.message);
    if (res.added > 0) {
      window.setTimeout(() => setRangeTip(null), 2600);
    }
  };

  const verification: VerificationResult | null =
    runtime.verification ?? runtime.lastResult?.verification ?? null;

  const displayRecord =
    verification?.matched_record ??
    (verification?.active_records && verification.active_records.length > 0
      ? verification.active_records[0]
      : null);

  return (
    <aside className="flex h-full w-[356px] shrink-0 flex-col border-l border-black/[0.06] bg-white/80 backdrop-blur-xl">
      <div className="flex-1 space-y-3.5 overflow-y-auto p-3.5">
        {/* Section 1: Priority Candidate Queue */}
        <section className="apple-card rounded-2xl p-3.5">
          <div className="flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-1.5">
              <ListOrdered className="h-4 w-4 shrink-0 stroke-[1.75] text-[#0071E3]" />
              <h2 className="whitespace-nowrap text-[13px] font-semibold tracking-tight text-[#1D1D1F]">
                备选座位优先级队列
              </h2>
              <span className="shrink-0 rounded-full bg-[#0071E3]/10 px-1.5 py-0.2 text-[10.5px] font-semibold text-[#0071E3] tabular-nums">
                {reservation.targetSeats.length}
              </span>
            </div>

            <div className="flex shrink-0 items-center gap-1">
              <button
                type="button"
                onClick={() => {
                  setRangeOpen((v) => !v);
                  setRangeTip(null);
                }}
                className={`inline-flex items-center gap-1 whitespace-nowrap rounded-lg px-2 py-1 text-[11px] font-medium transition ${
                  rangeOpen
                    ? "bg-[#0071E3]/12 text-[#0071E3]"
                    : "bg-[#F5F5F7] text-[#1D1D1F] hover:bg-black/[0.06]"
                }`}
              >
                <Plus className="h-3 w-3 shrink-0 stroke-[1.8]" />
                <span>连选区间</span>
              </button>

              {reservation.targetSeats.length > 0 && (
                <button
                  type="button"
                  onClick={onClearSeats}
                  className="inline-flex items-center gap-1 whitespace-nowrap rounded-lg px-2 py-1 text-[11px] font-medium text-[#86868B] transition hover:bg-[#FF3B30]/10 hover:text-[#FF3B30]"
                  title="清空全部备选座位"
                >
                  <Trash2 className="h-3 w-3 shrink-0 stroke-[1.75]" />
                  <span>清空</span>
                </button>
              )}
            </div>
          </div>

          {/* Collapsible Batch Range Helper */}
          <AnimatePresence initial={false}>
            {rangeOpen && (
              <motion.form
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.2 }}
                onSubmit={handleApplyRange}
                className="overflow-hidden"
              >
                <div className="mt-2.5 rounded-xl border border-black/[0.06] bg-[#F5F5F7] p-2.5">
                  <div className="flex items-center gap-1.5">
                    <input
                      type="text"
                      value={rangeStart}
                      onChange={(e) => setRangeStart(e.target.value)}
                      placeholder="起始 007"
                      className="h-7 w-full rounded-lg border border-black/[0.08] bg-white px-2 text-center text-[12px] font-medium text-[#1D1D1F] tabular-nums focus:border-[#0071E3] focus:outline-none"
                    />
                    <span className="text-[11px] text-[#86868B]">至</span>
                    <input
                      type="text"
                      value={rangeEnd}
                      onChange={(e) => setRangeEnd(e.target.value)}
                      placeholder="结束 012"
                      className="h-7 w-full rounded-lg border border-black/[0.08] bg-white px-2 text-center text-[12px] font-medium text-[#1D1D1F] tabular-nums focus:border-[#0071E3] focus:outline-none"
                    />
                    <button
                      type="submit"
                      className="h-7 shrink-0 rounded-lg bg-[#0071E3] px-2.5 text-[11px] font-medium text-white transition hover:bg-[#0077ED]"
                    >
                      批量加入
                    </button>
                  </div>
                  <p className="mt-1.5 text-[10.5px] text-[#86868B] tabular-nums">
                    {rangeTip ||
                      `当前阅览室共 ${availableSeats.length} 个有效席位，按顺序追加`}
                  </p>
                </div>
              </motion.form>
            )}
          </AnimatePresence>

          {/* Drag-to-Reorder Candidate List */}
          <div className="mt-2.5 max-h-[196px] overflow-y-auto pr-0.5">
            {reservation.targetSeats.length === 0 ? (
              <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-black/[0.1] bg-[#F5F5F7]/60 px-4 py-5 text-center">
                <Sparkles className="h-4.5 w-4.5 stroke-[1.6] text-[#86868B]" />
                <p className="mt-1.5 text-[12px] font-medium text-[#1D1D1F]">
                  尚未添加备选席位
                </p>
                <p className="mt-0.5 text-[11px] text-[#86868B]">
                  点击左侧平面图中的任意座位，即可按点击顺序建立抢座队列
                </p>
              </div>
            ) : (
              <Reorder.Group
                axis="y"
                values={reservation.targetSeats}
                onReorder={onReorderSeats}
                className="space-y-1.5"
              >
                {reservation.targetSeats.map((seatNum, idx) => {
                  const isPrimary = idx === 0;
                  return (
                    <Reorder.Item
                      key={seatNum}
                      value={seatNum}
                      whileDrag={{
                        scale: 1.02,
                        boxShadow: "0 10px 24px rgba(0, 0, 0, 0.10)",
                      }}
                      transition={{
                        type: "spring",
                        stiffness: 480,
                        damping: 32,
                      }}
                      className="flex cursor-grab items-center justify-between rounded-xl border border-black/[0.06] bg-[#F5F5F7]/85 px-2.5 py-1.5 select-none active:cursor-grabbing active:bg-white"
                    >
                      <div className="flex items-center gap-2">
                        <GripVertical className="h-3.5 w-3.5 shrink-0 stroke-[1.75] text-[#86868B]" />
                        <span
                          className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[10.5px] font-semibold tabular-nums ${
                            isPrimary
                              ? "bg-[#0071E3] text-white"
                              : "bg-white text-[#1D1D1F] ring-1 ring-black/[0.08]"
                          }`}
                        >
                          第 {idx + 1} 顺位
                        </span>
                        <span className="text-[12.5px] font-semibold text-[#1D1D1F] tabular-nums">
                          {seatNum} 号座位
                        </span>
                      </div>

                      <button
                        type="button"
                        onClick={() => onRemoveSeat(seatNum)}
                        className="rounded-md p-1 text-[#86868B] transition hover:bg-black/[0.06] hover:text-[#FF3B30]"
                        aria-label={`移除 ${seatNum} 号座位`}
                      >
                        <X className="h-3.5 w-3.5 stroke-[1.75]" />
                      </button>
                    </Reorder.Item>
                  );
                })}
              </Reorder.Group>
            )}
          </div>
        </section>

        {/* Section 2: Reservation Window & Schedule Rules */}
        <section className="apple-card space-y-3 rounded-2xl p-3.5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <SlidersHorizontal className="h-4 w-4 stroke-[1.75] text-[#1D1D1F]" />
              <h2 className="text-[13px] font-semibold tracking-tight text-[#1D1D1F]">
                预约时段与定时规则
              </h2>
            </div>

            <button
              type="button"
              onClick={onSaveConfig}
              disabled={saving}
              className={`inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-[11px] font-medium transition ${
                savedJustNow
                  ? "bg-[#34C759]/15 text-[#1E8E3E]"
                  : dirty
                    ? "bg-[#0071E3] text-white shadow-2xs hover:bg-[#0077ED]"
                    : "bg-[#F5F5F7] text-[#1D1D1F] hover:bg-black/[0.06]"
              }`}
            >
              {saving ? (
                <>
                  <RefreshCw className="h-3 w-3 animate-spin stroke-[1.8]" />
                  <span>保存中</span>
                </>
              ) : savedJustNow ? (
                <>
                  <Check className="h-3 w-3 stroke-[2]" />
                  <span>已保存</span>
                </>
              ) : (
                <>
                  <Save className="h-3 w-3 stroke-[1.75]" />
                  <span>{dirty ? "保存修改" : "保存配置"}</span>
                </>
              )}
            </button>
          </div>

          {/* Start & End Hourly Selectors */}
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <label className="block text-[11px] font-medium text-[#86868B]">
                开始时段
              </label>
              <select
                value={reservation.startTime}
                onChange={(e) => handleStartTimeChange(e.target.value)}
                className="h-8.5 w-full rounded-xl border border-black/[0.08] bg-[#F5F5F7] px-2.5 text-[12.5px] font-medium text-[#1D1D1F] tabular-nums transition focus:border-[#0071E3] focus:bg-white focus:outline-none"
              >
                {START_HOURS.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1">
              <label className="block text-[11px] font-medium text-[#86868B]">
                结束时段
              </label>
              <select
                value={reservation.endTime}
                onChange={(e) =>
                  onUpdateReservation({ endTime: e.target.value })
                }
                className="h-8.5 w-full rounded-xl border border-black/[0.08] bg-[#F5F5F7] px-2.5 text-[12.5px] font-medium text-[#1D1D1F] tabular-nums transition focus:border-[#0071E3] focus:bg-white focus:outline-none"
              >
                {validEndHours.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Static Next-Day Snipe Note + Target Snipe Time */}
          <div className="flex items-center justify-between gap-2 rounded-xl border border-black/[0.05] bg-[#F5F5F7]/80 px-3 py-2">
            <div className="flex items-center gap-1.5 text-[11.5px] font-medium text-[#1D1D1F]">
              <Clock className="h-3.5 w-3.5 shrink-0 stroke-[1.75] text-[#0071E3]" />
              <span className="whitespace-nowrap">
                每晚 22:00 自动抢次日座位
              </span>
            </div>
            <span className="shrink-0 rounded-md bg-white px-2 py-0.5 text-[11px] font-semibold text-[#86868B] ring-1 ring-black/[0.06] tabular-nums">
              {schedule.targetTime || "22:00:00"}
            </span>
          </div>
        </section>

        {/* Section 3: Unified Daily Auto-Reservation Control + Server Verification */}
        <section className="apple-card space-y-3 rounded-2xl p-3.5">
          <div className="flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-1.5">
              <CalendarClock className="h-4 w-4 shrink-0 stroke-[1.75] text-[#0071E3]" />
              <h2 className="whitespace-nowrap text-[13px] font-semibold tracking-tight text-[#1D1D1F]">
                每日自动抢座
              </h2>
            </div>

            <span
              className={`inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${
                winTaskEnabled
                  ? "border-[#34C759]/30 bg-[#34C759]/12 text-[#1E8E3E]"
                  : "border-black/[0.08] bg-black/[0.04] text-[#86868B]"
              }`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  winTaskEnabled ? "bg-[#34C759]" : "bg-[#86868B]"
                }`}
              />
              <span>{winTaskEnabled ? "已托管后台" : "未开启"}</span>
            </span>
          </div>

          {/* Wakeup Time & Snipe Time Summary Strip */}
          <div className="flex items-center justify-between gap-2 rounded-xl border border-black/[0.05] bg-[#F5F5F7]/90 px-3 py-2">
            <div className="flex items-center gap-2 text-[11.5px] text-[#86868B]">
              <span className="whitespace-nowrap">每日后台唤醒</span>
              <input
                type="time"
                value={winTimeInput}
                onChange={(e) => {
                  setWinTimeInput(e.target.value);
                  onUpdateSchedule({ winTaskTime: e.target.value });
                }}
                onBlur={() => {
                  if (winTaskEnabled && winTimeInput) {
                    onEnableDailyAutoReserve(winTimeInput);
                  }
                }}
                aria-label="每日后台唤醒时间"
                className="h-6.5 rounded-lg border border-black/[0.08] bg-white px-1.5 text-[12px] font-semibold text-[#1D1D1F] tabular-nums focus:border-[#0071E3] focus:outline-none"
              />
            </div>

            <div className="flex items-center gap-1 text-[11px] text-[#86868B] tabular-nums">
              <span>开抢</span>
              <span className="font-semibold text-[#1D1D1F]">
                {schedule.targetTime || "22:00:00"}
              </span>
            </div>
          </div>

          {/* Unified Alarm-Clock Mode Action Area */}
          {!winTaskEnabled ? (
            <div className="space-y-2">
              <button
                type="button"
                disabled={winTaskBusy || reservation.targetSeats.length === 0}
                onClick={() =>
                  onEnableDailyAutoReserve(winTimeInput || "21:59")
                }
                className="flex h-10 w-full items-center justify-center gap-2 whitespace-nowrap rounded-xl bg-[#0071E3] text-[13px] font-semibold text-white shadow-[0_4px_12px_rgba(0,113,227,0.25)] transition hover:bg-[#0077ED] active:scale-[0.985] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {winTaskBusy ? (
                  <>
                    <RefreshCw className="h-3.5 w-3.5 animate-spin stroke-[1.8]" />
                    <span>正在保存并托管至后台...</span>
                  </>
                ) : (
                  <>
                    <Power className="h-3.5 w-3.5 stroke-[1.9]" />
                    <span>开启每日自动抢座</span>
                  </>
                )}
              </button>
              <p className="text-center text-[11px] text-[#86868B]">
                {reservation.targetSeats.length === 0
                  ? "请先在左侧平面图点选至少 1 个备选座位"
                  : "开启后将自动保存配置并托管后台，届时可直接关闭窗口"}
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              <div className="rounded-xl border border-[#34C759]/25 bg-[#34C759]/[0.08] px-3 py-2 text-[11.5px] leading-snug text-[#1D1D1F]">
                <div className="flex items-center gap-1.5 font-semibold text-[#1E8E3E]">
                  <CheckCircle2 className="h-3.5 w-3.5 shrink-0 stroke-[1.8]" />
                  <span className="tabular-nums">
                    每晚 {winTimeInput || "21:59"} 自动唤醒，22:00 抢次日座位
                  </span>
                </div>
                <p className="mt-0.5 text-[11px] text-[#86868B]">
                  已托管至 Windows 后台计划任务，现在可直接关闭本窗口
                </p>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  disabled={winTaskBusy}
                  onClick={onDisableDailyAutoReserve}
                  className="inline-flex h-8.5 items-center justify-center gap-1.5 whitespace-nowrap rounded-xl border border-[#FF3B30]/25 bg-[#FF3B30]/[0.06] px-2 text-[11.5px] font-medium text-[#FF3B30] transition hover:bg-[#FF3B30]/[0.12] active:scale-[0.98] disabled:opacity-50"
                >
                  {winTaskBusy ? (
                    <RefreshCw className="h-3 w-3 shrink-0 animate-spin stroke-[1.8]" />
                  ) : (
                    <PowerOff className="h-3 w-3 shrink-0 stroke-[1.8]" />
                  )}
                  <span>取消自动抢座</span>
                </button>

                <button
                  type="button"
                  disabled={skipBusy}
                  onClick={onToggleSkipTonight}
                  className={`inline-flex h-8.5 items-center justify-center gap-1.5 whitespace-nowrap rounded-xl border px-2 text-[11.5px] font-medium transition active:scale-[0.98] disabled:opacity-50 ${
                    isSkippingTonight
                      ? "border-[#FF9500]/35 bg-[#FF9500]/12 text-[#C96F00] hover:bg-[#FF9500]/18"
                      : "border-black/[0.08] bg-white text-[#1D1D1F] hover:bg-black/[0.03]"
                  }`}
                >
                  <Moon className="h-3 w-3 shrink-0 stroke-[1.8]" />
                  <span>{isSkippingTonight ? "取消跳过今晚" : "仅跳过今晚"}</span>
                </button>
              </div>

              {isSkippingTonight && (
                <p className="rounded-lg bg-[#FF9500]/10 px-2.5 py-1.5 text-center text-[11px] font-medium text-[#C96F00]">
                  已暂停今晚 · 明晚自动恢复
                </p>
              )}
            </div>
          )}

          {/* Server Verification Action */}
          <button
            type="button"
            disabled={verifying}
            onClick={onVerify}
            className="inline-flex h-8.5 w-full items-center justify-center gap-1.5 whitespace-nowrap rounded-xl border border-black/[0.08] bg-white px-2.5 text-[11.5px] font-medium text-[#1D1D1F] transition hover:bg-black/[0.03] active:scale-[0.98] disabled:opacity-50"
          >
            <RefreshCw
              className={`h-3 w-3 shrink-0 stroke-[1.8] ${
                verifying ? "animate-spin text-[#0071E3]" : "text-[#34C759]"
              }`}
            />
            <span>检测服务器预约状态</span>
          </button>

          {/* Server Verification Result Card */}
          <div
            className={`rounded-xl border p-2.5 text-[11.5px] ${
              verification?.verified || displayRecord
                ? "border-[#34C759]/30 bg-[#34C759]/[0.07]"
                : "border-black/[0.06] bg-[#F5F5F7]/70"
            }`}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5 font-semibold text-[#1D1D1F]">
                {verification?.verified || displayRecord ? (
                  <CheckCircle2 className="h-3.5 w-3.5 stroke-[1.8] text-[#34C759]" />
                ) : (
                  <ShieldCheck className="h-3.5 w-3.5 stroke-[1.75] text-[#86868B]" />
                )}
                <span>
                  {verification?.verified
                    ? "服务器已确认目标预约"
                    : displayRecord
                      ? "服务器当前生效预约"
                      : "服务器预约状态核验"}
                </span>
              </div>
              {verification?.checked_at && (
                <span className="text-[10px] text-[#86868B] tabular-nums">
                  {verification.checked_at}
                </span>
              )}
            </div>

            {displayRecord ? (
              <div className="mt-2 grid grid-cols-2 gap-x-2 gap-y-1 rounded-lg bg-white/85 p-2 text-[11px] ring-1 ring-black/[0.05]">
                <div>
                  <span className="text-[#86868B]">场馆：</span>
                  <span className="font-medium text-[#1D1D1F]">
                    {displayRecord.room_name}
                  </span>
                </div>
                <div>
                  <span className="text-[#86868B]">席位：</span>
                  <span className="font-semibold text-[#0071E3] tabular-nums">
                    {displayRecord.seat_num} 号
                  </span>
                </div>
                <div className="tabular-nums">
                  <span className="text-[#86868B]">时段：</span>
                  <span className="font-medium text-[#1D1D1F]">
                    {displayRecord.day} {displayRecord.start_time}-
                    {displayRecord.end_time}
                  </span>
                </div>
                <div>
                  <span className="text-[#86868B]">状态：</span>
                  <span className="font-semibold text-[#34C759]">
                    {displayRecord.status_text}
                  </span>
                </div>
              </div>
            ) : (
              <p className="mt-1 text-[11px] leading-relaxed text-[#86868B]">
                {verification?.message ||
                  runtime.lastResult?.message ||
                  "尚未发起核验，点击“检测服务器预约状态”可直连超星接口核对真实预约记录"}
              </p>
            )}
          </div>
        </section>
      </div>
    </aside>
  );
};
