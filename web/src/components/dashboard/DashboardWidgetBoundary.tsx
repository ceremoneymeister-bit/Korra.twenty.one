import { Component, type ReactNode } from "react";

interface DashboardWidgetBoundaryProps {
  children: ReactNode;
  title: string;
  widgetId: string;
}

interface DashboardWidgetBoundaryState {
  failed: boolean;
}

/**
 * Не даёт одной неисправной карточке погасить весь личный дашборд.
 *
 * Источники и жизненный цикл у виджетов будут разными, поэтому граница стоит
 * вокруг каждого содержимого отдельно. Заголовок и оболочка карточки остаются
 * на месте: человек понимает, какой именно виджет не загрузился, а остальные
 * продолжают работать.
 */
export class DashboardWidgetBoundary extends Component<
  DashboardWidgetBoundaryProps,
  DashboardWidgetBoundaryState
> {
  state: DashboardWidgetBoundaryState = { failed: false };

  static getDerivedStateFromError(): DashboardWidgetBoundaryState {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return (
        <div
          role="alert"
          data-widget-error={this.props.widgetId}
          className="flex flex-1 flex-col gap-2"
        >
          <p className="text-sm font-semibold text-[var(--neo-text-primary)]">
            Не удалось показать карточку
          </p>

          <p className="text-sm leading-relaxed text-[var(--neo-text-secondary)]">
            «{this.props.title}» временно недоступна. Остальные карточки
            продолжают работать; попробуйте перезагрузить страницу.
          </p>
        </div>
      );
    }

    return this.props.children;
  }
}
