import { ArrowRight, Unplug } from "lucide-react";
import { Link } from "react-router";

/**
 * Честное состояние карточки, пока её источник не подключён.
 *
 * Первый срез дашборда не берёт данные ни из какого API: ноль вместо
 * неизвестного значения читается как «всё спокойно», а придуманное число —
 * как факт. Поэтому карточка прямо говорит, чего ей не хватает, и уводит на
 * экран, где эти сведения уже есть сегодня.
 */
export interface WidgetEmptyStateProps {
  /** Что появится в карточке, когда источник подключат. Одно предложение. */
  note: string;
  /** Куда пойти за теми же сведениями сейчас. Только существующие экраны. */
  action?: { label: string; to: string };
}

export function WidgetEmptyState({ action, note }: WidgetEmptyStateProps) {
  return (
    <div className="flex flex-1 flex-col gap-3">
      {/* Значок только дополняет надпись: состояние читается текстом. */}
      <p className="flex items-center gap-2 text-sm font-semibold text-[var(--neo-text-primary)]">
        <Unplug className="size-4 shrink-0" aria-hidden />
        Источник ещё не подключён
      </p>

      <p className="text-sm leading-relaxed text-[var(--neo-text-secondary)]">
        {note}
      </p>

      {action ? (
        <Link
          to={action.to}
          className="mt-auto inline-flex min-h-11 w-fit items-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold text-[var(--neo-text-primary)] transition-shadow hover:shadow-[var(--neo-inset-compact)]"
        >
          {action.label}
          <ArrowRight className="size-4 shrink-0" aria-hidden />
        </Link>
      ) : null}
    </div>
  );
}
