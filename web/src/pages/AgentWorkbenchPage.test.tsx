// @vitest-environment jsdom

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const workbenchMocks = vi.hoisted(() => ({
  refresh: vi.fn(),
  updateDisplayName: vi.fn(),
  hideTab: vi.fn(),
  showTab: vi.fn(),
  moveTab: vi.fn(),
  tabs: [
    { profile: "", label: "Корра" },
    {
      profile: "calculator",
      label: "Сметчик",
      description: "Считает стоимость проекта",
    },
  ],
  hiddenTabs: [] as Array<{
    profile: string;
    label: string;
    description?: string;
  }>,
}));

vi.mock("@/hooks/useAgentTabs", () => ({
  useAgentTabs: () => ({
    tabs: workbenchMocks.tabs,
    hiddenTabs: workbenchMocks.hiddenTabs,
    refresh: workbenchMocks.refresh,
    updateDisplayName: workbenchMocks.updateDisplayName,
    hideTab: workbenchMocks.hideTab,
    showTab: workbenchMocks.showTab,
    moveTab: workbenchMocks.moveTab,
  }),
}));

vi.mock("@/pages/BubbleChatPage", () => ({
  default: ({
    agentProfile = "",
    newChatRequest = 0,
  }: {
    agentProfile?: string;
    newChatRequest?: number;
  }) => (
    <div
      data-testid={`chat-${agentProfile || "default"}`}
      data-new-chat-request={newChatRequest}
    />
  ),
}));

import AgentWorkbenchPage from "./AgentWorkbenchPage";

let container: HTMLDivElement;
let root: Root;

const DEFAULT_TABS = [
  { profile: "", label: "Корра" },
  {
    profile: "calculator",
    label: "Сметчик",
    description: "Считает стоимость проекта",
  },
];

function LocationProbe() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

async function render(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

async function enterText(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )?.set;
  await act(async () => {
    setter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(() => {
  workbenchMocks.tabs = DEFAULT_TABS;
  workbenchMocks.hiddenTabs = [];
  workbenchMocks.refresh.mockReset();
  workbenchMocks.refresh.mockResolvedValue(undefined);
  workbenchMocks.updateDisplayName.mockReset();
  workbenchMocks.updateDisplayName.mockResolvedValue(undefined);
  workbenchMocks.hideTab.mockReset();
  workbenchMocks.showTab.mockReset();
  workbenchMocks.moveTab.mockReset();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
});

describe("AgentWorkbenchPage", () => {
  it("показывает display_name как подпись вкладки", async () => {
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );

    const tabs = Array.from(
      container.querySelectorAll<HTMLButtonElement>('[role="tab"]'),
    );
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Корра", "Сметчик"]);
    expect(container.textContent).not.toContain("calculator");
  });

  it("открывает меню вкладки, переименовывает агента и создаёт новый чат", async () => {
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );

    await act(async () => {
      container
        .querySelector<HTMLButtonElement>('button[aria-label="Меню агента «Сметчик»"]')
        ?.click();
    });

    let menu = document.body.querySelector<HTMLElement>('[role="menu"]')!;
    expect(menu.className).toContain("neo-select-menu");
    expect(menu.textContent).toContain("Переименовать");
    expect(menu.textContent).toContain("Открыть настройки профиля");
    expect(menu.textContent).toContain("Новый чат");

    await act(async () => {
      Array.from(menu.querySelectorAll("button"))
        .find((button) => button.textContent?.includes("Новый чат"))
        ?.click();
    });
    expect(
      container.querySelector('[data-testid="chat-calculator"]')?.getAttribute(
        "data-new-chat-request",
      ),
    ).toBe("1");
    expect(
      container
        .querySelector<HTMLButtonElement>('#agent-tab-calculator')
        ?.getAttribute("aria-selected"),
    ).toBe("true");

    await act(async () => {
      container
        .querySelector<HTMLButtonElement>('button[aria-label="Меню агента «Сметчик»"]')
        ?.click();
    });
    menu = document.body.querySelector<HTMLElement>('[role="menu"]')!;
    await act(async () => {
      Array.from(menu.querySelectorAll("button"))
        .find((button) => button.textContent?.includes("Переименовать"))
        ?.click();
    });

    const input = document.body.querySelector<HTMLInputElement>(
      "#agent-display-name-calculator",
    )!;
    expect(input.value).toBe("Сметчик");
    await enterText(input, "Главный сметчик");
    await act(async () => {
      document.body
        .querySelector<HTMLButtonElement>('button[aria-label="Сохранить имя"]')
        ?.click();
      await Promise.resolve();
    });

    expect(workbenchMocks.updateDisplayName).toHaveBeenCalledWith(
      "calculator",
      "Главный сметчик",
    );
  });

  it("объясняет 404 старой версией движка", async () => {
    workbenchMocks.updateDisplayName.mockRejectedValueOnce(
      new Error("404: Данные не найдены."),
    );
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );

    await act(async () => {
      container
        .querySelector<HTMLButtonElement>('button[aria-label="Меню агента «Сметчик»"]')
        ?.click();
    });
    const menu = document.body.querySelector<HTMLElement>('[role="menu"]')!;
    await act(async () => {
      Array.from(menu.querySelectorAll("button"))
        .find((button) => button.textContent?.includes("Переименовать"))
        ?.click();
    });
    const input = document.body.querySelector<HTMLInputElement>(
      "#agent-display-name-calculator",
    )!;
    await enterText(input, "Новое имя");
    await act(async () => {
      document.body
        .querySelector<HTMLButtonElement>('button[aria-label="Сохранить имя"]')
        ?.click();
      await Promise.resolve();
    });

    expect(document.body.textContent).toContain(
      "Переименование появится после обновления движка",
    );
  });

  it("управляет порядком и скрытием из меню вкладки, но не скрывает Корру", async () => {
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );

    await act(async () => {
      container
        .querySelector<HTMLButtonElement>('button[aria-label="Меню агента «Сметчик»"]')
        ?.click();
    });
    let menu = document.body.querySelector<HTMLElement>('[role="menu"]')!;
    await act(async () => {
      Array.from(menu.querySelectorAll("button"))
        .find((button) => button.textContent?.includes("Сдвинуть влево"))
        ?.click();
    });
    expect(workbenchMocks.moveTab).toHaveBeenCalledWith("calculator", "left");

    await act(async () => {
      container
        .querySelector<HTMLButtonElement>('button[aria-label="Меню агента «Сметчик»"]')
        ?.click();
    });
    menu = document.body.querySelector<HTMLElement>('[role="menu"]')!;
    await act(async () => {
      Array.from(menu.querySelectorAll("button"))
        .find((button) => button.textContent?.includes("Скрыть вкладку"))
        ?.click();
    });
    expect(workbenchMocks.hideTab).toHaveBeenCalledWith("calculator");

    await act(async () => {
      container
        .querySelector<HTMLButtonElement>('button[aria-label="Меню агента «Корра»"]')
        ?.click();
    });
    menu = document.body.querySelector<HTMLElement>('[role="menu"]')!;
    expect(menu.textContent).not.toContain("Скрыть вкладку");
  });

  it("возвращает скрытую вкладку через «+» и открывает конструктор нового агента", async () => {
    workbenchMocks.tabs = [{ profile: "", label: "Корра" }];
    workbenchMocks.hiddenTabs = [
      { profile: "calculator", label: "Сметчик" },
    ];
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
        <LocationProbe />
      </MemoryRouter>,
    );

    await act(async () => {
      container
        .querySelector<HTMLButtonElement>('button[aria-label="Добавить вкладку агента"]')
        ?.click();
    });
    let menu = document.body.querySelector<HTMLElement>('[role="menu"]')!;
    expect(menu.textContent).toContain("Сметчик");
    expect(menu.textContent).toContain("Создать нового агента");
    await act(async () => {
      Array.from(menu.querySelectorAll("button"))
        .find((button) => button.textContent?.includes("Сметчик"))
        ?.click();
    });
    expect(workbenchMocks.showTab).toHaveBeenCalledWith("calculator");

    await act(async () => {
      container
        .querySelector<HTMLButtonElement>('button[aria-label="Добавить вкладку агента"]')
        ?.click();
    });
    menu = document.body.querySelector<HTMLElement>('[role="menu"]')!;
    await act(async () => {
      Array.from(menu.querySelectorAll("button"))
        .find((button) => button.textContent?.includes("Создать нового агента"))
        ?.click();
    });
    expect(
      container.querySelector('[data-testid="location"]')?.textContent,
    ).toBe("/profiles/new");
  });
});
