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

it("K21-234: a hung poll times out and the next poll recovers", async () => {
  const { refreshChatRuns, CHAT_RUNS_TIMEOUT_MS, $chatRunsReachable } = await import("./chat-runs");
  vi.useFakeTimers();
  const fetcher = vi.fn().mockImplementationOnce(() => new Promise(() => {}))
    .mockResolvedValueOnce(new Response(JSON.stringify({ runs: [] })));
  vi.stubGlobal("fetch", fetcher);
  const first = refreshChatRuns();
  await vi.advanceTimersByTimeAsync(CHAT_RUNS_TIMEOUT_MS);
  await first;
  expect($chatRunsReachable.get()).toBe(false);
  await refreshChatRuns();
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect($chatRunsReachable.get()).toBe(true);
  vi.unstubAllGlobals(); vi.useRealTimers();
});

it("K21-235: 304 reuses the same snapshot and scopes ETags by conversation and token", async () => {
  const { getChatRuns } = await import("./chat-runs");
  const fetcher = vi.fn().mockResolvedValueOnce(new Response('{"runs":[]}', { headers: { ETag: '"snapshot"' } }))
    .mockResolvedValueOnce(new Response(null, { status: 304 }))
    .mockImplementation(async () => new Response('{"runs":[]}'));
  vi.stubGlobal("fetch", fetcher);
  const runs = await getChatRuns("lawyer", "etag-session");
  expect(await getChatRuns("lawyer", "etag-session")).toBe(runs);
  expect(fetcher.mock.calls[1][1].headers["If-None-Match"]).toBe('"snapshot"');
  await getChatRuns("accountant", "etag-session");
  expect(fetcher.mock.calls[2][1].headers["If-None-Match"]).toBeUndefined();
  window.__HERMES_SESSION_TOKEN__ = "new-test-token";
  await getChatRuns("lawyer", "etag-session");
  expect(fetcher.mock.calls[3][1].headers["If-None-Match"]).toBeUndefined();
  delete window.__HERMES_SESSION_TOKEN__;
  vi.unstubAllGlobals();
});

it("K21-234: leaving the page cancels polling and a late response cannot change stores", async () => {
  vi.resetModules();
  const { $chatRuns, $chatRunsReachable, refreshChatRuns } = await import("./chat-runs");
  vi.useFakeTimers();
  let resolve!: (value: Response) => void;
  const fetcher = vi.fn().mockImplementationOnce(() => new Promise<Response>(done => { resolve = done; }))
    .mockImplementation(async () => Response.json({ runs: [] }));
  vi.stubGlobal("fetch", fetcher);
  const off = $chatRuns.subscribe(() => {});
  await Promise.resolve();
  const before = $chatRuns.get();
  window.dispatchEvent(new Event("pagehide"));
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
  resolve(Response.json({ runs: [{ message_id: "late" }] }));
  await Promise.resolve(); await Promise.resolve();
  expect($chatRuns.get()).toBe(before);
  await refreshChatRuns();
  expect($chatRunsReachable.get()).toBe(true);
  off(); await vi.advanceTimersByTimeAsync(1000);
  vi.unstubAllGlobals(); vi.useRealTimers();
});
