import { useSyncExternalStore } from "react";

/** Граница десктопа панели — `lg` у Tailwind. */
export const DESKTOP_QUERY = "(min-width: 1024px)";

function supported(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function";
}

/**
 * Совпадает ли медиазапрос — без мигания на первом кадре.
 *
 * Значение читается синхронно при отрисовке, поэтому телефон сразу получает
 * телефонную шапку. Где `matchMedia` нет (тесты, старые встраивания), ответ
 * — `fallback`: по умолчанию «не совпадает».
 */
export function useMediaQuery(query: string, fallback = false): boolean {
  return useSyncExternalStore(
    (onChange) => {
      if (!supported()) return () => {};
      const list = window.matchMedia(query);
      list.addEventListener?.("change", onChange);
      return () => list.removeEventListener?.("change", onChange);
    },
    () => (supported() ? window.matchMedia(query).matches : fallback),
    () => fallback,
  );
}

/** Телефон или узкий планшет: всё, что ниже `lg`. Без matchMedia — десктоп. */
export function useBelowDesktop(): boolean {
  return !useMediaQuery(DESKTOP_QUERY, true);
}
