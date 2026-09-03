import { HERMES_BASE_PATH } from "@/lib/api";
import type { UploadedAttachment } from "@/lib/chat-attachments";

/* v2: запись на профиль (вкладку агента), а не одна на всю панель — иначе
 * отправка во вкладке B затирала упавшее сообщение вкладки A (ревью 03.09).
 * Старые v1-записи не читаем: у них нет профиля, и повтор ушёл бы не туда. */
const STORAGE_PREFIX = `korra-browser-chat-outbox-v2:${HERMES_BASE_PATH || "root"}`;

function storageKey(profile: string | undefined): string {
  return `${STORAGE_PREFIX}:${profile || "main"}`;
}

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

export function loadChatOutbox(profile = ""): ChatOutboxRecord | null {
  try {
    const raw = localStorage.getItem(storageKey(profile));
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!validRecord(parsed) || (parsed.profile ?? "") !== profile) {
      localStorage.removeItem(storageKey(profile));
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function saveChatOutbox(record: ChatOutboxRecord): boolean {
  try {
    localStorage.setItem(storageKey(record.profile ?? ""), JSON.stringify(record));
    return true;
  } catch {
    return false;
  }
}

export function clearChatOutbox(messageId: string, profile = ""): void {
  try {
    const current = loadChatOutbox(profile);
    if (!current || current.messageId === messageId) {
      localStorage.removeItem(storageKey(profile));
    }
  } catch {
    // Private browsing or a full storage quota must not break the chat.
  }
}

export function chatOutboxStorageKeyForTests(profile = ""): string {
  return storageKey(profile);
}
