import { loadChatOutbox } from "@/lib/chat-outbox";
import { atom, onMount } from "nanostores";
import { withBasePath } from "@/lib/api";

export interface ChatRun {
  message_id: string;
  session_id: string;
  profile: string;
  status: "running" | "queued" | "completed" | "failed" | "interrupted";
  updated_at: number;
  history_count: number;
  user_message: { role: "user"; content: string };
}

export const $chatRuns = atom<ChatRun[]>([]);
export const $chatRunsReachable = atom<boolean | null>(null);
export const $viewedChat = atom<{ profile: string; sessionId: string | null } | null>(null);
export const $unreadChatRuns = atom<ChatRun[]>([]);

export function chatRunUrl(path = "", profile?: string, sessionId?: string): string {
  const query = new URLSearchParams();
  if (profile !== undefined) query.set("profile", profile);
  if (sessionId) query.set("session_id", sessionId);
  return withBasePath(`/api/chat/runs${path}${query.size ? `?${query}` : ""}`);
}

export function chatRunHeaders(): Record<string, string> {
  return { Authorization: `Bearer ${window.__HERMES_SESSION_TOKEN__ ?? ""}` };
}

export async function getChatRuns(profile?: string, sessionId?: string): Promise<ChatRun[]> {
  const response = await fetch(chatRunUrl("", profile, sessionId), { headers: chatRunHeaders(), cache: "no-store" });
  if (!response.ok) throw new Error("Не удалось проверить работу агентов");
  return ((await response.json()) as { runs: ChatRun[] }).runs;
}

export function isRunBusy(run: ChatRun): boolean {
  return run.status === "running" || run.status === "queued";
}

const monitoringStarted = Date.now();
let refreshing: Promise<void> | null = null;
export function refreshChatRuns(): Promise<void> {
  if (refreshing) return refreshing;
  refreshing = getChatRuns().then(runs => {
    const previous = $chatRuns.get();
    const viewed = $viewedChat.get();
    const newlyReady = runs.filter(run => run.status === "completed" &&
      (previous.some(old => old.message_id === run.message_id && isRunBusy(old)) ||
        (!previous.some(old => old.message_id === run.message_id) &&
          (run.updated_at * 1000 >= monitoringStarted || loadChatOutbox(run.profile, run.session_id)?.messageId === run.message_id))) &&
      !(viewed?.profile === run.profile && viewed.sessionId === run.session_id));
    if (newlyReady.length) {
      $unreadChatRuns.set([...newlyReady, ...$unreadChatRuns.get()].filter((run, i, all) => all.findIndex(r => r.message_id === run.message_id) === i));
    }
    $chatRuns.set(runs);
    $chatRunsReachable.set(true);
  }).catch(() => { $chatRunsReachable.set(false); }).finally(() => { refreshing = null; });
  return refreshing;
}

export function markChatViewed(profile: string, sessionId: string | null): void {
  $viewedChat.set({ profile, sessionId });
  $unreadChatRuns.set($unreadChatRuns.get().filter(run => run.profile !== profile || run.session_id !== sessionId));
}

onMount($chatRuns, () => {
  void refreshChatRuns();
  const timer = window.setInterval(() => { if (!document.hidden) void refreshChatRuns(); }, 2000);
  const resume = () => { if (!document.hidden) void refreshChatRuns(); };
  window.addEventListener("focus", resume);
  window.addEventListener("online", resume);
  document.addEventListener("visibilitychange", resume);
  return () => {
    window.clearInterval(timer);
    window.removeEventListener("focus", resume);
    window.removeEventListener("online", resume);
    document.removeEventListener("visibilitychange", resume);
  };
});
