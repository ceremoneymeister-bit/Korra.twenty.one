import { HERMES_BASE_PATH } from "@/lib/api";

export function chatViewKey(profile = "", sessionId: string | null = null): string {
  return `korra-chat-view:${HERMES_BASE_PATH}:${profile}:${sessionId ?? "new"}`;
}
export function readChatView(key: string): string {
  try { return sessionStorage.getItem(key) ?? ""; } catch { return ""; }
}
export function writeChatView(key: string, value: string): void {
  try { if (value) sessionStorage.setItem(key, value); else sessionStorage.removeItem(key); } catch { /* Browser storage may be unavailable. */ }
}

/** Synthetic ids (a decision's `cron:<job>:<run>` source) are not chats. */
function isChatSessionId(value: string): boolean {
  return !value.startsWith("cron:");
}

/** undefined: no saved choice; null: the owner explicitly opened a new chat.
 * A tab keeps its own selection; a newly opened tab resumes the last choice.
 * Existing raw session IDs remain compatible with the previous release. */
export function readChatSelection(key: string): string | null | undefined {
  let value: string | null = null;
  try { value = sessionStorage.getItem(key); } catch { /* Try persistent storage. */ }
  if (value !== null) {
    // «Новый чат» chosen in this very tab.
    if (value === "") return null;
    return isChatSessionId(value) ? value : undefined;
  }
  try { value = localStorage.getItem(key); } catch { /* Storage unavailable. */ }
  // An empty value here was saved by 0.21.14 for every «Новый чат»: on a new
  // visit it is not a choice any more, the conversation continues.
  if (!value || !isChatSessionId(value)) return undefined;
  return value;
}

export function writeChatSelection(key: string, sessionId: string | null): void {
  if (sessionId && !isChatSessionId(sessionId)) return;
  // «Новый чат» holds for this browser tab only. A fresh visit — the phone
  // reopening the page — resumes the last conversation instead of greeting
  // the owner with an empty chat every time (Birukova, 28.09.2026).
  try { sessionStorage.setItem(key, sessionId ?? ""); } catch { /* Storage unavailable. */ }
  try {
    if (sessionId) localStorage.setItem(key, sessionId);
    else localStorage.removeItem(key);
  } catch { /* Keep the tab-local choice. */ }
}

/** Forget a saved chat that no longer exists (404), in this tab and later ones. */
export function forgetChatSelection(key: string): void {
  try { sessionStorage.removeItem(key); } catch { /* Storage unavailable. */ }
  try { localStorage.removeItem(key); } catch { /* Storage unavailable. */ }
}
