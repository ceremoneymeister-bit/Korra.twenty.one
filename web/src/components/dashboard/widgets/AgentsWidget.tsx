/* eslint-disable react-refresh/only-export-components -- виджет и его каталожное описание намеренно живут вместе */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useStore } from "@nanostores/react";
import { ChevronRight, RefreshCw } from "lucide-react";
import { Link } from "react-router";

import { ProductButton } from "@/components/ProductButton";
import { FittedText } from "@/components/dashboard/FittedText";
import { ActivityBars, AgentAvatar } from "@/components/dashboard/visuals";
import type {
  DashboardWidget,
  DashboardWidgetBodyProps,
} from "@/components/dashboard/widget-types";
import { useAvailableHeight } from "@/hooks/useAvailableHeight";
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
import {
  $dashboardState,
  dashboardTimeZone,
  formatMoment,
  sectionReady,
} from "@/lib/dashboard-state";
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
 *
 * Сколько строк показать — решает реальная высота, оставшаяся под список, а
 * не ширина полотна. Строка либо помещается целиком, либо не рисуется вовсе:
 * половина имени под обрезом читается как поломка, а не как «дальше есть
 * ещё», и именно так карточка выглядела на телефоне.
 */

/** Насколько старым может быть последний ответ, прежде чем мы скажем об этом. */
const STALE_AFTER_MS = 20_000;

/** Как часто карточка пересматривает возраст последнего ответа. */
const CLOCK_INTERVAL_MS = 2_000;

/**
 * Высота строки агента в пикселях — та же, что задана в `dashboard-grid.css`.
 *
 * Строка фиксированная, а не тянущаяся: только так «поместилось» можно
 * посчитать заранее, а не узнать по факту обрезки. 44 — минимальная цель
 * пальца; ниже опускаться нельзя, выше — терять строку на плитке телефона.
 */
const ROW_HEIGHT = 44;

/**
 * Сколько строк показать, пока высота ещё не измерена.
 *
 * Это не запасное правило на каждый день, а разумное первое приближение:
 * до раскладки (и в jsdom, где размеров нет вовсе) показывать пустой список
 * было бы хуже, чем показать столько, сколько обычно помещается.
 */
const ROWS_BEFORE_MEASURE = { s: 0, m: 2, l: 4 } as const;

/** Слово состояния справа в строке: «Работает / Свободен» с первого взгляда. */
const STATUS_WORD: Record<AgentActivity, string> = {
  waiting: "Ждёт решения",
  working: "Работает",
  ready: "Ответ готов",
  failed: "Сбой",
  idle: "Свободен",
  unknown: "",
};

/** Сколько целых строк помещается в измеренную высоту. */
function rowsThatFit(height: number): number {
  return Math.max(0, Math.floor(height / ROW_HEIGHT));
}

function AgentsBody({ size = "m" }: DashboardWidgetBodyProps) {
  const runs = useStore($chatRuns);
  const runsReachable = useStore($chatRunsReachable);
  const runsUpdatedAt = useStore($chatRunsUpdatedAt);
  const now = useNow(CLOCK_INTERVAL_MS);
  const [profiles, setProfiles] = useState<unknown>(null);
  const [rosterState, setRosterState] = useState<"loading" | "ready" | "error">("loading");
  const [listRef, listHeight] = useAvailableHeight<HTMLDivElement>();
  const dashboard = useStore($dashboardState);
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
        <NoteTitle>Не удалось прочитать агентов</NoteTitle>
        {/* На плитке 1×1 объяснение вытеснило бы повтор — а повторить важнее. */}
        {size === "s" ? null : (
          <FittedText
            text="Панель не ответила на запрос состава. Ничего не изменилось."
            className="text-[var(--neo-text-secondary)]"
          />
        )}
        <RetryButton onClick={retry} />
      </WidgetNote>
    );
  }

  // `runs=[]` — это подтверждённый ноль только после успешного ответа.
  // Пока `$chatRunsUpdatedAt` пуст, показываем состав, но не приписываем
  // людям ни свободное, ни рабочее состояние.
  const activityKnown = runsUpdatedAt !== null;
  const rows = agentRows({ profiles, runs, activityKnown });

  if (rows.length === 0) {
    return (
      <WidgetNote>
        <NoteTitle>Агентов пока нет</NoteTitle>
        {size === "s" ? null : (
          <>
            <FittedText
              text="Создайте первого — и он появится здесь вместе со своей работой."
              className="text-[var(--neo-text-secondary)]"
            />
            <WidgetLink to="/profiles">Создать агента</WidgetLink>
          </>
        )}
      </WidgetNote>
    );
  }

  // Активность отдельно от состава: список агентов может быть свежим, а поток
  // работ — уже нет. Молчать об этом нельзя, иначе «никто не работает»
  // выглядит фактом.
  const stale =
    runsUpdatedAt !== null &&
    (runsReachable === false || now - runsUpdatedAt > STALE_AFTER_MS);
  const activityState = !activityKnown ? "unknown" : stale ? "stale" : "live";
  const busy = busyAgentCount(rows);
  // Когда агент в последний раз работал — из общей сводки дашборда. Только
  // как пояснение к «Свободен»: состояние по-прежнему решает поток работ.
  const lastActive = new Map<string, number>();
  if (dashboard && sectionReady(dashboard.agents)) {
    for (const item of dashboard.agents.agents) {
      if (item.last_active_at) lastActive.set(item.profile, item.last_active_at);
    }
  }
  const timeZone = dashboardTimeZone(dashboard);
  const nowSeconds = now / 1000;

  if (size === "s") {
    const unknown = activityState === "unknown";
    return (
      <div className="flex min-h-0 flex-1 flex-col justify-center gap-1">
        <p className="kdw-metric-value leading-none text-[var(--neo-text-primary)]">
          {unknown ? "—" : busy}
          <span className="ml-2 text-base font-normal tracking-normal text-[var(--neo-text-secondary)]">
            {unknown ? `всего ${rows.length}` : `из ${rows.length}`}
          </span>
        </p>
        {unknown ? null : (
          <p className="truncate text-sm text-[var(--neo-text-secondary)]">
            {busy === 0 ? "никто не занят" : "сейчас в работе"}
          </p>
        )}
        <span className="kdw-avatar-stack" aria-hidden>
          {rows.slice(0, 3).map((row) => (
            <AgentAvatar
              key={row.profile || "__main__"}
              label={row.label}
              profile={row.profile}
              template={row.template}
              size="sm"
            />
          ))}
        </span>
        <StatusLine size={size} state={activityState} />
      </div>
    );
  }

  const fits = listHeight === null ? ROWS_BEFORE_MEASURE[size] : rowsThatFit(listHeight);
  const visible = rows.slice(0, Math.min(fits, rows.length));
  const hiddenCount = rows.length - visible.length;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      {/* Меряем именно это окно, а не всю карточку: его высоту целиком задаёт
          остаток от строки состояния, поэтому счёт строк не зависит ни от
          длины этой строки, ни от того, появилась ли рядом кнопка повтора. */}
      <div ref={listRef} className="min-h-0 flex-1 overflow-hidden">
        {visible.length > 0 ? (
          <ul className="korra-agent-rows">
            {visible.map((row) => (
              <li key={row.profile || "__main__"} className="flex min-w-0">
                <Link
                  to={agentHref(row)}
                  className="flex min-w-0 flex-1 items-center gap-3 rounded-[var(--neo-radius-control)] px-2 transition-shadow hover:shadow-[var(--neo-inset-compact)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--neo-accent-line)]"
                >
                  <AgentAvatar
                    label={row.label}
                    profile={row.profile}
                    template={row.template}
                    busy={row.activity === "working" || row.activity === "waiting"}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-semibold text-[var(--neo-text-primary)]">
                      {row.label}
                    </span>
                    {/* Вторая строка — чем агент занят: поручение, если оно
                        есть, иначе состояние словами. У свободного — когда он
                        работал в последний раз. */}
                    {row.activity === "unknown" ? null : (
                      <span className="block truncate text-xs text-[var(--neo-text-secondary)]">
                        {rowNote(row, size, lastActive.get(row.profile), nowSeconds, timeZone)}
                      </span>
                    )}
                  </span>
                  {row.activity === "unknown" ? null : (
                    <span
                      className={cn(
                        "kdw-status",
                        (row.activity === "working" || row.activity === "waiting") && "kdw-status--busy",
                        size === "m" && "kdw-status--narrow",
                      )}
                    >
                      {row.activity === "working" ? <ActivityBars /> : <span className="kdw-status-dot" aria-hidden />}
                      <span>{STATUS_WORD[row.activity]}</span>
                    </span>
                  )}
                  <ChevronRight
                    aria-hidden
                    className="size-4 shrink-0 text-[var(--neo-text-secondary)]"
                  />
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          /* Ни одной целой строки не помещается — показываем счёт, а за самим
             списком карточка уводит своим переходом в шапке. */
          <p className="truncate text-sm text-[var(--neo-text-secondary)]">
            {activityState === "unknown" ? (
              <>Состав: {rows.length}</>
            ) : (
              <>
                <span className="font-semibold text-[var(--neo-text-primary)]">{busy}</span> из{" "}
                {rows.length} в работе
              </>
            )}
          </p>
        )}
      </div>

      <StatusLine
        hiddenCount={visible.length > 0 ? hiddenCount : 0}
        size={size}
        state={activityState}
        onRetry={retry}
      />
    </div>
  );
}

/**
 * Строка под списком: ожидание первого снимка, живое состояние или его
 * давность, и сколько агентов осталось за кадром. Сколько бы места она ни
 * заняла, счёт строк списка от этого не сбивается: список меряет то, что ему
 * осталось, а не вычитает её.
 */
function StatusLine({
  hiddenCount = 0,
  onRetry,
  size,
  state,
}: {
  hiddenCount?: number;
  onRetry?: () => void;
  size: "s" | "m" | "l";
  state: "unknown" | "stale" | "live";
}) {
  const rest = hiddenCount > 0 ? ` · ещё ${hiddenCount}` : "";
  return (
    <div className="flex shrink-0 items-center gap-2">
      {state === "unknown" ? (
        <p
          className="min-w-0 flex-1 truncate text-xs text-[var(--neo-text-secondary)]"
          data-agents-unknown
          role="status"
        >
          {`Занятость уточняется${rest}`}
        </p>
      ) : state === "stale" ? (
        <p
          className="min-w-0 flex-1 truncate text-xs text-[var(--neo-text-secondary)]"
          data-agents-stale
          role="status"
        >
          {size === "s"
            ? "Последнее известное состояние"
            : `Показано последнее известное состояние${rest}`}
        </p>
      ) : (
        <p
          className="min-w-0 flex-1 truncate text-xs text-[var(--neo-text-secondary)]"
          data-agents-live
        >
          {size === "s" ? "Состояние живое" : `Состояние обновляется само${rest}`}
        </p>
      )}
      {/* Повтор нужен, когда первого ответа нет либо данные устарели, и есть место под кнопку. */}
      {state !== "live" && size !== "s" && onRetry ? <RetryButton onClick={onRetry} /> : null}
    </div>
  );
}

/**
 * Вторая строка агента: чем он занят.
 *
 * Работающий — его поручение (на M — если оно есть, иначе «Работает»);
 * свободный — «Готов к поручению» и, если известно, когда он работал в
 * последний раз.
 */
function rowNote(
  row: DashboardAgentRow,
  size: "m" | "l",
  lastActive: number | undefined,
  nowSeconds: number,
  timeZone: string,
): string {
  if (row.activity === "idle") {
    return lastActive && nowSeconds > 0
      ? `${row.note} · был в работе ${formatMoment(lastActive, nowSeconds, timeZone)}`
      : row.note;
  }
  // Ожидание решения сервер описывает той же фразой, что и состояние:
  // повторять её через точку незачем.
  if (!row.step || row.step === DECISION_PLACEHOLDER) return row.note;
  return size === "l" ? `${row.note} · ${row.step}` : row.step;
}

/** Подпись, которой поток работ отмечает разговор, ждущий решения. */
const DECISION_PLACEHOLDER = "Ожидает вашего решения";

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

function WidgetNote({ busy, children }: { busy?: boolean; children: ReactNode }) {
  return (
    <div
      aria-busy={busy || undefined}
      className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden"
    >
      {children}
    </div>
  );
}

/**
 * Заголовок состояния: не больше двух строк.
 *
 * Выше по колонке его не подвинуть — под ним живут объяснение и действие, и
 * именно они пропадают первыми, если заголовок расползается на три строки в
 * плитке 1×1. Две строки — предел, дальше многоточие.
 */
function NoteTitle({ children }: { children: ReactNode }) {
  return (
    <p className="korra-widget-line line-clamp-2 shrink-0 text-sm font-semibold text-[var(--neo-text-primary)]">
      {children}
    </p>
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
      className="inline-flex min-h-[44px] w-fit items-center gap-2 rounded-lg px-3 text-sm font-semibold text-[var(--neo-text-primary)] transition-shadow hover:shadow-[var(--neo-inset-compact)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--neo-accent-line)]"
    >
      {children}
      <ChevronRight className="size-4 shrink-0" aria-hidden />
    </Link>
  );
}

export const AGENTS_WIDGET: DashboardWidget = {
  id: "agents",
  title: "Агенты",
  purpose: "Кто из ваших агентов сейчас работает, а кто ждёт поручения.",
  action: { label: "Все агенты", short: "Все", to: "/agents" },
  Body: AgentsBody,
};

export type { DashboardAgentRow };
