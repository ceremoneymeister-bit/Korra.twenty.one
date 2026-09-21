import { ArrowRight, Unplug } from "lucide-react";
import { Link } from "react-router";

import type { WidgetSize } from "@/lib/dashboard-layout";
import { cn } from "@/lib/utils";

/**
 * Честное состояние карточки, пока её источник не подключён.
 *
 * Ноль вместо неизвестного значения читается как «всё спокойно», а
 * придуманное число — как факт. Поэтому карточка прямо говорит, чего ей не
 * хватает, и уводит на экран, где эти сведения уже есть сегодня.
 *
 * Плитка 1×1 не растягивается под текст — геометрию задаёт буква, а не
 * содержимое. Поэтому на компактном размере остаётся только заголовок
 * состояния и переход, а объяснение живёт там, где для него есть место.
 */
export interface WidgetEmptyStateProps {
  /** Что появится в карточке, когда источник подключат. Одно предложение. */
  note: string;
  /** Куда пойти за теми же сведениями сейчас. Только существующие экраны. */
  action?: { label: string; to: string };
  size?: WidgetSize;
}

export function WidgetEmptyState({ action, note, size = "m" }: WidgetEmptyStateProps) {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden">
      {/* Значок только дополняет надпись: состояние читается текстом. */}
      <p className="flex items-center gap-2 text-sm font-semibold text-[var(--neo-text-primary)]">
        <Unplug className="size-4 shrink-0" aria-hidden />
        Источник ещё не подключён
      </p>

      {size === "s" ? null : (
        // Плитка не растягивается под текст, поэтому объяснение обрывается по
        // целым строкам с многоточием: обрезанная посередине фраза читается
        // как поломка вёрстки, а не как «дальше есть ещё».
        <p
          data-widget-note
          className={cn(
            "min-h-0 text-sm leading-relaxed text-[var(--neo-text-secondary)]",
            size === "l" ? "line-clamp-6" : "line-clamp-2",
          )}
        >
          {note}
        </p>
      )}

      {action ? (
        <Link
          to={action.to}
          className="mt-auto inline-flex min-h-[44px] w-fit items-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold text-[var(--neo-text-primary)] transition-shadow hover:shadow-[var(--neo-inset-compact)]"
        >
          {action.label}
          <ArrowRight className="size-4 shrink-0" aria-hidden />
        </Link>
      ) : null}
    </div>
  );
}
