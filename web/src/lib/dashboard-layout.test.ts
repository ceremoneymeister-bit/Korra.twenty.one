import { describe, expect, it } from "vitest";

import {
  defaultLayout,
  defaultSizes,
  hideWidget,
  isDefaultLayout,
  moveWidget,
  normalizeLayout,
  resetLayout,
  resizeWidget,
  restoreWidget,
  restoreWidgets,
  sameLayout,
  visibleTileIds,
  visibleWidgetIds,
  widgetSize,
  withoutWidgets,
  type DashboardLayout,
  type WidgetCatalogShape,
} from "./dashboard-layout";

const CATALOG: WidgetCatalogShape = {
  ids: ["attention", "agents", "metrics", "upcoming", "results"],
  pinned: ["attention"],
};

const base = () => defaultLayout(CATALOG);

describe("раскладка дашборда", () => {
  it("по умолчанию показывает весь каталог стандартным размером", () => {
    const layout = base();
    expect(visibleWidgetIds(layout)).toEqual(CATALOG.ids);
    expect(isDefaultLayout(CATALOG, layout)).toBe(true);
    expect(layout.sizes).toEqual({
      agents: "m",
      metrics: "m",
      upcoming: "m",
      results: "m",
    });
  });

  it("закреплённая полоса не получает размера и не участвует в плитках", () => {
    expect(defaultSizes(CATALOG).attention).toBeUndefined();
    expect(visibleTileIds(CATALOG, base())).toEqual([
      "agents",
      "metrics",
      "upcoming",
      "results",
    ]);
    // Спросить размер у полосы всё равно можно — ответ не ломает вёрстку.
    expect(widgetSize(CATALOG, base(), "attention")).toBe("m");
  });

  it("убирает карточку и возвращает её на прежнее место, а не в конец", () => {
    const hidden = hideWidget(CATALOG, base(), "agents");
    expect(visibleWidgetIds(hidden)).toEqual([
      "attention",
      "metrics",
      "upcoming",
      "results",
    ]);
    expect(isDefaultLayout(CATALOG, hidden)).toBe(false);
    expect(visibleWidgetIds(restoreWidget(hidden, "agents"))).toEqual(CATALOG.ids);
  });

  it("не принимает карточку, которой нет в каталоге", () => {
    // Иначе в наборе копятся призраки удалённых виджетов, и «стандартный
    // набор» перестаёт быть отличим от изменённого.
    const layout = hideWidget(CATALOG, base(), "notes");
    expect(isDefaultLayout(CATALOG, layout)).toBe(true);
    expect(resizeWidget(CATALOG, base(), "notes", "l")).toEqual(base());
  });

  it("меняет размер только у плиток", () => {
    expect(widgetSize(CATALOG, resizeWidget(CATALOG, base(), "agents", "l"), "agents")).toBe("l");
    // Полоса размера не имеет: попытка ничего не меняет.
    expect(resizeWidget(CATALOG, base(), "attention", "s")).toEqual(base());
  });

  it("переставляет плитку через скрытую соседку на видимое место", () => {
    // Скрытая карточка между двумя видимыми не должна поглощать нажатие,
    // не меняя того, что человек видит.
    const hidden = hideWidget(CATALOG, base(), "metrics");
    const moved = moveWidget(CATALOG, hidden, "upcoming", -1);
    expect(visibleTileIds(CATALOG, moved)).toEqual(["upcoming", "agents", "results"]);
    expect(moved.order).toContain("metrics");
    expect(moved.hidden).toEqual(["metrics"]);
  });

  it("не двигает карточку за край и не трогает закреплённую полосу", () => {
    const layout = base();
    expect(moveWidget(CATALOG, layout, "agents", -1)).toBe(layout);
    expect(moveWidget(CATALOG, layout, "results", 1)).toBe(layout);
    expect(moveWidget(CATALOG, layout, "attention", 1)).toBe(layout);
  });

  it("чинит битую, устаревшую и чужую запись", () => {
    const repaired = normalizeLayout(CATALOG, {
      order: ["metrics", "notes", "metrics"],
      hidden: ["agents", "notes"],
      sizes: { agents: "xxl", notes: "l", results: "s" },
    } as unknown as DashboardLayout);
    expect(repaired.order[0]).toBe("metrics");
    expect(new Set(repaired.order)).toEqual(new Set(CATALOG.ids));
    expect(repaired.order).toHaveLength(CATALOG.ids.length);
    expect(repaired.hidden).toEqual(["agents"]);
    expect(repaired.sizes.agents).toBe("m");
    expect(repaired.sizes.results).toBe("s");
    expect(normalizeLayout(CATALOG, null)).toEqual(base());
    expect(normalizeLayout(CATALOG, { order: {}, hidden: "agents" } as unknown as DashboardLayout)).toEqual(base());
  });

  it("карточка, добавленная в каталог позже, появляется у того, кто уже настроил доску", () => {
    const older = normalizeLayout(CATALOG, {
      order: ["results", "agents"],
      hidden: [],
      sizes: {},
    } as unknown as DashboardLayout);
    expect(older.order.slice(0, 2)).toEqual(["results", "agents"]);
    expect(new Set(older.order)).toEqual(new Set(CATALOG.ids));
  });

  it("сброс возвращает весь каталог, сколько бы карточек ни убрали", () => {
    let layout = base();
    for (const id of CATALOG.ids) layout = hideWidget(CATALOG, layout, id);
    expect(visibleWidgetIds(layout)).toEqual([]);
    expect(isDefaultLayout(CATALOG, resetLayout(CATALOG))).toBe(true);
  });

  it("не меняет переданную раскладку на месте", () => {
    const layout = base();
    const snapshot = JSON.stringify(layout);
    hideWidget(CATALOG, layout, "agents");
    resizeWidget(CATALOG, layout, "agents", "l");
    moveWidget(CATALOG, layout, "agents", 1);
    expect(JSON.stringify(layout)).toBe(snapshot);
  });

  it("сравнение раскладок видит разницу в порядке, составе и размере", () => {
    expect(sameLayout(base(), base())).toBe(true);
    expect(sameLayout(base(), moveWidget(CATALOG, base(), "agents", 1))).toBe(false);
    expect(sameLayout(base(), hideWidget(CATALOG, base(), "agents"))).toBe(false);
    expect(sameLayout(base(), resizeWidget(CATALOG, base(), "agents", "s"))).toBe(false);
  });
});

describe("карточки, которым нет места на установке", () => {
  const catalog = { ids: ["attention", "agents", "metrics", "codex-quota", "files"], pinned: ["attention"] };

  it("не видны на доске, но сохраняют своё место и скрытость в раскладке", () => {
    const stored: DashboardLayout = {
      order: ["attention", "metrics", "codex-quota", "agents", "files"],
      hidden: ["codex-quota"],
      sizes: { agents: "m", metrics: "m", "codex-quota": "l", files: "s" },
    };
    const board = withoutWidgets(stored, ["codex-quota"]);
    expect(board.order).toEqual(["attention", "metrics", "agents", "files"]);
    expect(board.hidden).toEqual([]);

    // Человек переставил видимые плитки — недоступная встаёт за прежним соседом.
    const moved = moveWidget(catalog, board, "agents", -1);
    const saved = restoreWidgets(moved, stored, ["codex-quota"]);
    expect(saved.order).toEqual(["attention", "agents", "metrics", "codex-quota", "files"]);
    expect(saved.hidden).toEqual(["codex-quota"]);
    expect(saved.sizes["codex-quota"]).toBe("l");
  });

  it("без недоступных карточек ничего не меняет", () => {
    const layout: DashboardLayout = { order: ["attention", "agents"], hidden: [], sizes: {} };
    expect(withoutWidgets(layout, [])).toBe(layout);
    expect(restoreWidgets(layout, layout, [])).toBe(layout);
  });
});
