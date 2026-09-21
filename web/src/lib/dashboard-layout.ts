/**
 * Раскладка личного дашборда: порядок, скрытые карточки и размер плиток.
 *
 * Источник правды — сервер (`/api/dashboard/layout`): доска, собранная на
 * ноутбуке, открывается такой же на телефоне. Здесь нет ни React, ни сети, ни
 * localStorage — только чистые правила состава, чтобы решения проверялись без
 * DOM, а хук занимался передачей и конфликтами.
 *
 * Порядок хранится целиком, а не как «отличия от каталога»: карточка,
 * переставленная человеком, остаётся на своём месте и после того, как в
 * каталог добавится новая. Незнакомые идентификаторы отбрасываются — иначе в
 * наборе копятся призраки удалённых виджетов.
 *
 * Геометрия одна для всех плиток: S 1×1, M 2×1, L 2×2. Закреплённые карточки
 * (полоса «Требует внимания») живут над сеткой: у них нет ни размера, ни
 * позиции среди плиток.
 */

import type { DashboardLayoutPreference } from "@/lib/api";

export type WidgetSize = "s" | "m" | "l";

export const WIDGET_SIZES: readonly WidgetSize[] = ["s", "m", "l"];

/** Подписи размеров: буква — для компактной кнопки, слово — для голоса. */
export const WIDGET_SIZE_LABELS: Record<WidgetSize, { letter: string; name: string }> = {
  s: { letter: "S", name: "Компактный" },
  m: { letter: "M", name: "Стандартный" },
  l: { letter: "L", name: "Подробный" },
};

export const DEFAULT_WIDGET_SIZE: WidgetSize = "m";

export interface DashboardLayout {
  order: string[];
  hidden: string[];
  sizes: Record<string, WidgetSize>;
}

export interface WidgetCatalogShape {
  /** Все карточки в каталожном порядке. */
  ids: readonly string[];
  /** Карточки вне плиточной сетки: без размера и без перестановки. */
  pinned: readonly string[];
}

function isSize(value: unknown): value is WidgetSize {
  return typeof value === "string" && (WIDGET_SIZES as readonly string[]).includes(value);
}

function knownIds(catalog: WidgetCatalogShape, value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const seen: string[] = [];
  for (const id of value) {
    if (typeof id === "string" && catalog.ids.includes(id) && !seen.includes(id)) {
      seen.push(id);
    }
  }
  return seen;
}

/** Порядок по умолчанию — каталожный. */
export function defaultLayout(catalog: WidgetCatalogShape): DashboardLayout {
  return {
    order: [...catalog.ids],
    hidden: [],
    sizes: defaultSizes(catalog),
  };
}

export function defaultSizes(catalog: WidgetCatalogShape): Record<string, WidgetSize> {
  const sizes: Record<string, WidgetSize> = {};
  for (const id of catalog.ids) {
    if (!catalog.pinned.includes(id)) sizes[id] = DEFAULT_WIDGET_SIZE;
  }
  return sizes;
}

/**
 * Привести к рабочему виду всё, что пришло снаружи.
 *
 * Сервер уже нормализует запись, но ответ может прийти от более старой
 * панели, из другого окна или вовсе не дойти: экран должен собраться при
 * любом входе.
 */
export function normalizeLayout(
  catalog: WidgetCatalogShape,
  value: Partial<DashboardLayoutPreference> | DashboardLayout | null | undefined,
): DashboardLayout {
  const source = value && typeof value === "object" ? value : {};
  const stored = knownIds(catalog, (source as DashboardLayout).order);
  const order = [...stored, ...catalog.ids.filter((id) => !stored.includes(id))];
  const hiddenSet = new Set(knownIds(catalog, (source as DashboardLayout).hidden));
  const rawSizes = (source as DashboardLayout).sizes;
  const sizes = defaultSizes(catalog);
  if (rawSizes && typeof rawSizes === "object") {
    for (const id of Object.keys(sizes)) {
      const choice = (rawSizes as Record<string, unknown>)[id];
      if (isSize(choice)) sizes[id] = choice;
    }
  }
  return {
    order,
    hidden: order.filter((id) => hiddenSet.has(id)),
    sizes,
  };
}

/** Карточки, которые сейчас видно, в пользовательском порядке. */
export function visibleWidgetIds(layout: DashboardLayout): string[] {
  return layout.order.filter((id) => !layout.hidden.includes(id));
}

/** Плитки сетки — без закреплённой полосы. */
export function visibleTileIds(
  catalog: WidgetCatalogShape,
  layout: DashboardLayout,
): string[] {
  return visibleWidgetIds(layout).filter((id) => !catalog.pinned.includes(id));
}

export function widgetSize(
  catalog: WidgetCatalogShape,
  layout: DashboardLayout,
  id: string,
): WidgetSize {
  if (catalog.pinned.includes(id)) return DEFAULT_WIDGET_SIZE;
  return layout.sizes[id] ?? DEFAULT_WIDGET_SIZE;
}

export function hideWidget(
  catalog: WidgetCatalogShape,
  layout: DashboardLayout,
  id: string,
): DashboardLayout {
  if (!catalog.ids.includes(id) || layout.hidden.includes(id)) return layout;
  const hidden = [...layout.hidden, id];
  return { ...layout, hidden: layout.order.filter((item) => hidden.includes(item)) };
}

export function restoreWidget(layout: DashboardLayout, id: string): DashboardLayout {
  if (!layout.hidden.includes(id)) return layout;
  return { ...layout, hidden: layout.hidden.filter((item) => item !== id) };
}

export function resizeWidget(
  catalog: WidgetCatalogShape,
  layout: DashboardLayout,
  id: string,
  size: WidgetSize,
): DashboardLayout {
  if (catalog.pinned.includes(id) || !catalog.ids.includes(id)) return layout;
  if (layout.sizes[id] === size) return layout;
  return { ...layout, sizes: { ...layout.sizes, [id]: size } };
}

/**
 * Передвинуть плитку на соседнее видимое место.
 *
 * Соседей ищем среди ВИДИМЫХ плиток: скрытая карточка между ними не должна
 * поглощать нажатие, не меняя того, что человек видит. Закреплённая полоса в
 * перестановке не участвует и остаётся на своём месте.
 */
export function moveWidget(
  catalog: WidgetCatalogShape,
  layout: DashboardLayout,
  id: string,
  direction: -1 | 1,
): DashboardLayout {
  if (catalog.pinned.includes(id)) return layout;
  const visible = visibleTileIds(catalog, layout);
  const from = visible.indexOf(id);
  const to = from + direction;
  if (from < 0 || to < 0 || to >= visible.length) return layout;
  const neighbour = visible[to];
  const order = [...layout.order];
  const a = order.indexOf(id);
  const b = order.indexOf(neighbour);
  order[a] = neighbour;
  order[b] = id;
  return { ...layout, order, hidden: order.filter((item) => layout.hidden.includes(item)) };
}

export function resetLayout(catalog: WidgetCatalogShape): DashboardLayout {
  return defaultLayout(catalog);
}

export function isDefaultLayout(
  catalog: WidgetCatalogShape,
  layout: DashboardLayout,
): boolean {
  return sameLayout(layout, defaultLayout(catalog));
}

export function sameLayout(a: DashboardLayout, b: DashboardLayout): boolean {
  if (a === b) return true;
  const sameList = (x: readonly string[], y: readonly string[]) =>
    x.length === y.length && x.every((item, index) => item === y[index]);
  if (!sameList(a.order, b.order) || !sameList(a.hidden, b.hidden)) return false;
  const keys = new Set([...Object.keys(a.sizes), ...Object.keys(b.sizes)]);
  for (const key of keys) {
    if (a.sizes[key] !== b.sizes[key]) return false;
  }
  return true;
}
