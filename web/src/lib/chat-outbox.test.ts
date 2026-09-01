import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  chatOutboxStorageKeyForTests,
  clearChatOutbox,
  loadChatOutbox,
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

  it("drops malformed storage instead of trusting it", () => {
    localStorage.setItem(chatOutboxStorageKeyForTests(), JSON.stringify({ text: "missing id" }));
    expect(loadChatOutbox()).toBeNull();
    expect(localStorage.getItem(chatOutboxStorageKeyForTests())).toBeNull();
  });
});
