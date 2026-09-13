// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { $chatRuns } from "@/lib/chat-runs";

describe("подписка на прогоны чата", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ runs: [] }) }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("отписка не падает, когда окружение уже снесено", () => {
    const unsubscribe = $chatRuns.subscribe(() => {});
    unsubscribe();
    // nanostores откладывает реальный unmount примерно на секунду, а vitest к
    // этому моменту успевает снести jsdom файла: отложенная уборка вызывалась
    // уже без window и роняла весь прогон web «ReferenceError: window is not
    // defined» при зелёных тестах. Уборка обязана пережить исчезновение окна.
    vi.stubGlobal("window", undefined);
    expect(() => vi.advanceTimersByTime(2000)).not.toThrow();
  });
});
