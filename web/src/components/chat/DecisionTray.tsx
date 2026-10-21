/**
 * «Ваши ответы» над полем ввода: решения по документам из ленты копятся здесь и уходят агенту одним сообщением.
 *
 * Раньше каждое «Согласовать» уходило сразу отдельным сообщением и запускало агента — на три документа три
 * запуска, а пока агент отвечал, следующее решение принять было нельзя. Выбирать здесь можно и во время ответа,
 * ждёт только отправка.
 */
import { Button } from "@nous-research/ui/ui/components/button";
import { X } from "lucide-react";

import { shortName } from "@/lib/chat-artifacts";
import type { DecisionKind, DraftDecision } from "@/lib/artifact-decisions";
import { cn } from "@/lib/utils";

const CHOICES: [DecisionKind, string][] = [["approve", "Согласовать"], ["change", "Изменить"], ["defer", "Отложить"]];

export function DecisionTray({ drafts, onChange, onRemove, onSend, busy }: {
  drafts: DraftDecision[];
  onChange: (d: DraftDecision) => void;
  onRemove: (path: string) => void;
  onSend: () => void;
  /** Агент ещё отвечает — отправить нельзя, выбирать можно. */
  busy?: boolean;
}) {
  if (!drafts.length) return null;
  const needNote = drafts.filter(d => d.kind === "change" && !d.note?.trim()).length;
  return (
    <section aria-label="Ваши ответы по документам" className="mx-3 mb-2 rounded-2xl p-3 shadow-[var(--neo-depth-1,0_1px_3px_rgba(0,0,0,.12))] font-sans normal-case tracking-normal">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">Ваши ответы ({drafts.length})</h2>
        <span className="text-[11px] text-[var(--neo-text-secondary)]">уйдут агенту одним сообщением</span>
      </div>
      <ol className="flex flex-col gap-2">
        {drafts.map((d, i) => (
          <li key={d.path} className="@container grid gap-1.5 rounded-xl bg-[var(--neo-surface,transparent)] p-2">
            {/* Узкая панель (телефон): имя и «×» на первой строке, выбор — на второй, на всю ширину.
                Широкая: всё в одну строку. Ширину берём у самой панели, а не у экрана — рядом бывает боковая колонка. */}
            <div className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-2 gap-y-1.5 @[32rem]:grid-cols-[auto_minmax(0,1fr)_auto_auto]">
              <span className="text-xs text-[var(--neo-text-secondary)]">{i + 1}.</span>
              <span className="min-w-0 truncate text-sm font-medium" title={d.path}>{shortName(d.name, 44)}</span>
              <div role="radiogroup" aria-label={`Решение по «${d.name}»`}
                className="order-last col-span-3 flex gap-1 @[32rem]:order-none @[32rem]:col-span-1">
                {CHOICES.map(([kind, label]) => (
                  <button key={kind} type="button" role="radio" aria-checked={d.kind === kind}
                    onClick={() => onChange({ ...d, kind })}
                    className={cn("flex-1 cursor-pointer rounded-lg border-0 px-2 py-1.5 text-xs @[32rem]:flex-none @[32rem]:py-1",
                      d.kind === kind ? "bg-[var(--neo-accent)] font-semibold text-[var(--neo-accent-foreground,#1f1f1f)]"
                        : "bg-transparent text-[var(--neo-text-secondary)] hover:shadow-[var(--neo-inset-compact)]")}>
                    {label}
                  </button>
                ))}
              </div>
              <button type="button" aria-label={`Убрать «${d.name}» из ответов`} onClick={() => onRemove(d.path)}
                className="cursor-pointer border-0 bg-transparent p-1 text-[var(--neo-text-secondary)]">
                <X size={14} aria-hidden />
              </button>
            </div>
            {d.kind === "change" && (
              <input value={d.note ?? ""} onChange={e => onChange({ ...d, note: e.target.value })} autoFocus={!d.note}
                placeholder="Что поправить — например, «срок 30 дней»" aria-label={`Что поправить в «${d.name}»`}
                className="rounded-lg border border-border bg-background px-2 py-1.5 text-sm" />
            )}
          </li>
        ))}
      </ol>
      <div className="mt-2 flex items-center justify-end gap-3">
        <span role="status" className="text-[11px] text-[var(--neo-text-secondary)]">
          {needNote ? "Напишите, что поправить, — или отправьте как есть" : busy ? "Отправится, когда агент закончит ответ" : ""}
        </span>
        <Button type="button" size="sm" disabled={busy} onClick={onSend}>
          {`Отправить ответы (${drafts.length})`}
        </Button>
      </div>
    </section>
  );
}
