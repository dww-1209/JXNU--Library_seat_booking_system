import React, { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import {
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  FileText,
  RefreshCw,
  Terminal,
} from "lucide-react";
import type { LogEntry } from "../types";

export type LogFilterMode = "ALL" | "KEY" | "ERROR";

interface LogDrawerProps {
  open: boolean;
  onToggleOpen: () => void;
  entries: LogEntry[];
  logFile: string;
  streamConnected: boolean;
  onRefreshLogs: () => void;
}

function isErrorLevel(level: string): boolean {
  const u = level.toUpperCase();
  return u === "ERROR" || u === "CRITICAL" || u === "FATAL";
}

function isKeyEntry(entry: LogEntry): boolean {
  const lvl = entry.level.toUpperCase();
  const stg = entry.stage.toUpperCase();
  if (stg === "POLL" || stg === "HEARTBEAT" || lvl === "DEBUG") {
    return false;
  }
  return (
    lvl === "INFO" ||
    lvl === "SUCCESS" ||
    lvl === "WARNING" ||
    lvl === "WARN" ||
    lvl === "ERROR" ||
    lvl === "CRITICAL"
  );
}

function levelBadgeStyle(level: string): string {
  const u = level.toUpperCase();
  if (u === "ERROR" || u === "CRITICAL") {
    return "bg-[#FF3B30]/12 text-[#FF3B30] ring-1 ring-[#FF3B30]/25";
  }
  if (u === "WARNING" || u === "WARN") {
    return "bg-[#FF9500]/14 text-[#C96F00] ring-1 ring-[#FF9500]/25";
  }
  if (u === "SUCCESS") {
    return "bg-[#34C759]/14 text-[#1E8E3E] ring-1 ring-[#34C759]/25";
  }
  return "bg-black/[0.05] text-[#86868B] ring-1 ring-black/[0.06]";
}

export const LogDrawer: React.FC<LogDrawerProps> = ({
  open,
  onToggleOpen,
  entries,
  logFile,
  streamConnected,
  onRefreshLogs,
}) => {
  const [filter, setFilter] = useState<LogFilterMode>("ALL");
  const [copied, setCopied] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const errorCount = useMemo(
    () => entries.filter((e) => isErrorLevel(e.level)).length,
    [entries],
  );

  const filteredEntries = useMemo(() => {
    if (filter === "ERROR") {
      return entries.filter((e) => isErrorLevel(e.level));
    }
    if (filter === "KEY") {
      return entries.filter(isKeyEntry);
    }
    return entries;
  }, [entries, filter]);

  useEffect(() => {
    if (!open || !scrollRef.current) return;
    const el = scrollRef.current;
    el.scrollTop = el.scrollHeight;
  }, [open, filteredEntries.length]);

  const handleCopyLogs = async (e: React.MouseEvent) => {
    e.stopPropagation();
    const header = `# 砚席诊断日志导出 | 日志文件: ${logFile}`;
    const lines = filteredEntries.map((item) => {
      const detailStr =
        item.detail && Object.keys(item.detail).length > 0
          ? ` | ${JSON.stringify(item.detail)}`
          : "";
      return `[${item.timestamp}] [${item.level}] [${item.stage}] ${item.message}${detailStr}`;
    });
    const text = [header, ...lines].join("\n");

    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    }
  };

  const latestEntry = entries.length > 0 ? entries[entries.length - 1] : null;

  return (
    <div className="z-30 shrink-0 border-t border-black/[0.07] bg-white/90 backdrop-blur-2xl">
      {/* Expandable Console Body */}
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 236, opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ type: "spring", stiffness: 400, damping: 36 }}
            className="flex flex-col overflow-hidden border-b border-black/[0.06]"
          >
            {/* Console Toolbar */}
            <div className="flex h-9.5 shrink-0 items-center justify-between border-b border-black/[0.05] bg-[#F5F5F7]/80 px-4">
              <div className="flex items-center gap-3">
                <LayoutGroup id="log-filter-tabs">
                  <div className="inline-flex items-center rounded-lg bg-black/[0.05] p-0.5">
                    {(
                      [
                        { id: "ALL", label: "全部" },
                        { id: "KEY", label: "关键" },
                        { id: "ERROR", label: "错误" },
                      ] as const
                    ).map((tab) => {
                      const active = filter === tab.id;
                      return (
                        <button
                          key={tab.id}
                          type="button"
                          onClick={() => setFilter(tab.id)}
                          className="relative rounded-md px-2.5 py-0.5 text-[11px] font-medium transition"
                        >
                          {active && (
                            <motion.span
                              layoutId="logFilterActive"
                              transition={{
                                type: "spring",
                                stiffness: 480,
                                damping: 34,
                              }}
                              className="absolute inset-0 rounded-md bg-white shadow-2xs ring-1 ring-black/[0.06]"
                            />
                          )}
                          <span
                            className={`relative z-10 ${
                              active
                                ? "font-semibold text-[#1D1D1F]"
                                : "text-[#86868B]"
                            }`}
                          >
                            {tab.label}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </LayoutGroup>

                <div className="flex items-center gap-1.5 text-[11px] text-[#86868B]">
                  <FileText className="h-3 w-3 stroke-[1.75]" />
                  <span className="font-mono-code selectable-text">{logFile}</span>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={onRefreshLogs}
                  className="inline-flex items-center gap-1 rounded-md bg-white px-2 py-1 text-[11px] font-medium text-[#1D1D1F] ring-1 ring-black/[0.08] transition hover:bg-black/[0.03]"
                >
                  <RefreshCw className="h-3 w-3 stroke-[1.75] text-[#86868B]" />
                  <span>刷新</span>
                </button>

                <button
                  type="button"
                  onClick={handleCopyLogs}
                  className="inline-flex items-center gap-1 rounded-md bg-white px-2.5 py-1 text-[11px] font-medium text-[#1D1D1F] ring-1 ring-black/[0.08] transition hover:bg-black/[0.03]"
                >
                  {copied ? (
                    <>
                      <Check className="h-3 w-3 stroke-[2] text-[#34C759]" />
                      <span className="text-[#1E8E3E]">已复制日志</span>
                    </>
                  ) : (
                    <>
                      <Copy className="h-3 w-3 stroke-[1.75] text-[#86868B]" />
                      <span>复制日志</span>
                    </>
                  )}
                </button>
              </div>
            </div>

            {/* Log Entries Stream */}
            <div
              ref={scrollRef}
              className="font-mono-code selectable-text flex-1 space-y-1 overflow-y-auto bg-[#FAFAFC] px-4 py-2.5 text-[11.5px]"
            >
              {filteredEntries.length === 0 ? (
                <div className="flex h-full items-center justify-center text-[12px] text-[#86868B]">
                  当前筛选条件下暂无诊断记录
                </div>
              ) : (
                filteredEntries.map((entry, idx) => (
                  <div
                    key={`${entry.id}-${idx}`}
                    className="flex items-start gap-2 rounded-md px-1.5 py-0.5 transition hover:bg-black/[0.03]"
                  >
                    <span className="shrink-0 text-[#86868B] tabular-nums">
                      {entry.timestamp}
                    </span>
                    <span
                      className={`shrink-0 rounded px-1.5 py-0.2 text-[10px] font-semibold ${levelBadgeStyle(
                        entry.level,
                      )}`}
                    >
                      {entry.level}
                    </span>
                    <span className="shrink-0 rounded bg-[#0071E3]/[0.08] px-1.5 py-0.2 text-[10px] font-medium text-[#0071E3]">
                      {entry.stage}
                    </span>
                    <span className="min-w-0 flex-1 break-all text-[#1D1D1F]">
                      {entry.message}
                      {entry.detail && Object.keys(entry.detail).length > 0 && (
                        <span className="ml-2 text-[10.5px] text-[#86868B]">
                          {JSON.stringify(entry.detail)}
                        </span>
                      )}
                    </span>
                  </div>
                ))
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Always-Visible Bottom Status & Drawer Toggle Bar */}
      <div
        onClick={onToggleOpen}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggleOpen();
          }
        }}
        className="flex h-8.5 cursor-pointer items-center justify-between px-4 transition hover:bg-black/[0.02]"
      >
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="flex items-center gap-1.5 text-[11.5px] font-semibold text-[#1D1D1F]">
            <Terminal className="h-3.5 w-3.5 stroke-[1.8] text-[#0071E3]" />
            <span>实时诊断日志</span>
          </div>

          <span className="inline-flex items-center gap-1 rounded-full bg-black/[0.04] px-2 py-0.2 text-[10.5px] text-[#86868B] tabular-nums">
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                streamConnected ? "bg-[#34C759]" : "bg-[#FF9500]"
              }`}
            />
            <span>{streamConnected ? "SSE 实时连接" : "轮询重连中"}</span>
          </span>

          <span className="rounded-full bg-black/[0.04] px-2 py-0.2 text-[10.5px] font-medium text-[#86868B] tabular-nums">
            {entries.length} 条
          </span>

          {errorCount > 0 && (
            <span className="rounded-full bg-[#FF3B30]/12 px-2 py-0.2 text-[10.5px] font-semibold text-[#FF3B30] tabular-nums">
              {errorCount} 条异常
            </span>
          )}

          {latestEntry && !open && (
            <span className="font-mono-code hidden truncate text-[11px] text-[#86868B] md:inline">
              [{latestEntry.timestamp}] [{latestEntry.stage}] {latestEntry.message}
            </span>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2 text-[11px] font-medium text-[#86868B]">
          <span>{open ? "收起控制台" : "展开控制台"}</span>
          {open ? (
            <ChevronDown className="h-3.5 w-3.5 stroke-[1.8]" />
          ) : (
            <ChevronUp className="h-3.5 w-3.5 stroke-[1.8]" />
          )}
        </div>
      </div>
    </div>
  );
};
