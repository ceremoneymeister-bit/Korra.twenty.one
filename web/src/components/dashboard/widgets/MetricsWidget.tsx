/* eslint-disable react-refresh/only-export-components -- виджет и его каталожное описание намеренно живут вместе */
import { WidgetEmptyState } from "@/components/dashboard/WidgetEmptyState";
import type {
  DashboardWidget,
  DashboardWidgetBodyProps,
} from "@/components/dashboard/widget-types";

/**
 * «Мои показатели» — несколько чисел, которые владелец выбирает сам.
 *
 * Карточка намеренно остаётся без действия: подходящего экрана «мои
 * показатели» в панели ещё нет, а ссылка «в никуда» хуже её отсутствия.
 */
function MetricsBody({ size }: DashboardWidgetBodyProps) {
  return (
    <WidgetEmptyState size={size} note="Состав показателей задаёте вы сами — считать нечего, пока не выбрано, что именно считать. Ноль здесь читался бы как «всё спокойно», поэтому его не рисуем." />
  );
}

export const METRICS_WIDGET: DashboardWidget = {
  id: "metrics",
  title: "Мои показатели",
  purpose: "Несколько чисел о работе, за которыми вы следите постоянно.",
  Body: MetricsBody,
};
