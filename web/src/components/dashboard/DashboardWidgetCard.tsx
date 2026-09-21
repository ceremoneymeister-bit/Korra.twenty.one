import type { ReactNode } from "react";
import { X } from "lucide-react";
import { Card } from "@nous-research/ui/ui/components/card";
import { ProductButton } from "@/components/ProductButton";
import { cn } from "@/lib/utils";
import type { WidgetSize } from "@/lib/dashboard-layout";

/**
 * Оболочка карточки дашборда: заголовок, назначение и место под содержимое.
 *
 * Карточка ничего не знает ни про набор, ни про режим настройки — она только
 * показывает своё содержимое и, если ей дали обработчик, кнопку «Убрать».
 * Так виджет можно перенести, убрать из каталога или заменить, не трогая
 * страницу, а страница остаётся про состав, а не про вёрстку карточек.
 *
 * Высоту плитки задаёт сетка, а не содержимое: на компактном размере
 * назначение уходит в подсказку заголовка, иначе одно длинное предложение
 * вытеснило бы из плитки 1×1 то, ради чего её открыли.
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
  size,
  title,
  widgetId,
}: DashboardWidgetCardProps) {
  const compact = size === "s";
  // Назначение карточки — текст каталога. В плитке он полезен там, где есть
  // место: на S и M он вытесняет то, ради чего карточку открыли, а прочитать
  // его можно в «Добавить виджет». На полосе и на L он остаётся.
  const showPurpose = size === undefined || size === "l";
  return (
    <Card
      className={cn("flex h-full min-h-0 flex-col", className)}
      data-widget={widgetId}
    >
      <div
        className={cn(
          "flex shrink-0 items-start justify-between gap-3 px-5 pb-3 pt-5",
          compact && "px-4 pb-2 pt-4",
        )}
      >
        <div className="min-w-0">
          <h3
            className="truncate text-base font-semibold text-[var(--neo-text-primary)]"
            title={showPurpose ? undefined : purpose}
          >
            {title}
          </h3>

          {showPurpose ? (
            <p className="mt-1 text-sm leading-relaxed text-[var(--neo-text-secondary)]">
              {purpose}
            </p>
          ) : null}
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
            {compact ? null : "Убрать"}
          </ProductButton>
        ) : null}
      </div>

      <div
        className={cn(
          "flex min-h-0 flex-1 flex-col px-5 pb-5",
          compact && "px-4 pb-4",
        )}
      >
        {children}
      </div>
    </Card>
  );
}
