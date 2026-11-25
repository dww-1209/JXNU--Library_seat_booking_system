import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  Clock,
  Compass,
  Grid3X3,
  Layers,
  Maximize2,
  RefreshCw,
  Search,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import type { RoomSeatMap, SeatNode } from "../types";
import { normalizeSeatCode } from "../api";

interface SeatCanvasProps {
  seatMap: RoomSeatMap | null;
  loading: boolean;
  error: string | null;
  targetSeats: string[];
  onToggleSeat: (seatNum: string) => void;
  onRetry: () => void;
}

interface ViewportTransform {
  x: number;
  y: number;
  scale: number;
}

const MIN_SCALE = 0.22;
const MAX_SCALE = 3.2;

function clamp(val: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, val));
}

function easeOutQuint(t: number): number {
  return 1 - Math.pow(1 - t, 5);
}

export const SeatCanvas: React.FC<SeatCanvasProps> = ({
  seatMap,
  loading,
  error,
  targetSeats,
  onToggleSeat,
  onRetry,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const animFrameRef = useRef<number | null>(null);

  const [viewport, setViewport] = useState<ViewportTransform>({
    x: 32,
    y: 32,
    scale: 0.75,
  });
  const viewportRef = useRef<ViewportTransform>(viewport);
  viewportRef.current = viewport;

  const [isDragging, setIsDragging] = useState(false);
  const dragStateRef = useRef<{
    active: boolean;
    pointerId: number;
    startClientX: number;
    startClientY: number;
    originX: number;
    originY: number;
    moved: boolean;
  }>({
    active: false,
    pointerId: -1,
    startClientX: 0,
    startClientY: 0,
    originX: 0,
    originY: 0,
    moved: false,
  });

  const [searchQuery, setSearchQuery] = useState("");
  const [locatedSeatNum, setLocatedSeatNum] = useState<string | null>(null);
  const [searchHint, setSearchHint] = useState<string | null>(null);
  const [hoveredSeat, setHoveredSeat] = useState<string | null>(null);
  const [bgLoaded, setBgLoaded] = useState(false);

  const rankMap = useMemo(() => {
    const map = new Map<string, number>();
    targetSeats.forEach((code, idx) => {
      map.set(code, idx + 1);
    });
    return map;
  }, [targetSeats]);

  const worldBounds = useMemo(() => {
    if (!seatMap) {
      return { minX: 0, minY: 0, width: 1200, height: 800 };
    }
    const { room, has_background, seats } = seatMap;
    let maxRight = has_background && room.bg_width > 0 ? room.bg_width : 0;
    let maxBottom = has_background && room.bg_height > 0 ? room.bg_height : 0;
    let minLeft = has_background ? 0 : Number.POSITIVE_INFINITY;
    let minTop = has_background ? 0 : Number.POSITIVE_INFINITY;

    for (const s of seats) {
      if (s.x < minLeft) minLeft = s.x;
      if (s.y < minTop) minTop = s.y;
      if (s.x + s.width > maxRight) maxRight = s.x + s.width;
      if (s.y + s.height > maxBottom) maxBottom = s.y + s.height;
    }

    if (!Number.isFinite(minLeft)) minLeft = 0;
    if (!Number.isFinite(minTop)) minTop = 0;

    if (has_background && room.bg_width > 0 && room.bg_height > 0) {
      return {
        minX: 0,
        minY: 0,
        width: Math.max(room.bg_width, maxRight + 24),
        height: Math.max(room.bg_height, maxBottom + 24),
      };
    }

    const pad = 56;
    return {
      minX: Math.min(0, minLeft - pad),
      minY: Math.min(-16, minTop - pad - 16),
      width: Math.max(640, maxRight + pad),
      height: Math.max(480, maxBottom + pad),
    };
  }, [seatMap]);

  const cancelAnimation = useCallback(() => {
    if (animFrameRef.current !== null) {
      cancelAnimationFrame(animFrameRef.current);
      animFrameRef.current = null;
    }
  }, []);

  const animateViewport = useCallback(
    (target: ViewportTransform, durationMs = 340) => {
      cancelAnimation();
      const start = { ...viewportRef.current };
      const t0 = performance.now();

      const step = (now: number) => {
        const elapsed = now - t0;
        const p = clamp(elapsed / durationMs, 0, 1);
        const eased = easeOutQuint(p);
        setViewport({
          x: start.x + (target.x - start.x) * eased,
          y: start.y + (target.y - start.y) * eased,
          scale: start.scale + (target.scale - start.scale) * eased,
        });
        if (p < 1) {
          animFrameRef.current = requestAnimationFrame(step);
        } else {
          animFrameRef.current = null;
        }
      };

      animFrameRef.current = requestAnimationFrame(step);
    },
    [cancelAnimation],
  );

  const computeFitTransform = useCallback((): ViewportTransform => {
    const el = containerRef.current;
    if (!el) return { x: 24, y: 24, scale: 0.7 };
    const rect = el.getBoundingClientRect();
    const availW = Math.max(200, rect.width - 56);
    const availH = Math.max(200, rect.height - 72);
    const totalW = worldBounds.width - worldBounds.minX;
    const totalH = worldBounds.height - worldBounds.minY;
    const scaleX = availW / totalW;
    const scaleY = availH / totalH;
    const fitScale = clamp(Math.min(scaleX, scaleY), MIN_SCALE, 1.35);
    const x =
      (rect.width - totalW * fitScale) / 2 - worldBounds.minX * fitScale;
    const y =
      (rect.height - totalH * fitScale) / 2 - worldBounds.minY * fitScale;
    return { x, y, scale: fitScale };
  }, [worldBounds]);

  const fitToView = useCallback(
    (animated = true) => {
      const target = computeFitTransform();
      if (animated) {
        animateViewport(target, 360);
      } else {
        cancelAnimation();
        setViewport(target);
      }
    },
    [animateViewport, cancelAnimation, computeFitTransform],
  );

  // Reset & fit viewport when switching reading rooms
  useEffect(() => {
    setBgLoaded(false);
    setLocatedSeatNum(null);
    setSearchHint(null);
    if (!seatMap) return;
    const id = window.requestAnimationFrame(() => {
      fitToView(false);
    });
    return () => window.cancelAnimationFrame(id);
  }, [seatMap?.room.id, fitToView]);

  // Refit gently when window resizes
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    let initialized = false;
    const ro = new ResizeObserver(() => {
      if (!initialized) {
        initialized = true;
        return;
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    return () => cancelAnimation();
  }, [cancelAnimation]);

  // Native non-passive wheel zoom centered at pointer
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      cancelAnimation();
      const rect = el.getBoundingClientRect();
      const cursorX = e.clientX - rect.left;
      const cursorY = e.clientY - rect.top;

      const cur = viewportRef.current;
      const zoomFactor = Math.exp(-e.deltaY * 0.0014);
      const nextScale = clamp(cur.scale * zoomFactor, MIN_SCALE, MAX_SCALE);

      const worldX = (cursorX - cur.x) / cur.scale;
      const worldY = (cursorY - cur.y) / cur.scale;

      setViewport({
        scale: nextScale,
        x: cursorX - worldX * nextScale,
        y: cursorY - worldY * nextScale,
      });
    };

    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [cancelAnimation]);

  const zoomByFactor = (factor: number) => {
    const el = containerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const cx = rect.width / 2;
    const cy = rect.height / 2;
    const cur = viewportRef.current;
    const nextScale = clamp(cur.scale * factor, MIN_SCALE, MAX_SCALE);
    const worldX = (cx - cur.x) / cur.scale;
    const worldY = (cy - cur.y) / cur.scale;
    animateViewport(
      {
        scale: nextScale,
        x: cx - worldX * nextScale,
        y: cy - worldY * nextScale,
      },
      240,
    );
  };

  const focusSeatOnCanvas = useCallback(
    (seat: SeatNode) => {
      const el = containerRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const targetScale = clamp(Math.max(viewportRef.current.scale, 1.08), 0.75, 1.85);
      const seatCenterX = seat.x + seat.width / 2;
      const seatCenterY = seat.y + seat.height / 2;
      animateViewport(
        {
          scale: targetScale,
          x: rect.width / 2 - seatCenterX * targetScale,
          y: rect.height / 2 - seatCenterY * targetScale,
        },
        380,
      );
      setLocatedSeatNum(seat.seat_num);
    },
    [animateViewport],
  );

  const findMatchingSeat = useCallback(
    (rawQuery: string): SeatNode | undefined => {
      if (!seatMap || !rawQuery.trim()) return undefined;
      const normalized = normalizeSeatCode(rawQuery);
      return (
        seatMap.seats.find((s) => s.seat_num === normalized) ??
        seatMap.seats.find((s) => s.seat_num === rawQuery.trim()) ??
        seatMap.seats.find((s) => s.seat_num.includes(rawQuery.trim()))
      );
    },
    [seatMap],
  );

  const handleSearchChange = (value: string) => {
    setSearchQuery(value);
    setSearchHint(null);
    if (!value.trim()) {
      setLocatedSeatNum(null);
      return;
    }
    const found = findMatchingSeat(value);
    if (found) {
      focusSeatOnCanvas(found);
    }
  };

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!searchQuery.trim()) return;
    const found = findMatchingSeat(searchQuery);
    if (!found) {
      setSearchHint(`当前阅览室未找到座位 ${normalizeSeatCode(searchQuery)}`);
      return;
    }
    focusSeatOnCanvas(found);
    onToggleSeat(found.seat_num);
    const alreadySelected = rankMap.has(found.seat_num);
    setSearchHint(
      alreadySelected
        ? `已将 ${found.seat_num} 号座位移出备选队列`
        : `已将 ${found.seat_num} 号座位加入第 ${targetSeats.length + 1} 顺位`,
    );
  };

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    cancelAnimation();
    dragStateRef.current = {
      active: true,
      pointerId: e.pointerId,
      startClientX: e.clientX,
      startClientY: e.clientY,
      originX: viewportRef.current.x,
      originY: viewportRef.current.y,
      moved: false,
    };
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const st = dragStateRef.current;
    if (!st.active || st.pointerId !== e.pointerId) return;
    const dx = e.clientX - st.startClientX;
    const dy = e.clientY - st.startClientY;
    if (!st.moved && Math.hypot(dx, dy) > 4) {
      st.moved = true;
      setIsDragging(true);
    }
    if (st.moved) {
      setViewport((prev) => ({
        ...prev,
        x: st.originX + dx,
        y: st.originY + dy,
      }));
    }
  };

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    const st = dragStateRef.current;
    if (st.pointerId === e.pointerId) {
      st.active = false;
      setIsDragging(false);
    }
  };

  const handleSeatClick = (e: React.MouseEvent, seatNum: string) => {
    e.stopPropagation();
    if (dragStateRef.current.moved) return;
    onToggleSeat(seatNum);
  };

  const room = seatMap?.room;
  const bgUrl =
    seatMap?.has_background && room
      ? `/api/rooms/${encodeURIComponent(room.id)}/background`
      : null;

  const selectedInCurrentRoom = useMemo(() => {
    if (!seatMap) return 0;
    const roomSeatSet = new Set(seatMap.seats.map((s) => s.seat_num));
    return targetSeats.filter((code) => roomSeatSet.has(code)).length;
  }, [seatMap, targetSeats]);

  return (
    <section className="relative flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-[#F5F5F7]">
      {/* Top Translucent Workbench Toolbar */}
      <header className="z-20 flex h-14 shrink-0 items-center justify-between gap-3 border-b border-black/[0.06] bg-white/80 px-5 backdrop-blur-xl">
        <div className="flex min-w-0 items-center gap-3">
          {room ? (
            <>
              <div className="flex min-w-0 items-baseline gap-2">
                <h1 className="truncate text-[15px] font-semibold tracking-tight text-[#1D1D1F]">
                  {room.first_level}
                  {room.second_level ? ` · ${room.second_level}` : ""}
                  {room.third_level ? ` · ${room.third_level}` : ""}
                </h1>
                <span className="shrink-0 rounded-full bg-black/[0.045] px-2 py-0.5 text-[11px] font-medium text-[#86868B] tabular-nums">
                  共 {room.capacity || seatMap?.seats.length || 0} 席
                </span>
              </div>

              <div className="hidden items-center gap-1.5 rounded-full border border-black/[0.06] bg-white/90 px-2.5 py-0.5 text-[11px] text-[#86868B] xl:flex">
                <Clock className="h-3 w-3 stroke-[1.75] text-[#86868B]" />
                <span className="tabular-nums">
                  {room.open_time} - {room.close_time}
                </span>
              </div>

              <div className="hidden items-center gap-1 rounded-full border border-black/[0.06] bg-white/90 px-2.5 py-0.5 text-[11px] text-[#86868B] 2xl:flex">
                {seatMap?.has_background ? (
                  <>
                    <Layers className="h-3 w-3 stroke-[1.75] text-[#0071E3]" />
                    <span>实景平面图</span>
                  </>
                ) : (
                  <>
                    <Grid3X3 className="h-3 w-3 stroke-[1.75] text-[#0071E3]" />
                    <span>规整矩阵图</span>
                  </>
                )}
              </div>
            </>
          ) : (
            <div className="h-4 w-48 animate-pulse rounded-md bg-black/[0.06]" />
          )}
        </div>

        {/* Quick Seat Search + Selection Summary */}
        <div className="flex shrink-0 items-center gap-2.5">
          <form onSubmit={handleSearchSubmit} className="relative flex items-center">
            <Search className="pointer-events-none absolute left-2.5 h-3.5 w-3.5 stroke-[1.75] text-[#86868B]" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => handleSearchChange(e.target.value)}
              placeholder="输入座位号定位 (如 007)"
              aria-label="搜索座位号快速定位"
              className="h-8 w-56 rounded-lg border border-black/[0.08] bg-[#F5F5F7]/90 pr-7 pl-8 text-[12px] text-[#1D1D1F] placeholder-[#86868B] tabular-nums transition-all focus:w-64 focus:border-[#0071E3] focus:bg-white focus:ring-2 focus:ring-[#0071E3]/15 focus:outline-none"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => {
                  setSearchQuery("");
                  setLocatedSeatNum(null);
                  setSearchHint(null);
                }}
                className="absolute right-2 rounded p-0.5 text-[#86868B] hover:bg-black/[0.06] hover:text-[#1D1D1F]"
                title="清除搜索"
              >
                <X className="h-3 w-3 stroke-[1.75]" />
              </button>
            )}
          </form>

          <div className="flex h-8 items-center gap-1.5 rounded-lg border border-[#0071E3]/20 bg-[#0071E3]/[0.07] px-3 text-[12px] font-medium text-[#0071E3] tabular-nums">
            <span className="h-1.5 w-1.5 rounded-full bg-[#0071E3]" />
            <span>已选 {selectedInCurrentRoom} 席</span>
          </div>
        </div>
      </header>

      {/* Subtle Floating Search Feedback Pill */}
      <AnimatePresence>
        {searchHint && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.2 }}
            className="apple-frosted pointer-events-none absolute top-16 right-5 z-30 rounded-full px-3.5 py-1.5 text-[12px] font-medium text-[#1D1D1F] tabular-nums"
          >
            {searchHint}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Main Interactive SVG Viewport */}
      <div
        ref={containerRef}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
        className={`relative flex-1 overflow-hidden select-none ${
          isDragging ? "cursor-grabbing" : "cursor-grab"
        }`}
        style={{
          backgroundImage:
            "radial-gradient(rgba(29, 29, 31, 0.075) 1px, transparent 1px)",
          backgroundSize: "20px 20px",
        }}
      >
        {loading && !seatMap && (
          <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 bg-[#F5F5F7]/85 backdrop-blur-xs">
            <RefreshCw className="h-6 w-6 animate-spin stroke-[1.6] text-[#0071E3]" />
            <p className="text-[13px] font-medium text-[#86868B]">
              正在载入阅览室平面坐标...
            </p>
          </div>
        )}

        {error && !seatMap && (
          <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 p-6">
            <div className="apple-card flex max-w-sm flex-col items-center gap-3 rounded-2xl p-6 text-center">
              <Compass className="h-8 w-8 stroke-[1.5] text-[#FF3B30]" />
              <div className="space-y-1">
                <h3 className="text-[14px] font-semibold text-[#1D1D1F]">
                  座位图加载未完成
                </h3>
                <p className="text-[12px] leading-relaxed text-[#86868B]">
                  {error}
                </p>
              </div>
              <button
                type="button"
                onClick={onRetry}
                className="inline-flex items-center gap-1.5 rounded-lg bg-[#0071E3] px-4 py-1.5 text-[12px] font-medium text-white transition hover:bg-[#0077ED]"
              >
                <RefreshCw className="h-3.5 w-3.5 stroke-[1.75]" />
                <span>重新加载</span>
              </button>
            </div>
          </div>
        )}

        <AnimatePresence mode="wait">
          {seatMap && (
            <motion.div
              key={seatMap.room.id}
              initial={{ opacity: 0, scale: 0.985 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.99 }}
              transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
              className="h-full w-full"
            >
              <svg
                className="h-full w-full overflow-visible"
                aria-label={`${seatMap.room.display_name} 座位平面图`}
              >
                <defs>
                  <pattern
                    id="architectural-grid"
                    width="32"
                    height="32"
                    patternUnits="userSpaceOnUse"
                  >
                    <circle cx="2" cy="2" r="1.15" fill="rgba(29,29,31,0.10)" />
                    <path
                      d="M 32 0 L 0 0 0 32"
                      fill="none"
                      stroke="rgba(29,29,31,0.03)"
                      strokeWidth="1"
                    />
                  </pattern>
                  <filter
                    id="selected-seat-glow"
                    x="-35%"
                    y="-35%"
                    width="170%"
                    height="170%"
                  >
                    <feDropShadow
                      dx="0"
                      dy="3"
                      stdDeviation="5"
                      floodColor="#0071E3"
                      floodOpacity="0.32"
                    />
                  </filter>
                  <filter
                    id="sheet-ambient-shadow"
                    x="-5%"
                    y="-5%"
                    width="110%"
                    height="112%"
                  >
                    <feDropShadow
                      dx="0"
                      dy="10"
                      stdDeviation="18"
                      floodColor="#000000"
                      floodOpacity="0.06"
                    />
                  </filter>
                </defs>

                <g
                  style={{
                    transform: `translate3d(${viewport.x}px, ${viewport.y}px, 0) scale(${viewport.scale})`,
                    transformOrigin: "0 0",
                    willChange: "transform",
                  }}
                >
                  {/* Architectural Floor Sheet */}
                  <rect
                    x={worldBounds.minX}
                    y={worldBounds.minY}
                    width={worldBounds.width - worldBounds.minX}
                    height={worldBounds.height - worldBounds.minY}
                    rx={20}
                    fill="#FFFFFF"
                    stroke="rgba(0,0,0,0.08)"
                    strokeWidth={1.5}
                    filter="url(#sheet-ambient-shadow)"
                  />

                  {bgUrl ? (
                    <>
                      {!bgLoaded && (
                        <rect
                          x={0}
                          y={0}
                          width={worldBounds.width}
                          height={worldBounds.height}
                          rx={18}
                          fill="url(#architectural-grid)"
                        />
                      )}
                      <image
                        href={bgUrl}
                        x={0}
                        y={0}
                        width={seatMap.room.bg_width || worldBounds.width}
                        height={seatMap.room.bg_height || worldBounds.height}
                        preserveAspectRatio="none"
                        onLoad={() => setBgLoaded(true)}
                        style={{ opacity: bgLoaded ? 0.96 : 0 }}
                      />
                    </>
                  ) : (
                    <>
                      <rect
                        x={worldBounds.minX + 8}
                        y={worldBounds.minY + 8}
                        width={worldBounds.width - worldBounds.minX - 16}
                        height={worldBounds.height - worldBounds.minY - 16}
                        rx={14}
                        fill="url(#architectural-grid)"
                      />
                      <text
                        x={worldBounds.minX + 28}
                        y={worldBounds.minY + 34}
                        fill="#86868B"
                        fontSize={13}
                        fontWeight={500}
                        letterSpacing="0.04em"
                      >
                        {seatMap.room.display_name} · 标准矩阵席位视图（面向次日 22:00 预约目标预选）
                      </text>
                    </>
                  )}

                  {/* Pure Two-State Seat Nodes (Unselected vs Selected with Priority Rank) */}
                  {seatMap.seats.map((seat) => {
                    const rank = rankMap.get(seat.seat_num);
                    const isSelected = rank !== undefined;
                    const isLocated = locatedSeatNum === seat.seat_num;
                    const isHovered = hoveredSeat === seat.seat_num;

                    const cx = seat.x + seat.width / 2;
                    const cy = seat.y + seat.height / 2;
                    const fontSize = clamp(
                      Math.round(Math.min(seat.width, seat.height) * 0.29),
                      10,
                      13,
                    );
                    const rx = clamp(Math.min(seat.width, seat.height) * 0.2, 6, 10);

                    return (
                      <g
                        key={seat.seat_num}
                        data-seat-num={seat.seat_num}
                        role="button"
                        aria-pressed={isSelected}
                        aria-label={
                          isSelected
                            ? `座位 ${seat.seat_num}，当前第 ${rank} 顺位`
                            : `座位 ${seat.seat_num}，可选`
                        }
                        onClick={(e) => handleSeatClick(e, seat.seat_num)}
                        onMouseEnter={() => setHoveredSeat(seat.seat_num)}
                        onMouseLeave={() =>
                          setHoveredSeat((prev) =>
                            prev === seat.seat_num ? null : prev,
                          )
                        }
                        className="cursor-pointer transition-transform duration-150 ease-out active:scale-[0.92]"
                        style={{
                          transformBox: "fill-box",
                          transformOrigin: "center",
                        }}
                      >
                        {/* Search Locator Pulse Ring */}
                        {isLocated && (
                          <rect
                            x={seat.x - 5}
                            y={seat.y - 5}
                            width={seat.width + 10}
                            height={seat.height + 10}
                            rx={rx + 4}
                            fill="none"
                            stroke="#0071E3"
                            strokeWidth={2.4}
                            className="seat-locate-ring"
                          />
                        )}

                        {/* Soft Selection Ring */}
                        {isSelected && (
                          <rect
                            x={seat.x - 2.5}
                            y={seat.y - 2.5}
                            width={seat.width + 5}
                            height={seat.height + 5}
                            rx={rx + 2.5}
                            fill="rgba(0, 113, 227, 0.14)"
                            stroke="rgba(0, 113, 227, 0.45)"
                            strokeWidth={1.2}
                          />
                        )}

                        {/* Directional Chair Glyph (rotated by Chaoxing's `rotate` angle) */}
                        <g transform={`rotate(${seat.rotate}, ${cx}, ${cy})`}>
                          {/* Chair Cushion Body */}
                          <rect
                            x={seat.x + 1.5}
                            y={seat.y + 3.5}
                            width={Math.max(16, seat.width - 3)}
                            height={Math.max(16, seat.height - 5)}
                            rx={rx}
                            fill={
                              isSelected
                                ? "#0071E3"
                                : isHovered
                                  ? "#F0F7FF"
                                  : "#FFFFFF"
                            }
                            stroke={
                              isSelected
                                ? "#005BB5"
                                : isHovered
                                  ? "#0071E3"
                                  : "#D6D6DE"
                            }
                            strokeWidth={isSelected ? 1.4 : 1.2}
                            filter={
                              isSelected ? "url(#selected-seat-glow)" : undefined
                            }
                          />

                          {/* Directional Backrest Indicator */}
                          <rect
                            x={seat.x + Math.max(5, seat.width * 0.18)}
                            y={seat.y + 0.8}
                            width={Math.max(10, seat.width * 0.64)}
                            height={3.2}
                            rx={1.6}
                            fill={
                              isSelected
                                ? "#7CC0FF"
                                : isHovered
                                  ? "#0071E3"
                                  : "#B8B8C2"
                            }
                          />
                        </g>

                        {/* Always-Upright Tabular Seat Number */}
                        <text
                          x={cx}
                          y={cy + 0.8}
                          textAnchor="middle"
                          dominantBaseline="central"
                          fill={isSelected ? "#FFFFFF" : "#1D1D1F"}
                          fontSize={fontSize}
                          fontWeight={isSelected ? 650 : 560}
                          className="pointer-events-none tabular-nums"
                        >
                          {seat.seat_num}
                        </text>

                        {/* Spring-Animated Priority Rank Badge (1, 2, 3...) */}
                        {isSelected && rank !== undefined && (
                          <motion.g
                            key={`rank-${seat.seat_num}-${rank}`}
                            initial={{ scale: 0.35, opacity: 0 }}
                            animate={{ scale: 1, opacity: 1 }}
                            transition={{
                              type: "spring",
                              stiffness: 500,
                              damping: 26,
                            }}
                            style={{
                              transformBox: "fill-box",
                              transformOrigin: "center",
                            }}
                          >
                            <rect
                              x={seat.x + seat.width - (rank >= 10 ? 18 : 14)}
                              y={seat.y - 6}
                              width={rank >= 10 ? 22 : 18}
                              height={18}
                              rx={9}
                              fill="#1D1D1F"
                              stroke="#FFFFFF"
                              strokeWidth={2}
                            />
                            <text
                              x={
                                seat.x +
                                seat.width -
                                (rank >= 10 ? 18 : 14) +
                                (rank >= 10 ? 11 : 9)
                              }
                              y={seat.y + 3}
                              textAnchor="middle"
                              dominantBaseline="central"
                              fill="#FFFFFF"
                              fontSize={10}
                              fontWeight={700}
                              className="pointer-events-none tabular-nums"
                            >
                              {rank}
                            </text>
                          </motion.g>
                        )}
                      </g>
                    );
                  })}
                </g>
              </svg>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Loading Overlay Badge when switching rooms smoothly */}
        {loading && seatMap && (
          <div className="apple-frosted pointer-events-none absolute top-4 left-5 z-20 inline-flex items-center gap-2 rounded-full px-3 py-1 text-[11px] font-medium text-[#86868B]">
            <RefreshCw className="h-3 w-3 animate-spin stroke-[1.75] text-[#0071E3]" />
            <span>切换平面图中...</span>
          </div>
        )}

        {/* Bottom-Left Pure Two-State Legend Capsule */}
        <div className="apple-frosted pointer-events-none absolute bottom-4 left-5 z-20 flex items-center gap-4 rounded-full px-3.5 py-1.5 text-[11px] text-[#86868B]">
          <div className="flex items-center gap-1.5">
            <span className="inline-block h-3 w-3 rounded-[3px] border border-[#D6D6DE] bg-white" />
            <span className="text-[#1D1D1F]">次日可选席位</span>
          </div>
          <div className="h-3 w-px bg-black/[0.08]" />
          <div className="flex items-center gap-1.5">
            <span className="relative inline-flex h-3 w-3 items-center justify-center rounded-[3px] bg-[#0071E3]">
              <span className="absolute -top-1 -right-1 flex h-2.5 w-2.5 items-center justify-center rounded-full bg-[#1D1D1F] text-[7px] font-bold text-white tabular-nums">
                1
              </span>
            </span>
            <span className="text-[#1D1D1F]">抢座目标顺位</span>
          </div>
          <span className="hidden text-[#86868B] lg:inline">
            点击席位加入或移出备选队列
          </span>
        </div>

        {/* Bottom-Right Floating Frosted Zoom & Fit HUD Pill */}
        <div
          onPointerDown={(e) => e.stopPropagation()}
          className="apple-frosted absolute right-5 bottom-4 z-20 flex items-center gap-1 rounded-full p-1"
        >
          <button
            type="button"
            onClick={() => zoomByFactor(1 / 1.25)}
            className="flex h-7 w-7 items-center justify-center rounded-full text-[#1D1D1F] transition hover:bg-black/[0.06] active:scale-95"
            title="缩小画布"
            aria-label="缩小画布"
          >
            <ZoomOut className="h-3.5 w-3.5 stroke-[1.75]" />
          </button>
          <span className="min-w-[44px] text-center text-[11px] font-medium text-[#1D1D1F] tabular-nums">
            {Math.round(viewport.scale * 100)}%
          </span>
          <button
            type="button"
            onClick={() => zoomByFactor(1.25)}
            className="flex h-7 w-7 items-center justify-center rounded-full text-[#1D1D1F] transition hover:bg-black/[0.06] active:scale-95"
            title="放大画布"
            aria-label="放大画布"
          >
            <ZoomIn className="h-3.5 w-3.5 stroke-[1.75]" />
          </button>
          <div className="mx-0.5 h-3.5 w-px bg-black/[0.08]" />
          <button
            type="button"
            onClick={() => fitToView(true)}
            className="inline-flex h-7 items-center gap-1 rounded-full px-2.5 text-[11px] font-medium text-[#1D1D1F] transition hover:bg-black/[0.06] active:scale-95"
            title="适配画布大小"
          >
            <Maximize2 className="h-3 w-3 stroke-[1.75]" />
            <span>适配画布</span>
          </button>
        </div>
      </div>
    </section>
  );
};
