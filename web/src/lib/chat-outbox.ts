import { HERMES_BASE_PATH } from "@/lib/api";
import { MAX_ATTACHMENTS, type UploadedAttachment } from "@/lib/chat-attachments";

/* v2: запись на чат (профиль + сессия), а не одна на всю панель — иначе
 * отправка в другом чате затирала упавшее сообщение (ревью 03.09).
 * Старые v1-записи не читаем: у них нет профиля, и повтор ушёл бы не туда. */
const STORAGE_PREFIX = `korra-browser-chat-outbox-v2:${HERMES_BASE_PATH || "root"}`;

function profilePrefix(profile: string | undefined): string {
  return `${STORAGE_PREFIX}:${profile || "main"}:`;
}

function storageKey(profile: string | undefined, sessionId: string): string {
  return `${profilePrefix(profile)}${sessionId}`;
}

function profileKeys(profile: string | undefined): string[] {
  const prefix = profilePrefix(profile);
  const keys: string[] = [];
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index);
    if (key && key.startsWith(prefix)) keys.push(key);
  }
  return keys;
}

export interface ChatOutboxRecord {
  messageId: string;
  sessionId: string;
  text: string;
  attachments: UploadedAttachment[];
  createdAt: number;
  status: "sending" | "failed";
  error?: string;
  /** Server explicitly ended the attempt; repeating the same ID only replays it. */
  terminal?: boolean;
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
    Array.isArray(item.attachments) && item.attachments.length <= MAX_ATTACHMENTS && item.attachments.every(validAttachment) &&
    typeof item.createdAt === "number" && Number.isFinite(item.createdAt) &&
    (item.status === "sending" || item.status === "failed") &&
    (item.error === undefined || typeof item.error === "string") &&
    (item.terminal === undefined || typeof item.terminal === "boolean") &&
    (item.profile === undefined || (typeof item.profile === "string" && item.profile.length <= 100))
  );
}

function readRecord(key: string, profile: string): ChatOutboxRecord | null {
  const raw = localStorage.getItem(key);
  if (!raw) return null;
  const parsed: unknown = JSON.parse(raw);
  if (!validRecord(parsed) || (parsed.profile ?? "") !== profile) {
    localStorage.removeItem(key);
    return null;
  }
  return parsed;
}

/** Черновик конкретного чата, либо — без sessionId — самый старый черновик профиля. */
export function loadChatOutbox(profile = "", sessionId?: string): ChatOutboxRecord | null {
  try {
    if (sessionId) return readRecord(storageKey(profile, sessionId), profile);
    const records = profileKeys(profile)
      .map((key) => readRecord(key, profile))
      .filter((record): record is ChatOutboxRecord => record !== null)
      .sort((a, b) => a.createdAt - b.createdAt);
    return records[0] ?? null;
  } catch {
    return null;
  }
}

export function saveChatOutbox(record: ChatOutboxRecord): boolean {
  try {
    localStorage.setItem(storageKey(record.profile ?? "", record.sessionId), JSON.stringify(record));
    return true;
  } catch {
    return false;
  }
}

export function clearChatOutbox(messageId: string, profile = ""): void {
  try {
    for (const key of profileKeys(profile)) {
      const record = readRecord(key, profile);
      if (!record || record.messageId === messageId) localStorage.removeItem(key);
    }
  } catch {
    // Private browsing or a full storage quota must not break the chat.
  }
}

export function chatOutboxStorageKeyForTests(profile = "", sessionId = ""): string {
  return storageKey(profile, sessionId);
}
