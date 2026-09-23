// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { dashboardStateFixture } from "@/components/dashboard/dashboard-state.fixture";
import { ApiError, type DashboardLayoutPreference } from "@/lib/api";
import {
  $dashboardState,
  $dashboardStatus,
  refreshDashboardState,
  type DashboardState,
} from "@/lib/dashboard-state";
import DashboardPage from "./DashboardPage";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({
  getDashboardLayout: vi.fn(),
  setDashboardLayout: vi.fn(),
  getProfiles: vi.fn(),
  getDashboardCalendar: vi.fn(),
  getICloudCalendarFeed: vi.fn(),
}));
vi.mock(import("@/lib/api"), async (importOriginal) => ({
  ...(await importOriginal()),
  api: api as unknown as typeof import("@/lib/api").api,
}));

const CATALOG_TITLES = [
  "Требует внимания",
  "Агенты",
  "Мои показатели",
  "Ближайшие задачи",
  "Артефакты",
  "Календарь",
  "iCloud Calendar",
];
const CATALOG_IDS = ["attention", "agents", "metrics", "upcoming-tasks", "recent-results", "calendar", "icloud-calendar"];
const TILE_IDS = CATALOG_IDS.filter((id) => id !== "attention");

let root: Root;
let container: HTMLDivElement;

function pref(over: Partial<DashboardLayoutPreference> = {}): DashboardLayoutPreference {
  return {
    version: 1,
    revision: 1,
    initialized: true,
    order: [...CATALOG_IDS.filter((id) => id !== "icloud-calendar"), "codex-quota", "icloud-calendar"],
    hidden: [],
    sizes: Object.fromEntries(TILE_IDS.map((id) => [id, "m"])),
    ...over,
  };
}

function cardTitles(): string[] {
  return Array.from(container.querySelectorAll<HTMLElement>("[data-widget] h3")).map(
    (node) => node.textContent ?? "",
  );
}

function tiles(): { id: string; size: string | null }[] {
  return Array.from(container.querySelectorAll<HTMLElement>(".korra-dashboard__tile")).map(
    (node) => ({
      id: node.querySelector("[data-widget]")?.getAttribute("data-widget") ?? "",
      size: node.getAttribute("data-size"),
    }),
  );
}

function catalogRow(id: string): HTMLElement {
  const row = container.querySelector<HTMLElement>(`[data-catalog-widget="${id}"]`);
  expect(row, `строка каталога «${id}»`).not.toBeNull();
  return row!;
}

function button(text: string, scope: ParentNode = container): HTMLButtonElement {
  const found = Array.from(scope.querySelectorAll("button")).find(
    (node) => node.textContent?.trim() === text,
  );
  expect(found, `кнопка «${text}»`).toBeTruthy();
  return found as HTMLButtonElement;
}

function byLabel(label: string): HTMLElement {
  const found = container.querySelector<HTMLElement>(`[aria-label="${label}"]`);
  expect(found, label).not.toBeNull();
  return found!;
}

async function click(element: Element) {
  await act(async () =>
    element.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true })),
  );
}

/** Настоящий щелчок по radio: браузер сам переключает его и шлёт change. */
async function choose(input: HTMLInputElement) {
  await act(async () => input.click());
}

async function mount() {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () =>
    root.render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <DashboardPage />
      </MemoryRouter>,
    ),
  );
  // Сводка карточек — модульное хранилище, которое между тестами остаётся
  // подключённым; запрашиваем её явно через настоящий транспорт.
  await act(async () => {
    await refreshDashboardState();
  });
}

/** Сервер отвечает сводкой карточек; всё прочее по-прежнему недоступно. */
function serveState(state: DashboardState) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown) => {
      if (String(url).includes("/api/dashboard/state")) {
        return new Response(JSON.stringify(state), { headers: { "Content-Type": "application/json" } });
      }
      throw new Error("offline");
    }),
  );
}

beforeEach(async () => {
  vi.clearAllMocks();
  api.getDashboardLayout.mockResolvedValue(pref());
  api.setDashboardLayout.mockImplementation(
    async (layout: Pick<DashboardLayoutPreference, "revision" | "order" | "hidden" | "sizes">) =>
      pref({ ...layout, revision: layout.revision + 1 }),
  );
  // Дашборд должен собираться и без реальных агентов: эту границу проверяет
  // отдельный тест самой карточки.
  api.getProfiles.mockRejectedValue(new Error("offline"));
  // Календарь проверяется своим тестом; здесь он только должен не мешать доске.
  api.getDashboardCalendar.mockRejectedValue(new Error("offline"));
  api.getICloudCalendarFeed.mockResolvedValue({ state: "not_connected", account: null, fetched_at: null, events: [] });
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  $dashboardState.set(null);
  $dashboardStatus.set("idle");
  await mount();
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe("Личный дашборд", () => {
  it("здоровается настоящей датой, без придуманного имени, и даёт настройку", () => {
    const today = new Date().toLocaleDateString("ru-RU", {
      weekday: "long",
      day: "numeric",
      month: "long",
    });
    expect(container.textContent?.toLowerCase()).toContain(today.toLowerCase());
    expect(container.textContent).toContain("Хороший день.");
    expect(button("Настроить")).toBeTruthy();
  });

  it("показывает карточки-кандидаты в порядке каталога", () => {
    expect(cardTitles()).toEqual(CATALOG_TITLES);
  });

  it("«Требует внимания» — полоса над сеткой, остальные — плитки единой сетки", () => {
    expect(
      container.querySelector('.korra-dashboard__pinned [data-widget="attention"]'),
    ).not.toBeNull();
    expect(tiles()).toEqual(TILE_IDS.map((id) => ({ id, size: "m" })));
  });

  it("без сводки каждая карточка честно говорит о сбое и даёт повторить, а не показывает ноль", () => {
    const failures = {
      attention: "Не удалось проверить",
      metrics: "Не удалось посчитать показатели",
      "upcoming-tasks": "Не удалось прочитать расписание",
      "recent-results": "Не удалось прочитать файлы",
    };
    for (const [id, text] of Object.entries(failures)) {
      const card = container.querySelector<HTMLElement>(`[data-widget="${id}"]`);
      expect(card?.textContent).toContain(text);
      expect(card?.querySelector('[role="alert"]'), id).not.toBeNull();
      expect(Array.from(card!.querySelectorAll("button")).some((node) => node.textContent?.includes("Повторить")))
        .toBe(true);
      expect(card?.textContent).not.toContain("Сводка недоступна");
    }
  });

  it("уводит за теми же сведениями на существующие экраны", () => {
    const targets = Array.from(
      container.querySelectorAll<HTMLAnchorElement>("[data-widget] a"),
    )
      .map((node) => node.getAttribute("href"))
      .filter((href) => href !== null);
    expect(targets).toContain("/agents");
    expect(targets).toContain("/cron");
    expect(targets).toContain("/files");
  });

  it("не даёт убирать карточки, пока настройка не открыта", () => {
    expect(container.querySelector('[aria-label="Убрать карточку «Агенты»"]')).toBeNull();
    expect(container.querySelector("#dashboard-widget-catalog")).toBeNull();
  });

  it("открывает каталог со всеми карточками и закрывается по «Готово»", async () => {
    const add = button("Настроить");
    expect(add.getAttribute("aria-expanded")).toBe("false");

    await click(add);
    const catalog = container.querySelector("#dashboard-widget-catalog");
    expect(catalog).not.toBeNull();
    expect(button("Настроить").getAttribute("aria-expanded")).toBe("true");
    expect(
      Array.from(catalog!.querySelectorAll<HTMLElement>("[data-catalog-widget] p"))
        .map((node) => node.textContent)
        .filter((text) => CATALOG_TITLES.includes(text ?? "")),
    ).toEqual(CATALOG_TITLES);
    expect(container.querySelector('[aria-label="Убрать карточку «Агенты»"]')).not.toBeNull();

    await click(button("Готово"));
    expect(container.querySelector("#dashboard-widget-catalog")).toBeNull();
  });
});

describe("Раскладка дашборда хранится на сервере", () => {
  it("применяет ту раскладку, которую вернул сервер", async () => {
    await act(async () => root.unmount());
    container.remove();
    api.getDashboardLayout.mockResolvedValue(
      pref({
        revision: 4,
        order: ["attention", "recent-results", "agents", "metrics", "upcoming-tasks"],
        hidden: ["metrics"],
        sizes: { agents: "l", metrics: "m", "upcoming-tasks": "s", "recent-results": "s" },
      }),
    );
    await mount();

    expect(tiles()).toEqual([
      { id: "recent-results", size: "s" },
      { id: "agents", size: "l" },
      { id: "upcoming-tasks", size: "s" },
      // Карточки, которой не было в сохранённой раскладке, не теряем: в конец.
      { id: "calendar", size: "m" },
      { id: "icloud-calendar", size: "m" },
    ]);
    expect(cardTitles()).not.toContain("Мои показатели");
  });

  it("убранная карточка уходит на сервер с текущей ревизией и возвращается на место", async () => {
    await click(button("Настроить"));
    await click(button("Убрать", catalogRow("agents")));

    expect(api.setDashboardLayout).toHaveBeenCalledTimes(1);
    expect(api.setDashboardLayout.mock.calls[0][0]).toMatchObject({
      revision: 1,
      hidden: ["agents"],
    });
    expect(cardTitles()).toEqual(CATALOG_TITLES.filter((title) => title !== "Агенты"));
    expect(catalogRow("agents").textContent).toContain("Убрана");
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      "«Агенты» убрана",
    );

    await click(button("Вернуть", catalogRow("agents")));
    // Вторая запись обязана уйти с ревизией, подтверждённой первой.
    expect(api.setDashboardLayout.mock.calls[1][0]).toMatchObject({
      revision: 2,
      hidden: [],
    });
    expect(cardTitles()).toEqual(CATALOG_TITLES);
  });

  it("выбор размера — обычная radio-group, и он сохраняется", async () => {
    await click(button("Настроить"));
    const picker = container.querySelector<HTMLElement>('[data-size-picker="agents"]')!;
    const options = Array.from(picker.querySelectorAll<HTMLInputElement>("input"));
    expect(options.map((input) => input.value)).toEqual(["s", "m", "l"]);
    expect(options.map((input) => input.type)).toEqual(["radio", "radio", "radio"]);
    expect(options.find((input) => input.checked)?.value).toBe("m");
    expect(options[2].getAttribute("aria-label")).toBe(
      "Подробный размер карточки «Агенты»",
    );

    await choose(options[2]);
    expect(api.setDashboardLayout.mock.calls[0][0].sizes).toMatchObject({ agents: "l" });
    expect(tiles().find((tile) => tile.id === "agents")?.size).toBe("l");
  });

  it("порядок плиток меняется стрелками и тоже сохраняется", async () => {
    await click(button("Настроить"));
    await click(byLabel("Переместить карточку «Агенты» правее"));

    // Квота Codex на этой установке недоступна: на доске её нет, но
    // сохранённая раскладка её помнит и не теряет место.
    expect(api.setDashboardLayout.mock.calls[0][0].order).toEqual([
      "attention",
      "metrics",
      "agents",
      "upcoming-tasks",
      "recent-results",
      "calendar",
      "codex-quota",
      "icloud-calendar",
    ]);
    expect(tiles().map((tile) => tile.id)).toEqual([
      "metrics",
      "agents",
      "upcoming-tasks",
      "recent-results",
      "calendar",
      "icloud-calendar",
    ]);
    // Первую плитку левее не двигают, закреплённая полоса стрелок не имеет.
    expect((byLabel("Переместить карточку «Мои показатели» левее") as HTMLButtonElement).disabled)
      .toBe(true);
    expect(container.querySelector('[data-size-picker="attention"]')).toBeNull();
  });

  it("стандартный набор восстанавливается и сразу уходит на сервер", async () => {
    await click(button("Настроить"));
    expect(button("Вернуть стандартный набор").disabled).toBe(true);

    for (const id of CATALOG_IDS) await click(button("Убрать", catalogRow(id)));
    expect(cardTitles()).toEqual([]);
    expect(container.textContent).toContain("Все карточки убраны");

    await click(button("Вернуть стандартный набор"));
    expect(cardTitles()).toEqual(CATALOG_TITLES);
    expect(button("Вернуть стандартный набор").disabled).toBe(true);
    expect(api.setDashboardLayout.mock.lastCall?.[0]).toMatchObject({ hidden: [] });
  });

  it("проигранный конфликт показывает победителя и даёт повторить свой выбор", async () => {
    const winner = pref({ revision: 9, hidden: ["recent-results"] });
    // Так отвечает сервер на проигранный CAS: 409 и победившая запись в теле.
    api.setDashboardLayout.mockRejectedValueOnce(
      new ApiError(409, "409: Дашборд уже изменился в другом окне.", { preference: winner }),
    );
    api.getDashboardLayout.mockResolvedValue(winner);

    await click(button("Настроить"));
    await click(button("Убрать", catalogRow("agents")));

    expect(container.querySelector("[data-layout-status]")?.getAttribute("data-layout-status"))
      .toBe("conflict");
    expect(container.textContent).toContain("изменился в другом окне");
    // На экране — сохранённое другим окном состояние, а не наше.
    expect(cardTitles()).toEqual(CATALOG_TITLES.filter((title) => title !== "Артефакты"));

    // Повтор идёт уже от ревизии победителя.
    await click(button("Убрать", catalogRow("agents")));
    expect(api.setDashboardLayout.mock.lastCall?.[0]).toMatchObject({ revision: 9 });
    expect(cardTitles()).toEqual(
      CATALOG_TITLES.filter((title) => title !== "Артефакты" && title !== "Агенты"),
    );
  });

  it("без ответа сервера доска работает и честно говорит, что не сохраняется", async () => {
    await act(async () => root.unmount());
    container.remove();
    api.getDashboardLayout.mockRejectedValue(new Error("offline"));
    await mount();

    expect(container.querySelector("[data-layout-status]")?.getAttribute("data-layout-status"))
      .toBe("error");
    expect(container.textContent).toContain("не сохраняется");
    expect(cardTitles()).toEqual(CATALOG_TITLES);

    await click(button("Настроить"));
    await click(button("Убрать", catalogRow("agents")));
    expect(api.setDashboardLayout).not.toHaveBeenCalled();
    expect(cardTitles()).not.toContain("Агенты");

    // Связь вернулась — «Повторить» приводит экран к серверному состоянию.
    api.getDashboardLayout.mockResolvedValue(pref({ revision: 3, hidden: ["metrics"] }));
    await click(button("Повторить"));
    expect(cardTitles()).toEqual(CATALOG_TITLES.filter((title) => title !== "Мои показатели"));
  });
});

describe("Карточки на живой сводке", () => {
  async function remount(state: DashboardState) {
    await act(async () => root.unmount());
    container.remove();
    $dashboardState.set(null);
    serveState(state);
    await mount();
  }

  it("квота Codex встаёт на доску и в каталог только там, где есть подписка", async () => {
    await remount(dashboardStateFixture());
    expect(tiles().map((tile) => tile.id)).toContain("codex-quota");
    expect(container.querySelector('[data-widget="codex-quota"]')?.textContent).toContain("62 %");
    await click(button("Настроить"));
    expect(container.querySelector('[data-catalog-widget="codex-quota"]')).not.toBeNull();

    await remount(dashboardStateFixture({ quota: { available: false, status: "absent" } }));
    expect(tiles().map((tile) => tile.id)).not.toContain("codex-quota");
    await click(button("Настроить"));
    expect(container.querySelector('[data-catalog-widget="codex-quota"]')).toBeNull();
  });

  it("каждая карточка показывает настоящие данные сводки", async () => {
    await remount(dashboardStateFixture());
    const card = (id: string) => container.querySelector<HTMLElement>(`[data-widget="${id}"]`)!;
    expect(card("attention").textContent).toContain("Смета для клиента");
    expect(card("metrics").textContent).toContain("12");
    expect(card("upcoming-tasks").textContent).toContain("Итоги дня");
    expect(card("recent-results").textContent).toContain("Отчёт.md");
    expect(container.textContent).not.toContain("Сводка недоступна");
  });
});
