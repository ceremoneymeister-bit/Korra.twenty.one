/* eslint-disable react-refresh/only-export-components -- карточка и запись каталога живут вместе */
import { useCallback, useEffect, useRef, useState } from "react";
import { CalendarDays, RefreshCw } from "lucide-react";

import { ProductButton } from "@/components/ProductButton";
import { ErrorNote, LoadingNote, NoteTitle, WidgetLink, WidgetStack } from "@/components/dashboard/widget-states";
import { FittedText } from "@/components/dashboard/FittedText";
import type { DashboardWidget, DashboardWidgetBodyProps } from "@/components/dashboard/widget-types";
import { useAvailableHeight } from "@/hooks/useAvailableHeight";
import { ApiError, api, type ICloudCalendarEvent, type ICloudCalendarFeed } from "@/lib/api";
import { dayKey } from "@/lib/dashboard-calendar";

const REFRESH_MS = 5 * 60 * 1000;
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
  const target = new Date(`${date}T12:00:00Z`);
  return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", timeZone: "UTC" }).format(target);
}

function ICloudCalendarBody({ size = "m" }: DashboardWidgetBodyProps) {
  const [feed, setFeed] = useState<ICloudCalendarFeed | null>(null);
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [stale, setStale] = useState(false);
  const [authFailed, setAuthFailed] = useState(false);
  const ticket = useRef(0);
  const [listRef, listHeight] = useAvailableHeight<HTMLUListElement>();

  const load = useCallback(async () => {
    const current = ++ticket.current;
    try {
      const next = await api.getICloudCalendarFeed();
      if (current !== ticket.current) return;
      setFeed(next);
      setPhase("ready");
      setStale(false);
      setAuthFailed(false);
    } catch (cause) {
      if (current !== ticket.current) return;
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
    void load();
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    return () => { ticket.current += 1; window.clearInterval(timer); };
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
  const todayEvents = events.filter((event) => eventDay(event, zone) === today);
  const candidates = size === "m" && todayEvents.length ? todayEvents : events;
  const defaultRows = size === "s" ? 1 : size === "m" ? 2 : 7;
  const capacity = listHeight === null ? defaultRows : Math.max(1, Math.min(defaultRows, Math.floor(listHeight / 44)));
  const shown = candidates.slice(0, capacity);
  const hidden = candidates.length - shown.length;

  if (events.length === 0) {
    return <WidgetStack>
      <p className="text-sm font-semibold text-[var(--neo-text-primary)]">На ближайшую неделю встреч нет</p>
      {size !== "s" ? <p className="text-xs text-[var(--neo-text-secondary)]">Проверены календари учётной записи {feed.account}.</p> : null}
      <Freshness feed={feed} stale={stale} onRetry={load} />
    </WidgetStack>;
  }

  return <WidgetStack>
    {size === "m" && todayEvents.length === 0 ? <p className="truncate text-xs text-[var(--neo-text-secondary)]">Сегодня встреч нет · дальше</p> : null}
    <ul ref={listRef} className="min-h-0 flex-1 overflow-hidden" aria-label="События iCloud Calendar">
      {shown.map((event) => <li key={event.id} className="min-w-0">
        <a href={ICLOUD_CALENDAR_URL} target="_blank" rel="noopener noreferrer"
          className="flex min-h-[44px] min-w-0 items-center gap-2 rounded-[var(--neo-radius-control)] px-1 hover:shadow-[var(--neo-inset-compact)] focus-visible:outline-2 focus-visible:outline-[var(--neo-accent-line)]"
          aria-label={`${event.title}, ${when(event, zone, today)}, ${eventClock(event, zone)}. Открыть iCloud Calendar`}>
          <CalendarDays aria-hidden className="size-4 shrink-0 text-[var(--neo-text-secondary)]" />
          <span className="min-w-0 flex-1">
            <strong className="block truncate text-sm text-[var(--neo-text-primary)]">{event.title}</strong>
            <span className="block truncate text-xs text-[var(--neo-text-secondary)]">{when(event, zone, today)} · {eventClock(event, zone)}{size === "l" ? ` · ${event.calendar}` : ""}</span>
          </span>
        </a>
      </li>)}
    </ul>
    <Freshness feed={feed} stale={stale} onRetry={load} hidden={hidden} />
  </WidgetStack>;
}

function ConnectionStep({ size, title, text, action }: { size: "s" | "m" | "l"; title: string; text: string; action: string }) {
  return <WidgetStack>
    <NoteTitle>{title}</NoteTitle>
    {size === "s" ? null : <FittedText text={text} className="text-[var(--neo-text-secondary)]" />}
    <WidgetLink to="/env#section-icloud-calendar">{action}</WidgetLink>
  </WidgetStack>;
}

function Freshness({ feed, stale, onRetry, hidden = 0 }: { feed: ICloudCalendarFeed; stale: boolean; onRetry: () => void; hidden?: number }) {
  return <div className="flex shrink-0 items-center gap-2 text-xs text-[var(--neo-text-secondary)]">
    <span className="min-w-0 flex-1 truncate" role={stale ? "alert" : "status"}>
      {stale ? "iCloud не ответил · показаны последние данные" : feed.fetched_at ? `iCloud · обновлено ${new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: feed.timezone || "UTC" }).format(new Date(feed.fetched_at))}` : "iCloud Calendar"}{hidden > 0 ? ` · ещё ${hidden}` : ""}
    </span>
    <ProductButton ghost size="sm" onClick={() => void onRetry()} aria-label="Обновить iCloud Calendar" prefix={<RefreshCw className="size-4" aria-hidden />} />
  </div>;
}

export const ICLOUD_CALENDAR_WIDGET: DashboardWidget = {
  id: "icloud-calendar",
  title: "iCloud Calendar",
  purpose: "Встречи из календарей Apple на ближайшую неделю.",
  action: { label: "Открыть подключение iCloud", short: "Подключение", to: "/env#section-icloud-calendar" },
  Body: ICloudCalendarBody,
};
