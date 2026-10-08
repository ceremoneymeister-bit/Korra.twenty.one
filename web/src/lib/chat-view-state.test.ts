// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  appendRecoveredChatDraft,
  CHAT_DRAFT_UPDATE_EVENT,
  clearChatDraft,
  readChatDraft,
  writeChatDraft,
} from "./chat-view-state";

describe("late dictation draft recovery", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
  });

  it("keeps ordinary typed drafts local to the browser tab", () => {
    writeChatDraft("draft-a", "поручение");
    expect(readChatDraft("draft-a")).toBe("поручение");

    sessionStorage.clear();
    expect(readChatDraft("draft-a")).toBe("");
  });

  it("persists a late transcript and identifies its exact chat", () => {
    writeChatDraft("draft-a", "начало");
    const seen = vi.fn();
    window.addEventListener(CHAT_DRAFT_UPDATE_EVENT, seen);

    expect(appendRecoveredChatDraft("draft-a", "продолжение")).toBe(true);
    expect(readChatDraft("draft-a")).toBe("начало продолжение");
    expect(readChatDraft("draft-b")).toBe("");
    expect(seen).toHaveBeenCalledTimes(1);
    expect((seen.mock.calls[0][0] as CustomEvent).detail).toEqual({ key: "draft-a" });

    sessionStorage.clear();
    expect(readChatDraft("draft-a")).toBe("начало продолжение");
    window.removeEventListener(CHAT_DRAFT_UPDATE_EVENT, seen);
  });

  it("keeps edits to a recovered draft until delivery and then clears it", () => {
    expect(appendRecoveredChatDraft("draft-a", "поручение")).toBe(true);
    writeChatDraft("draft-a", "уточнённое поручение");

    sessionStorage.clear();
    expect(readChatDraft("draft-a")).toBe("уточнённое поручение");

    clearChatDraft("draft-a");
    expect(readChatDraft("draft-a")).toBe("");
  });

  it("does not claim recovery when persistent storage rejects the write", () => {
    // jsdom on Node 24 turns an own `setItem` into a stored item, so a spy on
    // the instance never fires there; replace the whole storage instead.
    vi.stubGlobal("localStorage", {
      getItem: () => null,
      setItem: () => { throw new DOMException("full", "QuotaExceededError"); },
      removeItem: () => {},
    });
    try {
      expect(appendRecoveredChatDraft("draft-a", "текст")).toBe(false);
      expect(sessionStorage.getItem("draft-a")).toBeNull();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
