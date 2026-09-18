import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  chatOutboxStorageKeyForTests,
  clearChatOutbox,
  legacyChatOutboxStorageKeyForTests,
  loadChatOutbox,
  loadChatOutboxRecords,
  saveChatOutbox,
  type ChatOutboxRecord,
} from "./chat-outbox";

const record: ChatOutboxRecord = {
  messageId: "12345678-1234-4234-8234-123456789abc",
  sessionId: "session-1",
  text: "Проверить доставку",
  attachments: [],
  createdAt: 123,
  status: "sending",
};

function makeStorage(): Storage {
  const store = new Map<string, string>();
  return {
    getItem: (key) => store.get(key) ?? null,
    setItem: (key, value) => void store.set(key, value),
    removeItem: (key) => void store.delete(key),
    clear: () => store.clear(),
    get length() {
      return store.size;
    },
    key: (index) => Array.from(store.keys())[index] ?? null,
  } as Storage;
}

describe("chat outbox", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", makeStorage());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("survives a reload through localStorage", () => {
    expect(saveChatOutbox(record)).toBe(true);
    expect(loadChatOutbox()).toEqual(record);
  });

  it("clears only the matching message", () => {
    saveChatOutbox(record);
    clearChatOutbox("another-message-id-1234");
    expect(loadChatOutbox()).toEqual(record);
    clearChatOutbox(record.messageId);
    expect(loadChatOutbox()).toBeNull();
  });

  it("keeps multiple failed messages in one chat and clears only the selected one", () => {
    const second = {
      ...record,
      messageId: "22345678-1234-4234-8234-123456789abc",
      text: "Второе сообщение",
      createdAt: 124,
      status: "failed" as const,
    };
    saveChatOutbox({ ...record, status: "failed" });
    saveChatOutbox(second);

    expect(loadChatOutboxRecords("", "session-1").map((item) => item.messageId))
      .toEqual([record.messageId, second.messageId]);
    clearChatOutbox(second.messageId);
    expect(loadChatOutboxRecords("", "session-1")).toEqual([{ ...record, status: "failed" }]);
  });

  it("migrates the v2 single-session record without losing its metadata", () => {
    const legacy = { ...record, status: "failed" as const, terminal: true, error: "Известная причина" };
    const legacyKey = legacyChatOutboxStorageKeyForTests("", record.sessionId);
    localStorage.setItem(legacyKey, JSON.stringify(legacy));

    expect(loadChatOutboxRecords("", record.sessionId)).toEqual([legacy]);
    expect(localStorage.getItem(legacyKey)).toBeNull();
    expect(localStorage.getItem(chatOutboxStorageKeyForTests("", record.sessionId, record.messageId)))
      .toBe(JSON.stringify(legacy));
  });

  it("drops malformed storage instead of trusting it", () => {
    localStorage.setItem(chatOutboxStorageKeyForTests(), JSON.stringify({ text: "missing id" }));
    expect(loadChatOutbox()).toBeNull();
    expect(localStorage.getItem(chatOutboxStorageKeyForTests())).toBeNull();
  });
});
