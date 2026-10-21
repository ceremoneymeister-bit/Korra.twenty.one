import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { startVisibleInterval } from "@/lib/visible-interval";
import { ChevronDown, Inbox } from "lucide-react";

import { CommandApprovalCard } from "@/components/chat/CommandApprovalCard";
import { DecisionAge } from "@/components/chat/DecisionAge";
import {
  fetchEffectDecisions,
  sendApprovalDecision,
} from "@/lib/chat-approvals";
import type {
  ApprovalChoiceValue,
  EffectDecisionStatus,
  SSEApprovalRequestData,
} from "@/lib/chat-types";
import { cn } from "@/lib/utils";

const POLL_MS = 5_000;

const STATUS_LABEL: Record<EffectDecisionStatus, string> = {
  pending: "Ждёт решения",
  approved: "Разрешено",
  denied: "Отклонено",
  executing: "Выполняется",
  succeeded: "Выполнено",
  failed: "Не выполнено",
  unknown: "Исход неизвестен",
  expired: "Срок отправки истёк",
  superseded: "Заменён новым результатом",
  needs_review: "Старая очередь",
};

interface EffectDecisionCenterProps {
  profile?: string;
  active?: boolean;
  currentSessionId?: string | null;
  onCurrentDecision?: (
    requestId: string,
    choice: ApprovalChoiceValue,
  ) => Promise<boolean>;
  /** На телефоне полоса решений видна, только когда решения есть: строка
   *  «Решения · нет ожидающих» занимала место под шапкой каждого агента.
   *  Десктоп не меняется. */
  hideWhenSettledOnPhone?: boolean;
  /** Счётчик «раскрыть решения» (кнопка «Решить» в листе работ). */
  expandRequest?: number;
}

function shortTarget(item: SSEApprovalRequestData): string {
  const first = (item.command ?? "").split("\n", 1)[0]?.trim();
  return first || (
    item.decision_kind === "payment"
      ? "Оплата"
      : item.decision_kind === "automation_recipients"
        ? "Получатели автоматизации"
        : "Внешняя отправка"
  );
}

/** Profile-wide durable decisions, intentionally outside one chat transcript. */
export function EffectDecisionCenter({
  profile,
  active = true,
  currentSessionId,
  onCurrentDecision,
  hideWhenSettledOnPhone = false,
  expandRequest = 0,
}: EffectDecisionCenterProps) {
  const [items, setItems] = useState<SSEApprovalRequestData[]>([]);
  const [open, setOpen] = useState(expandRequest > 0);
  const handledExpand = useRef(expandRequest);
  useEffect(() => {
    if (handledExpand.current === expandRequest) return;
    handledExpand.current = expandRequest;
    setOpen(true);
  }, [expandRequest]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");

  const refresh = useCallback(async (isCurrent: () => boolean = () => true) => {
    let unavailable: string[] = [];
    const next = await fetchEffectDecisions(profile, names => { unavailable = names; });
    if (!isCurrent()) return;
    setItems(next);
    setPollError(unavailable.length
      ? `Недоступны профили: ${unavailable.join(", ")}. Список решений неполный; ответы временно недоступны.`
      : "");
  }, [profile]);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const load = async () => {
      try {
        await refresh(() => !cancelled);
      } catch {
        if (!cancelled) setPollError("Решения этого профиля временно недоступны. Список не подтверждён; повторно отправлять действия не нужно.");
      }
    };
    void load();
    // Скрытая вкладка не опрашивает; возврат на неё обновляет сразу.
    const stop = startVisibleInterval(() => void load(), POLL_MS);
    return () => {
      cancelled = true;
      stop();
    };
  }, [active, refresh]);

  const pending = useMemo(
    () => items.filter((item) => item.effect_status === "pending"),
    [items],
  );
  const legacy = useMemo(
    () => items.filter((item) => item.effect_status === "needs_review"), [items],
  );
  const history = useMemo(
    () => items.filter((item) => item.effect_status !== "pending" && item.effect_status !== "needs_review"),
    [items],
  );

  const decide = useCallback(
    async (item: SSEApprovalRequestData, choice: ApprovalChoiceValue) => {
      const sourceSessionId = item.source_session_id ?? "";
      if (!sourceSessionId || busyId) return;
      setBusyId(item.request_id);
      setError("");
      let ok = false;
      try {
        if (sourceSessionId === currentSessionId && onCurrentDecision) {
          ok = await onCurrentDecision(item.request_id, choice);
        } else {
          const result = await sendApprovalDecision({
            sessionId: sourceSessionId,
            requestId: item.request_id,
            choice,
            ...(profile ? { profile } : {}),
          });
          ok = result.ok;
          if (!result.ok) setError(result.error);
        }
      } catch {
        setError("Не удалось передать решение. Повторите после проверки связи.");
      }
      try {
        await refresh();
      } catch {
        if (ok) {
          setError("Решение принято, но список пока не обновился. Повторно отправлять не нужно.");
        }
      } finally {
        setBusyId(null);
      }
    },
    [busyId, currentSessionId, onCurrentDecision, profile, refresh],
  );

  if (items.length === 0 && !pollError) return null;
  const settled = !pollError && pending.length === 0 && legacy.length === 0;

  return (
    <section
      className={cn(
        "relative z-20 border-b border-border/60 px-3 py-2 sm:px-5",
        hideWhenSettledOnPhone && settled && "max-lg:hidden",
      )}
      aria-label="Центр решений"
      data-decision-center
      data-decisions-settled={settled ? "true" : undefined}
    >
      {pollError && <p role="status" className="mb-2 text-xs text-[var(--neo-text-secondary)]">{pollError}</p>}
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "flex min-h-9 w-full items-center gap-2 rounded-[var(--neo-radius-control)] px-3 text-left",
          "bg-[var(--neo-surface)] text-xs text-[var(--neo-text-secondary)] shadow-[var(--neo-depth-1)]",
        )}
        aria-expanded={open}
      >
        <Inbox size={14} aria-hidden />
        <span className="font-medium text-[var(--neo-text-primary)]">Решения</span>
        <span>{pollError ? "требуют проверки" : pending.length > 0 ? `${pending.length} ждёт` : "нет ожидающих"}</span>
        {legacy.length > 0 && <span>· {legacy.length} в старой очереди</span>}
        <ChevronDown
          size={14}
          aria-hidden
          className={cn("ml-auto transition-transform", open && "rotate-180")}
        />
      </button>

      {open && (
        <div className="mt-2 max-h-[min(60vh,34rem)] space-y-3 overflow-auto rounded-[var(--neo-radius-card)] bg-[var(--neo-surface)] p-2 shadow-[var(--neo-depth-2)]">
          {pending.map((item) => (
            <div key={item.request_id}>
              {item.decision_fallback && <p className="mb-2 px-1 text-xs text-[var(--neo-text-secondary)]">{item.decision_reason || "Исходный чат недоступен. Ответьте здесь."}</p>}
              <CommandApprovalCard
                decisionKind={item.decision_kind}
                timestamp={item.timestamp}
                command={item.command}
                description={item.description}
                choices={pollError ? [] : item.choices ?? ["once", "deny"]}
                sending={busyId === item.request_id}
                onDecide={(choice) => void decide(item, choice)}
              />
            </div>
          ))}

          {legacy.length > 0 && (
            <div className="space-y-2" aria-label="Старая очередь">
              <p className="px-1 text-xs text-[var(--neo-text-secondary)]">Старая очередь · {legacy.length}</p>
              <p className="px-1 text-xs text-[var(--neo-text-secondary)]">Актуальность этих результатов неизвестна. Текст сохранён; для отправки запросите свежий результат у агента.</p>
              {legacy.map((item) => (
                <details key={item.request_id} className="rounded-[var(--neo-radius-control)] p-2 shadow-[var(--neo-inset-compact)]">
                  <summary className="cursor-pointer break-words text-xs">{shortTarget(item)}</summary>
                  <DecisionAge timestamp={item.timestamp} />
                  {item.decision_fallback && <p className="my-2 text-xs text-[var(--neo-text-secondary)]">{item.decision_reason || "Исходный чат недоступен. Ответьте здесь."}</p>}
                  <pre className="my-2 max-h-40 overflow-auto whitespace-pre-wrap break-words text-xs">{item.command}</pre>
                  {!pollError && (!item.choices || item.choices.includes("deny")) && <button type="button" disabled={busyId !== null} onClick={() => void decide(item, "deny")}
                    className="min-h-10 rounded-[var(--neo-radius-control)] bg-[var(--neo-surface)] px-3 text-xs shadow-[var(--neo-depth-1)] disabled:opacity-50">
                    {busyId === item.request_id ? "Закрываем…" : "Закрыть без отправки"}
                  </button>}
                </details>
              ))}
            </div>
          )}

          {history.length > 0 && (
            <div className="space-y-1" aria-label="История решений">
              <p className="px-1 text-[11px] font-medium text-[var(--neo-text-secondary)]">История</p>
              {history.map((item) => (
                <details
                  key={item.request_id}
                  className="rounded-[var(--neo-radius-control)] px-2 py-1.5 shadow-[var(--neo-inset-compact)]"
                >
                  <summary className="cursor-pointer break-words text-[11px] text-[var(--neo-text-secondary)]">
                    <span className="font-medium text-[var(--neo-text-primary)]">
                      {STATUS_LABEL[item.effect_status ?? "unknown"]}
                    </span>{" "}
                    · {shortTarget(item)}
                  </summary>
                  <DecisionAge timestamp={item.timestamp} />
                  {item.command && (
                    <pre className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap break-words px-1 font-mono text-[10.5px] text-[var(--neo-text-secondary)]">
                      {item.command}
                    </pre>
                  )}
                </details>
              ))}
            </div>
          )}
          {error && <p role="alert" className="px-1 text-xs text-[var(--destructive)]">{error}</p>}
        </div>
      )}
    </section>
  );
}
