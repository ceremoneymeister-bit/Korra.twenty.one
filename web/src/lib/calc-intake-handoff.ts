import { fetchJSON } from "@/lib/api";

export type IntakeHandoffStatus =
  | "prepared"
  | "connecting"
  | "running"
  | "received"
  | "needs_attention"
  | "stale";

export interface IntakeHandoff {
  handoff_id: string;
  order_id: string;
  order_name: string;
  session_id: string;
  profile: "default";
  snapshot_id: string;
  status: IntakeHandoffStatus;
  initial_run_active: boolean;
  session_created?: boolean;
  /** Server-authoritative compose guard. Older responses fall back to
   * `initial_run_active` until every Calc21 contour has the new field. */
  chat_blocked?: boolean;
  received_at: number | null;
  run_id?: string | null;
  error_code?: string | null;
  dispatch_status?:
    | "prepared"
    | "connecting"
    | "running"
    | "finished"
    | "error";
  updated_at?: number;
}

export interface IntakeHandoffLookup {
  handoff: IntakeHandoff | null;
}

export function findIntakeHandoff(orderId: string): Promise<IntakeHandoffLookup> {
  return fetchJSON<IntakeHandoffLookup>(
    `/api/calc/orders/${encodeURIComponent(orderId)}/intake-handoff`,
  );
}

export function createIntakeHandoff(orderId: string): Promise<IntakeHandoff> {
  return fetchJSON<IntakeHandoff>(
    `/api/calc/orders/${encodeURIComponent(orderId)}/intake-handoff`,
    { method: "POST" },
  );
}

export function getIntakeHandoff(handoffId: string): Promise<IntakeHandoff> {
  return fetchJSON<IntakeHandoff>(
    `/api/calc/intake-handoffs/${encodeURIComponent(handoffId)}`,
  );
}

export function intakeHandoffIsTerminal(status: IntakeHandoffStatus): boolean {
  return status === "received" || status === "needs_attention" || status === "stale";
}
