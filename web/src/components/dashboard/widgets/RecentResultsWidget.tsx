/* eslint-disable react-refresh/only-export-components -- виджет и его каталожное описание намеренно живут вместе */
import { FileText } from "lucide-react";

import { WidgetEmptyState } from "@/components/dashboard/WidgetEmptyState";
import type {
  DashboardWidget,
  DashboardWidgetBodyProps,
} from "@/components/dashboard/widget-types";

/**
 * «Артефакты» — что уже готово и можно забрать.
 *
 * Источник — материалы контура и завершённые разговоры. Пока его нет,
 * карточка уводит на экран файлов: там результаты видно целиком.
 */
function RecentResultsBody({ size }: DashboardWidgetBodyProps) {
  return (
    <WidgetEmptyState
      size={size}
      icon={FileText}
      note="Свежие ответы, файлы и отчёты появятся здесь с датой и автором. Пока они лежат в файлах."
    />
  );
}

export const RECENT_RESULTS_WIDGET: DashboardWidget = {
  id: "recent-results",
  title: "Артефакты",
  purpose: "Готовые ответы, файлы и отчёты за последние дни.",
  action: { label: "Открыть файлы", short: "Файлы", to: "/files" },
  Body: RecentResultsBody,
};
