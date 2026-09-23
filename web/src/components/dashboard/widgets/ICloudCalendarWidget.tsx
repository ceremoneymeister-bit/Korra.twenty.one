/* eslint-disable react-refresh/only-export-components -- карточка и запись каталога живут вместе */
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowUpRight, CalendarDays, RefreshCw } from "lucide-react";

import { ProductButton } from "@/components/ProductButton";
import { ErrorNote, LoadingNote, NoteTitle, WidgetLink, WidgetStack } from "@/components/dashboard/widget-states";
import { FittedText } from "@/components/dashboard/FittedText";
import type { DashboardWidget, DashboardWidgetBodyProps } from "@/components/dashboard/widget-types";
import { useAvailableHeight } from "@/hooks/useAvailableHeight";
import { ApiError, api, type ICloudCalendarEvent, type ICloudCalendarFeed } from "@/lib/api";
import { dayKey } from "@/lib/dashboard-calendar";

const REFRESH_MS = 5 * 60 * 1000;
const POLL_REFRESH_MS = 2 * 1000;
const FOLLOWUP_HEIGHT = 52;
const ICLOUD_CALENDAR_URL = "https://www.icloud.com/calendar/";

function eventDay(event: ICloudCalendarEvent, zone: string): string {
  return event.all_day ? event.start.slice(0, 10) : dayKey(event.start, zone);
}

function eventClock(event: ICloudCalendarEvent, zone: string): string {
  if (event.all_day) return "Весь день";
  return new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: zone }).format(new Date(event.start));
}

function when(event: ICloudCalendarEvent, zone: string, today: string): string {
  const date = eventDay(event, zone);
  if (date === today) return "Сегодня";
  const tomorrow = new Date(`${today}T12:00:00Z`);
  tomorrow.setUTCDate(tomorrow.getUTCDate() + 1);
  if (date === tomorrow.toISOString().slice(0, 10)) return "Завтра";
  const target = new Date(`${date}T12:00:00Z`);
  return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", timeZone: "UTC" }).format(target);
}

function datePart(date: string, part: "day" | "month"): string {
  return new Intl.DateTimeFormat("ru-RU", { [part]: part === "day" ? "numeric" : "short", timeZone: "UTC" })
    .format(new Date(`${date}T12:00:00Z`));
}

function eventCountLabel(count: number): string {
  const form = new Intl.PluralRules("ru-RU").select(count);
  return `${count} ${form === "one" ? "событие" : form === "few" ? "события" : "событий"}`;
}

function ICloudCalendarBody({ size = "m" }: DashboardWidgetBodyProps) {
  const [feed, setFeed] = useState<ICloudCalendarFeed | null>(null);
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [stale, setStale] = useState(false);
  const [authFailed, setAuthFailed] = useState(false);
  const [manualBusy, setManualBusy] = useState(false);
  const ticket = useRef(0);
  const mounted = useRef(true);
  const [listRef, listHeight] = useAvailableHeight<HTMLUListElement>();

  const load = useCallback(async (refresh = false) => {
    const current = ++ticket.current;
    try {
      const next = await api.getICloudCalendarFeed(refresh);
      if (current !== ticket.current || !mounted.current) return;
      setFeed(next);
      setPhase("ready");
      setStale(false);
      setAuthFailed(false);
    } catch (cause) {
      if (current !== ticket.current || !mounted.current) return;
      const detail = cause instanceof ApiError && cause.payload && typeof cause.payload === "object"
        ? (cause.payload as { detail?: { code?: string } }).detail : null;
      if (detail?.code === "auth_error") {
        setFeed(null);
        setAuthFailed(true);
        setPhase("error");
        return;
      }
      setPhase((previous) => previous === "ready" ? previous : "error");
      setStale(true);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void load();
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    return () => { mounted.current = false; ticket.current += 1; window.clearInterval(timer); };
  }, [load]);

  useEffect(() => {
    if (!feed?.refreshing) return;
    const timer = window.setInterval(() => void load(), POLL_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [feed?.refreshing, load]);

  const refresh = useCallback(async () => {
    setManualBusy(true);
    await load(true);
    if (mounted.current) setManualBusy(false);
  }, [load]);

  if (!feed && phase === "loading") return <LoadingNote text="Читаем iCloud Calendar…" />;
  if (authFailed) return <ConnectionStep size={size} title="Доступ к iCloud истёк"
    text="Проверьте пароль приложения в Apple Account и подключите iCloud Calendar заново."
    action="Переподключить iCloud" />;
  if (!feed) return <ErrorNote size={size} title="Не удалось прочитать iCloud Calendar" onRetry={() => void load()} />;
  if (feed.state === "not_connected") {
    return <ConnectionStep size={size} title="iCloud Calendar не подключён"
      text="Добавьте Apple ID и пароль приложения в «Ключах и доступах». После проверки встречи появятся здесь и будут доступны агентам."
      action="Подключить iCloud календарь" />;
  }

  const zone = feed.timezone || "UTC";
  const today = feed.now ? dayKey(feed.now, zone) : dayKey(new Date().toISOString(), zone);
  const now = feed.now ? new Date(feed.now).getTime() : Date.now();
  const events = feed.events.filter((event) => event.all_day
    ? (event.end ? event.end.slice(0, 10) > today : event.start.slice(0, 10) >= today)
    : event.end ? new Date(event.end).getTime() > now : new Date(event.start).getTime() >= now);
  const isStale = Boolean(stale || feed.stale || feed.error_code);
  const busy = Boolean(manualBusy || feed.refreshing);

  if (events.length === 0) {
    return <WidgetStack className="kdw-icloud">
      <div className="kdw-icloud-empty">
        <span className="kdw-icloud-empty-mark" aria-hidden><CalendarDays className="size-5" /></span>
        <div>
          <p className="font-semibold text-[var(--neo-text-primary)]">Неделя свободна</p>
          {size !== "s" ? <p className="text-xs text-[var(--neo-text-secondary)]">
            {isStale ? "В последнем чтении встреч не было" : "На ближайшие семь дней встреч нет"}
          </p> : null}
        </div>
      </div>
      <Freshness feed={feed} stale={isStale} busy={busy} onRetry={refresh} />
    </WidgetStack>;
  }

  const next = events[0];
  const followups = events.slice(1);
  const capacity = listHeight === null ? 3 : Math.max(0, Math.min(6, Math.floor(listHeight / FOLLOWUP_HEIGHT)));
  const shown = size === "l" ? followups.slice(0, capacity) : [];
  const hidden = followups.length - shown.length;
  const weekDays = size === "l" ? Array.from({ length: 7 }, (_, index) => {
    const date = new Date(`${today}T12:00:00Z`);
    date.setUTCDate(date.getUTCDate() + index);
    const key = date.toISOString().slice(0, 10);
    return {
      key,
      day: date.getUTCDate(),
      label: new Intl.DateTimeFormat("ru-RU", { weekday: "short", timeZone: "UTC" }).format(date),
      count: events.filter((event) => eventDay(event, zone) === key).length,
    };
  }) : [];

  return <WidgetStack className="kdw-icloud">
    <a href={ICLOUD_CALENDAR_URL} target="_blank" rel="noopener noreferrer"
      className={`kdw-icloud-featured kdw-icloud-featured--${size}`}
      aria-label={`${next.title}, ${when(next, zone, today)}, ${eventClock(next, zone)}. Открыть iCloud Calendar`}>
      {size === "s" ? <span className="kdw-icloud-compact-time">{eventClock(next, zone)}</span> :
        <span className="kdw-icloud-date" aria-hidden>
          <strong>{datePart(eventDay(next, zone), "day")}</strong>
          <span>{datePart(eventDay(next, zone), "month")}</span>
        </span>}
      <span className="kdw-icloud-featured-copy">
        <span className="kdw-icloud-eyebrow">Следующая · {when(next, zone, today).toLowerCase()}</span>
        <strong title={next.title}>{next.title}</strong>
        <span className="kdw-icloud-featured-meta">
          {size === "s" ? when(next, zone, today) : eventClock(next, zone)} · {next.calendar}
        </span>
      </span>
      {size === "s" ? null : <ArrowUpRight className="kdw-icloud-featured-arrow" aria-hidden />}
    </a>
    {size === "l" ? <ul ref={listRef} className="kdw-icloud-followups" aria-label="Следующие события iCloud Calendar">
      {shown.map((event) => <li key={event.id}>
        <a href={ICLOUD_CALENDAR_URL} target="_blank" rel="noopener noreferrer"
          aria-label={`${event.title}, ${when(event, zone, today)}, ${eventClock(event, zone)}. Открыть iCloud Calendar`}>
          <span className="kdw-icloud-followup-time">{event.all_day ? "весь день" : eventClock(event, zone)}</span>
          <span className="kdw-icloud-followup-copy">
            <strong title={event.title}>{event.title}</strong>
            <small>{when(event, zone, today)} · {event.calendar}</small>
          </span>
        </a>
      </li>)}
    </ul> : null}
    {size === "l" ? <div className="kdw-icloud-week" aria-label="Ближайшие семь дней">
      <span className="kdw-icloud-week-heading">Ближайшие 7 дней · {eventCountLabel(events.length)}</span>
      <div className="kdw-icloud-week-days">
        {weekDays.map((day) => <span key={day.key} className="kdw-icloud-week-day"
          aria-label={`${day.label}, ${day.day}: ${eventCountLabel(day.count)}`} title={`${day.label}, ${day.day}: ${eventCountLabel(day.count)}`}>
          <small>{day.label}</small>
          <i data-active={day.count > 0 || undefined} style={{ height: `${4 + Math.min(day.count, 4) * 6}px` }} aria-hidden />
          <strong>{day.day}</strong>
        </span>)}
      </div>
    </div> : null}
    <Freshness feed={feed} stale={isStale} busy={busy} onRetry={refresh} hidden={hidden} />
  </WidgetStack>;
}

function ConnectionStep({ size, title, text, action }: { size: "s" | "m" | "l"; title: string; text: string; action: string }) {
  return <WidgetStack>
    <NoteTitle>{title}</NoteTitle>
    {size === "s" ? null : <FittedText text={text} className="text-[var(--neo-text-secondary)]" />}
    <WidgetLink to="/env#section-icloud-calendar">{action}</WidgetLink>
  </WidgetStack>;
}

function Freshness({ feed, stale, busy, onRetry, hidden = 0 }: {
  feed: ICloudCalendarFeed; stale: boolean; busy: boolean; onRetry: () => void; hidden?: number;
}) {
  const time = feed.fetched_at ? new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: feed.timezone || "UTC" }).format(new Date(feed.fetched_at)) : "—";
  const status = busy ? `Обновляем · данные ${time}` : stale ? `Не удалось обновить · данные ${time}` : `iCloud · обновлено ${time}`;
  return <div className="kdw-icloud-freshness">
    <span className="min-w-0 flex-1 truncate" role="status" title={status}>
      {status}{hidden > 0 ? ` · ещё ${hidden}` : ""}
    </span>
    <ProductButton ghost size="sm" onClick={() => void onRetry()} disabled={busy}
      aria-label="Обновить iCloud Calendar" prefix={<RefreshCw className={`size-4 ${busy ? "animate-spin" : ""}`} aria-hidden />} />
  </div>;
}

export const ICLOUD_CALENDAR_WIDGET: DashboardWidget = {
  id: "icloud-calendar",
  title: "iCloud Calendar",
  purpose: "Встречи из календарей Apple на ближайшую неделю.",
  action: { label: "Открыть подключение iCloud", short: "Подключение", to: "/env#section-icloud-calendar" },
  Body: ICloudCalendarBody,
};
