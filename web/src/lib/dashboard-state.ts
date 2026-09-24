/**
 * Живые данные карточек дашборда (`GET /api/dashboard/state`).
 *
 * Один запрос отдаёт все карточки сразу: сервер сам держит короткий кэш и
 * отвечает дёшево, а кабинет пропускает именно этот адрес на чтение. Каждая
 * секция приходит со своим `status`: сбой одного источника не гасит соседние
 * карточки, а «не удалось прочитать» никогда не выглядит как ноль.
 *
 * Здесь же — чистые правила, которые карточки показывают человеку: динамика
 * к прошлому периоду, окно ленты дня вокруг «сейчас», сведение полосы
 * «Требует внимания» с ожидающими решениями чатов. Они проверяются тестом без
 * DOM, а виджеты остаются про вёрстку.
 */

import { atom, onMount } from "nanostores";

import { fetchJSON } from "@/lib/api";
import type { ChatRun } from "@/lib/chat-runs";
import { getOwnerTimeZone } from "@/lib/dashboard-flags";

export type SectionStatus = "ok" | "empty" | "error";
export type MetricsPeriod = "week" | "month";
export type MetricKey = "dialogs" | "messages" | "background" | "tokens";

export interface AttentionItem {
  id: string;
  source: string;
  kind: string;
  /** action — ждёт решения человека; problem — что-то сломалось; info — к сведению. */
  severity: "action" | "problem" | "info";
  title: string;
  detail: string;
  agent: string | null;
  profile: string | null;
  href: string;
  action: string;
  at: number | null;
}

export interface DashboardAttention {
  status: SectionStatus;
  /** Первая страница: сервер присылает не больше 20 строк. */
  items: AttentionItem[];
  /** Сколько всего ждёт, включая то, что не вошло в `items`. */
  count: number;
  /** `items` — не всё: `count` больше присланного. */
  truncated?: boolean;
  errors: { source: string; label: string }[];
  checked: string[];
}

export interface DashboardMetrics {
  status: SectionStatus;
  period: MetricsPeriod;
  days: number;
  start: number;
  end: number;
  labels: string[];
  series: Record<MetricKey, number[]>;
  totals: Record<MetricKey, number>;
  previous: Record<MetricKey, number>;
  unreadable: string[];
}

export type EventState = "planned" | "late" | "done" | "failed" | "running" | "unknown";

export interface DashboardEvent {
  id: string;
  job_id: string;
  title: string;
  schedule: string | null;
  profile: string;
  agent: string;
  at: number;
  state: EventState;
  href: string;
  repeats_today?: number;
  runs_today?: number;
  failed_today?: number;
}

export interface DashboardUpcoming {
  status: SectionStatus;
  now: number;
  day_start: number;
  day_end: number;
  events: DashboardEvent[];
  next_later: DashboardEvent | null;
  week: { date: string; planned: number; done: number; failed: number }[];
  jobs_total: number;
  jobs_active: number;
  unreadable: string[];
}

export type ArtifactKind =
  | "document"
  | "text"
  | "pdf"
  | "table"
  | "presentation"
  | "image"
  | "audio"
  | "video"
  | "archive"
  | "page"
  | "file";

export interface DashboardArtifact {
  path: string;
  name: string;
  folder: string;
  ext: string;
  kind: ArtifactKind;
  size: number;
  modified_at: number;
  section: "agent" | "shared" | "workspace";
  profile: string | null;
  agent: string | null;
  title?: string | null;
  excerpt?: string[];
}

export interface DashboardArtifacts {
  status: SectionStatus;
  items: DashboardArtifact[];
  recent_count: number;
  organized: boolean;
  root: string;
  truncated: boolean;
}

export interface QuotaWindow {
  key: "primary" | "secondary";
  used_percent: number;
  window_minutes: number | null;
  label: string;
  resets_at: number | null;
  expired: boolean;
}

export interface DashboardQuota {
  available: boolean;
  status: "absent" | "waiting" | "ok" | "reset" | "error";
  level?: "normal" | "warn" | "critical";
  used_percent?: number;
  window_minutes?: number | null;
  window_label?: string;
  resets_at?: number | null;
  windows?: QuotaWindow[];
  plan_type?: string | null;
  captured_at?: number;
  stale?: boolean;
}

export interface DashboardAgentsActivity {
  status: "ok" | "error";
  agents: { profile: string; label: string; last_active_at: number | null }[];
  unreadable: string[];
}

export interface DashboardState {
  version: number;
  generated_at: number;
  timezone: string | null;
  attention: DashboardAttention | { status: "error" };
  agents: DashboardAgentsActivity | { status: "error" };
  metrics: DashboardMetrics | { status: "error" };
  upcoming: DashboardUpcoming | { status: "error" };
  artifacts: DashboardArtifacts | { status: "error" };
  quota: DashboardQuota;
}

export type LoadStatus = "idle" | "loading" | "ready" | "error";

/** Последний пришедший ответ. Не сбрасывается при сбое: карточка вправе
 *  показать последнее известное, но обязана сказать, что оно не свежее. */
export const $dashboardState = atom<DashboardState | null>(null);
export const $dashboardStatus = atom<LoadStatus>("idle");
/** Когда ответ действительно пришёл (Date.now(), мс). */
export const $dashboardUpdatedAt = atom<number | null>(null);
export const $dashboardPeriod = atom<MetricsPeriod>("week");

/** Как часто открытая доска перечитывает сводку. Сервер кэширует секции на
 *  10–60 секунд, так что чаще спрашивать бессмысленно. */
export const DASHBOARD_REFRESH_MS = 30_000;

/** Секция с данными, а не отметка о сбое. */
export function sectionReady<T extends { status: string }>(
  section: T | { status: "error" } | undefined | null,
): section is T {
  return Boolean(section && section.status !== "error");
}

let inflight: Promise<void> | null = null;
let queued = false;

/**
 * Перечитать сводку. Параллельные вызовы сливаются в один запрос, а вызов во
 * время запроса ставит ровно один повтор — чтобы смена периода не потерялась.
 */
export function refreshDashboardState(): Promise<void> {
  if (inflight) {
    queued = true;
    return inflight;
  }
  if ($dashboardStatus.get() !== "ready") $dashboardStatus.set("loading");
  const period = $dashboardPeriod.get();
  inflight = fetchJSON<DashboardState>(`/api/dashboard/state?period=${period}`)
    .then((state) => {
      $dashboardState.set(state);
      $dashboardUpdatedAt.set(Date.now());
      $dashboardStatus.set("ready");
    })
    .catch(() => {
      $dashboardStatus.set("error");
    })
    .finally(() => {
      inflight = null;
      if (queued) {
        queued = false;
        void refreshDashboardState();
      }
    });
  return inflight;
}

export function setDashboardPeriod(period: MetricsPeriod): void {
  if ($dashboardPeriod.get() === period) return;
  $dashboardPeriod.set(period);
  void refreshDashboardState();
}

onMount($dashboardState, () => {
  void refreshDashboardState();
  const timer = window.setInterval(() => {
    if (!document.hidden) void refreshDashboardState();
  }, DASHBOARD_REFRESH_MS);
  const resume = () => {
    if (!document.hidden) void refreshDashboardState();
  };
  window.addEventListener("focus", resume);
  document.addEventListener("visibilitychange", resume);
  return () => {
    if (typeof window === "undefined") return;
    window.clearInterval(timer);
    window.removeEventListener("focus", resume);
    document.removeEventListener("visibilitychange", resume);
  };
});

// ── Числа ─────────────────────────────────────────────────────────────────

export interface MetricDelta {
  direction: "up" | "down" | "flat" | "new";
  /** Округлённый процент изменения; для `new` — null. */
  percent: number | null;
}

/** Динамика к прошлому периоду. Рост с нуля — «впервые», а не +∞ %. */
export function metricDelta(current: number, previous: number): MetricDelta {
  if (previous <= 0) {
    return current > 0 ? { direction: "new", percent: null } : { direction: "flat", percent: 0 };
  }
  const percent = Math.round(((current - previous) / previous) * 100);
  if (percent === 0) return { direction: "flat", percent: 0 };
  return { direction: percent > 0 ? "up" : "down", percent: Math.abs(percent) };
}

/** 950 → «950», 12 400 → «12,4 тыс.», 3 200 000 → «3,2 млн». */
export function formatCompact(value: number): string {
  const abs = Math.abs(value);
  const fmt = (n: number) =>
    new Intl.NumberFormat("ru-RU", { maximumFractionDigits: n >= 100 ? 0 : 1 }).format(n);
  if (abs >= 1_000_000_000) return `${fmt(value / 1_000_000_000)} млрд`;
  if (abs >= 1_000_000) return `${fmt(value / 1_000_000)} млн`;
  if (abs >= 10_000) return `${fmt(value / 1_000)} тыс.`;
  return new Intl.NumberFormat("ru-RU").format(value);
}

/** Слово после числа: 1 диалог, 2 диалога, 5 диалогов. */
export function plural(value: number, forms: readonly [string, string, string]): string {
  const n = Math.abs(value) % 100;
  const last = n % 10;
  if (n > 10 && n < 20) return forms[2];
  if (last === 1) return forms[0];
  if (last >= 2 && last <= 4) return forms[1];
  return forms[2];
}

// ── Время ─────────────────────────────────────────────────────────────────

/** Часовой пояс установки: в нём живут расписания и «сегодня». */
export function dashboardTimeZone(state: DashboardState | null): string {
  const zone = state?.timezone?.trim();
  if (zone) {
    try {
      new Intl.DateTimeFormat("ru-RU", { timeZone: zone }).format();
      return zone;
    } catch {
      /* неизвестный пояс — берём пояс владельца */
    }
  }
  return getOwnerTimeZone();
}

export function formatClock(seconds: number, timeZone: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone,
  }).format(new Date(seconds * 1000));
}

function dayKey(seconds: number, timeZone: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone,
  }).format(new Date(seconds * 1000));
}

/** «сегодня в 14:00», «завтра в 09:30», «пт, 25 сент. в 14:00». */
export function formatMoment(seconds: number, nowSeconds: number, timeZone: string): string {
  const clock = formatClock(seconds, timeZone);
  const day = dayKey(seconds, timeZone);
  if (day === dayKey(nowSeconds, timeZone)) return `сегодня в ${clock}`;
  if (day === dayKey(nowSeconds + 86_400, timeZone)) return `завтра в ${clock}`;
  if (day === dayKey(nowSeconds - 86_400, timeZone)) return `вчера в ${clock}`;
  const date = new Intl.DateTimeFormat("ru-RU", {
    weekday: "short",
    day: "numeric",
    month: "short",
    timeZone,
  }).format(new Date(seconds * 1000));
  return `${date} в ${clock}`;
}

/** «через 19 мин», «через 2 ч», «5 мин назад», «только что». */
export function formatRelative(seconds: number, nowSeconds: number): string {
  const delta = Math.round(seconds - nowSeconds);
  const abs = Math.abs(delta);
  if (abs < 60) return delta >= 0 ? "сейчас" : "только что";
  const minutes = Math.round(abs / 60);
  const hours = Math.round(abs / 3600);
  const days = Math.round(abs / 86_400);
  const text =
    abs < 3600
      ? `${minutes} мин`
      : abs < 86_400
        ? `${hours} ч`
        : `${days} ${plural(days, ["день", "дня", "дней"])}`;
  return delta >= 0 ? `через ${text}` : `${text} назад`;
}

// ── Лента дня ─────────────────────────────────────────────────────────────

const FINISHED: ReadonlySet<EventState> = new Set(["done", "failed", "unknown"]);

/**
 * Какие события дня показать в `count` строках.
 *
 * Сначала — последнее уже случившееся (чтобы видеть, что утренняя сводка
 * отработала), затем то, что идёт, и ближайшие запланированные. Если впереди
 * ничего нет, недостающие строки добирают предыдущие события.
 */
export function timelineWindow(
  events: readonly DashboardEvent[],
  nowSeconds: number,
  count: number,
): DashboardEvent[] {
  if (count <= 0) return [];
  const sorted = [...events].sort((a, b) => a.at - b.at);
  const past = sorted.filter((event) => FINISHED.has(event.state) && event.at <= nowSeconds);
  const ahead = sorted.filter((event) => !past.includes(event));
  const picked: DashboardEvent[] = [];
  if (past.length && ahead.length) picked.push(past[past.length - 1]);
  for (const event of ahead) {
    if (picked.length >= count) break;
    picked.push(event);
  }
  for (let i = past.length - 1; i >= 0 && picked.length < count; i -= 1) {
    if (!picked.includes(past[i])) picked.unshift(past[i]);
  }
  return picked.sort((a, b) => a.at - b.at).slice(0, count);
}

/** Ближайшее ещё не случившееся событие дня. */
export function nextEvent(
  events: readonly DashboardEvent[],
  nowSeconds: number,
): DashboardEvent | null {
  return (
    [...events]
      .filter((event) => event.state === "planned" || event.state === "running" || event.state === "late")
      .sort((a, b) => a.at - b.at)
      .find((event) => event.state !== "planned" || event.at >= nowSeconds - 60) ?? null
  );
}

// ── Требует внимания ──────────────────────────────────────────────────────

export interface AttentionRow extends AttentionItem {
  /** Источник строки: сводка сервера или живой поток работ чатов. */
  origin: "server" | "chat";
}

export interface AttentionView {
  rows: AttentionRow[];
  /** Что не удалось проверить — словами для человека. */
  unverified: string[];
  /** Все источники прочитаны и пусты: можно честно сказать «всё спокойно». */
  calm: boolean;
  /** Серверная часть — последнее известное: последний опрос не удался. */
  stale: boolean;
  /**
   * Последняя удачная проверка была полной и пустой. Это можно назвать, но
   * только как прошлое, с временем проверки, — не как «всё спокойно».
   */
  lastKnownCalm: boolean;
}

export interface ChatAttentionInput {
  runs: readonly ChatRun[];
  /** Хотя бы один ответ о работах уже был. */
  known: boolean;
  reachable: boolean | null;
  /** Подпись агента по имени профиля ("" — главный). */
  labelFor: (profile: string) => string;
  hrefFor: (profile: string, sessionId: string | null) => string;
}

/**
 * Полоса «Требует внимания»: серверная сводка плюс чаты, где агент ждёт
 * решения человека.
 *
 * Ожидание решения берётся из потока работ `/api/chat/runs` — он уже сводит
 * подтверждения внешних действий (`/api/chat/decisions`) и вопросы об опасных
 * командах всех каналов в одно состояние `waiting_decision` на разговор.
 * Отдельного запроса к решениям здесь нет, поэтому один и тот же вопрос не
 * появляется дважды.
 *
 * `stale` — сводка есть, но последний опрос не удался. Её строки остаются
 * на месте как последнее известное, а источники сервера считаются
 * непроверенными: «всё спокойно» после сбоя обновления было бы выдумкой.
 */
export function attentionView(
  section: DashboardAttention | null,
  sectionState: "ready" | "stale" | "pending" | "error",
  chat: ChatAttentionInput,
): AttentionView {
  const rows: AttentionRow[] = [];
  const seen = new Set<string>();
  if (chat.known) {
    for (const run of chat.runs) {
      if (run.status !== "waiting_decision") continue;
      const key = `${run.profile}\0${run.session_id}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const label = chat.labelFor(run.profile);
      const topic = (run.title || run.user_message?.content || "").replace(/\s+/g, " ").trim();
      rows.push({
        origin: "chat",
        id: `chat:${key}`,
        source: "chat",
        kind: "chat_decision",
        severity: "action",
        title: `${label} ждёт вашего решения`,
        detail: topic && topic !== "Ожидает вашего решения" ? topic : "Подтвердите или отклоните действие в чате.",
        agent: label,
        profile: run.profile,
        href: chat.hrefFor(run.profile, run.session_id || null),
        action: "Открыть чат",
        at: run.updated_at || null,
      });
    }
  }
  for (const item of section?.items ?? []) rows.push({ ...item, origin: "server" });

  const rank = { action: 0, problem: 1, info: 2 } as const;
  rows.sort((a, b) => rank[a.severity] - rank[b.severity] || (b.at ?? 0) - (a.at ?? 0));

  const unverified: string[] = [];
  if (sectionState === "error" || sectionState === "stale") {
    unverified.push("канбан, расписание, доставку ответов и подключения");
  } else if (section) {
    for (const error of section.errors) unverified.push(error.label);
  }
  const chatsUnverified = chat.reachable === false || (!chat.known && chat.reachable !== null);
  if (chatsUnverified) unverified.push("чаты агентов");

  const stale = sectionState === "stale" && section !== null;
  return {
    rows,
    unverified: [...new Set(unverified)],
    // «Всё спокойно» — только когда каждый источник действительно ответил.
    calm: rows.length === 0 && unverified.length === 0 && sectionState === "ready" && chat.known,
    stale,
    lastKnownCalm:
      stale &&
      section !== null &&
      rows.length === 0 &&
      section.errors.length === 0 &&
      chat.known &&
      !chatsUnverified,
  };
}

// ── Квота ─────────────────────────────────────────────────────────────────

export const QUOTA_WARN = 80;
export const QUOTA_CRITICAL = 95;

/** Оставшееся окно словами: «2 дн. 4 ч», «3 ч 10 мин», «12 мин». */
export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds / 60));
  const days = Math.floor(total / 1440);
  const hours = Math.floor((total % 1440) / 60);
  const minutes = total % 60;
  if (days > 0) return hours ? `${days} дн. ${hours} ч` : `${days} дн.`;
  if (hours > 0) return minutes ? `${hours} ч ${minutes} мин` : `${hours} ч`;
  return `${minutes} мин`;
}

// ── Артефакты ─────────────────────────────────────────────────────────────

export const ARTIFACT_KIND_LABELS: Record<ArtifactKind, string> = {
  document: "Документ",
  text: "Текст",
  pdf: "PDF",
  table: "Таблица",
  presentation: "Презентация",
  image: "Изображение",
  audio: "Аудио",
  video: "Видео",
  archive: "Архив",
  page: "Веб-страница",
  file: "Файл",
};

/** Адрес экрана «Файлы», открытого на папке файла с подсветкой его строки. */
export function artifactFolderHref(item: Pick<DashboardArtifact, "folder" | "name">): string {
  return `/files?${new URLSearchParams({ path: item.folder, highlight: item.name })}`;
}
