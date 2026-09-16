// @vitest-environment jsdom

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation, useNavigate } from "react-router";
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

// Удаление и подтягивание имени в роль ходят в API напрямую: вкладки об этом
// не знают, а сервер — единственный источник правды про роль.
const apiMocks = vi.hoisted(() => ({
  deleteProfile: vi.fn(),
  getProfileSoul: vi.fn(),
  updateProfileSoul: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, ...apiMocks } };
});

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
import { composeSoul } from "@/lib/agent-wizard";

let container: HTMLDivElement;
let root: Root;

const ENGINE_DEFAULT_SOUL =
  "You are Korra. Be direct: match the length of your reply to the weight of the ask.";

const DEFAULT_TABS = [
  { profile: "", label: "Корра" },
  {
    profile: "calculator",
    label: "Сметчик",
    description: "Считает стоимость проекта",
  },
];

function LocationProbe() {
  const { pathname, search } = useLocation();
  return <output data-testid="location">{`${pathname}${search}`}</output>;
}

/** Кнопка «Открыть чат» карточки агента — переход по ссылке снаружи экрана. */
function NavigateButton({ to }: { to: string }) {
  const navigate = useNavigate();
  return (
    <button type="button" data-testid="navigate" onClick={() => navigate(to)}>
      перейти
    </button>
  );
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
  sessionStorage.clear();
  localStorage.clear();
  workbenchMocks.tabs = DEFAULT_TABS;
  workbenchMocks.hiddenTabs = [];
  workbenchMocks.refresh.mockReset();
  workbenchMocks.refresh.mockResolvedValue(undefined);
  workbenchMocks.updateDisplayName.mockReset();
  workbenchMocks.updateDisplayName.mockResolvedValue(undefined);
  workbenchMocks.hideTab.mockReset();
  workbenchMocks.showTab.mockReset();
  workbenchMocks.moveTab.mockReset();
  apiMocks.deleteProfile.mockReset();
  apiMocks.deleteProfile.mockResolvedValue({ ok: true });
  apiMocks.getProfileSoul.mockReset();
  apiMocks.getProfileSoul.mockResolvedValue({
    content: composeSoul("Сметчик", "Считаешь сметы по моим расценкам."),
    exists: true,
  });
  apiMocks.updateProfileSoul.mockReset();
  apiMocks.updateProfileSoul.mockResolvedValue({ ok: true });
});

it("после закрытия вкладки возвращается к выбранному агенту", async () => {
  await render(<MemoryRouter initialEntries={["/agents"]}><AgentWorkbenchPage /></MemoryRouter>);
  await act(async () => Array.from(container.querySelectorAll<HTMLButtonElement>('[role="tab"]'))
    .find(tab => tab.textContent === "Сметчик")!.click());
  await act(async () => root.unmount());
  container.remove();
  sessionStorage.clear();
  await render(<MemoryRouter initialEntries={["/agents"]}><AgentWorkbenchPage /></MemoryRouter>);
  expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("Сметчик");
});

/** Открыть меню вкладки и нажать пункт по тексту. */
async function pickMenuItem(tabLabel: string, itemText: string) {
  await act(async () => {
    container
      .querySelector<HTMLButtonElement>(`button[aria-label="Меню агента «${tabLabel}»"]`)
      ?.click();
  });
  const menu = document.body.querySelector<HTMLElement>('[role="menu"]')!;
  await act(async () => {
    Array.from(menu.querySelectorAll("button"))
      .find((button) => button.textContent?.includes(itemText))
      ?.click();
    await Promise.resolve();
  });
}

async function renameTab(tabLabel: string, id: string, newName: string) {
  await pickMenuItem(tabLabel, "Переименовать");
  const input = document.body.querySelector<HTMLInputElement>(
    `#agent-display-name-${id}`,
  )!;
  await enterText(input, newName);
  await act(async () => {
    document.body
      .querySelector<HTMLButtonElement>('button[aria-label="Сохранить имя"]')
      ?.click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

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
    expect(menu.textContent).toContain("Роль и поведение");
    expect(menu.textContent).toContain("Модель");
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

  it("не закрывает форму из-за прокрутки длинного имени, но закрывает при прокрутке страницы", async () => {
    await render(<MemoryRouter initialEntries={["/agents"]}><AgentWorkbenchPage /></MemoryRouter>);
    await pickMenuItem("Сметчик", "Переименовать");
    const input = document.body.querySelector<HTMLInputElement>("#agent-display-name-calculator")!;
    await enterText(input, "Помощник владельца магазина");
    await act(async () => { input.dispatchEvent(new Event("scroll")); });
    expect(document.body.contains(input)).toBe(true);
    expect(input.value).toBe("Помощник владельца магазина");
    await act(async () => {
      document.body.querySelector<HTMLButtonElement>('button[aria-label="Сохранить имя"]')!.click();
    });
    expect(workbenchMocks.updateDisplayName).toHaveBeenCalledWith("calculator", "Помощник владельца магазина");
    await pickMenuItem("Сметчик", "Переименовать");
    await act(async () => { window.dispatchEvent(new Event("scroll")); });
    expect(document.body.querySelector('[role="menu"]')).toBeNull();
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

  it("по ссылке /agents?agent=default возвращается на главную вкладку", async () => {
    await render(
      <MemoryRouter initialEntries={["/agents?agent=calculator"]}>
        <AgentWorkbenchPage />
        <NavigateButton to="/agents?agent=default" />
        <LocationProbe />
      </MemoryRouter>,
    );
    expect(
      container
        .querySelector<HTMLButtonElement>("#agent-tab-calculator")
        ?.getAttribute("aria-selected"),
    ).toBe("true");

    await act(async () => {
      container
        .querySelector<HTMLButtonElement>('button[data-testid="navigate"]')
        ?.click();
    });

    expect(
      container
        .querySelector<HTMLButtonElement>("#agent-tab-")
        ?.getAttribute("aria-selected"),
    ).toBe("true");
    // Параметр снят: возврат назад-вперёд не должен переключать вкладку снова.
    expect(
      container.querySelector('[data-testid="location"]')?.textContent,
    ).toBe("/agents");
  });

  it("из меню вкладки ведёт к роли и модели именно этого агента", async () => {
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
        <LocationProbe />
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
        .find((button) => button.textContent?.includes("Роль и поведение"))
        ?.click();
    });
    expect(
      container.querySelector('[data-testid="location"]')?.textContent,
    ).toBe("/profiles?agent=calculator&edit=role");
    expect(document.body.querySelector('[role="menu"]')).toBeNull();

    // Главная вкладка — профиль самой панели, в настройках он `default`.
    await act(async () => {
      container
        .querySelector<HTMLButtonElement>('button[aria-label="Меню агента «Корра»"]')
        ?.click();
    });
    menu = document.body.querySelector<HTMLElement>('[role="menu"]')!;
    await act(async () => {
      Array.from(menu.querySelectorAll("button"))
        .find((button) => button.textContent?.includes("Модель"))
        ?.click();
    });
    expect(
      container.querySelector('[data-testid="location"]')?.textContent,
    ).toBe("/profiles?agent=default&edit=model");
  });

  it("из меню вкладки открывает навыки и расписание этого агента", async () => {
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
        <LocationProbe />
      </MemoryRouter>,
    );

    await pickMenuItem("Сметчик", "Навыки");
    expect(
      container.querySelector('[data-testid="location"]')?.textContent,
    ).toBe("/skills?profile=calculator");

    await pickMenuItem("Корра", "Расписание");
    expect(
      container.querySelector('[data-testid="location"]')?.textContent,
    ).toBe("/cron?profile=default");
  });

  it("удаляет агента из меню вкладки после подтверждения, но не Корру", async () => {
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );

    await act(async () => {
      container
        .querySelector<HTMLButtonElement>('button[aria-label="Меню агента «Корра»"]')
        ?.click();
    });
    expect(document.body.querySelector('[role="menu"]')?.textContent).not.toContain(
      "Удалить агента",
    );

    await pickMenuItem("Сметчик", "Удалить агента…");
    expect(apiMocks.deleteProfile).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain("Удалить агента «Сметчик»?");
    expect(document.body.textContent).toContain("вся история разговоров");

    await act(async () => {
      Array.from(document.body.querySelectorAll("button"))
        .find((button) => button.textContent?.trim() === "Удалить агента")
        ?.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(apiMocks.deleteProfile).toHaveBeenCalledWith("calculator");
    expect(workbenchMocks.refresh).toHaveBeenCalled();
    expect(document.body.textContent).toContain("Агент удалён.");
  });

  it("после переименования предупреждает, что в роли имя прежнее, и роль не пишет", async () => {
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );

    await renameTab("Сметчик", "calculator", "Главный сметчик");

    expect(workbenchMocks.updateDisplayName).toHaveBeenCalledWith(
      "calculator",
      "Главный сметчик",
    );
    expect(apiMocks.getProfileSoul).toHaveBeenCalledWith("calculator");
    // Роль из браузера не переписываем (решение с Астрой 06.09): только
    // читаем и подсказываем.
    expect(apiMocks.updateProfileSoul).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain(
      "В роли агент по-прежнему зовётся «Сметчик»",
    );
  });

  it("чужие инструкции с тем же словом при переименовании оставляет без замечаний", async () => {
    // Слово «Сметчик» есть, но это не наш шаблон имени — подсказка была бы
    // домыслом (замечание Астры 06.09).
    apiMocks.getProfileSoul.mockResolvedValue({
      content: "Ты — Сметчик, считаешь по прайсу. Сметчик отвечает кратко.",
      exists: true,
    });
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );

    await renameTab("Сметчик", "calculator", "Главный сметчик");

    expect(apiMocks.updateProfileSoul).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain("по-прежнему зовётся");
  });

  it("роль-дефолт движка при переименовании оставляет без замечаний", async () => {
    apiMocks.getProfileSoul.mockResolvedValue({ content: ENGINE_DEFAULT_SOUL, exists: true });
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );

    await renameTab("Сметчик", "calculator", "Главный сметчик");

    expect(apiMocks.updateProfileSoul).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain("по-прежнему зовётся");
  });
});
