// @vitest-environment jsdom
/** K21-385: «Вернуть в поле» после остановки хода возвращает текст и не молчит. */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const live = vi.hoisted(() => ({
  messages: [] as unknown[],
  retryPending: vi.fn(),
  discardPending: vi.fn(),
  send: vi.fn(),
}));

vi.mock("@/hooks/useChatStream", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/hooks/useChatStream")>()),
  useChatStream: () => ({
    isLoading: false, messages: live.messages, sessionId: "session-a", isStreaming: false,
    error: null, approvals: [], send: live.send, resolveApproval: vi.fn(),
    retryPending: live.retryPending, discardPending: live.discardPending, abort: vi.fn(),
    loadSession: vi.fn(), reset: vi.fn(),
    older: { hasOlder: false, loading: false, failed: false, archivedBefore: false }, loadOlder: vi.fn(),
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

import BubbleChatPage from "./BubbleChatPage";
import { saveChatOutbox } from "@/lib/chat-outbox";
import type { ChatMessage } from "@/lib/chat-types";

const MESSAGE_ID = "stopped-message-123456";
let container: HTMLDivElement;
let root: Root;

async function render(node: ReactNode) {
  await act(async () => root.render(<MemoryRouter>{node}</MemoryRouter>));
  await act(async () => {});
}

const composer = () => container.querySelector<HTMLTextAreaElement>("textarea")!;
const press = async (label: string) => {
  const target = [...container.querySelectorAll("button")].find(item => item.textContent?.includes(label))!;
  await act(async () => { target.click(); });
  await act(async () => {});
};

async function type(value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
  await act(async () => {
    setter?.call(composer(), value);
    composer().dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function stoppedBubble(content: string): ChatMessage {
  return {
    id: "user-stopped", clientMessageId: MESSAGE_ID, role: "user", content, timestamp: 1,
    delivery: "failed", failureConfirmed: true, failureReason: "Ход остановлен.",
  };
}

function keepOutboxCopy(text: string) {
  saveChatOutbox({
    messageId: MESSAGE_ID, sessionId: "session-a", text, attachments: [], createdAt: 1,
    status: "failed", terminal: true, error: "Ход остановлен.", profile: "",
  });
}

beforeEach(() => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({
    matches: false, media: "", onchange: null, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
  })));
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({})));
  live.retryPending.mockClear();
  live.discardPending.mockClear();
  live.send.mockClear();
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

it("копия на месте: текст дописывается к набранному, ничего не отправляется", async () => {
  live.messages = [stoppedBubble("Первая часть задачи")];
  keepOutboxCopy("Первая часть задачи");
  await render(<BubbleChatPage />);
  await type("Уже набрано");
  await press("Вернуть в поле");
  expect(composer().value).toBe("Уже набрано\n\nПервая часть задачи");
  expect(live.send).not.toHaveBeenCalled();
  expect(live.retryPending).not.toHaveBeenCalled();
  expect(container.textContent).toContain("Ничего не отправлено");
});

it("копии нет: текст берётся из отправленной реплики и тупика нет", async () => {
  live.messages = [stoppedBubble("Реплика без копии")];
  await render(<BubbleChatPage />);
  await press("Вернуть в поле");
  expect(composer().value).toBe("Реплика без копии");
  expect(live.send).not.toHaveBeenCalled();
  expect(container.textContent).toContain("из отправленной реплики");
});

it("копии нет и реплика пуста: объясняет причину, а не молчит", async () => {
  live.messages = [stoppedBubble("")];
  await render(<BubbleChatPage />);
  await press("Вернуть в поле");
  expect(composer().value).toBe("");
  expect(container.textContent).toContain("Сохранённой копии этого сообщения уже нет");
});
