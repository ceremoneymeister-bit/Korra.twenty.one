/**
 * Фон каркасных поверхностей (боковое меню, шапка) с семантическим запасом.
 *
 * Тема может переопределить любую из них через `componentStyles.<bucket>`
 * — ThemeProvider пишет `--component-<bucket>-background` на `:root`
 * (см. `componentStyleVars`). Но обе встроенные темы никаких
 * `componentStyles` не задают, и переменной на странице просто нет.
 *
 * `background: var(--component-sidebar-background)` без запасного значения
 * в этом случае невалиден «на момент вычисления»: объявление не
 * отбрасывается, а вычисляется в `unset`, то есть в `transparent` для
 * `background`. Инлайновый стиль перекрывает класс `bg-background-base`,
 * и выдвижное меню на телефоне становится прозрачным — сквозь него видно
 * переписку (скриншот владельца, 21.09).
 *
 * Отсюда правило: к каждой такой переменной обязателен семантический
 * запас — холст продукта (`--neo-background`), затем палитра темы
 * (`--background-base`), затем светлый холст по умолчанию. Переопределение
 * темы продолжает выигрывать: запас читается, только когда переменной нет.
 */

/** Наборы `componentStyles`, чей фон рисует каркас приложения. */
export type ComponentSurface = "sidebar" | "header" | "footer" | "page";

/** Холст продукта → палитра темы → светлый холст по умолчанию. */
export const COMPONENT_SURFACE_FALLBACK =
  "var(--neo-background, var(--background-base, #e8e8e8))";

/** CSS-значение фона для каркасной поверхности. */
export function componentSurfaceBackground(surface: ComponentSurface): string {
  return `var(--component-${surface}-background, ${COMPONENT_SURFACE_FALLBACK})`;
}
