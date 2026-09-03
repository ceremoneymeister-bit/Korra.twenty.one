// @vitest-environment jsdom

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const workbenchMocks = vi.hoisted(() => ({
  refresh: vi.fn(),
  updateDisplayName: vi.fn(),
}));

vi.mock("@/hooks/useAgentTabs", () => ({
  useAgentTabs: () => ({
    tabs: [
      { profile: "", label: "Корра" },
      {
        profile: "calculator",
        label: "Сметчик",
        description: "Считает стоимость проекта",
      },
    ],
    refresh: workbenchMocks.refresh,
    updateDisplayName: workbenchMocks.updateDisplayName,
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
  workbenchMocks.refresh.mockReset();
  workbenchMocks.refresh.mockResolvedValue(undefined);
  workbenchMocks.updateDisplayName.mockReset();
  workbenchMocks.updateDisplayName.mockResolvedValue(undefined);
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
});
