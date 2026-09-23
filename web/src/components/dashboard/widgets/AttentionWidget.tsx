/* eslint-disable react-refresh/only-export-components -- виджет и его каталожное описание намеренно живут вместе */
import { useState } from "react";
import { useStore } from "@nanostores/react";
import {
  AlertTriangle,
  ArrowUpRight,
  CheckCheck,
  ClipboardCheck,
  Hand,
  MessageCircleQuestion,
  type LucideIcon,
} from "lucide-react";
import { Link } from "react-router";

import type { DashboardWidget } from "@/components/dashboard/widget-types";
import { LoadingNote, RetryButton, useDashboardSection } from "@/components/dashboard/widget-states";
import { $chatRuns, $chatRunsReachable, $chatRunsUpdatedAt } from "@/lib/chat-runs";
import { agentHref } from "@/lib/dashboard-agents";
import {
  $dashboardState,
  attentionView,
  sectionReady,
  type AttentionRow,
} from "@/lib/dashboard-state";
import { cn } from "@/lib/utils";

/**
 * «Требует внимания» — полоса над сеткой, с неё начинается день.
 *
 * Источники: вопросы, приёмка и шаги владельца на канбане, недоставленные
 * ответы, сбои расписания, отозванный вход провайдера, неудачное обновление,
 * почти исчерпанная квота Codex — из `/api/dashboard/state`; и чаты, где
 * агент ждёт решения, — из живого потока работ. Одна–три строки ведут в
 * конкретный чат или задачу.
 *
 * «Всё спокойно» карточка говорит только тогда, когда каждый источник
 * действительно прочитан. Непрочитанный источник называется словами — пустая
 * полоса на месте сбоя читалась бы как «решать нечего».
 */

const VISIBLE_ROWS = 3;

function AttentionBody() {
  const view = useDashboardSection("attention");
  const state = useStore($dashboardState);
  const runs = useStore($chatRuns);
  const runsUpdatedAt = useStore($chatRunsUpdatedAt);
  const runsReachable = useStore($chatRunsReachable);
  const [expanded, setExpanded] = useState(false);

  if (view.phase === "loading" && runsUpdatedAt === null) {
    return <LoadingNote text="Проверяем, что ждёт вашего решения…" />;
  }

  const labels = new Map<string, string>();
  if (state && sectionReady(state.agents)) {
    for (const agent of state.agents.agents) labels.set(agent.profile, agent.label);
  }
  const attention = view.phase === "ready" ? view.section : null;
  const sectionState = view.phase === "ready" ? "ready" : view.phase === "error" ? "error" : "pending";
  const result = attentionView(attention, sectionState, {
    runs,
    known: runsUpdatedAt !== null,
    reachable: runsReachable,
    labelFor: (profile) => labels.get(profile) ?? (profile || "Корра"),
    hrefFor: (profile, sessionId) => agentHref({ profile, sessionId }),
  });

  if (result.calm) {
    return (
      <div className="flex min-w-0 items-center gap-3" data-attention-calm>
        <span className="kdw-attention-icon kdw-attention-icon--calm" aria-hidden>
          <CheckCheck className="size-4" />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold text-[var(--neo-text-primary)]">Решений от вас не ждут</p>
          <p className="truncate text-xs text-[var(--neo-text-secondary)]">
            Проверены чаты агентов, канбан, расписание, доставка ответов и подключения.
          </p>
        </div>
      </div>
    );
  }

  const visible = expanded ? result.rows : result.rows.slice(0, VISIBLE_ROWS);
  const hidden = result.rows.length - visible.length;
  const retry = view.phase === "loading" ? undefined : view.retry;

  return (
    <div className="flex flex-col gap-2">
      {visible.length ? (
        <ul className="kdw-attention-list" aria-label="Что ждёт вашего решения">
          {visible.map((row) => (
            <AttentionLine key={row.id} row={row} />
          ))}
        </ul>
      ) : null}

      {hidden > 0 || (expanded && result.rows.length > VISIBLE_ROWS) ? (
        <button
          type="button"
          className="kdw-text-button w-fit"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
        >
          {expanded ? "Свернуть" : `Ещё ${hidden}`}
        </button>
      ) : null}

      {!visible.length && !result.unverified.length ? (
        <p className="text-sm text-[var(--neo-text-secondary)]" aria-busy>
          Проверяем, что ждёт вашего решения…
        </p>
      ) : null}

      {result.unverified.length ? (
        <div className="flex flex-wrap items-center gap-2" role="alert" data-attention-unverified>
          <p className="min-w-0 flex-1 text-sm text-[var(--neo-text-secondary)]">
            {visible.length ? "Кроме того, не удалось проверить: " : "Не удалось проверить: "}
            {result.unverified.join(", ")}.
          </p>
          {retry ? <RetryButton onClick={retry} /> : null}
        </div>
      ) : null}
    </div>
  );
}

/** Знак строки по её сути: вопрос, приёмка, шаг владельца или сбой. */
const KIND_ICON: Record<string, LucideIcon> = {
  chat_decision: MessageCircleQuestion,
  kanban_question: MessageCircleQuestion,
  kanban_accept: ClipboardCheck,
  kanban_human_step: Hand,
};

function AttentionLine({ row }: { row: AttentionRow }) {
  const Icon = KIND_ICON[row.kind] ?? (row.severity === "action" ? MessageCircleQuestion : AlertTriangle);
  return (
    <li className="kdw-attention-row" data-attention-kind={row.kind} data-severity={row.severity}>
      <span
        className={cn("kdw-attention-icon", row.severity === "action" && "kdw-attention-icon--action")}
        aria-hidden
      >
        <Icon className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold text-[var(--neo-text-primary)]">{row.title}</p>
        <p className="truncate text-xs text-[var(--neo-text-secondary)]">
          {row.agent && !row.title.startsWith(row.agent) ? `${row.agent} · ` : ""}
          {row.detail}
        </p>
      </div>
      <Link
        to={row.href}
        className="kdw-attention-action"
        aria-label={`${row.action}: ${row.title}`}
      >
        <span className="kdw-attention-action-text">{row.action}</span>
        <ArrowUpRight className="size-4 shrink-0" aria-hidden />
      </Link>
    </li>
  );
}

export const ATTENTION_WIDGET: DashboardWidget = {
  id: "attention",
  title: "Требует внимания",
  purpose: "Подтверждения, ошибки и остановленные поручения, которые ждут вашего решения.",
  pinned: true,
  action: { label: "Открыть агентов", short: "Агенты", to: "/agents" },
  Body: AttentionBody,
};
