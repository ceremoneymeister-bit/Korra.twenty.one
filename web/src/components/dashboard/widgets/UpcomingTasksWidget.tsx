/* eslint-disable react-refresh/only-export-components -- виджет и его каталожное описание намеренно живут вместе */
import { AlertTriangle, Check, Clock3, Loader2, Repeat } from "lucide-react";

import type {
  DashboardWidget,
  DashboardWidgetBodyProps,
} from "@/components/dashboard/widget-types";
import {
  ErrorNote,
  FirstStep,
  LoadingNote,
  StaleMark,
  useDashboardSection,
  useNowSeconds,
} from "@/components/dashboard/widget-states";
import { useAvailableHeight } from "@/hooks/useAvailableHeight";
import {
  dashboardTimeZone,
  formatClock,
  formatMoment,
  formatRelative,
  nextEvent,
  timelineWindow,
  type DashboardEvent,
  type DashboardUpcoming,
} from "@/lib/dashboard-state";
import { cn } from "@/lib/utils";

/**
 * «Ближайшие задачи» — лента дня по часам: что уже отработало сегодня и что
 * запустится дальше. Источник — расписания всех агентов (`cron/jobs.json`) и
 * журнал запусков `executions.db`, собранные сервером.
 *
 * Частая задача («каждые 15 минут») идёт одной строкой с числом повторов —
 * иначе она вытеснила бы всё остальное. Просроченный запуск показывается
 * словами: это настоящий сигнал, что расписание не сработало.
 */

/** Высота строки ленты; совпадает с `.kdw-event` в `dashboard-widgets.css`. */
const ROW_HEIGHT = 46;
const ROWS_BEFORE_MEASURE = { s: 1, m: 3, l: 5 } as const;

function UpcomingBody({ size = "m" }: DashboardWidgetBodyProps) {
  const view = useDashboardSection("upcoming");
  const [listRef, listHeight] = useAvailableHeight<HTMLDivElement>();
  const now = useNowSeconds(view.phase === "ready" ? view.section.now : 0);

  if (view.phase === "loading") return <LoadingNote text="Читаем расписание…" />;
  if (view.phase === "error") {
    return (
      <ErrorNote
        size={size}
        title="Не удалось прочитать расписание"
        detail="Задачи и их запуски не пришли. Расписание при этом работает как раньше."
        onRetry={view.retry}
      />
    );
  }
  const upcoming = view.section;
  if (upcoming.status === "empty") {
    return (
      <FirstStep
        size={size}
        title="Расписаний пока нет"
        text="Поручите агенту регулярную задачу — например, утреннюю сводку, — и она появится в ленте дня."
        action={{ label: "Открыть задачи", to: "/cron" }}
      />
    );
  }

  const timeZone = dashboardTimeZone(view.state);

  if (size === "s") {
    const next = nextEvent(upcoming.events, now);
    return (
      <div className="flex min-h-0 flex-1 flex-col justify-center gap-1" data-upcoming-next>
        {next ? (
          <>
            <p className="kdw-next-time">{formatClock(next.at, timeZone)}</p>
            <p className="truncate text-sm font-semibold text-[var(--neo-text-primary)]">{next.title}</p>
            <p className="truncate text-xs text-[var(--neo-text-secondary)]">
              {stateText(next, now, timeZone)}
            </p>
          </>
        ) : (
          <NothingLeft upcoming={upcoming} now={now} timeZone={timeZone} />
        )}
      </div>
    );
  }

  const fits =
    listHeight === null ? ROWS_BEFORE_MEASURE[size] : Math.max(0, Math.floor(listHeight / ROW_HEIGHT));
  const rows = timelineWindow(upcoming.events, now, fits);
  const nextRow = nextEvent(rows, now);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      {size === "l" ? <WeekStrip upcoming={upcoming} /> : null}
      <div ref={listRef} className="min-h-0 flex-1 overflow-hidden">
        {rows.length ? (
          <ol className="kdw-timeline" aria-label="Лента дня">
            {rows.map((event) => (
              <EventRow
                key={event.id}
                event={event}
                next={event === nextRow}
                now={now}
                timeZone={timeZone}
              />
            ))}
          </ol>
        ) : upcoming.events.length === 0 ? (
          <NothingLeft upcoming={upcoming} now={now} timeZone={timeZone} />
        ) : (
          <p className="truncate text-sm text-[var(--neo-text-secondary)]">
            Сегодня событий: {upcoming.events.length}
          </p>
        )}
      </div>
      {upcoming.unreadable.length ? (
        <p className="truncate text-xs text-[var(--neo-text-secondary)]" role="status">
          Без данных: {upcoming.unreadable.join(", ")}
        </p>
      ) : null}
      <StaleMark stale={view.stale} />
    </div>
  );
}

function NothingLeft({
  now,
  timeZone,
  upcoming,
}: {
  now: number;
  timeZone: string;
  upcoming: DashboardUpcoming;
}) {
  const later = upcoming.next_later;
  if (upcoming.jobs_active === 0) {
    // Расписания есть, но все на паузе: «запусков нет» здесь — следствие
    // выбора владельца, и сказать об этом надо прямо.
    return (
      <div className="flex flex-col gap-1" data-upcoming-paused>
        <p className="text-sm font-semibold text-[var(--neo-text-primary)]">Все расписания на паузе</p>
        <p className="line-clamp-2 text-xs text-[var(--neo-text-secondary)]">
          Задач: {upcoming.jobs_total}. Включить нужные можно в разделе «Задачи».
        </p>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1" data-upcoming-empty-day>
      <p className="text-sm font-semibold text-[var(--neo-text-primary)]">На сегодня запусков больше нет</p>
      {later ? (
        <p className="line-clamp-2 text-xs text-[var(--neo-text-secondary)]">
          Дальше — {formatMoment(later.at, now, timeZone)}: {later.title}
        </p>
      ) : (
        <p className="text-xs text-[var(--neo-text-secondary)]">
          Активных расписаний: {upcoming.jobs_active} из {upcoming.jobs_total}
        </p>
      )}
    </div>
  );
}

function stateText(event: DashboardEvent, now: number, timeZone: string): string {
  const who = event.agent;
  switch (event.state) {
    case "done":
      return event.runs_today ? `${who} · готово, запусков сегодня: ${event.runs_today}` : `${who} · готово`;
    case "failed":
      return event.runs_today && event.failed_today !== undefined
        ? `${who} · сбой (${event.failed_today} из ${event.runs_today})`
        : `${who} · сбой`;
    case "running":
      return `${who} · выполняется`;
    case "late":
      return `${who} · не запустилась в ${formatClock(event.at, timeZone)}`;
    case "unknown":
      return `${who} · исход неизвестен`;
    default:
      return event.repeats_today
        ? `${who} · ${formatRelative(event.at, now)}, ещё ${event.repeats_today - 1} сегодня`
        : `${who} · ${formatRelative(event.at, now)}`;
  }
}

const STATE_ICON = {
  done: Check,
  failed: AlertTriangle,
  running: Loader2,
  late: AlertTriangle,
  unknown: Clock3,
  planned: Clock3,
} as const;

function EventRow({
  event,
  next,
  now,
  timeZone,
}: {
  event: DashboardEvent;
  next: boolean;
  now: number;
  timeZone: string;
}) {
  const Icon = event.repeats_today ? Repeat : STATE_ICON[event.state];
  return (
    <li
      className={cn("kdw-event", next && "kdw-event--next")}
      data-event-state={event.state}
    >
      <span className="kdw-event-time">{formatClock(event.at, timeZone)}</span>
      <span className="kdw-event-track" aria-hidden>
        <i />
      </span>
      <span className="kdw-event-copy">
        <strong>{event.title}</strong>
        <span>{stateText(event, now, timeZone)}</span>
      </span>
      <Icon
        aria-hidden
        className={cn(
          "kdw-event-icon size-4 shrink-0",
          (event.state === "failed" || event.state === "late") && "kdw-event-icon--alert",
        )}
      />
    </li>
  );
}

const WEEKDAY = new Intl.DateTimeFormat("ru-RU", { weekday: "short", timeZone: "UTC" });

function WeekStrip({ upcoming }: { upcoming: DashboardUpcoming }) {
  return (
    <ol className="kdw-week" aria-label="Запуски на неделю">
      {upcoming.week.map((day, index) => {
        const date = new Date(`${day.date}T00:00:00Z`);
        const total = day.planned + day.done + day.failed;
        return (
          <li
            key={day.date}
            className={cn(index === 0 && "kdw-week-today")}
            aria-label={`${WEEKDAY.format(date)}, ${date.getUTCDate()}: запусков ${total}`}
          >
            <span>{WEEKDAY.format(date)}</span>
            <strong>{date.getUTCDate()}</strong>
            <i data-count={Math.min(total, 3)} />
          </li>
        );
      })}
    </ol>
  );
}

export const UPCOMING_TASKS_WIDGET: DashboardWidget = {
  id: "upcoming-tasks",
  title: "Ближайшие задачи",
  purpose: "Что уже отработало сегодня и что запустится дальше по расписанию агентов.",
  action: { label: "Открыть задачи", short: "Задачи", to: "/cron" },
  Body: UpcomingBody,
};
