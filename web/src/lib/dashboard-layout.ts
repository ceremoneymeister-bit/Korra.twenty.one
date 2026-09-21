/**
 * Состав карточек личного дашборда в текущем просмотре.
 *
 * Состояние — только список убранных карточек, а порядок всегда задаёт
 * каталог. Тогда возвращённая карточка встаёт на своё прежнее место, а
 * «стандартный набор» — это пустой список, а не вторая копия каталога,
 * которая живёт своей жизнью и расходится с ним при добавлении виджета.
 *
 * Здесь нет ни React, ни хранения: набор живёт в памяти открытой вкладки,
 * и решения о составе проверяются тестом без DOM. Постоянное хранение —
 * отдельный шаг с серверным контрактом; localStorage тут намеренно не
 * заводится, чтобы он не стал источником правды явочным порядком.
 */

/** Стандартный набор — все карточки каталога на месте. */
export const DEFAULT_HIDDEN_WIDGETS: readonly string[] = [];

/** Карточки, которые сейчас видно, в порядке каталога. */
export function visibleWidgetIds(
  catalog: readonly string[],
  hidden: readonly string[],
): string[] {
  return catalog.filter((id) => !hidden.includes(id));
}

/** Убранные карточки, тоже в порядке каталога: каталог настройки не прыгает. */
export function hiddenWidgetIds(
  catalog: readonly string[],
  hidden: readonly string[],
): string[] {
  return catalog.filter((id) => hidden.includes(id));
}

/**
 * Убрать карточку с дашборда.
 *
 * Незнакомый идентификатор игнорируем: иначе в наборе накапливаются призраки
 * от карточек, которых в каталоге уже нет, и «стандартный набор» перестаёт
 * быть отличим от изменённого.
 */
export function hideWidget(
  catalog: readonly string[],
  hidden: readonly string[],
  id: string,
): string[] {
  if (!catalog.includes(id) || hidden.includes(id)) return [...hidden];
  return [...hidden, id];
}

/** Вернуть карточку на её место в каталоге. */
export function restoreWidget(hidden: readonly string[], id: string): string[] {
  return hidden.filter((item) => item !== id);
}

/** Восстановить стандартный набор. */
export function resetWidgets(): string[] {
  return [...DEFAULT_HIDDEN_WIDGETS];
}

/** Набор не отличается от стандартного — кнопку сброса нажимать незачем. */
export function isDefaultWidgetLayout(hidden: readonly string[]): boolean {
  return hidden.length === 0;
}
