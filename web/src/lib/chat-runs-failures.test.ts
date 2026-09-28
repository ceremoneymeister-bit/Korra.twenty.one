// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  $chatRuns,
  $failedChatRuns,
  $viewedChat,
  freshFailures,
  markChatViewed,
  refreshChatRuns,
  type ChatRun,
} from "@/lib/chat-runs";

const NOW = 2_000_000_000;

function run(status: ChatRun["status"], extra: Partial<ChatRun> = {}): ChatRun {
  return {
    message_id: "m-finance",
    session_id: "budget",
    profile: "finance",
    status,
    updated_at: NOW - 60,
    history_count: 0,
    user_message: { role: "user", content: "Обнови бюджет" },
    ...extra,
  };
}

afterEach(() => {
  localStorage.clear();
  $chatRuns.set([]);
  $failedChatRuns.set([]);
  $viewedChat.set(null);
  vi.unstubAllGlobals();
});

describe("знак «!» — работа не завершилась", () => {
  it("свежий сбой показывается, давний и уже открытый — нет", () => {
    expect(freshFailures([run("failed")], [], [], [], null, NOW)).toHaveLength(1);
    // Глобальный опрос держит последний ход разговора бессрочно: сбой
    // трёхдневной давности при первой загрузке не поднимается.
    expect(freshFailures([run("failed", { updated_at: NOW - 3 * 86400 })], [], [], [], null, NOW)).toEqual([]);
    // …а если человек видел, как работа шла и упала, — поднимается.
    expect(
      freshFailures([run("failed", { updated_at: NOW - 3 * 86400 })], [run("running")], [], [], null, NOW),
    ).toHaveLength(1);
    expect(freshFailures([run("failed")], [], [], ["m-finance"], null, NOW)).toEqual([]);
    // Открытый прямо сейчас чат знак не получает.
    expect(freshFailures([run("failed")], [], [], [], { profile: "finance", sessionId: "budget" }, NOW)).toEqual([]);
    // Новый ход в том же разговоре заменил упавший — знак уходит.
    expect(freshFailures([run("completed", { message_id: "m-next" })], [run("failed")], [run("failed")], [], null, NOW)).toEqual([]);
  });

  it("открытие чата снимает знак и после перезагрузки он не возвращается", async () => {
    const failed = run("failed", { updated_at: Date.now() / 1000 - 60 });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) =>
      String(input).includes("/api/chat/runs")
        ? Response.json({ runs: [failed] })
        : Response.json({ ok: true })));

    await refreshChatRuns();
    expect($failedChatRuns.get().map((item) => item.message_id)).toEqual(["m-finance"]);

    markChatViewed("finance", "budget");
    expect($failedChatRuns.get()).toEqual([]);

    // «Перезагрузка»: память страницы пуста, сервер отдаёт тот же сбой.
    $chatRuns.set([]);
    $viewedChat.set(null);
    await refreshChatRuns();
    expect($failedChatRuns.get()).toEqual([]);
  });
});
