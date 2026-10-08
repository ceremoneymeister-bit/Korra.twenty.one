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

const CHAT_DRAFT_RECOVERY_PREFIX = "korra-chat-draft-recovery:";

function chatDraftRecoveryKey(key: string): string {
  return `${CHAT_DRAFT_RECOVERY_PREFIX}${key}`;
}

/** A late STT response updates the mounted composer in the same browser tab. */
export const CHAT_DRAFT_UPDATE_EVENT = "korra-chat-draft-update";

/**
 * Read the tab-local draft. A durable recovery copy exists only after STT
 * finished after its composer closed; ordinary typed drafts remain tab-local.
 */
export function readChatDraft(key: string): string {
  try {
    const current = sessionStorage.getItem(key);
    if (current !== null) return current;
  } catch { /* Try the recovery copy. */ }
  try { return localStorage.getItem(chatDraftRecoveryKey(key)) ?? ""; } catch { return ""; }
}

/**
 * Keep editing a recovered draft durably until it is accepted for delivery.
 * Drafts which never needed recovery retain the existing tab-local behaviour.
 */
export function writeChatDraft(key: string, value: string): void {
  writeChatView(key, value);
  const recoveryKey = chatDraftRecoveryKey(key);
  try {
    if (localStorage.getItem(recoveryKey) === null) return;
    if (value) localStorage.setItem(recoveryKey, value);
    else localStorage.removeItem(recoveryKey);
  } catch { /* The tab-local copy remains available. */ }
}

/** Clear both the ordinary draft and any late-dictation recovery copy. */
export function clearChatDraft(key: string): void {
  writeChatView(key, "");
  try { localStorage.removeItem(chatDraftRecoveryKey(key)); } catch { /* Storage unavailable. */ }
}

/**
 * Recover text returned after its composer was unmounted. Persistent storage is
 * written first: if it is unavailable, the caller keeps the audio for retry.
 */
export function appendRecoveredChatDraft(key: string, text: string): boolean {
  const current = readChatDraft(key);
  const next = current && !/\s$/.test(current) ? `${current} ${text}` : `${current}${text}`;
  try {
    localStorage.setItem(chatDraftRecoveryKey(key), next);
  } catch {
    return false;
  }
  try { sessionStorage.setItem(key, next); } catch { /* Persistent copy is authoritative. */ }
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(CHAT_DRAFT_UPDATE_EVENT, { detail: { key } }));
  }
  return true;
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
