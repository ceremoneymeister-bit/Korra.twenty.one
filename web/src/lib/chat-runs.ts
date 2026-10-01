import { loadChatOutboxRecords } from "@/lib/chat-outbox";
import { atom, onMount } from "nanostores";
import { withBasePath } from "@/lib/api";

export interface ChatRun {
  message_id: string;
  session_id: string;
  profile: string;
  status: "running" | "queued" | "waiting_decision" | "completed" | "failed" | "interrupted" | "stale";
  updated_at: number;
  started_at?: number;
  history_count: number;
  /** Открытый чат: id строки истории с репликой хода; null — ещё не записана
   *  (или записана до того, как движок стал хранить id сообщения). */
  history_row_id?: number | null;
  /** Движок хранит id сообщения в строке реплики этого хода. */
  turn_tracked?: boolean;
  user_message: { role: "user"; content: string };
  source?: string | null;
  channel?: string | null;
  title?: string | null;
  delivery?: "pending" | "delivered" | "failed" | "unknown";
  unread?: boolean;
  /** У строки `waiting_decision`: сколько решений ждёт в этом разговоре. */
  pending_decisions?: number;
  event_revision?: string;
  failure?: {
    message?: string;
    type?: string;
    code?: string;
    reason?: string;
    resets_at?: string | number;
    reset_at?: string | number;
    resets_in_seconds?: number;
  };
}

export const $chatRuns = atom<ChatRun[]>([]);
export const $chatRunsReachable = atom<boolean | null>(null);
/** Когда список работ последний раз действительно пришёл (Date.now(), мс).
 *  Экран, который показывает активность агентов, обязан уметь сказать, что
 *  видимое — последнее известное, а не текущее: «не отвечает» без возраста
 *  данных читается как «сейчас никто не работает». */
export const $chatRunsUpdatedAt = atom<number | null>(null);
export const $viewedChat = atom<{ profile: string; sessionId: string | null } | null>(null);
export const $unreadChatRuns = atom<ChatRun[]>([]);
/** Ответы, чьё всплывающее уведомление человек закрыл. Закрыть карточку —
 *  не значит прочитать ответ: отметка у чата в списке остаётся до открытия
 *  именно этого чата (markChatViewed). */
export const $dismissedRunToasts = atom<string[]>([]);

export function dismissRunToasts(ids?: readonly string[]): void {
  const dismissed = ids ?? $unreadChatRuns.get().map(run => run.message_id);
  $dismissedRunToasts.set([...new Set([...$dismissedRunToasts.get(), ...dismissed])].slice(-200));
}

/**
 * Работы, которые не завершились и которые человек ещё не открыл.
 *
 * Знак «!» на агенте держится до открытия именно этого чата. Сбой, увиденный
 * в прошлый раз, не возвращается после перезагрузки: открытые помечаются в
 * localStorage. Сбой старше суток при первой загрузке не поднимается —
 * глобальный опрос держит последний ход каждого разговора бессрочно, и
 * давняя неудача иначе висела бы вечно.
 */
export const $failedChatRuns = atom<ChatRun[]>([]);
const FAILURE_ACK_KEY = "korra-chat-failures-seen";
const FAILURE_FRESH_SECONDS = 24 * 60 * 60;

function readAcknowledgedFailures(): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(FAILURE_ACK_KEY) ?? "[]");
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function acknowledgeFailures(ids: readonly string[]): void {
  if (!ids.length) return;
  try {
    const next = [...new Set([...readAcknowledgedFailures(), ...ids])].slice(-200);
    localStorage.setItem(FAILURE_ACK_KEY, JSON.stringify(next));
  } catch {
    // Private browsing: the mark lasts for this page only.
  }
}

/** Какие сбои из нового опроса показать знаком «!». Чистая функция для теста. */
export function freshFailures(
  runs: readonly ChatRun[],
  previous: readonly ChatRun[],
  known: readonly ChatRun[],
  acknowledged: readonly string[],
  viewed: { profile: string; sessionId: string | null } | null,
  nowSeconds: number,
): ChatRun[] {
  const seen = new Set(acknowledged);
  const next = runs.filter(run => {
    if (run.status !== "failed" || seen.has(run.message_id)) return false;
    if (viewed?.profile === run.profile && viewed.sessionId === run.session_id) return false;
    if (known.some(item => item.message_id === run.message_id)) return true;
    const wasBusy = previous.some(old => old.message_id === run.message_id && isRunBusy(old));
    const recent = nowSeconds - (Number(run.updated_at) || 0) < FAILURE_FRESH_SECONDS;
    return wasBusy || (!previous.some(old => old.message_id === run.message_id) && recent);
  });
  return next;
}

/** Есть ли у конкретного чата (profile + session) непрочитанный ответ. */
export function hasUnreadResponse(unread: ChatRun[], profile: string, sessionId: string | null | undefined): boolean {
  return Boolean(sessionId) && unread.some(run => run.profile === profile && run.session_id === sessionId);
}

export function chatRunUrl(path = "", profile?: string, sessionId?: string): string {
  const query = new URLSearchParams();
  if (profile !== undefined) query.set("profile", profile);
  if (sessionId) query.set("session_id", sessionId);
  return withBasePath(`/api/chat/runs${path}${query.size ? `?${query}` : ""}`);
}

export function chatRunHeaders(): Record<string, string> {
  return { Authorization: `Bearer ${window.__HERMES_SESSION_TOKEN__ ?? ""}` };
}

export const CHAT_RUNS_TIMEOUT_MS = 10_000;
const snapshots = new Map<string, { etag: string; runs: ChatRun[] }>();

export async function getChatRuns(profile?: string, sessionId?: string, signal?: AbortSignal): Promise<ChatRun[]> {
  const url = chatRunUrl("", profile, sessionId);
  const headers = chatRunHeaders();
  const key = `${headers.Authorization}\0${url}`;
  const cached = snapshots.get(key);
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal?.addEventListener("abort", cancel, { once: true });
  if (signal?.aborted) cancel();
  const timer = window.setTimeout(cancel, CHAT_RUNS_TIMEOUT_MS);
  let rejectAbort: () => void = () => {};
  const aborted = new Promise<never>((_, reject) => {
    rejectAbort = () => reject(new DOMException("Проверка работы агентов отменена или превысила 10 секунд", "AbortError"));
    controller.signal.addEventListener("abort", rejectAbort, { once: true });
    if (controller.signal.aborted) rejectAbort();
  });
  const read = async () => {
    let response = await fetch(url, { headers: { ...headers, ...(cached ? { "If-None-Match": cached.etag } : {}) }, cache: "no-store", signal: controller.signal });
    if (response.status === 304) {
      if (cached) return cached.runs;
      response = await fetch(url, { headers, cache: "no-store", signal: controller.signal });
    }
    if (!response.ok) throw new Error("Не удалось проверить работу агентов");
    const runs = ((await response.json()) as { runs: ChatRun[] }).runs;
    if (controller.signal.aborted) throw new DOMException("Отменено", "AbortError");
    const etag = response.headers?.get("ETag");
    if (etag) {
      snapshots.delete(key);
      snapshots.set(key, { etag, runs });
      if (snapshots.size > 32) snapshots.delete(snapshots.keys().next().value!);
    } else snapshots.delete(key);
    return runs;
  };
  try { return await Promise.race([read(), aborted]); }
  finally {
    window.clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
    controller.signal.removeEventListener("abort", rejectAbort);
  }
}

export function isRunBusy(run: ChatRun): boolean {
  return run.status === "running" || run.status === "queued" || run.status === "waiting_decision";
}

const serverReadInFlight = new Set<string>();

function markServerSessionRead(profile: string, sessionId: string): void {
  if (typeof fetch !== "function") return;
  const key = `${profile}\0${sessionId}`;
  if (serverReadInFlight.has(key)) return;
  serverReadInFlight.add(key);
  void fetch(withBasePath(`/api/sessions/${encodeURIComponent(sessionId)}`), {
    method: "PATCH",
    headers: { ...chatRunHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ unread: false, profile: profile || undefined }),
  }).catch(() => undefined).finally(() => serverReadInFlight.delete(key));
}

let refreshing: Promise<void> | null = null;
let refreshController: AbortController | null = null;

function cancelRefresh() {
  refreshController?.abort();
  refreshController = null;
  refreshing = null;
}
export function refreshChatRuns(): Promise<void> {
  if (refreshing) return refreshing;
  const controller = new AbortController();
  refreshController = controller;
  refreshing = getChatRuns(undefined, undefined, controller.signal).then(runs => {
    if (refreshController !== controller) return;
    const previous = $chatRuns.get();
    const viewed = $viewedChat.get();
    const newlyReady = runs.filter(run => run.status === "completed" &&
      (previous.some(old => old.message_id === run.message_id && isRunBusy(old)) ||
        (!previous.some(old => old.message_id === run.message_id) &&
          (run.unread === true || loadChatOutboxRecords(run.profile, run.session_id).some(item => item.messageId === run.message_id)))) &&
      !(viewed?.profile === run.profile && viewed.sessionId === run.session_id));
    if (newlyReady.length) {
      $unreadChatRuns.set([...newlyReady, ...$unreadChatRuns.get()].filter((run, i, all) => all.findIndex(r => r.message_id === run.message_id) === i));
    }
    for (const run of runs) {
      if (run.unread && viewed?.profile === run.profile && viewed.sessionId === run.session_id) {
        markServerSessionRead(run.profile, run.session_id);
      }
    }
    const failed = freshFailures(runs, previous, $failedChatRuns.get(), readAcknowledgedFailures(), viewed, Date.now() / 1000);
    const known = $failedChatRuns.get();
    if (failed.length !== known.length || failed.some((run, index) => run.message_id !== known[index]?.message_id)) {
      $failedChatRuns.set(failed);
    }
    $chatRuns.set(runs);
    $chatRunsUpdatedAt.set(Date.now());
    $chatRunsReachable.set(true);
  }).catch(() => {
    if (refreshController === controller) $chatRunsReachable.set(false);
  }).finally(() => {
    if (refreshController === controller) { refreshing = null; refreshController = null; }
  });
  return refreshing;
}

export function markChatViewed(profile: string, sessionId: string | null): void {
  $viewedChat.set({ profile, sessionId });
  $unreadChatRuns.set($unreadChatRuns.get().filter(run => run.profile !== profile || run.session_id !== sessionId));
  // Открыл чат — увидел, что работа не завершилась: знак «!» уходит и не
  // возвращается после перезагрузки.
  const opened = $failedChatRuns.get().filter(run => run.profile === profile && run.session_id === sessionId);
  if (opened.length) {
    acknowledgeFailures(opened.map(run => run.message_id));
    $failedChatRuns.set($failedChatRuns.get().filter(run => !opened.includes(run)));
  }
  acknowledgeFailures($chatRuns.get()
    .filter(run => run.status === "failed" && run.profile === profile && run.session_id === sessionId)
    .map(run => run.message_id));
  if (sessionId) markServerSessionRead(profile, sessionId);
}

onMount($chatRuns, () => {
  void refreshChatRuns();
  const timer = window.setInterval(() => { if (!document.hidden) void refreshChatRuns(); }, 2000);
  const resume = () => { if (!document.hidden) void refreshChatRuns(); };
  window.addEventListener("pagehide", cancelRefresh);
  window.addEventListener("pageshow", resume);
  window.addEventListener("focus", resume);
  window.addEventListener("online", resume);
  document.addEventListener("visibilitychange", resume);
  return () => {
    cancelRefresh();
    // nanostores убирает подписку отложенно, и окно успевает исчезнуть раньше
    // уборки: без проверки падал весь прогон web, а не один тест.
    if (typeof window === "undefined") return;
    window.clearInterval(timer);
    window.removeEventListener("pagehide", cancelRefresh);
    window.removeEventListener("pageshow", resume);
    window.removeEventListener("focus", resume);
    window.removeEventListener("online", resume);
    document.removeEventListener("visibilitychange", resume);
  };
});
