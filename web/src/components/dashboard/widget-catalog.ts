import type { WidgetCatalogShape } from "@/lib/dashboard-layout";
import type { DashboardWidget } from "./widget-types";
import { AGENTS_WIDGET } from "./widgets/AgentsWidget";
import { ATTENTION_WIDGET } from "./widgets/AttentionWidget";
import { METRICS_WIDGET } from "./widgets/MetricsWidget";
import { RECENT_RESULTS_WIDGET } from "./widgets/RecentResultsWidget";
import { UPCOMING_TASKS_WIDGET } from "./widgets/UpcomingTasksWidget";

/**
 * Каталог встроенных виджетов дашборда.
 *
 * Отдельно от страницы намеренно: оболочка отвечает за сетку и режим
 * настройки, каталог — за то, какие карточки вообще бывают. Порядок здесь —
 * порядок доски по умолчанию: возвращённая карточка встаёт на своё место, а
 * не в конец сетки.
 *
 * Сторонних виджетов тут нет и не появится «сами собой»: каталог собирается
 * из импортов на сборке, поэтому дашборд не исполняет чужой JavaScript.
 */
export const DASHBOARD_WIDGETS: readonly DashboardWidget[] = [
  ATTENTION_WIDGET,
  AGENTS_WIDGET,
  METRICS_WIDGET,
  UPCOMING_TASKS_WIDGET,
  RECENT_RESULTS_WIDGET,
];

/** Порядок каталога в том виде, в каком его читают функции состава. */
export const DASHBOARD_WIDGET_IDS: readonly string[] = DASHBOARD_WIDGETS.map(
  (widget) => widget.id,
);

/**
 * Форма каталога для правил раскладки — тот же контракт, что у сервера.
 *
 * Важно, что список закреплённых карточек берётся из самих описаний, а не
 * записан вторым списком: иначе карточка перестаёт быть полосой в одном
 * месте и остаётся ею в другом.
 */
export const DASHBOARD_CATALOG: WidgetCatalogShape = {
  ids: DASHBOARD_WIDGET_IDS,
  pinned: DASHBOARD_WIDGETS.filter((widget) => widget.pinned).map((widget) => widget.id),
};

export function findWidget(id: string): DashboardWidget | undefined {
  return DASHBOARD_WIDGETS.find((widget) => widget.id === id);
}
