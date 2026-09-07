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
