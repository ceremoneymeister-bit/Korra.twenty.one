import type { ReactNode } from "react";
import { ArrowUpRight, X } from "lucide-react";
import { Link } from "react-router";
import { Card } from "@nous-research/ui/ui/components/card";
import { ProductButton } from "@/components/ProductButton";
import { cn } from "@/lib/utils";
import type { WidgetSize } from "@/lib/dashboard-layout";
import type { DashboardWidgetAction } from "./widget-types";

/**
 * Оболочка карточки дашборда: заголовок, переход и место под содержимое.
 *
 * Карточка ничего не знает ни про набор, ни про режим настройки — она только
 * показывает своё содержимое и, если ей дали обработчик, кнопку «Убрать».
 * Так виджет можно перенести, убрать из каталога или заменить, не трогая
 * страницу, а страница остаётся про состав, а не про вёрстку карточек.
 *
 * Назначение карточки остаётся в каталоге и подсказке заголовка. Повторять
 * его абзацем на доске не нужно: на закреплённой полосе и большой плитке это
 * отнимает место у самой сводки.
 *
 * Переход карточки тоже живёт здесь, справа от заголовка. Раньше каждый
 * виджет держал его последней строкой содержимого — и на тесной плитке
 * именно он оказывался обрезан, хотя это единственное действие карточки.
 */
export interface DashboardWidgetCardProps {
  /** Название карточки — то же самое, что в каталоге настройки. */
  title: string;
  /** Одно предложение о том, что карточка показывает. */
  purpose: string;
  /** Идентификатор виджета; страница и тесты находят карточку по нему. */
  widgetId: string;
  /** Размер плитки; у закреплённой полосы его нет. */
  size?: WidgetSize;
  /** Куда уйти за полной картиной. */
  action?: DashboardWidgetAction;
  /** Кнопка «Убрать» появляется только в режиме настройки. */
  onRemove?: () => void;
  children: ReactNode;
  className?: string;
}

export function DashboardWidgetCard({
  action,
  children,
  className,
  onRemove,
  purpose,
  size,
  title,
  widgetId,
}: DashboardWidgetCardProps) {
  const compact = size === "s";
  return (
    <Card
      // Плитка задаёт высоту сама, полоса — растёт под содержимое. Без этого
      // различия `min-h-0` съедал у полосы её собственный минимум, и текст
      // вылезал за карточку, хотя места под неё было сколько угодно.
      className={cn("flex flex-col", size && "h-full min-h-0", className)}
      data-widget={widgetId}
    >
      <div
        className={cn(
          "flex shrink-0 flex-col gap-1 px-5 pb-2 pt-3",
          compact && "px-4 pt-2",
        )}
      >
        <div className="flex items-center justify-between gap-2">
          <h3
            className="min-w-0 truncate text-base font-semibold text-[var(--neo-text-primary)]"
            title={purpose}
          >
            {title}
          </h3>

          <div className="flex shrink-0 items-center gap-1">
            {action ? (
              <Link
                to={action.to}
                aria-label={action.label}
                className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center gap-1.5 rounded-[var(--neo-radius-control)] px-3 text-sm font-medium text-[var(--neo-text-secondary)] transition-shadow hover:text-[var(--neo-text-primary)] hover:shadow-[var(--neo-inset-compact)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--neo-accent-line)]"
              >
                <span className={cn(compact && "sr-only")}>{action.short}</span>
                <ArrowUpRight className="size-4 shrink-0" aria-hidden />
              </Link>
            ) : null}

            {onRemove ? (
              <ProductButton
                ghost
                size="sm"
                onClick={onRemove}
                aria-label={`Убрать карточку «${title}»`}
                prefix={<X className="size-4 shrink-0" aria-hidden />}
                className="shrink-0"
              >
                {compact ? null : "Убрать"}
              </ProductButton>
            ) : null}
          </div>
        </div>
      </div>

      <div
        className={cn(
          "flex flex-col px-5 pb-4",
          size && "min-h-0 flex-1",
          compact && "px-4 pb-3",
        )}
      >
        {children}
      </div>
    </Card>
  );
}
