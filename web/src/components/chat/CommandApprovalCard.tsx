/**
 * Одобрение опасной команды — карточка прямо в переписке.
 *
 * Пока карточка не отвечена, ход агента физически стоит: его поток
 * заблокирован в ожидании решения (`tools/approval.py`), и сам он не
 * продолжится и не «догадается». Поэтому карточка обязана сказать три вещи и
 * ничего сверх: что за команда, чем она опасна и какие ответы принимает
 * движок. Варианты не выдуманы здесь — их присылает сервер в поле `choices`
 * (`_approval_event_choices`), и если разрешать навсегда нельзя, такого
 * варианта в карточке не будет.
 *
 * Почему «Разрешить всегда» переспрашивает, а остальные ответы уходят по
 * первому клику: разовое разрешение и отказ живут один вызов, а «всегда»
 * пишет правило в `command_allowlist` конфига контура — оно переживает
 * перезапуск и действует во ВСЕХ каналах агента, не только в панели. Промах
 * мышью там навсегда снимает вопрос с целого класса команд. Остальным
 * ответам вторая кнопка была бы трением: агент стоит и ждёт.
 *
 * Для `execute_code` варианта «всегда» не бывает вовсе: его ключ покрывает не
 * команду, а инструмент целиком, и один клик снял бы гейт с любого питона
 * навсегда и везде. Список вариантов приходит с сервера, здесь он не
 * достраивается — см. `_chat_approval_event` в api_server.py.
 */

import { useState } from "react";
import { Ban, Check, Clock, Infinity as InfinityIcon, ShieldCheck } from "lucide-react";
import type { ComponentType } from "react";

import type { ApprovalChoiceValue } from "@/lib/chat-types";
import { cn } from "@/lib/utils";
// Переходы элементов чата живут в одном файле — там же единственный на всю
// папку блок prefers-reduced-motion.
import "./agent-trace.css";

type IconComponent = ComponentType<{ size?: number; "aria-hidden"?: boolean }>;

interface ChoiceMeta {
  label: string;
  hint: string;
  icon: IconComponent;
  /** Ответ необратим — спрашиваем второй раз. */
  confirm?: boolean;
  danger?: boolean;
}

const CHOICE_META: Record<ApprovalChoiceValue, ChoiceMeta> = {
  once: {
    label: "Разрешить",
    hint: "Только эту команду и только сейчас",
    icon: Check,
  },
  session: {
    label: "Разрешить до конца чата",
    hint: "Такие же команды в этой сессии больше не спросят",
    icon: Clock,
  },
  always: {
    label: "Разрешить всегда",
    hint: "Запишется в постоянный allowlist контура: подействует во всех каналах, не только в панели",
    icon: InfinityIcon,
    confirm: true,
  },
  deny: {
    label: "Отклонить",
    hint: "Агент получит отказ и не станет искать обход",
    icon: Ban,
    danger: true,
  },
};

const SETTLED_LABEL: Record<ApprovalChoiceValue, string> = {
  once: "Разрешено один раз",
  session: "Разрешено до конца чата",
  always: "Разрешено всегда",
  deny: "Отклонено",
};

export interface CommandApprovalCardProps {
  /** Команда как её показывает движок: секреты уже вырезаны на сервере. */
  command?: string;
  /** Чем именно опасна команда. */
  description?: string;
  /** Варианты ответа, которые принимает движок для этого запроса. */
  choices: ApprovalChoiceValue[];
  /** Решение уже отправляется — второй клик не нужен. */
  sending?: boolean;
  /** Ответ принят: карточка уступает место отметке об исходе. */
  decision?: ApprovalChoiceValue;
  /** Отвечать больше некому: ход кончился или истекло время ожидания. */
  expired?: boolean;
  /** Что пошло не так у прошлой попытки. */
  error?: string;
  /** Пояснение к принятому решению — например, что ответ придёт в историю. */
  note?: string;
  onDecide: (choice: ApprovalChoiceValue) => void;
}

export function CommandApprovalCard({
  command,
  description,
  choices,
  sending,
  decision,
  expired,
  error,
  note,
  onDecide,
}: CommandApprovalCardProps) {
  // Какой необратимый ответ ждёт подтверждения. Сбрасывается выбором другого.
  const [confirming, setConfirming] = useState<ApprovalChoiceValue | null>(null);

  const settled = decision !== undefined;
  const trimmedCommand = command?.trim() ?? "";
  const trimmedDescription = description?.trim() ?? "";

  return (
    <div
      className={cn(
        "korra-trace__row max-w-[34rem] rounded-[var(--neo-radius-card)] p-3",
        "bg-[var(--neo-surface)] shadow-[var(--neo-depth-2)]",
        "font-sans normal-case tracking-normal",
      )}
      role="group"
      aria-label="Решение по команде агента"
    >
      <div className="mb-2 flex items-start gap-2">
        <ShieldCheck
          size={14}
          aria-hidden
          className="mt-0.5 flex-none text-[var(--neo-text-secondary)]"
        />
        <div className="min-w-0">
          <p className="text-xs leading-snug font-medium text-[var(--neo-text-primary)]">
            Агент просит разрешение на команду
          </p>
          {trimmedDescription && (
            <p className="mt-0.5 text-[11px] leading-snug text-[var(--neo-text-secondary)]">
              {trimmedDescription}
            </p>
          )}
        </div>
      </div>

      {trimmedCommand && (
        <pre
          className={cn(
            "mb-2 max-h-32 overflow-auto rounded-[var(--neo-radius-control)] px-2.5 py-2",
            "bg-[var(--neo-surface)] shadow-[var(--neo-inset-compact)]",
            "font-mono text-[10.5px] leading-relaxed whitespace-pre-wrap break-words",
            "text-[var(--neo-text-secondary)]",
          )}
        >
          {trimmedCommand}
        </pre>
      )}

      {settled ? (
        <div>
          <p className="flex items-center gap-1.5 text-[11px] text-[var(--neo-text-secondary)]">
            {decision === "deny" ? (
              <Ban size={12} aria-hidden />
            ) : (
              <Check size={12} aria-hidden />
            )}
            {SETTLED_LABEL[decision]}
          </p>
          {note && (
            <p className="mt-1 text-[10.5px] leading-snug text-[var(--neo-text-secondary)]">
              {note}
            </p>
          )}
        </div>
      ) : expired ? (
        <p className="text-[11px] leading-snug text-[var(--neo-text-secondary)]">
          Агент больше не ждёт ответа: ход закончился или истекло время.
        </p>
      ) : (
        <div className="flex flex-col gap-1">
          {choices.map((choice) => {
            const meta = CHOICE_META[choice];
            if (!meta) return null;
            const Icon = meta.icon;
            const awaiting = confirming === choice;
            return (
              <button
                key={choice}
                type="button"
                disabled={sending}
                onClick={() => {
                  if (meta.confirm && !awaiting) {
                    setConfirming(choice);
                    return;
                  }
                  setConfirming(null);
                  onDecide(choice);
                }}
                className={cn(
                  "korra-approval__option",
                  "flex w-full items-center gap-2 rounded-[var(--neo-radius-control)] px-2 py-1.5",
                  "border-0 bg-transparent text-left outline-0",
                  "text-xs text-[var(--neo-text-secondary)]",
                  sending ? "cursor-not-allowed opacity-60" : "cursor-pointer",
                  awaiting &&
                    "shadow-[var(--neo-inset-compact)] text-[var(--neo-text-primary)]",
                )}
              >
                <span className="flex-none">
                  <Icon size={13} aria-hidden />
                </span>
                <span
                  className={cn(
                    "flex-none font-medium",
                    meta.danger && "text-[var(--destructive)]",
                  )}
                >
                  {awaiting ? `${meta.label} — точно?` : meta.label}
                </span>
                <span className="min-w-0 flex-1 truncate text-[10.5px] text-[var(--neo-text-secondary)]">
                  {awaiting ? "Нажмите ещё раз, чтобы записать правило" : meta.hint}
                </span>
              </button>
            );
          })}
        </div>
      )}

      {error && !settled && (
        <p className="mt-2 text-[11px] text-[var(--destructive)]">{error}</p>
      )}
    </div>
  );
}
