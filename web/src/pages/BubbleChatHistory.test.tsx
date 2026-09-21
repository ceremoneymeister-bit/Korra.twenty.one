// @vitest-environment jsdom
/**
 * История чатов на телефоне и планшете.
 *
 * Панель открывается кнопкой «Чаты», перекрывает переписку и должна: жить
 * над шапкой приложения, закрываться своим крестиком (цель пальца 44 px),
 * уважать безопасные области и уходить сама, как только человек выбрал чат
 * или начал новый.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const chatMocks = vi.hoisted(() => ({
  loadSession: vi.fn(),
  reset: vi.fn(),
  refresh: vi.fn(),
  sessions: [
    { id: "session-a", title: "Договор клиента", last_active: 1, source: null, preview: "" },
    { id: "session-b", title: "Смета на ремонт", last_active: 2, source: null, preview: "" },
  ],
}));

vi.mock("@/hooks/useChatStream", () => ({
  useChatStream: () => ({
    isLoading: false, messages: [], sessionId: "session-a", isStreaming: false,
    error: null, approvals: [], send: vi.fn(), resolveApproval: vi.fn(),
    retryPending: vi.fn(), discardPending: vi.fn(), abort: vi.fn(),
    loadSession: chatMocks.loadSession, reset: chatMocks.reset,
  }),
}));
vi.mock("@/hooks/useSessionList", () => ({
  useSessionList: () => ({
    sessions: chatMocks.sessions, loading: false, error: null, refresh: chatMocks.refresh,
  }),
}));
vi.mock("@/hooks/useSessionSearch", () => ({
  useSessionSearch: () => ({
    sessions: [], resultQuery: "", hasResults: false, loading: false, error: null, refresh: vi.fn(),
  }),
}));
vi.mock("@/hooks/useSessionRun", () => ({ useSessionRun: () => null }));
vi.mock("@/hooks/useDictation", () => ({
  useDictation: () => ({ state: "idle", supported: false, toggle: vi.fn(), cancel: vi.fn() }),
}));
vi.mock("@/contexts/useProfileScope", () => ({ useProfileScope: () => ({ profiles: [] }) }));
vi.mock("thinking-orbs", () => ({ ThinkingOrb: () => <span /> }));

import BubbleChatPage from "./BubbleChatPage";

let container: HTMLDivElement;
let root: Root;
let viewportListener: ((event: MediaQueryListEvent) => void) | undefined;

async function render(node: ReactNode) {
  await act(async () => root.render(<MemoryRouter>{node}</MemoryRouter>));
}

const panel = () => document.querySelector<HTMLElement>('[role="dialog"][aria-modal="true"]');
const openHistory = async () => {
  const open = [...container.querySelectorAll("button")]
    .find(button => button.getAttribute("aria-label") === "Открыть историю чатов")!;
  await act(async () => open.click());
};

beforeEach(() => {
  viewportListener = undefined;
  vi.stubGlobal("matchMedia", vi.fn(() => ({
    matches: false,
    media: "(min-width: 1024px)",
    onchange: null,
    addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => {
      viewportListener = listener;
    },
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })));
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  chatMocks.loadSession.mockReset();
  chatMocks.reset.mockReset();
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("история чатов на мобильной подаче", () => {
  it("живёт в портале, а не внутри контекста наложения основной области", async () => {
    await render(<BubbleChatPage agentProfile="lawyer" />);
    await openHistory();
    const dialog = panel()!;
    expect(dialog).not.toBeNull();
    // Внутри `#main-content` (`relative z-2`) любой z-index сравнивался бы с
    // двойкой, и шапка приложения (`fixed z-40`) закрывала бы и заголовок,
    // и крестик. Портал в body ставит панель в общий порядок наложения.
    expect(container.contains(dialog)).toBe(false);
    expect(dialog.parentElement).toBe(document.body);
    expect(dialog.className).toMatch(/z-\[70\]/);
  });

  it("мобильная подача держится до `lg` — iPad в портрете тоже её получает", async () => {
    await render(<BubbleChatPage agentProfile="lawyer" />);
    // Постоянный список слева появляется только на десктопе.
    const aside = container.querySelector<HTMLElement>("aside.korra-chat-history")!;
    expect(aside.className).toContain("lg:flex");
    expect(aside.className).not.toContain("md:flex");
    await openHistory();
    expect(panel()!.className).toContain("lg:hidden");
  });

  it("крестик закрывает панель и остаётся целью пальца 44 px", async () => {
    await render(<BubbleChatPage agentProfile="lawyer" />);
    await openHistory();
    const close = panel()!.querySelector<HTMLButtonElement>("[data-chat-history-close]")!;
    expect(close.getAttribute("aria-label")).toBe("Закрыть историю чатов");
    // `size-11` в этом проекте не 44 px: шкала Tailwind умножена на
    // плотность темы (0.9) и на 15-пиксельный rem — выходило 37 px.
    expect(close.className).toMatch(/size-\[44px\]/);
    // Крестик лежит выше затемнения, которое растянуто на весь экран.
    expect(close.className).toMatch(/z-10/);
    await act(async () => close.click());
    expect(panel()).toBeNull();
  });

  it("уважает безопасные области и прокручивает свой список", async () => {
    await render(<BubbleChatPage agentProfile="lawyer" />);
    await openHistory();
    const drawer = panel()!.querySelector<HTMLElement>("[data-chat-history-panel]")!;
    expect(drawer.className).toContain("pt-[env(safe-area-inset-top,0px)]");
    expect(drawer.className).toContain("pb-[env(safe-area-inset-bottom,0px)]");
    expect(drawer.className).toContain("pl-[env(safe-area-inset-left,0px)]");
    const list = panel()!.querySelector<HTMLElement>('nav[aria-label="Список чатов"]')!;
    expect(list.className).toContain("overflow-y-auto");
    expect(list.className).toContain("overscroll-contain");
    // Пока панель открыта, страница под ней не уезжает.
    expect(document.body.style.overflow).toBe("hidden");
  });

  it("закрывается после выбора чата", async () => {
    await render(<BubbleChatPage agentProfile="lawyer" />);
    await openHistory();
    const item = [...panel()!.querySelectorAll<HTMLButtonElement>(".korra-chat-history__item")]
      .find(button => button.textContent?.includes("Смета на ремонт"))!;
    await act(async () => item.click());
    expect(chatMocks.loadSession).toHaveBeenCalledWith("session-b");
    expect(panel()).toBeNull();
    expect(document.body.style.overflow).not.toBe("hidden");
  });

  it("закрывается после «Новый чат»", async () => {
    await render(<BubbleChatPage agentProfile="lawyer" />);
    await openHistory();
    const fresh = [...panel()!.querySelectorAll("button")]
      .find(button => button.textContent?.includes("Новый чат"))!;
    await act(async () => fresh.click());
    expect(chatMocks.reset).toHaveBeenCalled();
    expect(panel()).toBeNull();
  });

  it("закрывается при повороте планшета в десктопную ширину", async () => {
    await render(<BubbleChatPage agentProfile="lawyer" />);
    await openHistory();
    expect(panel()).not.toBeNull();

    await act(async () => viewportListener?.({ matches: true } as MediaQueryListEvent));

    expect(panel()).toBeNull();
    expect(document.body.style.overflow).not.toBe("hidden");
  });
});
