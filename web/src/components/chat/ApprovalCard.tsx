/**
 * Вопрос человеку по артефакту — карточка вместо трёх кнопок в строку.
 *
 * Протокол не меняется ни на байт: наверх уходит тот же
 * `onDecision(kind, item)` из ChatArtifact, те же три исхода
 * (`approve` / `change` / `defer`), и решение по-прежнему улетает обычным
 * сообщением в чат. Здесь только форма: один вопрос, варианты выбора и
 * подвал с «Пропустить» и лаймовым «Продолжить», как в эталоне владельца.
 *
 * Почему выбор не отправляется сразу по клику: «Согласовать» — необратимая
 * отправка в чат, а эталонный автопереход через 480 мс превратил бы промах
 * мышью в согласование. Вариант выделяется, отправляет подвал.
 */

import { useState } from "react";
import { Check, Clock, Pencil } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";

import { cn } from "@/lib/utils";
// Переходы обоих компонентов чата живут в одном файле — там же лежит
// единственный на всю папку блок prefers-reduced-motion.
import "./agent-trace.css";

/** Тот же союз решений, что и раньше, — просто объявлен рядом с карточкой. */
export type ApprovalChoice = "approve" | "change" | "defer";

export interface ApprovalOption {
  value: ApprovalChoice;
  label: string;
  hint?: string;
  icon: React.ReactNode;
}

const OPTIONS: ApprovalOption[] = [
  {
    value: "approve",
    label: "Согласовать",
    hint: "Ответ уйдёт агенту, он обновит статус",
    icon: <Check size={13} aria-hidden />,
  },
  {
    value: "change",
    label: "Изменить",
    hint: "Открою поле — скажете, что поправить",
    icon: <Pencil size={13} aria-hidden />,
  },
  {
    value: "defer",
    label: "Отложить",
    hint: "Вернёмся к этому позже",
    icon: <Clock size={13} aria-hidden />,
  },
];

/** Отметка о принятом решении — вопрос уступает ей место. */
export function ApprovalSettled({ decision }: { decision: "approve" | "defer" }) {
  return (
    <div className="mt-1.5 flex items-center gap-1.5 font-sans text-[11px] normal-case tracking-normal text-[var(--neo-text-secondary)]">
      <Check size={12} aria-hidden className="text-[var(--neo-accent)]" />
      {decision === "approve" ? "Согласовано" : "Отложено"}
    </div>
  );
}

export interface ApprovalCardProps {
  /** Один вопрос за раз — по эталону. */
  question: string;
  /** Идёт ответ агента: отправить решение сейчас нельзя. */
  busy?: boolean;
  /** Ошибка доставки предыдущей попытки. */
  failed?: boolean;
  onSubmit: (choice: ApprovalChoice) => void;
  onSkip: () => void;
}

export function ApprovalCard({
  question,
  busy,
  failed,
  onSubmit,
  onSkip,
}: ApprovalCardProps) {
  const [choice, setChoice] = useState<ApprovalChoice | null>(null);

  return (
    <div
      className={cn(
        "mt-2 max-w-[26rem] rounded-[var(--neo-radius-card)] p-3",
        "bg-[var(--neo-surface)] shadow-[var(--neo-depth-2)]",
        "font-sans normal-case tracking-normal",
      )}
      role="group"
      aria-label="Решение по артефакту"
    >
      <p className="mb-2 text-xs leading-snug text-[var(--neo-text-primary)]">
        {question}
      </p>

      <div className="flex flex-col gap-1">
        {OPTIONS.map((option) => {
          const selected = choice === option.value;
          return (
            <button
              key={option.value}
              type="button"
              disabled={busy}
              aria-pressed={selected}
              onClick={() => setChoice(option.value)}
              className={cn(
                "korra-approval__option",
                "flex w-full items-center gap-2 rounded-[var(--neo-radius-control)] px-2 py-1.5",
                "border-0 bg-transparent text-left outline-0",
                "text-xs text-[var(--neo-text-secondary)]",
                busy ? "cursor-not-allowed opacity-60" : "cursor-pointer",
                selected &&
                  "shadow-[var(--neo-inset-compact)] text-[var(--neo-text-primary)]",
              )}
            >
              <span className="flex-none">{option.icon}</span>
              <span className="font-medium">{option.label}</span>
              {option.hint && (
                <span className="min-w-0 flex-1 truncate text-[10.5px] text-[var(--neo-text-secondary)]">
                  {option.hint}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {failed && (
        <p className="mt-2 text-[11px] text-[var(--destructive)]">
          Решение не отправилось — попробуйте ещё раз
        </p>
      )}

      <div className="mt-2.5 flex items-center justify-end gap-1.5">
        <Button
          type="button"
          size="sm"
          ghost
          onClick={onSkip}
          className="min-h-0 normal-case tracking-normal"
        >
          Пропустить
        </Button>
        <Button
          type="button"
          size="sm"
          disabled={busy || choice === null}
          onClick={() => choice && onSubmit(choice)}
          className="min-h-0 normal-case tracking-normal"
        >
          Продолжить
        </Button>
      </div>
    </div>
  );
}
