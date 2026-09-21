import { describe, expect, it } from "vitest";

import {
  DEFAULT_HIDDEN_WIDGETS,
  hiddenWidgetIds,
  hideWidget,
  isDefaultWidgetLayout,
  resetWidgets,
  restoreWidget,
  visibleWidgetIds,
} from "./dashboard-layout";

const CATALOG = ["attention", "agents", "metrics", "upcoming", "results"];

describe("состав дашборда", () => {
  it("по умолчанию показывает весь каталог в его порядке", () => {
    expect(visibleWidgetIds(CATALOG, DEFAULT_HIDDEN_WIDGETS)).toEqual(CATALOG);
    expect(isDefaultWidgetLayout(DEFAULT_HIDDEN_WIDGETS)).toBe(true);
  });

  it("убирает карточку, не трогая порядок остальных", () => {
    const hidden = hideWidget(CATALOG, [], "agents");
    expect(visibleWidgetIds(CATALOG, hidden)).toEqual([
      "attention",
      "metrics",
      "upcoming",
      "results",
    ]);
    expect(hiddenWidgetIds(CATALOG, hidden)).toEqual(["agents"]);
    expect(isDefaultWidgetLayout(hidden)).toBe(false);
  });

  it("возвращает карточку на её место в каталоге, а не в конец", () => {
    const hidden = hideWidget(CATALOG, [], "agents");
    expect(visibleWidgetIds(CATALOG, restoreWidget(hidden, "agents"))).toEqual(
      CATALOG,
    );
  });

  it("не меняет набор при повторном убирании и возврате лишнего", () => {
    const once = hideWidget(CATALOG, [], "metrics");
    expect(hideWidget(CATALOG, once, "metrics")).toEqual(once);
    expect(restoreWidget(once, "attention")).toEqual(once);
  });

  it("не принимает карточку, которой нет в каталоге", () => {
    // Иначе в наборе копятся призраки удалённых виджетов, и «стандартный
    // набор» перестаёт быть отличим от изменённого.
    expect(hideWidget(CATALOG, [], "notes")).toEqual([]);
    expect(isDefaultWidgetLayout(hideWidget(CATALOG, [], "notes"))).toBe(true);
  });

  it("сброс возвращает весь каталог, сколько бы карточек ни убрали", () => {
    let hidden: string[] = [];
    for (const id of CATALOG) hidden = hideWidget(CATALOG, hidden, id);
    expect(visibleWidgetIds(CATALOG, hidden)).toEqual([]);
    expect(visibleWidgetIds(CATALOG, resetWidgets())).toEqual(CATALOG);
    expect(isDefaultWidgetLayout(resetWidgets())).toBe(true);
  });

  it("не меняет переданный набор на месте", () => {
    const hidden: string[] = [];
    hideWidget(CATALOG, hidden, "agents");
    expect(hidden).toEqual([]);
  });
});
