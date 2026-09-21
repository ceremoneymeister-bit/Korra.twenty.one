/* eslint-disable react-refresh/only-export-components -- виджет и его каталожное описание намеренно живут вместе */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useStore } from "@nanostores/react";
import { ArrowRight, RefreshCw } from "lucide-react";
import { Link } from "react-router";

import { ProductButton } from "@/components/ProductButton";
import type {
  DashboardWidget,
  DashboardWidgetBodyProps,
} from "@/components/dashboard/widget-types";
import { api } from "@/lib/api";
import {
  $chatRuns,
  $chatRunsReachable,
  $chatRunsUpdatedAt,
  refreshChatRuns,
} from "@/lib/chat-runs";
import {
  agentHref,
  agentRows,
  busyAgentCount,
  type AgentActivity,
  type DashboardAgentRow,
} from "@/lib/dashboard-agents";
import { cn } from "@/lib/utils";

/**
 * «Агенты» — первый реальный источник дашборда.
 *
 * Состав берётся из `GET /api/profiles`, занятость — из общего потока работ
 * (`lib/chat-runs`), которым уже живёт полоса вкладок и уведомления. Ничего
 * не придумывается: пока состав не пришёл, карточка говорит об этом и даёт
 * повторить, а не показывает ноль занятых как «всё спокойно».
 *
 * Переход ведёт к нужному агенту и в тот самый чат, где идёт работа.
 */

/** Насколько старым может быть последний ответ, прежде чем мы скажем об этом. */
const STALE_AFTER_MS = 20_000;

/** Как часто карточка пересматривает возраст последнего ответа. */
const CLOCK_INTERVAL_MS = 2_000;

/**
 * Текущее время как подписка, а не как чтение часов в рендере.
 *
 * Возраст данных меняется сам по себе, без нового ответа сервера: без такого
 * тика «последнее известное состояние» появлялось бы только при следующей
 * перерисовке по другой причине.
 */
function useNow(intervalMs: number): number {
  const [now, setNow] = useState(0);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    const first = window.setTimeout(tick, 0);
    const timer = window.setInterval(tick, intervalMs);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(timer);
    };
  }, [intervalMs]);
  return now;
}

/** Строк на плитке: S — только счёт, M — короткий список, L — с шагом. */
const ROWS_BY_SIZE = { s: 0, m: 3, l: 6 } as const;

const DOT_BY_ACTIVITY: Record<AgentActivity, string> = {
  waiting: "bg-[var(--neo-accent-line)]",
  working: "bg-[var(--neo-accent-line)]",
  ready: "bg-[var(--neo-accent-line)]",
  failed: "bg-[var(--neo-text-secondary)]",
  idle: "bg-[var(--neo-text-secondary)]/40",
};

function AgentsBody({ size }: DashboardWidgetBodyProps) {
  const runs = useStore($chatRuns);
  const runsReachable = useStore($chatRunsReachable);
  const runsUpdatedAt = useStore($chatRunsUpdatedAt);
  const now = useNow(CLOCK_INTERVAL_MS);
  const [profiles, setProfiles] = useState<unknown>(null);
  const [rosterState, setRosterState] = useState<"loading" | "ready" | "error">("loading");
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const loadRoster = useCallback(async () => {
    try {
      const response = await api.getProfiles();
      if (!mountedRef.current) return;
      setProfiles(response?.profiles ?? []);
      setRosterState("ready");
    } catch {
      if (!mountedRef.current) return;
      setRosterState("error");
    }
  }, []);

  useEffect(() => {
    // Состав читаем запросом, а не мгновенной правкой состояния: карточка
    // меняется уже в ответе.
    void (async () => {
      await loadRoster();
    })();
  }, [loadRoster]);

  const retry = useCallback(() => {
    // Ответ об ошибке сменяем ожиданием только по нажатию: при обновлении
    // уже показанного состава строки не должны мигать.
    setRosterState((previous) => (previous === "error" ? "loading" : previous));
    void loadRoster();
    void refreshChatRuns();
  }, [loadRoster]);

  if (rosterState === "loading") {
    return (
      <WidgetNote busy>
        <span className="text-sm text-[var(--neo-text-secondary)]">Читаем ваших агентов…</span>
      </WidgetNote>
    );
  }

  if (rosterState === "error") {
    return (
      <WidgetNote>
        <p className="text-sm font-semibold text-[var(--neo-text-primary)]">
          Не удалось прочитать агентов
        </p>
        <p className="text-sm leading-relaxed text-[var(--neo-text-secondary)]">
          Панель не ответила на запрос состава. Ничего не изменилось — можно
          повторить.
        </p>
        <RetryButton onClick={retry} />
      </WidgetNote>
    );
  }

  const rows = agentRows({ profiles, runs });

  if (rows.length === 0) {
    return (
      <WidgetNote>
        <p className="text-sm font-semibold text-[var(--neo-text-primary)]">
          Агентов пока нет
        </p>
        <p className="text-sm leading-relaxed text-[var(--neo-text-secondary)]">
          Создайте первого — и он появится здесь вместе со своей работой.
        </p>
        <WidgetLink to="/profiles">Создать агента</WidgetLink>
      </WidgetNote>
    );
  }

  // Активность отдельно от состава: список агентов может быть свежим, а поток
  // работ — уже нет. Молчать об этом нельзя, иначе «никто не работает»
  // выглядит фактом.
  const stale =
    runsReachable === false ||
    runsUpdatedAt === null ||
    now - runsUpdatedAt > STALE_AFTER_MS;
  const busy = busyAgentCount(rows);
  const visible = rows.slice(0, ROWS_BY_SIZE[size]);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      {size === "s" ? (
        <div className="flex min-h-0 flex-1 flex-col justify-center gap-1">
          <p className="text-5xl leading-none font-medium text-[var(--neo-text-primary)]">
            {busy}
            <span className="ml-2 text-base text-[var(--neo-text-secondary)]">
              из {rows.length}
            </span>
          </p>
          <p className="text-sm text-[var(--neo-text-secondary)]">
            {busy === 0 ? "никто не занят" : "сейчас в работе"}
          </p>
        </div>
      ) : (
        <ul className="flex min-h-0 flex-1 flex-col gap-1 overflow-hidden">
          {visible.map((row) => (
            <li key={row.profile || "__main__"} className="min-w-0">
              <Link
                to={agentHref(row)}
                className="flex min-h-11 w-full items-center gap-3 rounded-[var(--neo-radius-control)] px-2 transition-shadow hover:shadow-[var(--neo-inset-compact)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--neo-accent-line)]"
              >
                <span
                  aria-hidden
                  className={cn("size-2 shrink-0 rounded-full", DOT_BY_ACTIVITY[row.activity])}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold text-[var(--neo-text-primary)]">
                    {row.label}
                  </span>
                  <span className="block truncate text-xs text-[var(--neo-text-secondary)]">
                    {row.note}
                    {size === "l" && row.step ? ` · ${row.step}` : ""}
                  </span>
                </span>
                <ArrowRight
                  aria-hidden
                  className="size-4 shrink-0 text-[var(--neo-text-secondary)]"
                />
              </Link>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-auto flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
        {stale ? (
          <p
            className="min-w-0 flex-1 text-xs text-[var(--neo-text-secondary)]"
            data-agents-stale
            role="status"
          >
            {size === "s"
              ? "Последнее известное состояние"
              : "Показано последнее известное состояние — связь с работами прервалась."}
          </p>
        ) : (
          <p
            className="min-w-0 flex-1 text-xs text-[var(--neo-text-secondary)]"
            data-agents-live
          >
            {size === "s" ? "Состояние живое" : "Состояние обновляется само."}
          </p>
        )}
        {/* На плитке 1×1 нет места и для повтора, и для перехода: там ведём
            на экран агентов, где видно всё состояние целиком. */}
        {stale && size !== "s" ? <RetryButton onClick={retry} /> : null}
        <WidgetLink to="/agents">
          {size === "s" ? "К агентам" : "Все агенты"}
        </WidgetLink>
      </div>
    </div>
  );
}

function WidgetNote({ busy, children }: { busy?: boolean; children: ReactNode }) {
  return (
    <div
      aria-busy={busy || undefined}
      className="flex min-h-0 flex-1 flex-col justify-center gap-2"
    >
      {children}
    </div>
  );
}

function RetryButton({ onClick }: { onClick: () => void }) {
  return (
    <ProductButton
      outlined
      size="sm"
      onClick={onClick}
      prefix={<RefreshCw className="size-4 shrink-0" aria-hidden />}
    >
      Повторить
    </ProductButton>
  );
}

function WidgetLink({ children, to }: { children: ReactNode; to: string }) {
  return (
    <Link
      to={to}
      className="inline-flex min-h-11 items-center gap-2 rounded-lg px-3 text-sm font-semibold text-[var(--neo-text-primary)] transition-shadow hover:shadow-[var(--neo-inset-compact)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--neo-accent-line)]"
    >
      {children}
      <ArrowRight className="size-4 shrink-0" aria-hidden />
    </Link>
  );
}

export const AGENTS_WIDGET: DashboardWidget = {
  id: "agents",
  title: "Агенты",
  purpose: "Кто из ваших агентов сейчас работает, а кто ждёт поручения.",
  Body: AgentsBody,
};

export type { DashboardAgentRow };
