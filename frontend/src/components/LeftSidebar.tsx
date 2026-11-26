import React, { useMemo } from "react";
import { LayoutGroup, motion } from "motion/react";
import {
  Building2,
  CheckCircle2,
  ChevronRight,
  RefreshCw,
  ShieldCheck,
  UserCheck,
} from "lucide-react";
import type { AuthSummary, Room, VerificationResult } from "../types";

interface LeftSidebarProps {
  rooms: Room[];
  roomsLoading: boolean;
  selectedRoomId: string;
  onSelectRoom: (room: Room) => void;
  auth: AuthSummary;
  authChecking: boolean;
  onOpenLogin: () => void;
  verification: VerificationResult | null;
  verifying: boolean;
  onVerify: () => void;
}

export const LeftSidebar: React.FC<LeftSidebarProps> = ({
  rooms,
  roomsLoading,
  selectedRoomId,
  onSelectRoom,
  auth,
  authChecking,
  onOpenLogin,
  verification,
  verifying,
  onVerify,
}) => {

  const groupedRooms = useMemo(() => {
    const map = new Map<string, Room[]>();
    for (const r of rooms) {
      const group = r.first_level || "图书馆区域";
      const list = map.get(group);
      if (list) {
        list.push(r);
      } else {
        map.set(group, [r]);
      }
    }
    return Array.from(map.entries());
  }, [rooms]);

  const primaryRecord =
    verification?.matched_record ??
    (verification?.active_records && verification.active_records.length > 0
      ? verification.active_records[0]
      : null);

  return (
    <aside className="flex h-full w-[258px] shrink-0 flex-col border-r border-black/[0.06] bg-white/75 backdrop-blur-xl">
      {/* Brand Header & Account Capsule */}
      <div className="space-y-3 border-b border-black/[0.06] px-4 pt-4 pb-3.5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <img
              src="/yanxi-icon.png"
              alt="砚席"
              className="h-9 w-9 shrink-0 rounded-[10px] object-contain shadow-xs"
            />
            <div>
              <div className="flex items-center gap-1.5">
                <span className="text-[14px] font-semibold tracking-tight text-[#1D1D1F]">
                  砚席
                </span>
                <span className="rounded bg-black/[0.05] px-1.5 py-0.2 text-[10px] font-medium text-[#86868B]">
                  工作台
                </span>
              </div>
              <p className="text-[11px] text-[#86868B]">
                图书馆次日席位规划与定时预约
              </p>
            </div>
          </div>
        </div>

        {/* Account Status Capsule */}
        <button
          type="button"
          onClick={onOpenLogin}
          className="group flex w-full items-center justify-between rounded-xl border border-black/[0.06] bg-[#F5F5F7]/90 px-3 py-2 text-left transition hover:border-[#0071E3]/30 hover:bg-white"
        >
          <div className="flex min-w-0 items-center gap-2">
            <span
              className={`h-2 w-2 shrink-0 rounded-full ${
                authChecking
                  ? "animate-pulse bg-[#86868B]"
                  : auth.loggedIn
                    ? "bg-[#34C759]"
                    : "bg-[#FF9500]"
              }`}
            />
            <div className="min-w-0">
              <div className="truncate text-[12px] font-medium text-[#1D1D1F] tabular-nums">
                {authChecking
                  ? "正在检测登录状态..."
                  : auth.loggedIn
                    ? auth.phoneMasked || "已登录账号"
                    : auth.phoneMasked || "未登录账号"}
              </div>
              <div className="truncate text-[10.5px] text-[#86868B]">
                {authChecking
                  ? "同步学习通会话"
                  : auth.loggedIn
                    ? "会话在线 · 点击管理"
                    : "点击登录学习通"}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-1 text-[#86868B] transition group-hover:text-[#0071E3]">
            <UserCheck className="h-3.5 w-3.5 stroke-[1.75]" />
            <ChevronRight className="h-3.5 w-3.5 stroke-[1.75]" />
          </div>
        </button>
      </div>

      {/* Grouped Reading Room List */}
      <div className="flex-1 overflow-y-auto px-2.5 py-3">
        {(authChecking || roomsLoading) && rooms.length === 0 ? (
          <div className="space-y-2.5 px-1.5">
            <div className="flex items-center gap-2 px-1 text-[11.5px] font-medium text-[#86868B]">
              <RefreshCw className="h-3 w-3 animate-spin stroke-[1.75] text-[#0071E3]" />
              <span>正在同步馆藏教室...</span>
            </div>
            {[1, 2, 3, 4, 5].map((n) => (
              <div
                key={n}
                className="h-10 animate-pulse rounded-xl bg-black/[0.04]"
              />
            ))}
          </div>
        ) : !authChecking && rooms.length === 0 && !auth.loggedIn ? (
          <div className="flex h-full flex-col items-center justify-center px-4 py-10 text-center">
            <Building2 className="h-6 w-6 stroke-[1.5] text-[#AEAEB2]" />
            <p className="mt-2 text-[12px] font-medium text-[#86868B]">
              登录后自动加载全馆教室
            </p>
          </div>
        ) : (
          <LayoutGroup id="sidebar-rooms">
            <div className="space-y-4">
              {groupedRooms.map(([groupName, groupItems]) => (
                <div key={groupName} className="space-y-1">
                  <div className="flex items-center justify-between px-2.5 pb-1">
                    <div className="flex items-center gap-1.5 text-[11px] font-semibold tracking-wide text-[#86868B]">
                      <Building2 className="h-3 w-3 stroke-[1.75]" />
                      <span>{groupName}</span>
                    </div>
                    <span className="text-[10px] font-medium text-[#86868B] tabular-nums">
                      {groupItems.length} 间
                    </span>
                  </div>

                  <div className="space-y-0.5">
                    {groupItems.map((room) => {
                      const active = room.id === selectedRoomId;
                      const roomTitle =
                        room.third_level || room.display_name || `阅览室 ${room.id}`;

                      return (
                        <button
                          key={room.id}
                          type="button"
                          onClick={() => onSelectRoom(room)}
                          className="relative flex w-full items-center justify-between rounded-xl px-2.5 py-2 text-left transition-colors"
                        >
                          {active && (
                            <motion.div
                              layoutId="activeRoomPill"
                              transition={{
                                type: "spring",
                                stiffness: 420,
                                damping: 34,
                              }}
                              className="absolute inset-0 rounded-xl border border-[#0071E3]/20 bg-[#0071E3]/[0.10] shadow-[0_1px_2px_rgba(0,113,227,0.06)]"
                            />
                          )}

                          <div className="relative z-10 flex min-w-0 items-center gap-2">
                            {room.second_level && (
                              <span
                                className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10.5px] font-semibold tabular-nums ${
                                  active
                                    ? "bg-[#0071E3] text-white"
                                    : "bg-black/[0.05] text-[#86868B]"
                                }`}
                              >
                                {room.second_level}
                              </span>
                            )}
                            <span
                              className={`truncate text-[12.5px] ${
                                active
                                  ? "font-semibold text-[#0071E3]"
                                  : "font-medium text-[#1D1D1F] hover:text-[#0071E3]"
                              }`}
                            >
                              {roomTitle}
                            </span>
                          </div>

                          <span
                            className={`relative z-10 shrink-0 rounded-full px-2 py-0.5 text-[10.5px] tabular-nums ${
                              active
                                ? "font-semibold text-[#0071E3]"
                                : "font-medium text-[#86868B]"
                            }`}
                          >
                            {room.capacity} 席
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </LayoutGroup>
        )}
      </div>

      {/* Bottom Card: Quick Verification */}
      <div className="border-t border-black/[0.06] bg-[#F5F5F7]/65 p-3">
        {/* Quick Server Verification Card */}
        <div className="apple-card rounded-xl p-2.5">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-1.5">
              {verification?.verified ? (
                <CheckCircle2 className="h-3.5 w-3.5 stroke-[1.8] text-[#34C759]" />
              ) : (
                <ShieldCheck className="h-3.5 w-3.5 stroke-[1.75] text-[#0071E3]" />
              )}
              <span className="text-[11.5px] font-semibold text-[#1D1D1F]">
                核验预约结果
              </span>
            </div>

            <button
              type="button"
              onClick={onVerify}
              disabled={verifying}
              className="inline-flex items-center gap-1 rounded-md bg-[#F5F5F7] px-2 py-0.5 text-[10.5px] font-medium text-[#1D1D1F] transition hover:bg-black/[0.07] disabled:opacity-50"
            >
              <RefreshCw
                className={`h-2.5 w-2.5 stroke-[1.8] ${
                  verifying ? "animate-spin text-[#0071E3]" : "text-[#86868B]"
                }`}
              />
              <span>{verifying ? "核验中" : "立即核验"}</span>
            </button>
          </div>

          <div className="mt-1.5 text-[11px] leading-snug">
            {primaryRecord ? (
              <div className="space-y-0.5 rounded-lg bg-[#34C759]/[0.08] px-2 py-1.5 text-[#1D1D1F]">
                <div className="flex items-center justify-between font-medium">
                  <span className="truncate">{primaryRecord.room_name}</span>
                  <span className="shrink-0 font-semibold text-[#34C759] tabular-nums">
                    {primaryRecord.seat_num} 号
                  </span>
                </div>
                <div className="flex items-center justify-between text-[10.5px] text-[#86868B] tabular-nums">
                  <span>
                    {primaryRecord.day} {primaryRecord.start_time}-
                    {primaryRecord.end_time}
                  </span>
                  <span>{primaryRecord.status_text}</span>
                </div>
              </div>
            ) : (
              <p className="text-[10.5px] text-[#86868B] tabular-nums">
                {verification?.message ||
                  "点击核验实时查询超星服务器当前生效预约"}
              </p>
            )}
          </div>
        </div>
      </div>
    </aside>
  );
};
