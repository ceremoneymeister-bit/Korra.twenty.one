import { HERMES_BASE_PATH } from "@/lib/api";
import type { UploadedAttachment } from "@/lib/chat-attachments";

const STORAGE_KEY = `korra-browser-chat-outbox-v1:${HERMES_BASE_PATH || "root"}`;

export interface ChatOutboxRecord {
  messageId: string;
  sessionId: string;
  text: string;
  attachments: UploadedAttachment[];
  createdAt: number;
  status: "sending" | "failed";
  error?: string;
  /** Профиль (вкладка агента), из которой ушло сообщение; "" — главный. */
  profile?: string;
}

function validAttachment(value: unknown): value is UploadedAttachment {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return (
    typeof item.path === "string" && item.path.length > 0 &&
    typeof item.name === "string" && item.name.length > 0 &&
    typeof item.kind === "string" &&
    typeof item.size === "number" && item.size >= 0 &&
    typeof item.reader === "string"
  );
}

function validRecord(value: unknown): value is ChatOutboxRecord {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return (
    typeof item.messageId === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]{15,79}$/.test(item.messageId) &&
    typeof item.sessionId === "string" && item.sessionId.length > 0 && item.sessionId.length <= 200 &&
    typeof item.text === "string" && item.text.length <= 100_000 &&
    Array.isArray(item.attachments) && item.attachments.length <= 5 && item.attachments.every(validAttachment) &&
    typeof item.createdAt === "number" && Number.isFinite(item.createdAt) &&
    (item.status === "sending" || item.status === "failed") &&
    (item.error === undefined || typeof item.error === "string") &&
    (item.profile === undefined || (typeof item.profile === "string" && item.profile.length <= 100))
  );
}

export function loadChatOutbox(): ChatOutboxRecord | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!validRecord(parsed)) {
      localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function saveChatOutbox(record: ChatOutboxRecord): boolean {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(record));
    return true;
  } catch {
    return false;
  }
}

export function clearChatOutbox(messageId: string): void {
  try {
    const current = loadChatOutbox();
    if (!current || current.messageId === messageId) {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // Private browsing or a full storage quota must not break the chat.
  }
}

export function chatOutboxStorageKeyForTests(): string {
  return STORAGE_KEY;
}
