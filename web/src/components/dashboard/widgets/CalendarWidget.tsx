/* eslint-disable react-refresh/only-export-components -- виджет и его каталожное описание намеренно живут вместе */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Bot, CalendarDays, ChevronRight, ListTodo, RefreshCw, Users } from "lucide-react";
import { Link } from "react-router";

import { ProductButton } from "@/components/ProductButton";
import { FittedText } from "@/components/dashboard/FittedText";
import type {
  DashboardWidget,
  DashboardWidgetBodyProps,
} from "@/components/dashboard/widget-types";
import { useProfileScope } from "@/contexts/useProfileScope";
import { useAvailableHeight } from "@/hooks/useAvailableHeight";
import { api, type DashboardCalendarFeed, type DashboardCalendarItem } from "@/lib/api";
import {
  KIND_LABELS,
  SCHEDULE_UNREAD,
  agentLabel,
  calendarNotice,
  dayKey,
  dayLabel,
  emptyDayTitle,
  groupByDay,
  isPast,
  nextItem,
  scheduleUnread,
  timeLabel,
  todayItems,
  todayKeyOf,
  timesPerWeek,
  whenLabel,
  type CalendarNotice,
} from "@/lib/dashboard-calendar";
import { cn } from "@/lib/utils";

/**
 * «Календарь» — неделя владельца одной лентой: встречи из Google, задачи с
 * датой и запуски агентов.
 *
 * S — ближайшее, M — сегодняшний день, L — неделя по дням. Сколько строк
 * показать, решает измеренная высота, как у «Агентов»: строка либо
 * помещается целиком, либо не рисуется.
 *
 * Календарь читается через то же подключение Google, которым пользуются
 * агенты. Если его нет, карточка не прикидывается пустой неделей: она
 * говорит, чего не хватает, и ведёт на экран сервисов тем же OAuth, что и
 * раньше. Задачи и запуски агентов видны и без Google.
 *
 * Сбой чтения расписания агентов виден на любом размере и при любом
 * состоянии Google: пустой день без прочитанного расписания — не «свободно».
 */

/** Высота строки — та же цель пальца, что у «Агентов». В разметке она задана
 *  в пикселях (`h-[44px]`), а не шкалой отступов: шкала плотности меняется с
 *  шириной окна, и счёт помещающихся строк разошёлся бы с тем, что нарисовано. */
const ROW_HEIGHT = 44;
/** Заголовок дня на плитке L. */
const DAY_HEIGHT = 28;
/** Сколько строк показать до первого измерения (и в jsdom). */
const ROWS_BEFORE_MEASURE = { m: 3, l: 6 } as const;
/** Карточка сама перечитывает календарь, пока открыта. */
const REFRESH_MS = 5 * 60 * 1000;

const KIND_ICONS = {
  event: Users,
  task: ListTodo,
  agent_run: Bot,
} as const;

function CalendarBody({ size = "m" }: DashboardWidgetBodyProps) {
  const { profiles } = useProfileScope();
  const names = useMemo(
    () =>
      Object.fromEntries(
        (profiles ?? []).map((item) => [item.name, item.display_name?.trim() || ""]),
      ) as Record<string, string>,
    [profiles],
  );
  const [feed, setFeed] = useState<DashboardCalendarFeed | null>(null);
  const [requestState, setRequestState] = useState<"loading" | "ready" | "error">("loading");
  const [refreshing, setRefreshing] = useState(false);
  const mounted = useRef(true);
  const ticket = useRef(0);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const load = useCallback(async (refresh: boolean) => {
    const current = ++ticket.current;
    try {
      const next = await api.getDashboardCalendar(refresh);
      if (!mounted.current || current !== ticket.current) return;
      setFeed(next);
      setRequestState("ready");
    } catch {
      if (!mounted.current || current !== ticket.current) return;
      // Уже показанную неделю не стираем из-за одного неудачного опроса.
      setRequestState((previous) => (previous === "ready" ? previous : "error"));
    }
  }, []);

  useEffect(() => {
    void (async () => {
      await load(false);
    })();
    const timer = window.setInterval(() => void load(false), REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const retry = useCallback(async () => {
    setRefreshing(true);
    setRequestState((previous) => (previous === "error" ? "loading" : previous));
    await load(true);
    if (mounted.current) setRefreshing(false);
  }, [load]);

  if (requestState === "loading" && !feed) {
    return (
      <Note busy>
        <span className="text-sm text-[var(--neo-text-secondary)]">Читаем календарь…</span>
      </Note>
    );
  }

  if (!feed) {
    return (
      <Note>
        <NoteTitle>Не удалось прочитать календарь</NoteTitle>
        {size === "s" ? null : (
          <FittedText
            text="Панель не ответила на запрос. Ничего не изменилось."
            className="text-[var(--neo-text-secondary)]"
          />
        )}
        <RetryButton onClick={() => void retry()} busy={refreshing} />
      </Note>
    );
  }

  const notice = calendarNotice(feed);
  if (size === "s") return <CompactBody feed={feed} notice={notice} onRetry={retry} busy={refreshing} />;
  return (
    <ListBody
      feed={feed}
      notice={notice}
      size={size}
      names={names}
      onRetry={retry}
      busy={refreshing}
    />
  );
}

/** S: ближайшее — идущая сейчас или следующая встреча, задача или запуск. */
function CompactBody({
  busy,
  feed,
  notice,
  onRetry,
}: {
  busy: boolean;
  feed: DashboardCalendarFeed;
  notice: CalendarNotice | null;
  onRetry: () => void;
}) {
  const upcoming = nextItem(feed);
  const unread = scheduleUnread(feed);
  // Без подключённого календаря плитка S не показывает запуск агента вместо
  // встречи: её назначение — встречи, поэтому первым делом — как подключить.
  // О запусках она в этом состоянии не судит, поэтому и сбой расписания
  // здесь не называет: на 390 px третьей строке рядом с кнопкой нет места.
  // M и L показывают его всегда.
  if (notice && (notice.tone === "action" || !upcoming)) {
    return (
      <Note>
        <NoteTitle>{notice.title}</NoteTitle>
        <NoticeAction notice={notice} onRetry={onRetry} busy={busy} />
      </Note>
    );
  }
  if (!upcoming) {
    if (unread) {
      // «Неделя свободна» обещала бы и про запуски, которых не прочитали.
      return (
        <Note>
          <NoteTitle>Встреч на неделе нет</NoteTitle>
          <ScheduleUnreadText short />
        </Note>
      );
    }
    return (
      <Note>
        <NoteTitle>Неделя свободна</NoteTitle>
        <p className="truncate text-xs text-[var(--neo-text-secondary)]">
          Ни встреч, ни запусков до {whenDayOfWindowEnd(feed)}
        </p>
      </Note>
    );
  }
  const { item, ongoing } = upcoming;
  return (
    <div className="flex min-h-0 flex-1 flex-col justify-center gap-1" data-calendar-next>
      <p className="truncate text-xs text-[var(--neo-text-secondary)]">
        {ongoing ? "Сейчас" : whenDay(item, feed)}
      </p>
      <p className="text-3xl leading-none font-medium tabular-nums text-[var(--neo-text-primary)]">
        {item.all_day ? "Весь день" : timeLabel(item.start, feed.timezone)}
      </p>
      {/* С отметкой сбоя название — в одну строку: на 390 px плитке S две
          строки названия и отметка вместе не помещаются. */}
      <p
        className={cn(
          "text-sm font-semibold text-[var(--neo-text-primary)]",
          unread ? "line-clamp-1" : "line-clamp-2",
        )}
      >
        {item.title}
      </p>
      {/* Ближайшее без прочитанного расписания может оказаться не ближайшим:
          отметка сбоя занимает место подписи вида строки. */}
      {unread ? (
        <ScheduleUnreadText short text={notice ? "Данные не обновились" : undefined} />
      ) : notice ? (
        <p className="truncate text-xs text-[var(--neo-text-secondary)]" role="status">
          {notice.title}
        </p>
      ) : (
        <p className="truncate text-xs text-[var(--neo-text-secondary)]">{KIND_LABELS[item.kind]}</p>
      )}
    </div>
  );
}

/**
 * Отметка сбоя расписания одной строкой текста. `shrink-0` обязателен:
 * строка с `truncate` иначе сжимается колонкой до нуля и молча исчезает.
 * `short` — для плитки S: 145 px на 390 px экране, полная фраза не входит.
 */
function ScheduleUnreadText({ short, text }: { short?: boolean; text?: string }) {
  return (
    <p
      className="shrink-0 truncate text-xs text-[var(--neo-text-secondary)]"
      role="alert"
      title={SCHEDULE_UNREAD}
      data-calendar-schedule-error
    >
      {text ?? (short ? "Запуски не прочитаны" : SCHEDULE_UNREAD)}
    </p>
  );
}

/**
 * M и L при прочитанном Google: сбой расписания с повтором на месте строки
 * «Google · обновлено» — кнопка занимает 44 px, и вторая такая строка
 * отняла бы у списка встречу.
 */
function ScheduleUnreadLine({ busy, hidden, onRetry }: { busy: boolean; hidden: number; onRetry: () => void }) {
  return (
    <div className="flex shrink-0 items-center gap-2" role="alert" data-calendar-schedule-error>
      {/* Рядом кнопка на 126 px: полная фраза на 390 px обрезалась бы на
          «не пр…». Значок робота говорит, чьё расписание. */}
      <p className="min-w-0 flex-1 truncate text-xs text-[var(--neo-text-secondary)]" title={SCHEDULE_UNREAD}>
        <Bot aria-hidden className="mr-1 inline size-3.5 align-[-2px]" />
        Расписание не прочитано
        {hidden > 0 ? ` · ещё ${hidden}` : ""}
      </p>
      <RetryButton onClick={onRetry} busy={busy} />
    </div>
  );
}

/** День ближайшего: «Сегодня», «Завтра» или «чт, 25 сентября»; время — крупно ниже. */
function whenDay(item: DashboardCalendarItem, feed: DashboardCalendarFeed): string {
  const today = todayKeyOf(feed);
  return dayLabel(dayKey(item.start, feed.timezone), today);
}

function whenDayOfWindowEnd(feed: DashboardCalendarFeed): string {
  const days = groupByDay(feed);
  return days[days.length - 1]?.label.toLowerCase() ?? "конца недели";
}

type Entry =
  | { type: "day"; key: string; label: string; empty: boolean }
  | { type: "item"; key: string; item: DashboardCalendarItem };

/** M — сегодня, L — неделя по дням; строки — сколько помещается целиком. */
function ListBody({
  busy,
  feed,
  names,
  notice,
  onRetry,
  size,
}: {
  busy: boolean;
  feed: DashboardCalendarFeed;
  names: Record<string, string>;
  notice: CalendarNotice | null;
  onRetry: () => void;
  size: "m" | "l";
}) {
  const [listRef, listHeight] = useAvailableHeight<HTMLDivElement>();

  const entries: Entry[] = useMemo(() => {
    if (size === "m") {
      return todayItems(feed).map((item) => ({ type: "item" as const, key: item.id, item }));
    }
    const result: Entry[] = [];
    for (const day of groupByDay(feed)) {
      result.push({ type: "day", key: `day:${day.key}`, label: day.label, empty: day.items.length === 0 });
      for (const item of day.items) result.push({ type: "item", key: `${day.key}:${item.id}`, item });
    }
    return result;
  }, [feed, size]);

  const itemCount = entries.filter((entry) => entry.type === "item").length;

  // День, который не помещается целиком, начинаем с того, что ещё впереди:
  // прошедшая утренняя планёрка не должна вытеснять встречу через час.
  let fitting = entries;
  if (size === "m") {
    const capacity = listHeight === null ? ROWS_BEFORE_MEASURE.m : Math.floor(listHeight / ROW_HEIGHT);
    let skip = 0;
    while (
      entries.length - skip > capacity &&
      skip < entries.length &&
      entries[skip].type === "item" &&
      isPast((entries[skip] as { item: DashboardCalendarItem }).item, feed)
    ) {
      skip += 1;
    }
    fitting = entries.slice(skip);
  }

  // Сколько целых строк помещается: заголовки дней ниже строк встреч.
  const visible: Entry[] = [];
  if (listHeight === null) {
    let rows = 0;
    for (const entry of fitting) {
      if (entry.type === "item" && rows >= ROWS_BEFORE_MEASURE[size]) break;
      if (entry.type === "item") rows += 1;
      visible.push(entry);
    }
  } else {
    let used = 0;
    for (const entry of fitting) {
      const height = entry.type === "day" ? DAY_HEIGHT : ROW_HEIGHT;
      if (used + height > listHeight) break;
      used += height;
      visible.push(entry);
    }
  }
  // Заголовок дня, чьи строки не поместились, в конце окна только путает:
  // он обещает встречи, которых не видно. «Свободно» — полноценный ответ.
  for (let last = visible[visible.length - 1]; last?.type === "day" && !last.empty; last = visible[visible.length - 1]) {
    visible.pop();
  }
  const hidden = itemCount - visible.filter((entry) => entry.type === "item").length;
  const nothingToday = size === "m" && itemCount === 0;
  const upcoming = nothingToday ? nextItem(feed) : null;
  const unread = scheduleUnread(feed);
  const emptyTitle = emptyDayTitle(feed);

  if (
    itemCount === 0 &&
    notice &&
    feed.items.length === 0 &&
    // Ни встреч, ни расписания не прочитано — о свободных днях сказать нечего.
    (notice.tone === "action" || !emptyTitle)
  ) {
    if (unread) {
      // Два состояния и два действия в 98 px тела M на 390 px: заголовок со
      // строкой сбоя одним блоком, пояснение — сколько останется, кнопки в
      // ряд. Повтор в уведомлении Google перечитывает и расписание.
      return (
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="flex shrink-0 flex-col gap-0.5">
            <NoteTitle>{notice.title}</NoteTitle>
            <ScheduleUnreadText />
          </div>
          <div className="flex min-h-0 flex-1 flex-col py-1">
            <FittedText text={notice.text} linesBeforeMeasure={1} className="text-[var(--neo-text-secondary)]" />
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <NoticeAction notice={notice} onRetry={onRetry} busy={busy} />
            {notice.action?.kind === "retry" ? null : <RetryButton onClick={onRetry} busy={busy} />}
          </div>
        </div>
      );
    }
    return (
      <Note>
        <NoteTitle>{notice.title}</NoteTitle>
        <FittedText text={notice.text} className="text-[var(--neo-text-secondary)]" />
        <NoticeAction notice={notice} onRetry={onRetry} busy={busy} />
      </Note>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div ref={listRef} className="min-h-0 flex-1 overflow-hidden">
        {nothingToday ? (
          <div className="flex flex-col gap-1">
            {emptyTitle ? (
              <p className="text-sm font-semibold text-[var(--neo-text-primary)]">{emptyTitle}</p>
            ) : null}
            {upcoming ? (
              <p className="truncate text-sm text-[var(--neo-text-secondary)]">
                Дальше: {whenLabel(upcoming.item, feed, { withDay: true })} · {upcoming.item.title}
              </p>
            ) : null}
          </div>
        ) : (
          <ul className="grid" aria-label={size === "m" ? "Сегодня" : "Эта неделя"}>
            {visible.map((entry) =>
              entry.type === "day" ? (
                <li key={entry.key} className="flex h-[28px] items-end pb-1">
                  <h4 className="text-xs font-semibold tracking-wide text-[var(--neo-text-secondary)] uppercase">
                    {entry.label}
                    {entry.empty ? <span className="ml-2 normal-case font-normal">· свободно</span> : null}
                  </h4>
                </li>
              ) : (
                <li key={entry.key} className="flex h-[44px] min-w-0">
                  <Row item={entry.item} feed={feed} names={names} />
                </li>
              ),
            )}
          </ul>
        )}
      </div>
      {unread && !notice ? (
        <ScheduleUnreadLine hidden={nothingToday ? 0 : hidden} onRetry={onRetry} busy={busy} />
      ) : (
        <>
          {unread ? <ScheduleUnreadText /> : null}
          <StatusLine feed={feed} notice={notice} hidden={nothingToday ? 0 : hidden} onRetry={onRetry} busy={busy} />
        </>
      )}
    </div>
  );
}

function secondaryText(item: DashboardCalendarItem, names: Record<string, string>): string {
  if (item.kind === "event") return item.location || KIND_LABELS.event;
  const who = agentLabel(item.profile, names);
  const repeat = item.repeats && item.occurrences ? ` · ${timesPerWeek(item.occurrences)}` : "";
  return `${KIND_LABELS[item.kind]} · ${who}${repeat}`;
}

function Row({
  feed,
  item,
  names,
}: {
  feed: DashboardCalendarFeed;
  item: DashboardCalendarItem;
  names: Record<string, string>;
}) {
  const Icon = KIND_ICONS[item.kind];
  const content = (
    <>
      {/* Ширина задана в пикселях, а не шкалой отступов: шкала плотности
          меняет её с шириной окна, а колонка времени должна быть ровной. */}
      <span
        className={cn(
          "w-[64px] shrink-0 whitespace-nowrap tabular-nums text-[var(--neo-text-primary)]",
          item.all_day ? "text-xs font-medium" : "text-sm font-semibold",
        )}
      >
        {item.all_day ? "весь день" : timeLabel(item.start, feed.timezone)}
      </span>
      <Icon aria-hidden className="size-4 shrink-0 text-[var(--neo-text-secondary)]" />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-semibold text-[var(--neo-text-primary)]">
          {item.title}
        </span>
        <span className="block truncate text-xs text-[var(--neo-text-secondary)]">
          {secondaryText(item, names)}
        </span>
      </span>
    </>
  );
  const className = cn(
    "flex min-w-0 flex-1 items-center gap-2 rounded-[var(--neo-radius-control)] px-2",
    isPast(item, feed) && "opacity-60",
  );
  const interactive =
    "transition-shadow hover:shadow-[var(--neo-inset-compact)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--neo-accent-line)]";
  if (item.kind === "event" && item.url) {
    return (
      <a
        href={item.url}
        target="_blank"
        rel="noreferrer noopener"
        className={cn(className, interactive)}
        aria-label={`${item.title}, ${whenLabel(item, feed, { withDay: true })}. Откроется в Google Календаре`}
      >
        {content}
      </a>
    );
  }
  if (item.kind !== "event") {
    return (
      <Link to="/cron" className={cn(className, interactive)}>
        {content}
        <ChevronRight aria-hidden className="size-4 shrink-0 text-[var(--neo-text-secondary)]" />
      </Link>
    );
  }
  return <div className={className}>{content}</div>;
}

/**
 * Строка под списком: откуда встречи и насколько они свежие, либо чего не
 * хватает. Одна строка — сколько бы места она ни заняла, список меряет то,
 * что ему осталось.
 */
function StatusLine({
  busy,
  feed,
  hidden,
  notice,
  onRetry,
}: {
  busy: boolean;
  feed: DashboardCalendarFeed;
  hidden: number;
  notice: CalendarNotice | null;
  onRetry: () => void;
}) {
  const rest = hidden > 0 ? ` · ещё ${hidden}` : "";
  if (notice) {
    return (
      <div className="flex shrink-0 items-center gap-2" data-calendar-notice={feed.google.state}>
        <p className="min-w-0 flex-1 truncate text-xs text-[var(--neo-text-secondary)]" role="status">
          <CalendarDays aria-hidden className="mr-1 inline size-3.5 align-[-2px]" />
          {notice.title}
          {rest}
        </p>
        <NoticeAction notice={notice} onRetry={onRetry} busy={busy} compact />
      </div>
    );
  }
  // Сбой расписания здесь не пишется: его показывает `ScheduleUnreadLine`
  // вместо этой строки или `ScheduleUnreadText` над строкой уведомления.
  const fetched = feed.google.fetched_at ? timeLabel(feed.google.fetched_at, feed.timezone) : "";
  return (
    <p className="shrink-0 truncate text-xs text-[var(--neo-text-secondary)]" data-calendar-live>
      Google{fetched ? ` · обновлено в ${fetched}` : ""}
      {rest}
    </p>
  );
}

function NoticeAction({
  busy,
  compact,
  notice,
  onRetry,
}: {
  busy: boolean;
  compact?: boolean;
  notice: CalendarNotice;
  onRetry: () => void;
}) {
  if (!notice.action) return null;
  if (notice.action.kind === "retry") {
    return <RetryButton onClick={onRetry} busy={busy} label={notice.action.label} />;
  }
  return (
    <Link
      to={notice.action.to}
      className={cn(
        "inline-flex min-h-[44px] w-fit shrink-0 items-center gap-1.5 rounded-lg px-3 text-sm font-semibold text-[var(--neo-text-primary)] transition-shadow hover:shadow-[var(--neo-inset-compact)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--neo-accent-line)]",
        !compact && "border border-[var(--neo-accent-line)]",
      )}
    >
      {notice.action.label}
      <ChevronRight className="size-4 shrink-0" aria-hidden />
    </Link>
  );
}

function Note({ busy, children }: { busy?: boolean; children: ReactNode }) {
  return (
    <div aria-busy={busy || undefined} className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden">
      {children}
    </div>
  );
}

function NoteTitle({ children }: { children: ReactNode }) {
  return (
    <p className="korra-widget-line line-clamp-2 shrink-0 text-sm font-semibold text-[var(--neo-text-primary)]">
      {children}
    </p>
  );
}

function RetryButton({ busy, label = "Повторить", onClick }: { busy?: boolean; label?: string; onClick: () => void }) {
  return (
    <ProductButton
      outlined
      size="sm"
      onClick={onClick}
      disabled={busy}
      className="shrink-0"
      prefix={<RefreshCw className={cn("size-4 shrink-0", busy && "animate-spin")} aria-hidden />}
    >
      {label}
    </ProductButton>
  );
}

export const CALENDAR_WIDGET: DashboardWidget = {
  id: "calendar",
  title: "Календарь",
  purpose: "Встречи из Google, задачи с датой и запуски агентов на неделю.",
  action: { label: "Подключённые сервисы", short: "Сервисы", to: "/env#section-services" },
  Body: CalendarBody,
};
