// @vitest-environment jsdom
/** Единая лента после сжатия: архив рядом с живым хвостом. */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const live = vi.hoisted(() => ({
  messages: [
    { id: "session-a-h201", role: "user", content: "Живой вопрос", timestamp: 1 },
    { id: "session-a-h202", role: "assistant", content: "Живой ответ", timestamp: 2, turnComplete: true },
  ],
  older: { hasOlder: false, loading: false, failed: false, archivedBefore: true },
  loadOlder: vi.fn(),
}));

vi.mock("@/hooks/useChatStream", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/hooks/useChatStream")>()),
  useChatStream: () => ({
    isLoading: false, messages: live.messages, sessionId: "session-a", isStreaming: false,
    error: null, approvals: [], send: vi.fn(), resolveApproval: vi.fn(),
    retryPending: vi.fn(), discardPending: vi.fn(), abort: vi.fn(),
    loadSession: vi.fn(), reset: vi.fn(), older: live.older, loadOlder: live.loadOlder,
  }),
}));
vi.mock("@/hooks/useSessionList", () => ({
  useSessionList: () => ({ sessions: [], loading: false, error: null, refresh: vi.fn() }),
}));
vi.mock("@/hooks/useSessionSearch", () => ({
  useSessionSearch: () => ({
    sessions: [], resultQuery: "", hasResults: false, loading: false, error: null, refresh: vi.fn(),
  }),
}));
vi.mock("@/hooks/useSessionRun", () => ({ useSessionRun: () => null }));
vi.mock("@/hooks/useDictation", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/hooks/useDictation")>()),
  useDictation: () => ({ state: "idle", supported: false, toggle: vi.fn(), cancel: vi.fn() }),
}));
vi.mock("@/contexts/useProfileScope", () => ({ useProfileScope: () => ({ profiles: [] }) }));
vi.mock("thinking-orbs", () => ({ ThinkingOrb: () => <span /> }));

import BubbleChatPage, { BubbleChatTranscript } from "./BubbleChatPage";
import type { ChatMessage } from "@/lib/chat-types";

let container: HTMLDivElement;
let root: Root;
let requests: Array<{ url: string; method: string }>;

function stubArchiveApi() {
  requests = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://korra.test");
    requests.push({ url: `${url.pathname}${url.search}`, method: init?.method ?? "GET" });
    return Response.json({});
  }));
}

async function render(node: ReactNode) {
  await act(async () => root.render(<MemoryRouter>{node}</MemoryRouter>));
  await act(async () => {});
}

const button = (label: string) =>
  [...container.querySelectorAll("button")].find(item => item.textContent?.includes(label));
const press = async (label: string) => {
  await act(async () => { button(label)!.click(); });
  await act(async () => {});
};
const composer = () => container.querySelector<HTMLTextAreaElement>("textarea")!;
const bubbleTexts = () => (container.textContent ?? "");

beforeEach(() => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({
    matches: false, media: "", onchange: null, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
  })));
  stubArchiveApi();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  localStorage.clear();
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

const archived: ChatMessage[] = [
  { id: "ancestor-h201", role: "user", content: "Давний вопрос", timestamp: 1, readOnly: true },
  { id: "ancestor-h202", role: "assistant", content: "Давний ответ", timestamp: 2, readOnly: true },
];

it("K21-203: архив и живой хвост видны сразу, композер и догрузка общие", async () => {
  const original = live.messages;
  live.messages = [...archived, ...original] as typeof original;
  live.older = { hasOlder: true, loading: false, failed: false, archivedBefore: true };
  try {
    await render(<BubbleChatPage agentProfile="lawyer" />);
    expect(container.querySelectorAll("[data-chat-message]")).toHaveLength(4);
    expect(bubbleTexts()).toContain("Давний вопрос");
    expect(bubbleTexts()).toContain("Живой ответ");
    expect(button("Показать раннюю часть")).toBeUndefined();
    const draft = composer();
    await press("Показать более ранние сообщения");
    expect(live.loadOlder).toHaveBeenCalledTimes(1);
    expect(composer()).toBe(draft);
    expect(requests.some(request => request.url.includes("archive=true"))).toBe(false);
  } finally {
    live.messages = original;
    live.older = { hasOlder: false, loading: false, failed: false, archivedBefore: true };
    live.loadOlder.mockClear();
  }
});

it("K21-203: архив не предлагает повтор или запись, живой хвост сохраняет действия", async () => {
  const retry = vi.fn();
  const messages: ChatMessage[] = [
    { ...archived[0]!, delivery: "failed", clientMessageId: "old" },
    { ...archived[1]!, historyId: 202, learning: { undone: false } },
    { id: "live-user", role: "user", content: "Живой вопрос", timestamp: 3, delivery: "failed", clientMessageId: "new" },
    { id: "live-learning", role: "assistant", content: "Учёл", timestamp: 4, historyId: 203, learning: { undone: false } },
  ];
  await render(<BubbleChatTranscript messages={messages} sessionId="session-a" profile="lawyer" onRetry={retry} />);
  expect([...container.querySelectorAll("button")].filter(node => node.textContent?.includes("Проверить и повторить"))).toHaveLength(1);
  expect([...container.querySelectorAll("button")].filter(node => node.textContent === "Отменить")).toHaveLength(1);
  await press("Проверить и повторить");
  expect(retry).toHaveBeenCalledWith({ sessionId: "session-a", messageId: "new" });
  await press("Отменить");
  expect(requests.filter(request => request.url.includes("learning-undo"))).toEqual([
    expect.objectContaining({ method: "POST", url: expect.stringContaining("/session-a/messages/203/learning-undo") }),
  ]);
});

it("K21-203: ошибка догрузки оставляет архивную ленту и предлагает повтор", async () => {
  const load = vi.fn();
  await render(<BubbleChatTranscript messages={archived} sessionId="session-a" older={{ hasOlder: true, loading: false, failed: true }} onLoadOlder={load} />);
  expect(bubbleTexts()).toContain("Давний ответ");
  await press("Не удалось загрузить более ранние сообщения");
  expect(load).toHaveBeenCalledTimes(1);
});

it("K21-203: архивный артефакт доступен для чтения, решение есть только у живого ответа", async () => {
  const onDecision = vi.fn();
  const messages: ChatMessage[] = [
    { ...archived[1]!, content: "[артефакт] /opt/data/workspace/archive.xlsx" },
    { id: "live-artifact", role: "assistant", content: "[артефакт] /opt/data/workspace/live.xlsx", timestamp: 3 },
  ];
  await render(<BubbleChatTranscript messages={messages} sessionId="session-a" onDecision={onDecision} />);
  expect(container.querySelectorAll("[data-chat-message]")).toHaveLength(2);
  expect(container.querySelectorAll("a[download]")).toHaveLength(2);
  expect(bubbleTexts()).not.toContain("Что делаем с «archive.xlsx»");
  expect(bubbleTexts()).toContain("Что делаем с «live.xlsx»");
});
