/* eslint-disable react-refresh/only-export-components -- виджет и его каталожное описание намеренно живут вместе */
import { CalendarClock } from "lucide-react";

import { WidgetEmptyState } from "@/components/dashboard/WidgetEmptyState";
import type {
  DashboardWidget,
  DashboardWidgetBodyProps,
} from "@/components/dashboard/widget-types";

/**
 * «Ближайшие задачи» — что произойдёт по расписанию без вашего участия.
 *
 * Источник — расписание контура; сегодня его полностью показывает экран
 * «Задачи», поэтому карточка уводит туда, а не повторяет список наполовину.
 */
function UpcomingTasksBody({ size }: DashboardWidgetBodyProps) {
  return (
    <WidgetEmptyState
      size={size}
      icon={CalendarClock}
      note="Ближайшие запуски по расписанию и их прошлый результат целиком видно на экране «Задачи»."
    />
  );
}

export const UPCOMING_TASKS_WIDGET: DashboardWidget = {
  id: "upcoming-tasks",
  title: "Ближайшие задачи",
  purpose: "Что запланировано по расписанию на ближайшие дни.",
  action: { label: "Открыть задачи", short: "Задачи", to: "/cron" },
  Body: UpcomingTasksBody,
};
