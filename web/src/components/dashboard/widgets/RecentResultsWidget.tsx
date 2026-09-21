/* eslint-disable react-refresh/only-export-components -- виджет и его каталожное описание намеренно живут вместе */
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
      note="Здесь появятся свежие ответы, файлы и отчёты — с датой и автором. Пока дашборд не читает материалы и историю разговоров."
      action={{ label: "Открыть файлы", to: "/files" }}
    />
  );
}

export const RECENT_RESULTS_WIDGET: DashboardWidget = {
  id: "recent-results",
  title: "Артефакты",
  purpose: "Готовые ответы, файлы и отчёты за последние дни.",
  Body: RecentResultsBody,
};
