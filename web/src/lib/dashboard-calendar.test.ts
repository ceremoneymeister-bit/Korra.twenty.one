import { describe, expect, it } from "vitest";

import type { DashboardCalendarFeed, DashboardCalendarItem } from "@/lib/api";
import {
  calendarNotice,
  connectHref,
  dayKey,
  dayLabel,
  groupByDay,
  isPast,
  nextItem,
  timeLabel,
  timesPerWeek,
  todayItems,
  whenLabel,
} from "./dashboard-calendar";

const TZ = "Asia/Novosibirsk";

function feed(items: DashboardCalendarItem[], over: Partial<DashboardCalendarFeed> = {}): DashboardCalendarFeed {
  return {
    timezone: TZ,
    now: "2026-09-23T11:30:00+07:00",
    window: { start: "2026-09-23T00:00:00+07:00", end: "2026-09-30T00:00:00+07:00", days: 7 },
    google: {
      state: "connected",
      source_profile: "default",
      action: null,
      account: "owner@example.com",
      fetched_at: "2026-09-23T04:25:00+00:00",
      stale: false,
      error: null,
    },
    schedule: { state: "ok" },
    items,
    ...over,
  };
}

const standup: DashboardCalendarItem = {
  kind: "event",
  id: "a",
  title: "Планёрка",
  start: "2026-09-23T09:00:00+07:00",
  end: "2026-09-23T09:30:00+07:00",
  all_day: false,
};
const call: DashboardCalendarItem = {
  kind: "event",
  id: "b",
  title: "Созвон",
  start: "2026-09-23T11:00:00+07:00",
  end: "2026-09-23T12:00:00+07:00",
  all_day: false,
};
const reminder: DashboardCalendarItem = {
  kind: "task",
  id: "job:r",
  title: "Позвонить бухгалтеру",
  start: "2026-09-24T10:00:00+07:00",
  profile: "default",
};
const conference: DashboardCalendarItem = {
  kind: "event",
  id: "c",
  title: "Конференция",
  start: "2026-09-25",
  end: "2026-09-27",
  all_day: true,
};

describe("время владельца, а не браузера", () => {
  it("считает день и время в поясе из ответа сервера", () => {
    // 20:30 UTC — уже следующий день в Новосибирске.
    expect(dayKey("2026-09-23T20:30:00Z", TZ)).toBe("2026-09-24");
    expect(timeLabel("2026-09-23T20:30:00Z", TZ)).toBe("03:30");
    expect(dayKey("2026-09-25", TZ)).toBe("2026-09-25");
  });

  it("склоняет число частых запусков", () => {
    expect(timesPerWeek(304)).toBe("304 раза за неделю");
    expect(timesPerWeek(312)).toBe("312 раз за неделю");
    expect(timesPerWeek(21)).toBe("21 раз за неделю");
    expect(timesPerWeek(168)).toBe("168 раз за неделю");
  });

  it("называет дни по-человечески", () => {
    expect(dayLabel("2026-09-23", "2026-09-23")).toBe("Сегодня");
    expect(dayLabel("2026-09-24", "2026-09-23")).toBe("Завтра");
    expect(dayLabel("2026-09-25", "2026-09-23")).toMatch(/^пт, 25 сентября$/);
  });
});

describe("лента недели", () => {
  it("раскладывает строки по дням окна и не теряет свободные дни", () => {
    const days = groupByDay(feed([standup, call, reminder, conference]));
    expect(days).toHaveLength(7);
    expect(days[0]).toMatchObject({ key: "2026-09-23", label: "Сегодня" });
    expect(days[0].items.map((item) => item.title)).toEqual(["Планёрка", "Созвон"]);
    expect(days[1].items.map((item) => item.title)).toEqual(["Позвонить бухгалтеру"]);
    // Событие на два дня (конец у Google не включительно) — в обоих днях.
    expect(days[2].items.map((item) => item.title)).toEqual(["Конференция"]);
    expect(days[3].items.map((item) => item.title)).toEqual(["Конференция"]);
    expect(days[4].items).toEqual([]);
    expect(todayItems(feed([standup, reminder])).map((item) => item.title)).toEqual(["Планёрка"]);
  });

  it("ближайшим считает идущую встречу, а прошедшую приглушает", () => {
    const current = feed([standup, call, reminder]);
    expect(isPast(standup, current)).toBe(true);
    expect(isPast(call, current)).toBe(false);
    expect(nextItem(current)).toEqual({ item: call, ongoing: true });
    const later = feed([standup, reminder], { now: "2026-09-23T15:00:00+07:00" });
    expect(nextItem(later)).toEqual({ item: reminder, ongoing: false });
    expect(whenLabel(reminder, later, { withDay: true })).toBe("Завтра, 10:00");
    expect(whenLabel(conference, later)).toBe("Весь день");
    expect(nextItem(feed([]))).toBeNull();
  });
});

describe("что сказать владельцу", () => {
  it("ведёт подключать календарь к нужному агенту", () => {
    expect(connectHref("default")).toBe("/connections?connect=calendar&profile=default");
    const notice = calendarNotice(
      feed([], {
        google: { ...feed([]).google, state: "not_connected", action: "connect", fetched_at: null },
      }),
    );
    expect(notice).toMatchObject({
      tone: "action",
      title: "Календарь не подключён",
      action: { kind: "link", label: "Подключить", to: "/connections?connect=calendar&profile=default" },
    });
  });

  it("различает истёкший доступ, календарь без прав и сбой Google", () => {
    const base = feed([]).google;
    const expired = calendarNotice(feed([], { google: { ...base, state: "reauthorization_required", source_profile: "assistant" } }));
    expect(expired?.title).toBe("Доступ к Google истёк");
    expect(expired?.action).toEqual({
      kind: "link",
      label: "Переподключить",
      to: "/connections?connect=calendar&profile=assistant",
    });
    expect(calendarNotice(feed([], { google: { ...base, state: "calendar_not_selected" } }))?.title).toBe(
      "Google подключён без календаря",
    );
    expect(calendarNotice(feed([], { google: { ...base, state: "app_unavailable" } }))?.action).toBeNull();
    expect(calendarNotice(feed([], { google: { ...base, state: "error" } }))?.action).toEqual({
      kind: "retry",
      label: "Повторить",
    });
    expect(calendarNotice(feed([]))).toBeNull();
  });

  it("честно помечает встречи последнего удачного чтения", () => {
    const stale = calendarNotice(feed([call], { google: { ...feed([]).google, stale: true } }));
    expect(stale).toMatchObject({ title: "Google не ответил", text: "Встречи показаны на 11:25." });
  });
});
