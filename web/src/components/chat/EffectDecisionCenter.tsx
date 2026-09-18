import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, Inbox } from "lucide-react";

import { CommandApprovalCard } from "@/components/chat/CommandApprovalCard";
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
};

interface EffectDecisionCenterProps {
  profile?: string;
  active?: boolean;
  currentSessionId?: string | null;
  onCurrentDecision?: (
    requestId: string,
    choice: ApprovalChoiceValue,
  ) => Promise<boolean>;
}

function shortTarget(item: SSEApprovalRequestData): string {
  const first = (item.command ?? "").split("\n", 1)[0]?.trim();
  return first || (item.decision_kind === "payment" ? "Оплата" : "Внешняя отправка");
}

/** Profile-wide durable decisions, intentionally outside one chat transcript. */
export function EffectDecisionCenter({
  profile,
  active = true,
  currentSessionId,
  onCurrentDecision,
}: EffectDecisionCenterProps) {
  const [items, setItems] = useState<SSEApprovalRequestData[]>([]);
  const [open, setOpen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const next = await fetchEffectDecisions(profile);
    setItems(next);
  }, [profile]);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const load = async () => {
      try {
        const next = await fetchEffectDecisions(profile);
        if (!cancelled) setItems(next);
      } catch {
        // Existing cards remain usable. A later poll heals a transient outage.
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [active, profile]);

  const pending = useMemo(
    () => items.filter((item) => item.effect_status === "pending"),
    [items],
  );
  const history = useMemo(
    () => items.filter((item) => item.effect_status !== "pending"),
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

  if (items.length === 0) return null;

  return (
    <section className="relative z-20 border-b border-border/60 px-3 py-2 sm:px-5" aria-label="Центр решений">
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
        <span>{pending.length > 0 ? `${pending.length} ждёт` : "нет ожидающих"}</span>
        <ChevronDown
          size={14}
          aria-hidden
          className={cn("ml-auto transition-transform", open && "rotate-180")}
        />
      </button>

      {open && (
        <div className="mt-2 max-h-[min(60vh,34rem)] space-y-3 overflow-auto rounded-[var(--neo-radius-card)] bg-[var(--neo-surface)] p-2 shadow-[var(--neo-depth-2)]">
          {pending.map((item) => (
            <CommandApprovalCard
              key={item.request_id}
              decisionKind={item.decision_kind}
              command={item.command}
              description={item.description}
              choices={item.choices ?? ["once", "deny"]}
              sending={busyId === item.request_id}
              onDecide={(choice) => void decide(item, choice)}
            />
          ))}

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
