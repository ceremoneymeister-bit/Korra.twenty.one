import type { ChatMessage } from "@/lib/chat-types";

export interface HistoryWindow {
  cursor: number | string | null;
  hasOlder: boolean;
}

export interface CachedChatHistory {
  messages: ChatMessage[];
  history: HistoryWindow;
}

export function chatHistoryKey(basePath: string, profile: string | undefined, sessionId: string): string {
  return JSON.stringify([basePath, profile || "default", sessionId]);
}

/** Tab-local LRU. Keep whole windows: trimming their prefix loses loaded pages
 * and the reading anchor. Oversized conversations simply use the normal loader.
 * Reloading the page or replacing the bundle naturally drops this memory. */
export class ChatHistoryCache {
  private entries = new Map<string, { value: CachedChatHistory; bytes: number }>();
  private bytes = 0;
  private maxChats: number;
  private maxBytes: number;
  constructor(maxChats = 8, maxBytes = 8 * 1024 * 1024) {
    this.maxChats = maxChats;
    this.maxBytes = maxBytes;
  }

  get(key: string): CachedChatHistory | undefined {
    const entry = this.entries.get(key);
    if (!entry) return undefined;
    this.entries.delete(key);
    this.entries.set(key, entry);
    return entry.value;
  }

  set(key: string, value: CachedChatHistory): void {
    const existing = this.entries.get(key)?.value;
    if (existing?.messages === value.messages && existing.history.cursor === value.history.cursor &&
      existing.history.hasOlder === value.history.hasOlder) return;
    this.delete(key);
    const bytes = JSON.stringify(value).length * 2;
    if (bytes > this.maxBytes) return;
    this.entries.set(key, { value, bytes });
    this.bytes += bytes;
    while (this.entries.size > this.maxChats || this.bytes > this.maxBytes) {
      this.delete(this.entries.keys().next().value!);
    }
  }

  delete(key: string): void {
    const entry = this.entries.get(key);
    if (entry) this.bytes -= entry.bytes;
    this.entries.delete(key);
  }

  clear(): void {
    this.entries.clear();
    this.bytes = 0;
  }
}

export const chatHistoryCache = new ChatHistoryCache();
export const CHAT_HISTORY_REMOVED = "korra-chat-history-removed";

/** Also invalidate mounted readers: a pre-delete request must not refill the
 * cache or show the deleted conversation after the DELETE has succeeded. */
export function forgetCachedChat(key: string): void {
  chatHistoryCache.delete(key);
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(CHAT_HISTORY_REMOVED, { detail: key }));
}
