import type { ReactNode } from "react";
import { X } from "lucide-react";
import { Card } from "@nous-research/ui/ui/components/card";
import { ProductButton } from "@/components/ProductButton";
import { cn } from "@/lib/utils";

/**
 * Оболочка карточки дашборда: заголовок, назначение и место под содержимое.
 *
 * Карточка ничего не знает ни про набор, ни про режим настройки — она только
 * показывает своё содержимое и, если ей дали обработчик, кнопку «Убрать».
 * Так виджет можно перенести, убрать из каталога или заменить, не трогая
 * страницу, а страница остаётся про состав, а не про вёрстку карточек.
 */
export interface DashboardWidgetCardProps {
  /** Название карточки — то же самое, что в каталоге настройки. */
  title: string;
  /** Одно предложение о том, что карточка показывает. */
  purpose: string;
  /** Идентификатор виджета; страница и тесты находят карточку по нему. */
  widgetId: string;
  /** Кнопка «Убрать» появляется только в режиме настройки. */
  onRemove?: () => void;
  children: ReactNode;
  className?: string;
}

export function DashboardWidgetCard({
  children,
  className,
  onRemove,
  purpose,
  title,
  widgetId,
}: DashboardWidgetCardProps) {
  return (
    <Card
      className={cn("flex h-full flex-col", className)}
      data-widget={widgetId}
    >
      <div className="flex items-start justify-between gap-3 px-5 pb-3 pt-5">
        <div className="min-w-0">
          <h3 className="text-base font-semibold text-[var(--neo-text-primary)]">
            {title}
          </h3>

          <p className="mt-1 text-sm leading-relaxed text-[var(--neo-text-secondary)]">
            {purpose}
          </p>
        </div>

        {onRemove ? (
          <ProductButton
            ghost
            size="sm"
            onClick={onRemove}
            aria-label={`Убрать карточку «${title}»`}
            prefix={<X className="size-4 shrink-0" aria-hidden />}
            className="shrink-0"
          >
            Убрать
          </ProductButton>
        ) : null}
      </div>

      <div className="flex flex-1 flex-col px-5 pb-5">{children}</div>
    </Card>
  );
}
