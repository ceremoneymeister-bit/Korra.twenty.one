/* eslint-disable react-refresh/only-export-components -- виджет и его каталожное описание намеренно живут вместе */
import { TriangleAlert } from "lucide-react";

import { WidgetEmptyState } from "@/components/dashboard/WidgetEmptyState";
import type {
  DashboardWidget,
  DashboardWidgetBodyProps,
} from "@/components/dashboard/widget-types";

/**
 * «Требует внимания» — главная карточка дашборда: с неё начинается день.
 *
 * Источник — очередь подтверждений и сбоев агентов. Пока её здесь нет,
 * карточка не показывает ни нулей, ни примеров: пустая строка «всё спокойно»
 * в этом месте опаснее честного «источник не подключён».
 */
function AttentionBody({ size }: DashboardWidgetBodyProps) {
  return (
    <WidgetEmptyState
      size={size}
      icon={TriangleAlert}
      note="Подтверждения, ошибки и остановленные поручения пока приходят прямо в разговоре."
    />
  );
}

export const ATTENTION_WIDGET: DashboardWidget = {
  id: "attention",
  title: "Требует внимания",
  purpose: "Подтверждения, ошибки и остановленные поручения, которые ждут вашего решения.",
  pinned: true,
  action: { label: "Открыть агентов", short: "Агенты", to: "/agents" },
  Body: AttentionBody,
};
