import type {
  AuthSummary,
  EngineState,
  LogEntry,
  LoginResponse,
  LogsResponse,
  ReservationConfig,
  ReservationRecord,
  Room,
  RoomSeatMap,
  RunResult,
  RuntimeStatus,
  SanitizedAppConfig,
  ScheduleConfig,
  SeatNode,
  VerificationResult,
  WindowsSchedulerResponse,
  WinTaskStatus,
} from "./types";

type UnknownRecord = Record<string, unknown>;

function asRecord(val: unknown): UnknownRecord {
  return val !== null && typeof val === "object" && !Array.isArray(val)
    ? (val as UnknownRecord)
    : {};
}

function pickStr(obj: UnknownRecord, keys: string[], fallback = ""): string {
  for (const k of keys) {
    const v = obj[k];
    if (typeof v === "string" && v.length > 0) return v;
    if (typeof v === "number" && Number.isFinite(v)) return String(v);
  }
  for (const k of keys) {
    const v = obj[k];
    if (typeof v === "string") return v;
  }
  return fallback;
}

function pickNullableStr(
  obj: UnknownRecord,
  keys: string[],
): string | null {
  for (const k of keys) {
    const v = obj[k];
    if (typeof v === "string" && v.length > 0) return v;
    if (typeof v === "number" && Number.isFinite(v)) return String(v);
  }
  return null;
}

function pickNum(obj: UnknownRecord, keys: string[], fallback = 0): number {
  for (const k of keys) {
    const v = obj[k];
    if (typeof v === "number" && Number.isFinite(v)) return v;
    if (typeof v === "string" && v.trim() !== "") {
      const parsed = Number(v);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return fallback;
}

function pickNullableNum(
  obj: UnknownRecord,
  keys: string[],
): number | null {
  for (const k of keys) {
    const v = obj[k];
    if (typeof v === "number" && Number.isFinite(v)) return v;
    if (typeof v === "string" && v.trim() !== "") {
      const parsed = Number(v);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return null;
}

function pickBool(obj: UnknownRecord, keys: string[], fallback = false): boolean {
  for (const k of keys) {
    const v = obj[k];
    if (typeof v === "boolean") return v;
    if (v === 1 || v === "true") return true;
    if (v === 0 || v === "false") return false;
  }
  return fallback;
}

export function normalizeSeatCode(raw: string): string {
  const trimmed = raw.trim();
  if (/^\d+$/.test(trimmed)) {
    return trimmed.padStart(3, "0");
  }
  return trimmed;
}

export function normalizeRoom(raw: unknown): Room {
  const r = asRecord(raw);
  const first_level = pickStr(
    r,
    ["first_level", "firstLevel", "firstLevelName"],
    "未分组区域",
  );
  const second_level = pickStr(
    r,
    ["second_level", "secondLevel", "secondLevelName"],
    "",
  );
  const third_level = pickStr(
    r,
    ["third_level", "thirdLevel", "thirdLevelName"],
    "",
  );
  const fallbackName = [first_level, second_level, third_level]
    .filter(Boolean)
    .join(" · ");
  return {
    id: pickStr(r, ["id", "room_id", "roomId"], ""),
    first_level,
    second_level,
    third_level,
    display_name: pickStr(r, ["display_name", "displayName", "name"], fallbackName),
    capacity: pickNum(r, ["capacity", "totalSeats", "seatCount"], 0),
    start_seat_num: pickNum(r, ["start_seat_num", "startSeatNum"], 1),
    open_time: pickStr(r, ["open_time", "openTime"], "08:00"),
    close_time: pickStr(r, ["close_time", "closeTime"], "22:00"),
    pic_seat_bg: pickNullableStr(r, ["pic_seat_bg", "picSeatBg"]),
    bg_width: pickNum(r, ["bg_width", "bgWidth", "picSeatBgWidth"], 0),
    bg_height: pickNum(r, ["bg_height", "bgHeight", "picSeatBgHeight"], 0),
    pic_seat_mode: pickNum(r, ["pic_seat_mode", "picSeatMode"], 0),
  };
}

export function normalizeSeatNode(raw: unknown): SeatNode {
  const s = asRecord(raw);
  const rawCode = pickStr(s, ["seat_num", "seatNum", "num"], "");
  return {
    seat_num: normalizeSeatCode(rawCode),
    x: pickNum(s, ["x", "xleft", "left"], 0),
    y: pickNum(s, ["y", "ytop", "top"], 0),
    width: pickNum(s, ["width", "w"], 41),
    height: pickNum(s, ["height", "h"], 41),
    rotate: pickNum(s, ["rotate", "rotation", "angle"], 0),
  };
}

export function normalizeRoomSeatMap(raw: unknown): RoomSeatMap {
  const m = asRecord(raw);
  const room = normalizeRoom(m.room ?? m);
  const rawSeats = Array.isArray(m.seats) ? m.seats : [];
  const hasBg = pickBool(
    m,
    ["has_background", "hasBackground"],
    Boolean(room.pic_seat_bg && room.pic_seat_mode !== 2),
  );
  return {
    room,
    has_background: hasBg,
    seats: rawSeats.map(normalizeSeatNode),
  };
}

export function normalizeReservationRecord(raw: unknown): ReservationRecord {
  const r = asRecord(raw);
  return {
    id: pickNullableStr(r, ["id", "reserveId", "record_id"]),
    room_id: pickNullableStr(r, ["room_id", "roomId", "seatRoomId"]),
    room_name: pickStr(
      r,
      ["room_name", "roomName", "firstLevelName", "location"],
      "未知阅览室",
    ),
    seat_num: normalizeSeatCode(pickStr(r, ["seat_num", "seatNum"], "")),
    day: pickStr(r, ["day", "date", "today"], ""),
    start_time: pickStr(r, ["start_time", "startTime"], ""),
    end_time: pickStr(r, ["end_time", "endTime"], ""),
    status_text: pickStr(r, ["status_text", "statusText", "status"], "有效预约"),
  };
}

export function normalizeVerificationResult(
  raw: unknown,
): VerificationResult | null {
  if (!raw || typeof raw !== "object") return null;
  const unwrapped = asRecord(raw);
  const v =
    unwrapped.verification && typeof unwrapped.verification === "object"
      ? asRecord(unwrapped.verification)
      : unwrapped;
  if (Object.keys(v).length === 0) return null;

  const matchedRaw = v.matched_record ?? v.matchedRecord ?? null;
  const activeRaw = v.active_records ?? v.activeRecords ?? [];
  return {
    verified: pickBool(v, ["verified", "ok", "matched"], false),
    matched_record:
      matchedRaw && typeof matchedRaw === "object"
        ? normalizeReservationRecord(matchedRaw)
        : null,
    active_records: Array.isArray(activeRaw)
      ? activeRaw.map(normalizeReservationRecord)
      : [],
    message: pickStr(v, ["message", "msg", "summary"], ""),
    checked_at: pickStr(v, ["checked_at", "checkedAt", "timestamp"], ""),
  };
}

export function normalizeRunResult(raw: unknown): RunResult | null {
  if (!raw || typeof raw !== "object") return null;
  const r = asRecord(raw);
  if (Object.keys(r).length === 0) return null;
  return {
    success: pickBool(r, ["success", "ok"], false),
    locked_seat: pickNullableStr(r, ["locked_seat", "lockedSeat", "seat_num"]),
    verified: pickBool(r, ["verified"], false),
    verification: normalizeVerificationResult(r.verification),
    attempts: pickNum(r, ["attempts", "attemptCount"], 0),
    elapsed_sec: pickNum(r, ["elapsed_sec", "elapsedSec"], 0),
    message: pickStr(r, ["message", "msg"], ""),
  };
}

export function normalizeWinTaskStatus(raw: unknown): WinTaskStatus {
  const w = asRecord(raw);
  return {
    enabled: pickBool(
      w,
      ["enabled", "installed", "exists", "active", "winTaskEnabled"],
      false,
    ),
    runTime: pickStr(w, ["runTime", "run_time", "winTaskTime", "time"], "21:58"),
    nextRunTime: pickStr(w, ["nextRunTime", "next_run_time", "nextRun"], ""),
    taskName: pickStr(
      w,
      ["taskName", "task_name", "name"],
      "ChaoxingSeatReserverApp",
    ),
    message: pickStr(w, ["message", "status", "detail"], ""),
  };
}

const VALID_ENGINE_STATES: ReadonlySet<string> = new Set([
  "IDLE",
  "WAITING",
  "PREWARMING",
  "RUNNING",
  "VERIFYING",
  "COMPLETED",
  "FAILED",
]);

export function normalizeEngineState(raw: unknown): EngineState {
  if (typeof raw === "string") {
    const upper = raw.toUpperCase().trim();
    if (VALID_ENGINE_STATES.has(upper)) {
      return upper as EngineState;
    }
  }
  return "IDLE";
}

export function normalizeRuntimeStatus(raw: unknown): RuntimeStatus {
  const s = asRecord(raw);
  return {
    state: normalizeEngineState(s.state ?? s.engine_state ?? s.engineState),
    targetTime: pickStr(s, ["targetTime", "target_time"], "22:00:00"),
    targetDay: pickStr(s, ["targetDay", "target_day"], ""),
    countdownSec: pickNullableNum(s, [
      "countdownSec",
      "countdown_sec",
      "remainingSec",
    ]),
    clockOffsetMs: pickNullableNum(s, [
      "clockOffsetMs",
      "clock_offset_ms",
      "offsetMs",
    ]),
    lastResult: normalizeRunResult(s.lastResult ?? s.last_result),
    verification: normalizeVerificationResult(s.verification),
    winTask: normalizeWinTaskStatus(s.winTask ?? s.win_task),
  };
}

export function normalizeAppConfig(raw: unknown): SanitizedAppConfig {
  const root = asRecord(raw);
  const res = asRecord(root.reservation);
  const sch = asRecord(root.schedule);
  const auth = asRecord(root.auth);
  const creds = asRecord(auth.credentials);

  const rawSeats = res.targetSeats ?? res.target_seats;
  const targetSeats = Array.isArray(rawSeats)
    ? rawSeats.map((x) => normalizeSeatCode(String(x))).filter(Boolean)
    : [];

  const phone = pickStr(auth, ["phone"], pickStr(creds, ["phone"], ""));
  const phoneMasked = pickStr(
    auth,
    ["phoneMasked", "phone_masked"],
    phone.length >= 7
      ? `${phone.slice(0, 3)}****${phone.slice(-4)}`
      : phone,
  );

  return {
    reservation: {
      deptIdEnc: pickStr(res, ["deptIdEnc", "dept_id_enc"], "df23c42633fa45e1"),
      roomId: pickStr(res, ["roomId", "room_id"], "2552"),
      roomName: pickStr(res, ["roomName", "room_name"], ""),
      targetSeats,
      startTime: pickStr(res, ["startTime", "start_time"], "08:00"),
      endTime: pickStr(res, ["endTime", "end_time"], "22:00"),
      reserveDaysAhead: pickNum(
        res,
        ["reserveDaysAhead", "reserve_days_ahead"],
        1,
      ),
    },
    schedule: {
      enabled: pickBool(sch, ["enabled"], true),
      targetTime: pickStr(sch, ["targetTime", "target_time"], "22:00:00"),
      prepareSecondsAhead: pickNum(
        sch,
        ["prepareSecondsAhead", "prepare_seconds_ahead"],
        60,
      ),
      winTaskEnabled: pickBool(
        sch,
        ["winTaskEnabled", "win_task_enabled"],
        false,
      ),
      winTaskTime: pickStr(sch, ["winTaskTime", "win_task_time"], "21:59"),
      skipDate: pickNullableStr(sch, ["skipDate", "skip_date"]),
    },
    auth: {
      phoneMasked,
      phone,
      hasPassword: pickBool(
        auth,
        ["hasPassword", "has_password"],
        Boolean(pickStr(creds, ["password"], "")),
      ),
      loggedIn: pickBool(auth, ["loggedIn", "logged_in"], false),
      message: pickStr(auth, ["message"], ""),
    },
  };
}

export function normalizeLogEntry(raw: unknown, fallbackId = 0): LogEntry {
  const l = asRecord(raw);
  const detailRaw = l.detail ?? l.extra ?? null;
  return {
    id: pickNum(l, ["id", "seq"], fallbackId),
    timestamp: pickStr(l, ["timestamp", "time", "created_at"], ""),
    level: pickStr(l, ["level"], "INFO").toUpperCase(),
    stage: pickStr(l, ["stage", "module", "phase"], "SYSTEM"),
    message: pickStr(l, ["message", "msg"], ""),
    detail:
      detailRaw && typeof detailRaw === "object" && !Array.isArray(detailRaw)
        ? (detailRaw as Record<string, unknown>)
        : null,
  };
}

async function requestJson<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  const text = await res.text();
  let parsed: unknown = {};
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = { detail: text };
    }
  }

  if (!res.ok) {
    const obj = asRecord(parsed);
    const errMsg = pickStr(
      obj,
      ["detail", "message", "error"],
      `HTTP ${res.status} 请求失败`,
    );
    throw new Error(errMsg);
  }
  return parsed as T;
}

export const api = {
  async getHealth(): Promise<boolean> {
    const data = await requestJson<UnknownRecord>("/api/health");
    return pickBool(data, ["ok", "healthy"], true);
  },

  async getConfig(): Promise<SanitizedAppConfig> {
    const data = await requestJson<unknown>("/api/config");
    return normalizeAppConfig(data);
  },

  async saveConfig(payload: {
    reservation?: Partial<ReservationConfig>;
    schedule?: Partial<ScheduleConfig>;
  }): Promise<SanitizedAppConfig> {
    const data = await requestJson<unknown>("/api/config", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    const obj = asRecord(data);
    if (obj.config && typeof obj.config === "object") {
      return normalizeAppConfig(obj.config);
    }
    return normalizeAppConfig(data);
  },

  async getAuthStatus(): Promise<AuthSummary> {
    const data = await requestJson<unknown>("/api/auth/status");
    const a = asRecord(data);
    return {
      loggedIn: pickBool(a, ["loggedIn", "logged_in", "ok"], false),
      message: pickStr(a, ["message", "msg"], ""),
      phoneMasked: pickStr(a, ["phoneMasked", "phone_masked"], ""),
      phone: pickStr(a, ["phone"], ""),
      hasPassword: pickBool(a, ["hasPassword", "has_password"], false),
    };
  },

  async login(params: {
    phone: string;
    password: string;
    deptIdEnc?: string;
  }): Promise<LoginResponse> {
    const data = await requestJson<unknown>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify(params),
    });
    const r = asRecord(data);
    return {
      success: pickBool(r, ["success", "loggedIn", "ok"], false),
      message: pickStr(r, ["message", "msg"], ""),
      phoneMasked: pickStr(r, ["phoneMasked", "phone_masked"], ""),
    };
  },

  async getRooms(): Promise<Room[]> {
    const data = await requestJson<unknown>("/api/rooms");
    if (Array.isArray(data)) {
      return data.map(normalizeRoom);
    }
    const obj = asRecord(data);
    const list = Array.isArray(obj.rooms) ? obj.rooms : [];
    return list.map(normalizeRoom);
  },

  async getRoomSeatMap(roomId: string): Promise<RoomSeatMap> {
    const data = await requestJson<unknown>(
      `/api/rooms/${encodeURIComponent(roomId)}/seats`,
    );
    return normalizeRoomSeatMap(data);
  },

  async getRuntimeStatus(): Promise<RuntimeStatus> {
    const data = await requestJson<unknown>("/api/runtime/status");
    return normalizeRuntimeStatus(data);
  },

  async startSchedule(): Promise<RuntimeStatus> {
    const data = await requestJson<unknown>("/api/runtime/start-schedule", {
      method: "POST",
      body: JSON.stringify({}),
    });
    const obj = asRecord(data);
    return normalizeRuntimeStatus(obj.status ?? data);
  },

  async stopRuntime(): Promise<RuntimeStatus> {
    const data = await requestJson<unknown>("/api/runtime/stop", {
      method: "POST",
      body: JSON.stringify({}),
    });
    const obj = asRecord(data);
    return normalizeRuntimeStatus(obj.status ?? data);
  },

  async runNow(dryVerifyOnly = false): Promise<unknown> {
    return requestJson<unknown>("/api/runtime/run-now", {
      method: "POST",
      body: JSON.stringify({ dryVerifyOnly }),
    });
  },

  async verifyReservation(targetDay?: string): Promise<VerificationResult> {
    const body = targetDay ? { targetDay } : {};
    const data = await requestJson<unknown>("/api/runtime/verify", {
      method: "POST",
      body: JSON.stringify(body),
    });
    return (
      normalizeVerificationResult(data) ?? {
        verified: false,
        matched_record: null,
        active_records: [],
        message: "未获取到预约核验详情",
        checked_at: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
      }
    );
  },

  async toggleWindowsScheduler(params: {
    enabled: boolean;
    runTime?: string;
  }): Promise<WindowsSchedulerResponse> {
    const data = await requestJson<unknown>("/api/scheduler/windows", {
      method: "POST",
      body: JSON.stringify(params),
    });
    const obj = asRecord(data);
    return {
      success: pickBool(obj, ["success", "ok"], true),
      message: pickStr(obj, ["message", "msg"], ""),
      status: normalizeWinTaskStatus(obj.status ?? obj.winTask ?? obj),
    };
  },

  async getLogs(): Promise<LogsResponse> {
    const data = await requestJson<unknown>("/api/logs");
    const obj = asRecord(data);
    const rawList = Array.isArray(obj.entries)
      ? obj.entries
      : Array.isArray(obj.logs)
        ? obj.logs
        : Array.isArray(data)
          ? data
          : [];
    return {
      logFile: pickStr(
        obj,
        ["logFile", "log_file", "path"],
        "logs/chaoxing_reserver.log",
      ),
      entries: rawList.map((item, idx) => normalizeLogEntry(item, idx + 1)),
    };
  },

  connectLogStream(callbacks: {
    onLog: (entry: LogEntry) => void;
    onStatus: (status: RuntimeStatus) => void;
    onConnectionChange?: (connected: boolean) => void;
  }): () => void {
    let es: EventSource | null = null;
    let reconnectTimer: number | null = null;
    let disposed = false;

    const connect = () => {
      if (disposed) return;
      es = new EventSource("/api/logs/stream");

      es.onopen = () => {
        callbacks.onConnectionChange?.(true);
      };

      es.addEventListener("log", (ev: MessageEvent) => {
        try {
          const parsed = JSON.parse(String(ev.data));
          callbacks.onLog(normalizeLogEntry(parsed, Date.now()));
        } catch {
          // ignore malformed heartbeat
        }
      });

      es.addEventListener("status", (ev: MessageEvent) => {
        try {
          const parsed = JSON.parse(String(ev.data));
          callbacks.onStatus(normalizeRuntimeStatus(parsed));
        } catch {
          // ignore malformed status payload
        }
      });

      es.onmessage = (ev: MessageEvent) => {
        if (!ev.data) return;
        try {
          const parsed = asRecord(JSON.parse(String(ev.data)));
          if ("message" in parsed && ("level" in parsed || "stage" in parsed)) {
            callbacks.onLog(normalizeLogEntry(parsed, Date.now()));
          } else if ("state" in parsed || "countdownSec" in parsed) {
            callbacks.onStatus(normalizeRuntimeStatus(parsed));
          }
        } catch {
          // ignore keepalive comments
        }
      };

      es.onerror = () => {
        callbacks.onConnectionChange?.(false);
        es?.close();
        es = null;
        if (!disposed && reconnectTimer === null) {
          reconnectTimer = window.setTimeout(() => {
            reconnectTimer = null;
            connect();
          }, 2500);
        }
      };
    };

    connect();

    return () => {
      disposed = true;
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
      }
      es?.close();
    };
  },
};
