// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import DashboardPage from "./DashboardPage";

let root: Root;
let container: HTMLDivElement;

const CATALOG_TITLES = [
  "Требует внимания",
  "Агенты",
  "Мои показатели",
  "Ближайшие задачи",
  "Последние результаты",
];

function cardTitles(): string[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>("[data-widget] h3"),
  ).map((node) => node.textContent ?? "");
}

function catalogRow(id: string): HTMLElement {
  const row = container.querySelector<HTMLElement>(
    `[data-catalog-widget="${id}"]`,
  );
  expect(row).not.toBeNull();
  return row!;
}

function button(text: string, scope: ParentNode = container): HTMLButtonElement {
  const found = Array.from(scope.querySelectorAll("button")).find(
    (node) => node.textContent?.trim() === text,
  );
  expect(found, `кнопка «${text}»`).toBeTruthy();
  return found!;
}

async function click(element: Element) {
  await act(async () =>
    element.dispatchEvent(
      new MouseEvent("click", { bubbles: true, cancelable: true }),
    ),
  );
}

beforeEach(async () => {
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
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("Личный дашборд", () => {
  it("объясняет назначение экрана и предлагает добавить виджет", () => {
    expect(container.textContent).toContain("Личный дашборд");
    expect(container.textContent).toContain("Один экран о вашей работе");
    expect(button("Добавить виджет")).toBeTruthy();
  });

  it("показывает пять карточек-кандидатов в порядке каталога", () => {
    expect(cardTitles()).toEqual(CATALOG_TITLES);
  });

  it("вместо выдуманных данных честно называет неподключённый источник", () => {
    const cards = container.querySelectorAll<HTMLElement>("[data-widget]");
    expect(cards).toHaveLength(CATALOG_TITLES.length);
    for (const card of cards) {
      expect(card.textContent).toContain("Источник ещё не подключён");
    }
  });

  it("не даёт убирать карточки, пока настройка не открыта", () => {
    expect(
      container.querySelector('[aria-label="Убрать карточку «Агенты»"]'),
    ).toBeNull();
    expect(container.querySelector("#dashboard-widget-catalog")).toBeNull();
  });

  it("открывает каталог со всеми карточками и закрывается по «Готово»", async () => {
    const add = button("Добавить виджет");
    expect(add.getAttribute("aria-expanded")).toBe("false");

    await click(add);
    const catalog = container.querySelector("#dashboard-widget-catalog");
    expect(catalog).not.toBeNull();
    expect(button("Добавить виджет").getAttribute("aria-expanded")).toBe("true");
    expect(
      Array.from(
        catalog!.querySelectorAll<HTMLElement>("[data-catalog-widget] p"),
      )
        .map((node) => node.textContent)
        .filter((text) => CATALOG_TITLES.includes(text ?? "")),
    ).toEqual(CATALOG_TITLES);
    // В режиме настройки карточка получает своё действие «Убрать».
    expect(
      container.querySelector('[aria-label="Убрать карточку «Агенты»"]'),
    ).not.toBeNull();

    await click(button("Готово"));
    expect(container.querySelector("#dashboard-widget-catalog")).toBeNull();
    expect(
      container.querySelector('[aria-label="Убрать карточку «Агенты»"]'),
    ).toBeNull();
  });

  it("убирает карточку и возвращает её на прежнее место", async () => {
    await click(button("Добавить виджет"));
    await click(button("Убрать", catalogRow("agents")));

    expect(cardTitles()).toEqual(CATALOG_TITLES.filter((t) => t !== "Агенты"));
    expect(catalogRow("agents").textContent).toContain("Убрана");
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      "«Агенты» убрана",
    );

    await click(button("Вернуть", catalogRow("agents")));
    expect(cardTitles()).toEqual(CATALOG_TITLES);
    expect(catalogRow("agents").textContent).toContain("На дашборде");
  });

  it("убирает карточку прямо на ней и не теряет её из каталога", async () => {
    await click(button("Добавить виджет"));
    await click(
      container.querySelector('[aria-label="Убрать карточку «Мои показатели»"]')!,
    );

    expect(cardTitles()).not.toContain("Мои показатели");
    expect(catalogRow("metrics").textContent).toContain("Убрана");
  });

  it("восстанавливает стандартный набор после того, как убрали всё", async () => {
    await click(button("Добавить виджет"));
    const reset = button("Вернуть стандартный набор");
    expect(reset.disabled).toBe(true);

    for (const id of [
      "attention",
      "agents",
      "metrics",
      "upcoming-tasks",
      "recent-results",
    ]) {
      await click(button("Убрать", catalogRow(id)));
    }
    expect(cardTitles()).toEqual([]);
    expect(container.textContent).toContain("Все карточки убраны");

    await click(button("Вернуть стандартный набор"));
    expect(cardTitles()).toEqual(CATALOG_TITLES);
    expect(button("Вернуть стандартный набор").disabled).toBe(true);
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      "стандартный набор",
    );
  });

  it("из пустого дашборда открывает каталог обратно", async () => {
    await click(button("Добавить виджет"));
    for (const id of [
      "attention",
      "agents",
      "metrics",
      "upcoming-tasks",
      "recent-results",
    ]) {
      await click(button("Убрать", catalogRow(id)));
    }
    await click(button("Готово"));

    expect(container.textContent).toContain("Все карточки убраны");
    await click(button("Открыть каталог карточек"));
    expect(container.querySelector("#dashboard-widget-catalog")).not.toBeNull();

    await click(button("Вернуть", catalogRow("agents")));
    expect(cardTitles()).toEqual(["Агенты"]);
  });

  it("уводит за теми же сведениями на существующие экраны", () => {
    const targets = Array.from(
      container.querySelectorAll<HTMLAnchorElement>("[data-widget] a"),
    ).map((node) => node.getAttribute("href"));
    expect(targets).toEqual(["/agents", "/agents", "/cron", "/files"]);
  });
});
