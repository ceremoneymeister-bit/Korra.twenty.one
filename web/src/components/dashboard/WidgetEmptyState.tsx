import type { LucideIcon } from "lucide-react";

import { FittedText } from "@/components/dashboard/FittedText";
import type { WidgetSize } from "@/lib/dashboard-layout";
import { cn } from "@/lib/utils";

/**
 * Честное состояние карточки, пока её источник не подключён.
 *
 * Ноль вместо неизвестного значения читается как «всё спокойно», а
 * придуманное число — как факт. Поэтому карточка показывает знак своей темы и
 * одну строку о том, чего ей не хватает, а переход к тем же сведениям живёт в
 * шапке карточки — там он не соревнуется за высоту с текстом.
 */
export interface WidgetEmptyStateProps {
  /** Знак темы карточки: он дополняет надпись, а не заменяет её. */
  icon: LucideIcon;
  /** Что появится в карточке, когда источник подключат. Одно предложение. */
  note: string;
  size?: WidgetSize;
}

export function WidgetEmptyState({ icon: Icon, note, size }: WidgetEmptyStateProps) {
  // Полоса над сеткой растёт под содержимое: укорачивать там нечего, а
  // измерять — значит замкнуть наблюдателя сам на себя.
  const pinned = size === undefined;
  return (
    <div
      className={cn(
        "flex flex-col gap-2",
        !pinned && "min-h-0 flex-1 overflow-hidden",
      )}
    >
      {/* На плитке 1×1 эта строка не умещается ни при каком сокращении, а
          «Источник ещё не подкл…» — не то, ради чего карточку открыли. Поэтому
          она переносится, а знак темы держится первой строки. */}
      <p className="korra-widget-line flex shrink-0 items-start gap-2 text-sm font-semibold text-[var(--neo-text-primary)]">
        <Icon className="mt-[3px] size-4 shrink-0" aria-hidden />
        <span className="min-w-0 line-clamp-2">Источник ещё не подключён</span>
      </p>

      {pinned ? (
        <p
          data-widget-note
          className="korra-widget-line text-sm text-[var(--neo-text-secondary)]"
        >
          {note}
        </p>
      ) : (
        <FittedText text={note} className="text-[var(--neo-text-secondary)]" />
      )}
    </div>
  );
}
