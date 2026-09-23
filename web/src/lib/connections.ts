/**
 * Правила экрана «Сервисы» без React и сети.
 *
 * Подключение Google принадлежит одному агенту — источнику. Остальным
 * владелец открывает его явно: токен не копируется, агент пользуется тем же
 * доступом, а отключение у источника закрывает его всем. Здесь решается, кому
 * это подключение можно открыть одним переключателем, а кому нет и почему.
 *
 * Правила повторяют серверные (`configure_sharing`), чтобы экран не
 * предлагал того, что сервер отклонит: у агента со своим подключением или с
 * начатым подключением общий доступ не включить, а цепочки запрещены.
 */

import type { ConnectionsGoogleProfile } from "@/lib/api";

export const GOOGLE_SERVICE_LABELS: Record<string, string> = {
  calendar: "Календарь",
  email: "Почта",
  drive: "Диск",
  sheets: "Таблицы",
  docs: "Документы",
  contacts: "Контакты",
};

/** Порядок чипов: календарь первым — ради него сюда чаще всего приходят. */
const SERVICE_ORDER = ["calendar", "email", "drive", "sheets", "docs", "contacts"];

export function serviceLabels(services: string[]): string[] {
  return [...services]
    .sort((a, b) => SERVICE_ORDER.indexOf(a) - SERVICE_ORDER.indexOf(b))
    .map((service) => GOOGLE_SERVICE_LABELS[service] ?? service);
}

export function profileLabel(row: Pick<ConnectionsGoogleProfile, "profile" | "label">): string {
  if (row.label) return row.label;
  return row.profile === "default" ? "Главный агент" : row.profile;
}

/** Агенты, которые держат собственное подключение Google. */
export function googleSources(rows: ConnectionsGoogleProfile[]): ConnectionsGoogleProfile[] {
  return rows.filter((row) => row.access === "own");
}

/** Источник пригоден, чтобы им делиться: сервер откажет, если нет. */
export function sourceIsUsable(source: ConnectionsGoogleProfile): boolean {
  return source.state === "connected" || Boolean(source.legacy_compatible);
}

export type BorrowStatus =
  | { kind: "source" }
  | { kind: "shared" }
  | { kind: "available" }
  | { kind: "own" }
  | { kind: "lender" }
  | { kind: "pending" }
  | { kind: "other"; source: string };

/** Каким может быть доступ агента `row` к подключению `source`. */
export function borrowStatus(
  row: ConnectionsGoogleProfile,
  source: ConnectionsGoogleProfile,
): BorrowStatus {
  if (row.profile === source.profile) return { kind: "source" };
  if (row.access === "own") return row.shared_with?.length ? { kind: "lender" } : { kind: "own" };
  if (row.access === "shared") {
    return row.shared_from === source.profile
      ? { kind: "shared" }
      : { kind: "other", source: row.shared_from ?? "" };
  }
  if (row.pending) return { kind: "pending" };
  return { kind: "available" };
}

export function canToggle(status: BorrowStatus): boolean {
  return status.kind === "shared" || status.kind === "available";
}

export interface SharingState {
  /** Кому это подключение можно открыть или уже открыто. */
  eligible: string[];
  /** Кому открыто сейчас. */
  shared: string[];
  /** Открыто всем, кому можно (и таких хотя бы один). */
  all: boolean;
}

export function sharingState(
  rows: ConnectionsGoogleProfile[],
  source: ConnectionsGoogleProfile,
): SharingState {
  const eligible: string[] = [];
  const shared: string[] = [];
  for (const row of rows) {
    const status = borrowStatus(row, source);
    if (!canToggle(status)) continue;
    eligible.push(row.profile);
    if (status.kind === "shared") shared.push(row.profile);
  }
  return { eligible, shared, all: eligible.length > 0 && shared.length === eligible.length };
}

/** Новый состав общего доступа после переключения одного агента. */
export function toggledSharing(shared: string[], profile: string, on: boolean): string[] {
  const next = new Set(shared);
  if (on) next.add(profile);
  else next.delete(profile);
  return [...next].sort();
}

/** Пояснение в строке агента: что у него сейчас с этим подключением. */
export function borrowStatusText(
  status: BorrowStatus,
  names: (profile: string) => string,
): string {
  switch (status.kind) {
    case "source":
      return "Источник подключения";
    case "shared":
      return "Пользуется этим подключением";
    case "available":
      return "Нет доступа";
    case "own":
      return "Своё подключение Google";
    case "lender":
      return "Делится своим подключением Google";
    case "pending":
      return "Подключает свой Google — дождитесь или отмените";
    case "other":
      return `Пользуется подключением агента «${names(status.source)}»`;
  }
}

/**
 * Что агент сможет с доступом: календарь — встроенный инструмент у всех,
 * почта, диск и таблицы — через навык Google Workspace, которого у готовых
 * агентов из пакетов может не быть.
 */
export function capabilityNote(row: ConnectionsGoogleProfile, services: string[]): string | null {
  const other = services.filter((service) => service !== "calendar");
  if (!other.length || row.tools.workspace_skill) return null;
  const hasCalendar = services.includes("calendar") && row.tools.calendar;
  const rest = serviceLabels(other).join(", ").toLowerCase();
  return hasCalendar
    ? `Календарь доступен сразу; ${rest} — после установки навыка Google Workspace.`
    : `${rest[0]?.toUpperCase() ?? ""}${rest.slice(1)} — после установки навыка Google Workspace.`;
}

/**
 * Кому предложить подключить собственный Google: агентам без доступа.
 * Сначала тот, кого назвали в адресе, затем начатое подключение, затем
 * главный агент.
 */
export function connectCandidates(rows: ConnectionsGoogleProfile[]): ConnectionsGoogleProfile[] {
  return rows.filter((row) => row.access === "none");
}

export function defaultConnectTarget(
  rows: ConnectionsGoogleProfile[],
  requested?: string | null,
): string | null {
  if (requested && rows.some((row) => row.profile === requested)) return requested;
  const candidates = connectCandidates(rows);
  const pending = candidates.find((row) => row.pending);
  if (pending) return pending.profile;
  if (candidates.some((row) => row.profile === "default")) return "default";
  return candidates[0]?.profile ?? null;
}
