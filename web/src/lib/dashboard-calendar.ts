/**
 * Правила карточки «Календарь» без React и сети.
 *
 * Сервер отдаёт неделю владельца одной лентой: встречи из Google, задачи с
 * датой и запуски агентов. Здесь решается, что из неё показать на плитке
 * каждого размера, как назвать день и время и что сказать, если Google не
 * подключён или не ответил. Всё считается в часовом поясе владельца из
 * ответа сервера, а не в поясе браузера: «сегодня» у человека в Новосибирске
 * не должно зависеть от того, откуда он открыл кабинет.
 */

import type { DashboardCalendarFeed, DashboardCalendarItem } from "@/lib/api";

export type CalendarItemKind = DashboardCalendarItem["kind"];

export const KIND_LABELS: Record<CalendarItemKind, string> = {
  event: "Встреча",
  task: "Задача",
  agent_run: "Запуск агента",
};

/** Куда ведёт «Подключить» из карточки: экран сервисов с нужным агентом. */
export function connectHref(profile: string | null | undefined): string {
  const params = new URLSearchParams({ connect: "calendar" });
  if (profile) params.set("profile", profile);
  return `/connections?${params.toString()}`;
}

function partsIn(date: Date, timeZone: string) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return {
    day: `${get("year")}-${get("month")}-${get("day")}`,
    time: `${get("hour")}:${get("minute")}`,
  };
}

function isDateOnly(value: string): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(value);
}

/** Дата в поясе владельца: YYYY-MM-DD. */
export function dayKey(value: string, timeZone: string): string {
  if (isDateOnly(value)) return value;
  return partsIn(new Date(value), timeZone).day;
}

/** Время строки: «14:00». */
export function timeLabel(value: string, timeZone: string): string {
  return partsIn(new Date(value), timeZone).time;
}

function shiftDay(key: string, days: number): string {
  const [year, month, day] = key.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
}

/** «Сегодня», «Завтра» или «чт, 25 сентября». */
export function dayLabel(key: string, todayKey: string): string {
  if (key === todayKey) return "Сегодня";
  if (key === shiftDay(todayKey, 1)) return "Завтра";
  const [year, month, day] = key.split("-").map(Number);
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: "UTC",
    weekday: "short",
    day: "numeric",
    month: "long",
  }).format(new Date(Date.UTC(year, month - 1, day)));
}

export function todayKeyOf(feed: DashboardCalendarFeed): string {
  return dayKey(feed.now, feed.timezone);
}

/** Дни, на которые выпадает строка: многодневное событие — на каждый. */
function itemDays(item: DashboardCalendarItem, timeZone: string): string[] {
  const first = dayKey(item.start, timeZone);
  if (!item.all_day || !item.end || !isDateOnly(item.end)) return [first];
  const days: string[] = [];
  // У Google конец события на весь день — следующий день, не включительно.
  for (let key = first; key < item.end && days.length < 31; key = shiftDay(key, 1)) {
    days.push(key);
  }
  return days.length ? days : [first];
}

export interface CalendarDay {
  key: string;
  label: string;
  items: DashboardCalendarItem[];
}

/** Неделя по дням окна; пустые дни сохраняются — «свободно» тоже ответ. */
export function groupByDay(feed: DashboardCalendarFeed): CalendarDay[] {
  const today = todayKeyOf(feed);
  const first = feed.window.start.slice(0, 10);
  const days: CalendarDay[] = Array.from({ length: feed.window.days }, (_, index) => {
    const key = shiftDay(first, index);
    return { key, label: dayLabel(key, today), items: [] };
  });
  const byKey = new Map(days.map((day) => [day.key, day]));
  for (const item of feed.items) {
    for (const key of itemDays(item, feed.timezone)) {
      byKey.get(key)?.items.push(item);
    }
  }
  return days;
}

export function todayItems(feed: DashboardCalendarFeed): DashboardCalendarItem[] {
  return groupByDay(feed)[0]?.items ?? [];
}

function endOf(item: DashboardCalendarItem): number {
  return new Date(item.end && !isDateOnly(item.end) ? item.end : item.start).getTime();
}

/** Встреча уже прошла — её строка остаётся в дне, но приглушена. */
export function isPast(item: DashboardCalendarItem, feed: DashboardCalendarFeed): boolean {
  if (item.all_day) return false;
  return endOf(item) <= new Date(feed.now).getTime();
}

export interface NextItem {
  item: DashboardCalendarItem;
  /** Встреча уже идёт. */
  ongoing: boolean;
}

/** Ближайшее: идущая сейчас или следующая по времени строка недели. */
export function nextItem(feed: DashboardCalendarFeed): NextItem | null {
  const now = new Date(feed.now).getTime();
  const timed = feed.items
    .filter((item) => !item.all_day && endOf(item) > now)
    .sort((a, b) => new Date(a.start).getTime() - new Date(b.start).getTime());
  if (timed.length) {
    const item = timed[0];
    return { item, ongoing: new Date(item.start).getTime() <= now };
  }
  const allDay = feed.items.find((item) => item.all_day);
  return allDay ? { item: allDay, ongoing: dayKey(allDay.start, feed.timezone) === todayKeyOf(feed) } : null;
}

/** Время строки на плитке: «Весь день», «14:00» или «Завтра, 10:00». */
export function whenLabel(
  item: DashboardCalendarItem,
  feed: DashboardCalendarFeed,
  { withDay = false }: { withDay?: boolean } = {},
): string {
  const key = dayKey(item.start, feed.timezone);
  const today = todayKeyOf(feed);
  const day = key === today ? "" : dayLabel(key, today);
  if (item.all_day) return withDay && day ? `${day}, весь день` : "Весь день";
  const time = timeLabel(item.start, feed.timezone);
  return withDay && day ? `${day}, ${time}` : time;
}

export interface CalendarNotice {
  /** Требуется действие владельца или это временная неудача. */
  tone: "action" | "warning";
  title: string;
  text: string;
  /** link — переход на экран сервисов, retry — повторить чтение. */
  action: { kind: "link"; label: string; to: string } | { kind: "retry"; label: string } | null;
}

/**
 * Что сказать о Google, если встречи сейчас показать нельзя или они старые.
 *
 * Сообщения сервера — английские и для агента; владельцу нужен его язык и
 * одно понятное действие.
 */
export function calendarNotice(feed: DashboardCalendarFeed): CalendarNotice | null {
  const google = feed.google;
  const to = connectHref(google.source_profile);
  if (google.stale) {
    const at = google.fetched_at ? timeLabel(google.fetched_at, feed.timezone) : "";
    return {
      tone: "warning",
      title: "Google не ответил",
      text: at ? `Встречи показаны на ${at}.` : "Показаны последние известные встречи.",
      action: { kind: "retry", label: "Повторить" },
    };
  }
  switch (google.state) {
    case "connected":
      return null;
    case "not_connected":
      return {
        tone: "action",
        title: "Календарь не подключён",
        text: "Подключите Google — здесь появятся встречи, а агенты смогут отвечать о вашем расписании.",
        action: { kind: "link", label: "Подключить", to },
      };
    case "calendar_not_selected":
      return {
        tone: "action",
        title: "Google подключён без календаря",
        text: "Переподключите Google и отметьте «Календарь».",
        action: { kind: "link", label: "Переподключить", to },
      };
    case "reauthorization_required":
      return {
        tone: "action",
        title: "Доступ к Google истёк",
        text: "Google больше не принимает сохранённый доступ. Переподключение займёт минуту.",
        action: { kind: "link", label: "Переподключить", to },
      };
    case "app_unavailable":
      return {
        tone: "warning",
        title: "Google на сервере не настроен",
        text: "Подключение появится после настройки сервера. Напишите в поддержку Korra.",
        action: null,
      };
    default:
      return {
        tone: "warning",
        title: "Не удалось прочитать календарь",
        text: "Google не ответил. Задачи и запуски агентов показаны.",
        action: { kind: "retry", label: "Повторить" },
      };
  }
}

/** «304 раза за неделю», «5 раз за неделю» — для свёрнутых частых запусков. */
export function timesPerWeek(count: number): string {
  const tail = count % 10;
  const tens = count % 100;
  const word = tail >= 2 && tail <= 4 && (tens < 12 || tens > 14) ? "раза" : "раз";
  return `${count} ${word} за неделю`;
}

/** Подпись агента: имя из профиля, «Главный агент» или идентификатор. */
export function agentLabel(profile: string | undefined, names: Record<string, string>): string {
  if (!profile || profile === "default") return names.default || "Главный агент";
  return names[profile] || profile;
}
