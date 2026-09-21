/**
 * Кто перетаскивает вкладку агента, а кто прокручивает полосу.
 *
 * Полоса вкладок шире экрана телефона, и единственный способ добраться до
 * дальнего агента — провести по ней пальцем. Раньше `pointerdown` начинал
 * перетаскивание независимо от устройства: первое же движение пальца
 * забирало указатель через `setPointerCapture`, гасило прокрутку
 * `preventDefault()` и меняло порядок вкладок вместо того, чтобы показать
 * следующую (скриншоты владельца с iPhone 15 Pro Max, 21.09).
 *
 * Правило: перетаскивает только мышь. Палец и перо — прямое управление,
 * там жест принадлежит прокрутке, а порядок вкладок меняется через меню
 * «Сдвинуть влево/вправо», которое было и остаётся.
 *
 * Модуль намеренно без React и без DOM: решение проверяется тестом на
 * простых объектах события.
 */

/** Сдвиг в CSS px, после которого движение мыши считается перетаскиванием. */
export const TAB_DRAG_THRESHOLD_PX = 8;

/** То, что нужно знать о событии указателя, чтобы принять решение. */
export interface TabPointer {
  /** `PointerEvent.pointerType`: "mouse" | "touch" | "pen". */
  pointerType?: string;
  /** `PointerEvent.button`: перетаскивает только основная кнопка. */
  button?: number;
}

/**
 * Указатель, которым вкладки переставляют.
 *
 * Отсутствующий `pointerType` считаем мышью: так ведут себя синтетические
 * события jsdom и старые браузеры без Pointer Events, и десктопное
 * поведение от этого не меняется.
 */
export function isReorderPointer(event: TabPointer): boolean {
  const kind = event.pointerType?.trim() || "mouse";
  return kind === "mouse";
}

/**
 * Начинать ли перетаскивание вкладки.
 *
 * `onMenuTrigger` — палец/курсор опустился на кнопку «⋮»: это нажатие
 * принадлежит меню, а не полосе.
 */
export function shouldStartTabDrag(
  event: TabPointer,
  onMenuTrigger: boolean,
): boolean {
  if (onMenuTrigger) return false;
  if ((event.button ?? 0) !== 0) return false;
  return isReorderPointer(event);
}

/** Мышь ушла от точки нажатия дальше порога — это уже перетаскивание. */
export function hasCrossedDragThreshold(startX: number, currentX: number): boolean {
  if (!Number.isFinite(startX) || !Number.isFinite(currentX)) return false;
  return Math.abs(currentX - startX) >= TAB_DRAG_THRESHOLD_PX;
}
