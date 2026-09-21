/**
 * Цвет системного хрома Safari и Chrome под активную тему.
 *
 * На iPhone строка состояния, панель адреса и безопасные области
 * закрашиваются не фоном страницы, а значением `meta[name="theme-color"]`.
 * Этого тега у панели не было вовсе, поэтому Safari брал цвет один раз —
 * при открытии — и после переключения темы держал прежний: светлый
 * интерфейс в чёрной рамке (скриншот владельца, 21.09).
 *
 * Здесь одна обязанность: назвать непрозрачный холст темы и записать его
 * в `<meta>`, создав тег, если его нет. Тот же холст рисуют `html`, `body`
 * и `#root` через `--neo-background`, поэтому зоны страницы и системного
 * хрома не расходятся.
 */

import type { DashboardTheme } from "./types";

/** Светлый холст продукта — им же открывается документ до загрузки JS. */
export const DEFAULT_THEME_COLOR = "#e8e8e8";

/** Непрозрачный цвет, которым закрашен холст темы. */
export function resolveThemeColor(theme: Pick<DashboardTheme, "neumorphism" | "palette"> | null | undefined): string {
  const candidates = [theme?.neumorphism?.background, theme?.palette?.background?.hex];
  for (const candidate of candidates) {
    const value = candidate?.trim();
    // Полупрозрачное и вычисляемое значение системному хрому не подходит:
    // он не умеет смешивать его с тем, что под ним. Берём только
    // непрозрачный hex, иначе честнее оставить холст по умолчанию.
    if (value && /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i.test(value)) return value;
  }
  return DEFAULT_THEME_COLOR;
}

/**
 * Записать цвет в `meta[name="theme-color"]`, создав тег при необходимости.
 *
 * Возвращает записанное значение — вызывающему не нужно перечитывать DOM.
 */
export function applyThemeColorMeta(
  color: string,
  doc: Document | undefined = typeof document === "undefined" ? undefined : document,
): string | null {
  if (!doc) return null;
  let meta = doc.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (!meta) {
    meta = doc.createElement("meta");
    meta.name = "theme-color";
    doc.head.append(meta);
  }
  meta.setAttribute("content", color);
  return color;
}
