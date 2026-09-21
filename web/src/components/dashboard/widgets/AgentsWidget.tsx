/* eslint-disable react-refresh/only-export-components -- виджет и его каталожное описание намеренно живут вместе */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useStore } from "@nanostores/react";
import { ChevronRight, RefreshCw } from "lucide-react";
import { Link } from "react-router";

import { ProductButton } from "@/components/ProductButton";
import { FittedText } from "@/components/dashboard/FittedText";
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

const DOT_BY_ACTIVITY: Record<AgentActivity, string> = {
  waiting: "bg-[var(--neo-accent-line)]",
  working: "bg-[var(--neo-accent-line)]",
  ready: "bg-[var(--neo-accent-line)]",
  failed: "bg-[var(--neo-text-secondary)]",
  idle: "bg-[var(--neo-text-secondary)]/40",
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

  const rows = agentRows({ profiles, runs });

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
    runsReachable === false ||
    runsUpdatedAt === null ||
    now - runsUpdatedAt > STALE_AFTER_MS;
  const busy = busyAgentCount(rows);

  if (size === "s") {
    return (
      <div className="flex min-h-0 flex-1 flex-col justify-center gap-1">
        <p className="text-5xl leading-none font-medium text-[var(--neo-text-primary)]">
          {busy}
          <span className="ml-2 text-base text-[var(--neo-text-secondary)]">
            из {rows.length}
          </span>
        </p>
        <p className="truncate text-sm text-[var(--neo-text-secondary)]">
          {busy === 0 ? "никто не занят" : "сейчас в работе"}
        </p>
        <StatusLine size={size} stale={stale} />
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
                  <AgentMark row={row} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-semibold text-[var(--neo-text-primary)]">
                      {row.label}
                    </span>
                    {/* Состояние словом остаётся на любом размере: точка у
                        метки его лишь дублирует. На L к нему добавляется само
                        поручение — там для этого есть ширина. */}
                    <span className="block truncate text-xs text-[var(--neo-text-secondary)]">
                      {size === "l" && row.step ? `${row.note} · ${row.step}` : row.note}
                    </span>
                  </span>
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
            <span className="font-semibold text-[var(--neo-text-primary)]">{busy}</span> из{" "}
            {rows.length} в работе
          </p>
        )}
      </div>

      <StatusLine
        hiddenCount={visible.length > 0 ? hiddenCount : 0}
        size={size}
        stale={stale}
        onRetry={retry}
      />
    </div>
  );
}

/**
 * Строка под списком: живое состояние или его давность, и сколько агентов
 * осталось за кадром. Сколько бы места она ни заняла, счёт строк списка от
 * этого не сбивается: список меряет то, что ему осталось, а не вычитает её.
 */
function StatusLine({
  hiddenCount = 0,
  onRetry,
  size,
  stale,
}: {
  hiddenCount?: number;
  onRetry?: () => void;
  size: "s" | "m" | "l";
  stale: boolean;
}) {
  const rest = hiddenCount > 0 ? ` · ещё ${hiddenCount}` : "";
  return (
    <div className="flex shrink-0 items-center gap-2">
      {stale ? (
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
      {/* Повтор нужен только там, где данные устарели и есть место под кнопку. */}
      {stale && size !== "s" && onRetry ? <RetryButton onClick={onRetry} /> : null}
    </div>
  );
}

/**
 * Метка агента: первая буква его настоящего имени.
 *
 * Никаких придуманных портретов и цветов по вкусу — знак строится из того,
 * как агента зовут в этом контуре, а состояние по-прежнему читается точкой и
 * словом рядом.
 */
function AgentMark({ row }: { row: DashboardAgentRow }) {
  return (
    <span
      aria-hidden
      className="relative grid size-8 shrink-0 place-items-center rounded-[var(--neo-radius-control)] text-sm font-semibold text-[var(--neo-text-primary)] shadow-[var(--neo-inset-compact)]"
    >
      {row.label.trim().slice(0, 1).toUpperCase()}
      <span
        className={cn(
          "absolute -right-0.5 -top-0.5 size-2 rounded-full",
          DOT_BY_ACTIVITY[row.activity],
        )}
      />
    </span>
  );
}

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
