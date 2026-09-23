import type { WidgetCatalogShape } from "@/lib/dashboard-layout";
import type { DashboardState } from "@/lib/dashboard-state";
import type { DashboardWidget } from "./widget-types";
import { AGENTS_WIDGET } from "./widgets/AgentsWidget";
import { ATTENTION_WIDGET } from "./widgets/AttentionWidget";
import { CALENDAR_WIDGET } from "./widgets/CalendarWidget";
import { ICLOUD_CALENDAR_WIDGET } from "./widgets/ICloudCalendarWidget";
import { CODEX_QUOTA_WIDGET } from "./widgets/CodexQuotaWidget";
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
  // 0.21.13: встречи Google, задачи с датой и запуски агентов. Последним —
  // так же, как в серверном каталоге (`korra_cli/dashboard_layout.py`).
  CALENDAR_WIDGET,
  CODEX_QUOTA_WIDGET,
  ICLOUD_CALENDAR_WIDGET,
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

/**
 * Карточки, которым на этой установке нет места (например, квота Codex без
 * подписки). Их не видно ни на доске, ни в каталоге, но сохранённая раскладка
 * их помнит: вернётся источник — вернётся и карточка на прежнее место.
 */
export function unavailableWidgetIds(state: DashboardState | null): string[] {
  return DASHBOARD_WIDGETS.filter((widget) => widget.isAvailable && !widget.isAvailable(state)).map(
    (widget) => widget.id,
  );
}
