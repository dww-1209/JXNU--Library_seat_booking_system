export interface Room {
  id: string;
  first_level: string;
  second_level: string;
  third_level: string;
  display_name: string;
  capacity: number;
  start_seat_num: number;
  open_time: string;
  close_time: string;
  pic_seat_bg: string | null;
  bg_width: number;
  bg_height: number;
  pic_seat_mode: number;
}

export interface SeatNode {
  seat_num: string;
  x: number;
  y: number;
  width: number;
  height: number;
  rotate: number;
}

export interface RoomSeatMap {
  room: Room;
  has_background: boolean;
  seats: SeatNode[];
}

export interface ReservationConfig {
  deptIdEnc: string;
  roomId: string;
  roomName: string;
  targetSeats: string[];
  startTime: string;
  endTime: string;
  reserveDaysAhead: number;
}

export interface ScheduleConfig {
  enabled: boolean;
  targetTime: string;
  prepareSecondsAhead: number;
  winTaskEnabled: boolean;
  winTaskTime: string;
  skipDate: string | null;
}

export interface AuthSummary {
  phoneMasked: string;
  phone: string;
  hasPassword: boolean;
  loggedIn: boolean;
  message?: string;
}

export interface SanitizedAppConfig {
  reservation: ReservationConfig;
  schedule: ScheduleConfig;
  auth: AuthSummary;
}

export interface ReservationRecord {
  id: string | null;
  room_id: string | null;
  room_name: string;
  seat_num: string;
  day: string;
  start_time: string;
  end_time: string;
  status_text: string;
}

export interface VerificationResult {
  verified: boolean;
  matched_record: ReservationRecord | null;
  active_records: ReservationRecord[];
  message: string;
  checked_at: string;
}

export type EngineState =
  | "IDLE"
  | "WAITING"
  | "PREWARMING"
  | "RUNNING"
  | "VERIFYING"
  | "COMPLETED"
  | "FAILED";

export interface RunResult {
  success: boolean;
  locked_seat: string | null;
  verified: boolean;
  verification: VerificationResult | null;
  attempts: number;
  elapsed_sec: number;
  message: string;
}

export interface WinTaskStatus {
  enabled: boolean;
  runTime: string;
  nextRunTime: string;
  taskName: string;
  message: string;
}

export interface RuntimeStatus {
  state: EngineState;
  targetTime: string;
  targetDay: string;
  countdownSec: number | null;
  clockOffsetMs: number | null;
  lastResult: RunResult | null;
  verification: VerificationResult | null;
  winTask: WinTaskStatus;
}

export interface LogEntry {
  id: number;
  timestamp: string;
  level: string;
  stage: string;
  message: string;
  detail: Record<string, unknown> | null;
}

export interface LogsResponse {
  logFile: string;
  entries: LogEntry[];
}

export interface LoginResponse {
  success: boolean;
  message: string;
  phoneMasked: string;
}

export interface WindowsSchedulerResponse {
  success: boolean;
  message: string;
  status: WinTaskStatus;
}
