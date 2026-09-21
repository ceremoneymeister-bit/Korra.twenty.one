import { WidgetEmptyState } from "@/components/dashboard/WidgetEmptyState";
import type { DashboardWidget } from "@/components/dashboard/widget-types";

/**
 * «Требует внимания» — главная карточка дашборда: с неё начинается день.
 *
 * Источник — очередь подтверждений и сбоев агентов. Пока её здесь нет,
 * карточка не показывает ни нулей, ни примеров: пустая строка «всё спокойно»
 * в этом месте опаснее честного «источник не подключён».
 */
function AttentionBody() {
  return (
    <WidgetEmptyState
      note="Здесь соберутся запросы на подтверждение, ошибки и остановленные поручения. Пока дашборд не читает очередь агентов — эти события приходят прямо в разговоре."
      action={{ label: "Открыть агентов", to: "/agents" }}
    />
  );
}

export const ATTENTION_WIDGET: DashboardWidget = {
  id: "attention",
  title: "Требует внимания",
  purpose: "Подтверждения, ошибки и остановленные поручения, которые ждут вашего решения.",
  wide: true,
  Body: AttentionBody,
};
