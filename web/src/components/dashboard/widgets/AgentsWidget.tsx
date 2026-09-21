/* eslint-disable react-refresh/only-export-components -- виджет и его каталожное описание намеренно живут вместе */
import { WidgetEmptyState } from "@/components/dashboard/WidgetEmptyState";
import type { DashboardWidget } from "@/components/dashboard/widget-types";

/**
 * «Агенты» — кто из агентов контура занят, а кто свободен.
 *
 * Источник — профили контура и их занятость. Список профилей панель уже
 * знает, но занятость без неё читается как «никто не работает», поэтому
 * карточка ждёт полного источника, а не половины.
 */
function AgentsBody() {
  return (
    <WidgetEmptyState
      note="Здесь будут ваши агенты: кто сейчас работает, кто ждёт поручения и когда отвечал в последний раз. Пока дашборд не читает их состояние."
      action={{ label: "Открыть агентов", to: "/agents" }}
    />
  );
}

export const AGENTS_WIDGET: DashboardWidget = {
  id: "agents",
  title: "Агенты",
  purpose: "Кто из ваших агентов сейчас работает, а кто ждёт поручения.",
  Body: AgentsBody,
};
