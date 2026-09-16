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

/** undefined: no saved choice; null: the owner explicitly opened a new chat.
 * A tab keeps its own selection; a newly opened tab resumes the last choice.
 * Existing raw session IDs remain compatible with the previous release. */
export function readChatSelection(key: string): string | null | undefined {
  let value: string | null = null;
  try { value = sessionStorage.getItem(key); } catch { /* Try persistent storage. */ }
  if (value === null) {
    try { value = localStorage.getItem(key); } catch { /* Storage unavailable. */ }
  }
  return value === null ? undefined : value || null;
}

export function writeChatSelection(key: string, sessionId: string | null): void {
  // Keep an explicit empty value: removing the key would let a failed outbox
  // message select its old conversation on the next page load.
  const value = sessionId ?? "";
  try { sessionStorage.setItem(key, value); } catch { /* Storage unavailable. */ }
  try { localStorage.setItem(key, value); } catch { /* Keep the tab-local choice. */ }
}
