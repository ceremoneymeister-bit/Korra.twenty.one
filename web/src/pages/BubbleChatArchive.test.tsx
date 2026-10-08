// @vitest-environment jsdom
/**
 * «Показать раннюю часть» (K21-203): сжатая часть разговора открывается для
 * чтения вместо живой ленты, листается до начала и закрывается; живая лента
 * и композер остаются прежними.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

import BubbleChatPage from "./BubbleChatPage";

/** Семьдесят сообщений архива: 70 строк базы, id 1..70, от старых к новым. */
const ARCHIVE = Array.from({ length: 70 }, (_, index) => ({
  id: index + 1,
  role: index % 2 === 0 ? "user" : "assistant",
  content: `архив ${index + 1}`,
  timestamp: index + 1,
}));

let container: HTMLDivElement;
let root: Root;
let requests: Array<{ url: string; method: string }>;

function stubArchiveApi() {
  requests = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://korra.test");
    requests.push({ url: `${url.pathname}${url.search}`, method: init?.method ?? "GET" });
    if (!url.pathname.endsWith("/messages") || url.searchParams.get("archive") !== "true") {
      return Response.json({});
    }
    const limit = Number(url.searchParams.get("limit"));
    const offset = Number(url.searchParams.get("offset"));
    const end = Math.max(ARCHIVE.length - offset, 0);
    const page = ARCHIVE.slice(Math.max(end - limit, 0), end);
    return Response.json({
      session_id: "session-a",
      messages: page,
      pagination: { limit, offset, order: "latest", returned: page.length, total: ARCHIVE.length, has_more: offset + page.length < ARCHIVE.length },
    });
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
const archiveRegion = () => container.querySelector('[aria-label="Ранняя часть разговора"]');
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

describe("ранняя часть разговора после сжатия", () => {
  it("строка сжатия предлагает прочитать раннюю часть; архив — только чтение, до начала, закрывается", async () => {
    await render(<BubbleChatPage agentProfile="lawyer" />);
    expect(bubbleTexts()).toContain("Более ранняя часть этого разговора сохранена");
    expect(bubbleTexts()).toContain("Живой вопрос");
    expect(archiveRegion()).toBeNull();

    // Черновик в композере переживает открытие и закрытие архива.
    const draft = composer();
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
    await act(async () => { setter?.call(draft, "черновик ответа"); draft.dispatchEvent(new Event("input", { bubbles: true })); });

    await press("Показать раннюю часть");
    expect(archiveRegion()?.textContent).toContain("только чтение");
    expect(requests.at(-1)?.url).toContain("archive=true");
    expect(requests.at(-1)?.url).toContain("offset=0");
    // Последняя страница архива вместо живой ленты; живое сообщение не повторяется.
    expect(bubbleTexts()).toContain("архив 70");
    expect(bubbleTexts()).toContain("архив 21");
    expect(bubbleTexts()).not.toContain("архив 20");
    expect(bubbleTexts()).not.toContain("Живой вопрос");
    expect(bubbleTexts()).not.toContain("Это начало разговора");
    // Только чтение: у архива нет «повторить» и «убрать», композер прежний.
    expect(button("Проверить отправку")).toBeUndefined();
    expect(composer()).toBe(draft);
    expect(composer().value).toBe("черновик ответа");

    await press("Показать более ранние сообщения");
    expect(requests.at(-1)?.url).toContain("offset=50");
    expect(bubbleTexts()).toContain("архив 1");
    expect(bubbleTexts()).toContain("Это начало разговора");
    expect(button("Показать более ранние сообщения")).toBeUndefined();
    const shown = ARCHIVE.filter(row => new RegExp(`архив ${row.id}(?!\\d)`).test(bubbleTexts()));
    expect(shown).toHaveLength(70);

    await press("Вернуться к чату");
    expect(archiveRegion()).toBeNull();
    expect(bubbleTexts()).toContain("Живой вопрос");
    expect(bubbleTexts()).toContain("Живой ответ");
    expect(bubbleTexts()).not.toContain("архив 70");
    expect(composer()).toBe(draft);
    expect(composer().value).toBe("черновик ответа");
    expect(live.loadOlder).not.toHaveBeenCalled();
    expect(requests.every(request => request.method === "GET")).toBe(true);
  });

  it("без сжатой части кнопки нет", async () => {
    live.older = { hasOlder: false, loading: false, failed: false, archivedBefore: false };
    try {
      await render(<BubbleChatPage agentProfile="lawyer" />);
      expect(button("Показать раннюю часть")).toBeUndefined();
    } finally {
      live.older = { hasOlder: false, loading: false, failed: false, archivedBefore: true };
    }
  });

  it("сбой загрузки архива показывает повтор и не трогает живую ленту", async () => {
    let failing = true;
    const ok = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (failing) return new Response("{}", { status: 500 });
      return ok(input, init);
    }));
    await render(<BubbleChatPage agentProfile="lawyer" />);
    await press("Показать раннюю часть");
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("Не удалось загрузить раннюю часть");
    failing = false;
    await press("Повторить");
    expect(bubbleTexts()).toContain("архив 70");
    await press("Вернуться к чату");
    expect(bubbleTexts()).toContain("Живой вопрос");
  });
});
