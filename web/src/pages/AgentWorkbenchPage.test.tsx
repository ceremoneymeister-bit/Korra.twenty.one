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
  reorderTab: vi.fn(),
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
    reorderTab: workbenchMocks.reorderTab,
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
import { $activeAgentProfile } from "@/lib/active-agent";

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
  workbenchMocks.reorderTab.mockReset();
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
  it("publishes the selected tab profile for shell status", async () => {
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );
    expect($activeAgentProfile.get()).toBe("");
    await act(async () => Array.from(container.querySelectorAll<HTMLButtonElement>('[role="tab"]'))
      .find(tab => tab.textContent === "Сметчик")!.click());
    expect($activeAgentProfile.get()).toBe("calculator");
  });

  it("обычным pointer-кликом выбирает вкладку, не включая drag capture", async () => {
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );
    const wrapper = container.querySelector<HTMLElement>('[data-agent-tab-profile="calculator"]')!;
    const tab = wrapper.querySelector<HTMLButtonElement>('[role="tab"]')!;
    const capture = vi.fn();
    wrapper.setPointerCapture = capture;
    const fire = (type: string) => {
      const event = new Event(type, { bubbles: true, cancelable: true });
      Object.defineProperties(event, {
        button: { value: 0 },
        pointerId: { value: 17 },
        clientX: { value: 100 },
        clientY: { value: 10 },
      });
      tab.dispatchEvent(event);
    };
    await act(async () => {
      fire("pointerdown");
      fire("pointerup");
      tab.click();
    });
    expect(capture).not.toHaveBeenCalled();
    expect(tab.getAttribute("aria-selected")).toBe("true");
    expect($activeAgentProfile.get()).toBe("calculator");
  });

  /** Провести указателем от «Сметчика» к главной вкладке. */
  async function dragTab(pointerType: string) {
    const source = container.querySelector<HTMLElement>('[data-agent-tab-profile="calculator"]')!;
    const target = container.querySelector<HTMLElement>('[data-agent-tab-profile=""]')!;
    const tabButton = source.querySelector<HTMLElement>('[role="tab"]')!;
    const capture = vi.fn();
    source.setPointerCapture = capture;
    Object.defineProperty(document, "elementFromPoint", {
      configurable: true,
      value: vi.fn(() => target),
    });
    const events: Event[] = [];
    const fire = (type: string, clientX: number) => {
      const event = new Event(type, { bubbles: true, cancelable: true });
      Object.defineProperties(event, {
        button: { value: 0 },
        pointerId: { value: 17 },
        pointerType: { value: pointerType },
        clientX: { value: clientX },
        clientY: { value: 10 },
      });
      events.push(event);
      tabButton.dispatchEvent(event);
    };
    await act(async () => {
      fire("pointerdown", 100);
      fire("pointermove", 60);
      fire("pointerup", 60);
    });
    Object.defineProperty(document, "elementFromPoint", { configurable: true, value: undefined });
    return { capture, moved: events[1] };
  }

  it("мышью вкладку перетаскивают: порядок меняется", async () => {
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );
    const { capture, moved } = await dragTab("mouse");
    expect(workbenchMocks.reorderTab).toHaveBeenCalledWith("calculator", "");
    expect(capture).toHaveBeenCalled();
    // Перетаскивание забирает жест у прокрутки — для мыши это и нужно.
    expect(moved.defaultPrevented).toBe(true);
  });

  it("пальцем полосу прокручивают: порядок не меняется и жест остаётся у браузера", async () => {
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );
    const { capture, moved } = await dragTab("touch");
    expect(workbenchMocks.reorderTab).not.toHaveBeenCalled();
    expect(capture).not.toHaveBeenCalled();
    // Ни захвата указателя, ни preventDefault — иначе Safari отменит
    // горизонтальную прокрутку полосы вкладок.
    expect(moved.defaultPrevented).toBe(false);
    // Порядок вкладок на телефоне меняют через меню — оно на месте.
    expect(container.querySelector('button[aria-label="Меню агента «Сметчик»"]')).not.toBeNull();
  });

  it("палец не отменяет выбор вкладки нажатием", async () => {
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );
    const wrapper = container.querySelector<HTMLElement>('[data-agent-tab-profile="calculator"]')!;
    const tab = wrapper.querySelector<HTMLButtonElement>('[role="tab"]')!;
    const fire = (type: string, clientX: number) => {
      const event = new Event(type, { bubbles: true, cancelable: true });
      Object.defineProperties(event, {
        button: { value: 0 }, pointerId: { value: 3 }, pointerType: { value: "touch" },
        clientX: { value: clientX }, clientY: { value: 10 },
      });
      tab.dispatchEvent(event);
    };
    await act(async () => {
      fire("pointerdown", 100);
      fire("pointerup", 100);
      tab.click();
    });
    expect(tab.getAttribute("aria-selected")).toBe("true");
  });

  it("полоса вкладок прокручивается по горизонтали и не запрещает жест", async () => {
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );
    const scroller = container.querySelector<HTMLElement>(".korra-agent-tabs__scroller")!;
    expect(scroller.className).toContain("overflow-x-auto");
    const wrapper = container.querySelector<HTMLElement>('[data-agent-tab-profile="calculator"]')!;
    // `touch-pan-y` запрещал браузеру горизонтальный жест внутри полосы.
    expect(wrapper.className).not.toContain("touch-pan-y");
  });

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

/** Двенадцать агентов клиента из приёмки 0.21.12: двое последних
 *  не попадали на полосу из-за предела в десять вкладок. */
const TWELVE_TABS = [
  { profile: "", label: "Корра" },
  { profile: "finance", label: "Финансы и управление" },
  { profile: "interior", label: "Интерьер и архитектура" },
  { profile: "migrationlab", label: "migrationlab" },
  { profile: "studio_docs_bot", label: "Канцлер" },
  { profile: "studio_assistant_bot", label: "Ассистент" },
  { profile: "studio_visual_bot", label: "Визуализатор" },
  { profile: "studio_light_bot", label: "Светодизайнер" },
  { profile: "studio_marketing_bot", label: "Маркетинг" },
  { profile: "studio_project_bot", label: "Архитектурное бюро" },
  { profile: "studio_sales_bot", label: "Продажи", description: "Ведёт сделки" },
  { profile: "studio_secretary_bot", label: "Секретарь" },
];

/**
 * jsdom не раскладывает страницу: полоса «помещается» при нулевых размерах.
 * Здесь задаём ей ширину и содержимое шире неё — как у двенадцати вкладок
 * на экране 1440 px, — а каждой вкладке место по её порядку.
 */
function mockStripGeometry({ overflow, width = 600 }: { overflow: boolean; width?: number }) {
  const isStrip = (element: Element) => element.classList.contains("korra-agent-tabs__scroller");
  const scrollLeft = new WeakMap<Element, number>();
  const patched: Array<[string, PropertyDescriptor | undefined]> = [];
  const define = (name: string, descriptor: PropertyDescriptor) => {
    patched.push([name, Object.getOwnPropertyDescriptor(HTMLElement.prototype, name)]);
    Object.defineProperty(HTMLElement.prototype, name, { configurable: true, ...descriptor });
  };
  define("clientWidth", { get(this: HTMLElement) { return isStrip(this) ? width : 0; } });
  define("scrollWidth", { get(this: HTMLElement) { return isStrip(this) ? (overflow ? 2400 : width) : 0; } });
  define("scrollLeft", {
    get(this: HTMLElement) { return scrollLeft.get(this) ?? 0; },
    set(this: HTMLElement, value: number) { scrollLeft.set(this, value); },
  });
  const originalRect = HTMLElement.prototype.getBoundingClientRect;
  HTMLElement.prototype.getBoundingClientRect = function (this: HTMLElement) {
    if (isStrip(this)) return new DOMRect(0, 0, width, 40);
    const strip = this.closest(".korra-agent-tabs__scroller");
    const profile = this.dataset.agentTabProfile;
    if (strip && profile !== undefined) {
      const index = Array.from(strip.children).indexOf(this);
      return new DOMRect(index * 200 - (scrollLeft.get(strip) ?? 0), 0, 190, 40);
    }
    return originalRect.call(this);
  };
  return () => {
    for (const [name, descriptor] of patched.reverse()) {
      if (descriptor) Object.defineProperty(HTMLElement.prototype, name, descriptor);
      else delete (HTMLElement.prototype as unknown as Record<string, unknown>)[name];
    }
    HTMLElement.prototype.getBoundingClientRect = originalRect;
  };
}

describe("много агентов", () => {
  let restoreGeometry: (() => void) | null = null;

  afterEach(() => {
    restoreGeometry?.();
    restoreGeometry = null;
  });

  const listTrigger = () =>
    container.querySelector<HTMLButtonElement>("button[data-agent-list-trigger]");
  const agentList = () => document.body.querySelector<HTMLElement>("[data-agent-list]");

  it("все двенадцать агентов — вкладки полосы, и список «Все агенты» открывает любого", async () => {
    workbenchMocks.tabs = TWELVE_TABS;
    restoreGeometry = mockStripGeometry({ overflow: true });
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );

    const tabs = Array.from(container.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
    expect(tabs.map((tab) => tab.id)).toContain("agent-tab-studio_sales_bot");
    expect(tabs.map((tab) => tab.id)).toContain("agent-tab-studio_secretary_bot");

    const trigger = listTrigger()!;
    expect(trigger.getAttribute("aria-label")).toBe("Все агенты: 12");
    await act(async () => trigger.click());
    const list = agentList()!;
    expect(list.getAttribute("role")).toBe("dialog");
    const items = Array.from(list.querySelectorAll<HTMLButtonElement>("[data-agent-list-item]"));
    expect(items.map((item) => item.textContent)).toEqual(TWELVE_TABS.map((tab) => tab.label));
    expect(items[0].getAttribute("aria-current")).toBe("true");

    await act(async () => items[11].click());
    expect(agentList()).toBeNull();
    expect(container.querySelector("#agent-tab-studio_secretary_bot")?.getAttribute("aria-selected")).toBe("true");
    expect($activeAgentProfile.get()).toBe("studio_secretary_bot");
    // Вкладка доведена до видимой части полосы: 12-я стоит на 2200 px.
    const scroller = container.querySelector<HTMLElement>(".korra-agent-tabs__scroller")!;
    expect(scroller.scrollLeft).toBeGreaterThanOrEqual(11 * 200 + 190 - 600);
    expect(workbenchMocks.showTab).not.toHaveBeenCalled();
  });

  it("на узкой полосе телефона начало подписи выбранной вкладки не уходит за левый край", async () => {
    workbenchMocks.tabs = TWELVE_TABS;
    // Полоса 195 px, вкладка с «⋮» — 190: зазор справа в 8 px сдвинул бы
    // начало подписи за левый край.
    restoreGeometry = mockStripGeometry({ overflow: true, width: 195 });
    await render(
      <MemoryRouter initialEntries={["/agents?agent=studio_secretary_bot"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );
    const wrapper = container.querySelector<HTMLElement>('[data-agent-tab-profile="studio_secretary_bot"]')!;
    const box = wrapper.getBoundingClientRect();
    expect(box.left).toBeGreaterThanOrEqual(0);
    expect(box.right).toBeLessThanOrEqual(195);
  });

  it("поиск находит агента по имени, профилю и роли; Enter открывает первого найденного", async () => {
    workbenchMocks.tabs = TWELVE_TABS;
    restoreGeometry = mockStripGeometry({ overflow: true });
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );
    await act(async () => listTrigger()!.click());
    const input = agentList()!.querySelector<HTMLInputElement>('input[type="search"]')!;
    await enterText(input, "сделки");
    expect(
      Array.from(agentList()!.querySelectorAll("[data-agent-list-item]")).map((item) => item.textContent),
    ).toEqual(["Продажи"]);
    await enterText(input, "нет такого");
    expect(agentList()!.textContent).toContain("Нет агента с таким именем");
    await enterText(input, "secretary");
    await act(async () => {
      input.form!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(container.querySelector("#agent-tab-studio_secretary_bot")?.getAttribute("aria-selected")).toBe("true");
  });

  it("скрытый агент в списке помечен и при выборе возвращается на полосу", async () => {
    workbenchMocks.tabs = TWELVE_TABS.slice(0, 11);
    workbenchMocks.hiddenTabs = [TWELVE_TABS[11]];
    restoreGeometry = mockStripGeometry({ overflow: true });
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );
    await act(async () => listTrigger()!.click());
    const item = agentList()!.querySelector<HTMLButtonElement>('[data-agent-list-item="studio_secretary_bot"]')!;
    expect(item.textContent).toContain("вкладка скрыта");
    await act(async () => item.click());
    expect(workbenchMocks.showTab).toHaveBeenCalledWith("studio_secretary_bot");
  });

  it("клавиатура: стрелки по списку, Escape возвращает фокус на кнопку", async () => {
    workbenchMocks.tabs = TWELVE_TABS;
    restoreGeometry = mockStripGeometry({ overflow: true });
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );
    const trigger = listTrigger()!;
    // Нажатие с клавиатуры — `detail === 0`: фокус сразу в поиске.
    await act(async () => {
      trigger.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 0 }));
    });
    const list = agentList()!;
    const input = list.querySelector<HTMLInputElement>('input[type="search"]')!;
    expect(document.activeElement).toBe(input);
    const press = async (key: string) => {
      await act(async () => {
        (document.activeElement as HTMLElement).dispatchEvent(
          new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }),
        );
      });
    };
    await press("ArrowDown");
    expect((document.activeElement as HTMLElement).dataset.agentListItem).toBe("");
    await press("End");
    expect((document.activeElement as HTMLElement).dataset.agentListItem).toBe("studio_secretary_bot");
    await press("ArrowUp");
    expect((document.activeElement as HTMLElement).dataset.agentListItem).toBe("studio_sales_bot");
    await press("Escape");
    expect(agentList()).toBeNull();
    expect(document.activeElement).toBe(trigger);

    // Tab за пределы списка закрывает его, а не оставляет висеть над страницей.
    await act(async () => {
      trigger.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 0 }));
    });
    expect(agentList()).not.toBeNull();
    await act(async () => {
      container.querySelector<HTMLButtonElement>('button[aria-label="Добавить вкладку агента"]')!.focus();
    });
    expect(agentList()).toBeNull();
  });

  it("на 3–7 агентах полоса прежняя: списка нет, даже когда полоса прокручивается", async () => {
    workbenchMocks.tabs = TWELVE_TABS.slice(0, 7);
    restoreGeometry = mockStripGeometry({ overflow: true });
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );
    expect(listTrigger()).toBeNull();
    expect(container.querySelectorAll('[role="tab"]')).toHaveLength(7);
    expect(container.querySelector('button[aria-label="Добавить вкладку агента"]')).not.toBeNull();
  });

  it("список не нужен, когда все вкладки помещаются", async () => {
    workbenchMocks.tabs = TWELVE_TABS;
    restoreGeometry = mockStripGeometry({ overflow: false });
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );
    expect(listTrigger()).toBeNull();
  });

  it("Home и End на полосе ведут к первой и последней вкладке", async () => {
    workbenchMocks.tabs = TWELVE_TABS;
    restoreGeometry = mockStripGeometry({ overflow: true });
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );
    const first = container.querySelector<HTMLButtonElement>("#agent-tab-")!;
    first.focus();
    await act(async () => {
      first.dispatchEvent(new KeyboardEvent("keydown", { key: "End", bubbles: true, cancelable: true }));
    });
    expect(container.querySelector("#agent-tab-studio_secretary_bot")?.getAttribute("aria-selected")).toBe("true");
    const last = container.querySelector<HTMLButtonElement>("#agent-tab-studio_secretary_bot")!;
    await act(async () => {
      last.dispatchEvent(new KeyboardEvent("keydown", { key: "Home", bubbles: true, cancelable: true }));
    });
    expect(first.getAttribute("aria-selected")).toBe("true");
  });

  it("колесо мыши листает переполненную полосу вбок и отдаёт жест странице у края", async () => {
    workbenchMocks.tabs = TWELVE_TABS;
    restoreGeometry = mockStripGeometry({ overflow: true });
    await render(
      <MemoryRouter initialEntries={["/agents"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );
    const scroller = container.querySelector<HTMLElement>(".korra-agent-tabs__scroller")!;
    const wheel = (deltaY: number, deltaX = 0) => {
      const event = new WheelEvent("wheel", { deltaY, deltaX, bubbles: true, cancelable: true });
      scroller.dispatchEvent(event);
      return event;
    };
    expect(wheel(120).defaultPrevented).toBe(true);
    expect(scroller.scrollLeft).toBe(120);
    // Жест тачпада вбок браузер обрабатывает сам.
    expect(wheel(5, 40).defaultPrevented).toBe(false);
    // В начале полосы колесо вверх ей не нужно — листается страница.
    scroller.scrollLeft = 0;
    expect(wheel(-120).defaultPrevented).toBe(false);
  });

  it("ссылка на двенадцатого агента открывает его, а не оставляет главную вкладку", async () => {
    workbenchMocks.tabs = TWELVE_TABS;
    await render(
      <MemoryRouter initialEntries={["/agents?agent=studio_sales_bot"]}>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );
    expect(container.querySelector("#agent-tab-studio_sales_bot")?.getAttribute("aria-selected")).toBe("true");
  });
});
