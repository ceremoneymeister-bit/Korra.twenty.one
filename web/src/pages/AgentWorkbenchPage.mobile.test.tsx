// @vitest-environment jsdom

/**
 * Мобильная шапка «D» экрана агентов (принята 28.09): режимы «Вкладки» и
 * «Список агентов», знаки состояний, шторки, история браузера. Десктоп —
 * прежняя полоса вкладок; её поведение проверяет AgentWorkbenchPage.test.tsx.
 */

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const TABS = [
  { profile: "", label: "Нюра" },
  { profile: "designer", label: "Нюра | Дизайнер" },
  { profile: "bitrix", label: "Нюра | Bitrix" },
  { profile: "docs", label: "Нюра | Документы" },
  { profile: "finance", label: "Нюра | Финансы" },
  { profile: "rop", label: "Нюра | РОП" },
  { profile: "lawyer", label: "Нюра | Юрист" },
];

const workbenchMocks = vi.hoisted(() => ({
  refresh: vi.fn(async () => undefined),
  hideTab: vi.fn(),
  showTab: vi.fn(),
  moveTab: vi.fn(),
}));

vi.mock("@/hooks/useAgentTabs", () => ({
  useAgentTabs: () => ({
    tabs: TABS,
    hiddenTabs: [],
    refresh: workbenchMocks.refresh,
    updateDisplayName: vi.fn(),
    hideTab: workbenchMocks.hideTab,
    showTab: workbenchMocks.showTab,
    moveTab: workbenchMocks.moveTab,
    reorderTab: vi.fn(),
  }),
}));

const apiMocks = vi.hoisted(() => ({
  getDashboardView: vi.fn(),
  setDashboardView: vi.fn(),
  deleteProfile: vi.fn(),
  getProfileSoul: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, ...apiMocks } };
});

vi.mock("@/pages/BubbleChatPage", async () => {
  const { createPortal } = await import("react-dom");
  return {
    default: ({
      agentProfile = "",
      active,
      mobileHistory,
      decisionsRequest = 0,
    }: {
      agentProfile?: string;
      active?: boolean;
      mobileHistory?: { open: boolean; onOpenChange: (open: boolean) => void; host: HTMLElement | null };
      decisionsRequest?: number;
    }) => (
      <div
        data-testid={`chat-${agentProfile || "default"}`}
        data-active={String(Boolean(active))}
        data-history-open={String(Boolean(mobileHistory?.open))}
        data-decisions-request={decisionsRequest}
      >
        <textarea aria-label={`Сообщение ${agentProfile || "default"}`} />
        {mobileHistory?.open && mobileHistory.host
          ? createPortal(<div data-testid="history-sheet">Чаты агента</div>, mobileHistory.host)
          : null}
      </div>
    ),
  };
});

import AgentWorkbenchPage from "./AgentWorkbenchPage";
import { $agentConversations } from "@/lib/agent-conversations";
import { $agentsView, resetAgentsViewForTests } from "@/lib/agents-view";
import { $chatRuns, $dismissedRunToasts, $failedChatRuns, $unreadChatRuns, $viewedChat, refreshChatRuns, type ChatRun } from "@/lib/chat-runs";
import { $mobileNavOpen } from "@/lib/mobile-nav";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location" data-state={JSON.stringify(location.state ?? null)}>{location.pathname}</output>;
}

function stubViewport(desktop: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: query.includes("min-width: 1024px") ? desktop : false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

function run(profile: string, status: ChatRun["status"], extra: Partial<ChatRun> = {}): ChatRun {
  return {
    message_id: `${profile}-${status}`,
    session_id: `${profile}-chat`,
    profile,
    status,
    updated_at: Date.now() / 1000 - 30,
    started_at: Date.now() / 1000 - 240,
    history_count: 1,
    user_message: { role: "user", content: `Задача ${profile}` },
    ...extra,
  };
}

const BUSY_RUNS = [
  run("designer", "running", { title: "Медаль Мирнинского района" }),
  run("rop", "queued"),
  run("bitrix", "waiting_decision", { pending_decisions: 4, title: "Поздравить без указания имени" }),
  run("docs", "completed", { unread: true, title: "Договор аренды: правки" }),
  run("finance", "failed", { title: "Бюджет на октябрь" }),
];

async function render(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
  // Опрос работ: хранилище nanostores снимает подписку с задержкой, и между
  // тестами само не перечитывает — читаем явно, как это делает таймер.
  await act(async () => { await refreshChatRuns(); });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
}

function page(start = "/agents?agent=designer") {
  return (
    <MemoryRouter initialEntries={[start]}>
      <AgentWorkbenchPage />
      <LocationProbe />
    </MemoryRouter>
  );
}

function byLabel(prefix: string): HTMLElement {
  const found = [...container.querySelectorAll<HTMLElement>("[aria-label]")].find((element) =>
    element.getAttribute("aria-label")?.startsWith(prefix),
  );
  if (!found) throw new Error(`Нет элемента «${prefix}…»`);
  return found;
}

async function click(element: Element) {
  await act(async () => {
    (element as HTMLElement).click();
  });
}

function setView(mode: "tabs" | "list") {
  const pref = { version: 1 as const, revision: 2, agents_mobile: mode, pinned: [], scope: "0123456789abcdef" };
  apiMocks.getDashboardView.mockResolvedValue(pref);
  $agentsView.set({ mode, pinned: [], revision: 2, scope: pref.scope, status: "idle", message: "" });
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  resetAgentsViewForTests();
  stubViewport(false);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) =>
    String(input).includes("/api/chat/runs") ? Response.json({ runs: BUSY_RUNS }) : Response.json({ ok: true })));
  $agentConversations.set({
    designer: { sessionId: "designer-chat", title: "Медаль Мирнинского района", chatCount: 6, latestTitle: "Медаль Мирнинского района", lastActive: 100 },
    "": { sessionId: "main", title: "Поздравления на октябрь", chatCount: 14, latestTitle: "Поздравления на октябрь", lastActive: 90 },
  });
  setView("tabs");
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  document.body.innerHTML = "";
  $chatRuns.set([]);
  $unreadChatRuns.set([]);
  $failedChatRuns.set([]);
  $dismissedRunToasts.set([]);
  $viewedChat.set(null);
  $mobileNavOpen.set(false);
  vi.unstubAllGlobals();
  Reflect.deleteProperty(window, "matchMedia");
});

describe("«Вкладки» на телефоне", () => {
  it("строка разговора и полоса вместо «Агенты», вкладок и строки «Чаты»", async () => {
    await render(page());
    expect(container.querySelector("[role=tablist]")).toBeNull();
    expect(container.querySelector("[data-agents-header='tabs']")).not.toBeNull();
    expect(byLabel("Разговор «Медаль Мирнинского района» с агентом «Нюра | Дизайнер»")).toBeTruthy();
    const chats = byLabel("Чаты агента «Нюра | Дизайнер»: 6");
    expect(chats.textContent).toContain("Чаты");

    // ☰ открывает общее меню приложения — фиксированной шапки здесь нет.
    await click(byLabel("Меню Korra"));
    expect($mobileNavOpen.get()).toBe(true);
  });

  it("знаки различаются формой, число — только у решений", async () => {
    await render(page());
    const rail = container.querySelector(".k-rail")!;
    const sign = (profile: string) =>
      rail.querySelector(`[data-agent-rail-item='${profile}'] [data-agent-sign]`)?.getAttribute("data-agent-sign") ?? null;
    const ring = (profile: string) =>
      rail.querySelector(`[data-agent-rail-item='${profile}'] [data-agent-ring]`)?.getAttribute("data-agent-ring") ?? null;

    expect(ring("designer")).toBe("run");
    expect(sign("bitrix")).toBe("decision");
    expect(rail.querySelector("[data-agent-rail-item='bitrix'] [data-agent-sign]")?.textContent).toBe("4");
    expect(sign("docs")).toBe("unread");
    expect(sign("finance")).toBe("error");
    expect(rail.querySelector("[data-agent-rail-item='docs'] [data-agent-sign]")?.textContent).toBe("");
    expect(byLabel("Нюра | Bitrix").getAttribute("aria-label")).toContain("ждёт 4 решения");
    // Без текста «В работе»: кольцо с числом и подписью для диктора.
    expect(container.textContent).not.toContain("В работе");
    expect(byLabel("Идут работы: 3")).toBeTruthy();
  });

  it("«Чаты» открывает список разговоров открытого агента шторкой под шапкой", async () => {
    await render(page());
    await click(byLabel("Чаты агента"));
    expect(container.querySelector("[data-testid=chat-designer]")?.getAttribute("data-history-open")).toBe("true");
    expect(container.querySelector("[data-agent-chats-host] [data-testid=history-sheet], [data-testid=history-sheet]")).not.toBeNull();
    // Шторка раскрывается внутри экрана, а не поверх шапки.
    expect(container.querySelector(".k-body [data-testid=history-sheet]")).not.toBeNull();
  });

  it("другой аватар — другой агент; повторное касание открытого — меню агента", async () => {
    await render(page());
    await click(byLabel("Нюра | Bitrix"));
    expect(container.querySelector<HTMLElement>("[data-testid=chat-bitrix]")?.closest<HTMLElement>("[data-agent-panel]")?.style.display).toBe("");
    expect(container.querySelector<HTMLElement>("[data-testid=chat-designer]")?.closest<HTMLElement>("[data-agent-panel]")?.style.display).toBe("none");

    await click(byLabel("Нюра | Bitrix, открыт"));
    const dialog = container.querySelector("[role=dialog]");
    expect(dialog?.textContent).toContain("Действия агента");
    expect(dialog?.textContent).toContain("Закрепить в полосе");
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(container.querySelector("[role=dialog]")).toBeNull();
  });

  it("«Все агенты» — шторка с ждущими вас сверху; «Идут работы» ведёт к решениям", async () => {
    await render(page());
    await click(byLabel("Все агенты: 7"));
    const sheet = container.querySelector("[role=dialog]")!;
    const text = sheet.textContent ?? "";
    expect(text.indexOf("Ждут вас")).toBeLessThan(text.indexOf("Остальные"));
    expect(text.indexOf("Bitrix")).toBeLessThan(text.indexOf("Юрист"));
    expect(text).toContain("Вид: вкладки");
    await click(sheet.querySelector("[data-agent-list-item='lawyer']")!);
    expect(container.querySelector<HTMLElement>("[data-testid=chat-lawyer]")?.closest<HTMLElement>("[data-agent-panel]")?.style.display).toBe("");

    await click(byLabel("Идут работы: 3"));
    const works = container.querySelector("[role=dialog]")!;
    expect(works.textContent).toContain("Ждут вашего решения");
    expect(works.textContent).toContain("Не завершились");
    await click([...works.querySelectorAll("button")].find((button) => button.textContent === "Решить")!);
    expect(container.querySelector("[data-testid=chat-bitrix]")?.getAttribute("data-decisions-request")).toBe("1");
  });

  it("новый ответ другого агента — под шапкой, без рода, касание открывает ответ", async () => {
    await render(page());
    const notice = container.querySelector("[data-agent-notice]")!;
    expect(notice.textContent).toContain("Нюра | Документы · Договор аренды: правки");
    expect(notice.textContent).not.toMatch(/ответил/);
    await click(byLabel("Новый ответ · Нюра | Документы"));
    expect(container.querySelector<HTMLElement>("[data-testid=chat-docs]")?.closest<HTMLElement>("[data-agent-panel]")?.style.display).toBe("");
    expect(container.querySelector("[data-agent-notice]")).toBeNull();
  });

  it("при наборе полоса уходит, строка разговора остаётся", async () => {
    await render(page());
    const field = container.querySelector<HTMLTextAreaElement>("[aria-label='Сообщение designer']")!;
    await act(async () => field.focus());
    expect(container.querySelector("[data-agents-mobile]")?.getAttribute("data-typing")).toBe("true");
    expect(container.querySelector("[data-agents-header='tabs']")).not.toBeNull();
  });
});

describe("«Список агентов» на телефоне", () => {
  beforeEach(() => setView("list"));

  it("главный экран — список; агент открывается записью в истории, «‹» возвращает", async () => {
    await render(page("/agents"));
    const home = container.querySelector("[data-agent-list-home]")!;
    expect(home).not.toBeNull();
    const text = home.textContent ?? "";
    expect(text.indexOf("Ждут вас")).toBeLessThan(text.indexOf("Остальные"));
    // Пока виден список, чат агента не считается открытым.
    expect(container.querySelector("[data-testid=chat-designer]")?.getAttribute("data-active")).toBe("false");

    await click(home.querySelector("[data-agent-list-item='designer']")!);
    expect(container.querySelector("[data-agent-list-home]")).toBeNull();
    expect(container.querySelector("[data-agents-header='agent']")).not.toBeNull();
    expect(container.querySelector("[data-testid=chat-designer]")?.getAttribute("data-active")).toBe("true");
    expect(container.querySelector("[data-testid=location]")?.getAttribute("data-state")).toContain("korraAgentScreen");
    // «‹» говорит, что другие ждут.
    expect(byLabel("Все агенты").getAttribute("aria-label")).toBe("Все агенты. Ждут вас: 3");

    await click(byLabel("Все агенты"));
    expect(container.querySelector("[data-agent-list-home]")).not.toBeNull();
    expect(container.querySelector("[data-testid=location]")?.getAttribute("data-state")).toBe("null");
  });

  it("ссылка на агента открывает его сразу, «‹» показывает список", async () => {
    await render(page("/agents?agent=bitrix"));
    expect(container.querySelector("[data-agent-list-home]")).toBeNull();
    expect(container.querySelector("[data-testid=chat-bitrix]")?.getAttribute("data-active")).toBe("true");
    await click(byLabel("Все агенты"));
    expect(container.querySelector("[data-agent-list-home]")).not.toBeNull();
  });
});

describe("десктоп не меняется", () => {
  it("на lg и шире — прежняя полоса вкладок при любом выборе вида", async () => {
    stubViewport(true);
    setView("list");
    await render(page());
    expect(container.querySelector("[role=tablist]")).not.toBeNull();
    expect(container.querySelector(".korra-agent-tabs")).not.toBeNull();
    expect(container.querySelector("[data-agents-mobile]")).toBeNull();
    expect(container.querySelector("[data-agent-list-home]")).toBeNull();
    expect(container.textContent).toContain("В работе");
  });
});
