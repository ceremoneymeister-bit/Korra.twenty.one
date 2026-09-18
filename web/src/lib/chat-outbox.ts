import { HERMES_BASE_PATH } from "@/lib/api";
import { MAX_ATTACHMENTS, type UploadedAttachment } from "@/lib/chat-attachments";

/* v3: отдельная запись на client_message_id. v2 держал только одну запись на
 * чат, поэтому новая отправка либо блокировалась, либо затирала прежнюю. */
const STORAGE_PREFIX = `korra-browser-chat-outbox-v3:${HERMES_BASE_PATH || "root"}`;
const LEGACY_STORAGE_PREFIX = `korra-browser-chat-outbox-v2:${HERMES_BASE_PATH || "root"}`;

function profilePrefix(prefix: string, profile: string | undefined): string {
  return `${prefix}:${profile || "main"}:`;
}

function sessionPrefix(profile: string | undefined, sessionId: string): string {
  return `${profilePrefix(STORAGE_PREFIX, profile)}${sessionId}:`;
}

function storageKey(profile: string | undefined, sessionId: string, messageId: string): string {
  return `${sessionPrefix(profile, sessionId)}${messageId}`;
}

function legacyStorageKey(profile: string | undefined, sessionId: string): string {
  return `${profilePrefix(LEGACY_STORAGE_PREFIX, profile)}${sessionId}`;
}

function profileKeys(prefix: string, profile: string | undefined): string[] {
  const scopedPrefix = profilePrefix(prefix, profile);
  const keys: string[] = [];
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index);
    if (key && key.startsWith(scopedPrefix)) keys.push(key);
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
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    localStorage.removeItem(key);
    return null;
  }
  if (!validRecord(parsed) || (parsed.profile ?? "") !== profile) {
    localStorage.removeItem(key);
    return null;
  }
  return parsed;
}

/** Однократно переносит прежнюю единственную запись чата в коллекцию v3. */
function migrateLegacyRecords(profile: string, sessionId?: string): void {
  for (const key of profileKeys(LEGACY_STORAGE_PREFIX, profile)) {
    if (sessionId && key !== legacyStorageKey(profile, sessionId)) continue;
    const record = readRecord(key, profile);
    if (record) {
      const target = storageKey(profile, record.sessionId, record.messageId);
      if (localStorage.getItem(target) === null) {
        localStorage.setItem(target, JSON.stringify(record));
      }
    }
    localStorage.removeItem(key);
  }
}

/** Все сохранённые сообщения чата/профиля в порядке отправки. */
export function loadChatOutboxRecords(profile = "", sessionId?: string): ChatOutboxRecord[] {
  try {
    migrateLegacyRecords(profile, sessionId);
    const records = profileKeys(STORAGE_PREFIX, profile)
      .filter((key) => !sessionId || key.startsWith(sessionPrefix(profile, sessionId)))
      .map((key) => readRecord(key, profile))
      .filter((record): record is ChatOutboxRecord => record !== null)
      .filter((record) => !sessionId || record.sessionId === sessionId)
      .sort((a, b) => a.createdAt - b.createdAt || a.messageId.localeCompare(b.messageId));
    return records;
  } catch {
    return [];
  }
}

/** Самая старая запись — совместимый сокращённый доступ для баннеров. */
export function loadChatOutbox(profile = "", sessionId?: string): ChatOutboxRecord | null {
  return loadChatOutboxRecords(profile, sessionId)[0] ?? null;
}

export function saveChatOutbox(record: ChatOutboxRecord): boolean {
  try {
    const profile = record.profile ?? "";
    migrateLegacyRecords(profile, record.sessionId);
    localStorage.setItem(storageKey(profile, record.sessionId, record.messageId), JSON.stringify(record));
    return true;
  } catch {
    return false;
  }
}

export function clearChatOutbox(messageId: string, profile = ""): void {
  try {
    migrateLegacyRecords(profile);
    for (const key of profileKeys(STORAGE_PREFIX, profile)) {
      const record = readRecord(key, profile);
      if (!record || record.messageId === messageId) localStorage.removeItem(key);
    }
  } catch {
    // Private browsing or a full storage quota must not break the chat.
  }
}

export function isChatOutboxStorageKey(key: string | null): boolean {
  return Boolean(key && (key.startsWith(`${STORAGE_PREFIX}:`) || key.startsWith(`${LEGACY_STORAGE_PREFIX}:`)));
}

export function chatOutboxStorageKeyForTests(
  profile = "",
  sessionId = "",
  messageId = "test-message-id-123456",
): string {
  return storageKey(profile, sessionId, messageId);
}

export function legacyChatOutboxStorageKeyForTests(profile = "", sessionId = ""): string {
  return legacyStorageKey(profile, sessionId);
}
