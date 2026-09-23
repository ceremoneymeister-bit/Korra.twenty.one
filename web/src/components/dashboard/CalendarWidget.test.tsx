// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CALENDAR_WIDGET } from "@/components/dashboard/widgets/CalendarWidget";
import type { DashboardCalendarFeed, DashboardCalendarItem } from "@/lib/api";
import type { WidgetSize } from "@/lib/dashboard-layout";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ getDashboardCalendar: vi.fn() }));
vi.mock(import("@/lib/api"), async (importOriginal) => ({
  ...(await importOriginal()),
  api: api as unknown as typeof import("@/lib/api").api,
}));

const TZ = "Asia/Novosibirsk";

function feed(items: DashboardCalendarItem[], google: Partial<DashboardCalendarFeed["google"]> = {}): DashboardCalendarFeed {
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
      ...google,
    },
    schedule: { state: "ok" },
    items,
  };
}

const MEETING: DashboardCalendarItem = {
  kind: "event",
  id: "m1",
  title: "Созвон с поставщиком",
  start: "2026-09-23T14:00:00+07:00",
  end: "2026-09-23T15:00:00+07:00",
  all_day: false,
  url: "https://calendar.google.com/event?eid=m1",
};
const PAST: DashboardCalendarItem = {
  kind: "event",
  id: "m0",
  title: "Планёрка",
  start: "2026-09-23T09:00:00+07:00",
  end: "2026-09-23T09:30:00+07:00",
  all_day: false,
};
const RUN: DashboardCalendarItem = {
  kind: "agent_run",
  id: "job:brief:1",
  title: "Утренняя сводка",
  start: "2026-09-24T08:00:00+07:00",
  profile: "secretary",
  job_id: "brief",
};
const TASK: DashboardCalendarItem = {
  kind: "task",
  id: "job:call:1",
  title: "Позвонить бухгалтеру",
  start: "2026-09-25T10:00:00+07:00",
  profile: "default",
  job_id: "call",
};

let root: Root | null = null;
let container: HTMLDivElement | null = null;

async function mount(size: WidgetSize) {
  const { Body } = CALENDAR_WIDGET;
  container = document.createElement("div");
  document.body.appendChild(container);
  const created = createRoot(container);
  root = created;
  await act(async () =>
    created.render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Body size={size} />
      </MemoryRouter>,
    ),
  );
  for (let i = 0; i < 4; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

function text(): string {
  return container?.textContent ?? "";
}

function link(label: string): HTMLAnchorElement {
  const found = Array.from(container!.querySelectorAll("a")).find((node) =>
    node.textContent?.includes(label),
  );
  expect(found, `ссылка «${label}»`).toBeTruthy();
  return found as HTMLAnchorElement;
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  container?.remove();
  root = null;
  container = null;
});

describe("Календарь на дашборде", () => {
  it("в каталоге называется по-русски и уводит к сервисам", () => {
    expect(CALENDAR_WIDGET).toMatchObject({
      id: "calendar",
      title: "Календарь",
      action: { to: "/connections", short: "Сервисы" },
    });
  });

  it("S показывает ближайшее — крупное время и что это", async () => {
    api.getDashboardCalendar.mockResolvedValue(feed([PAST, MEETING, RUN]));
    await mount("s");
    const next = container!.querySelector("[data-calendar-next]");
    expect(next?.textContent).toContain("Сегодня");
    expect(next?.textContent).toContain("14:00");
    expect(next?.textContent).toContain("Созвон с поставщиком");
    expect(next?.textContent).toContain("Встреча");
    expect(api.getDashboardCalendar).toHaveBeenCalledWith(false);
  });

  it("M показывает день: прошедшее приглушено, встреча открывается в Google", async () => {
    api.getDashboardCalendar.mockResolvedValue(feed([PAST, MEETING, RUN]));
    await mount("m");
    const rows = Array.from(container!.querySelectorAll("ul[aria-label='Сегодня'] li"));
    expect(rows.map((node) => node.textContent)).toEqual([
      expect.stringContaining("09:00Планёрка"),
      expect.stringContaining("14:00Созвон с поставщиком"),
    ]);
    expect(rows[0].querySelector(".opacity-60")).not.toBeNull();
    const meeting = link("Созвон с поставщиком");
    expect(meeting.getAttribute("href")).toBe(MEETING.url);
    expect(meeting.getAttribute("target")).toBe("_blank");
    expect(meeting.getAttribute("aria-label")).toContain("Откроется в Google Календаре");
    expect(container!.querySelector("[data-calendar-live]")?.textContent).toBe("Google · обновлено в 11:25");
  });

  it("M на тесной плитке начинает с того, что ещё впереди", async () => {
    const EARLY = { ...PAST, id: "m-1", title: "Разминка", start: "2026-09-23T08:00:00+07:00", end: "2026-09-23T08:15:00+07:00" };
    const LATE = { ...MEETING, id: "m2", title: "Обзор заказов", start: "2026-09-23T17:30:00+07:00", end: "2026-09-23T18:00:00+07:00" };
    api.getDashboardCalendar.mockResolvedValue(feed([EARLY, PAST, MEETING, LATE]));
    await mount("m");
    const titles = Array.from(container!.querySelectorAll("ul[aria-label='Сегодня'] li")).map((node) => node.textContent);
    // До измерения помещаются три строки: одна прошедшая уходит первой.
    expect(titles).toEqual([
      expect.stringContaining("Планёрка"),
      expect.stringContaining("Созвон с поставщиком"),
      expect.stringContaining("Обзор заказов"),
    ]);
    expect(container!.querySelector("[data-calendar-live]")?.textContent).toContain("ещё 1");
  });

  it("L показывает неделю по дням, задачи и запуски ведут в «Задачи»", async () => {
    api.getDashboardCalendar.mockResolvedValue(feed([MEETING, RUN, TASK]));
    await mount("l");
    const headings = Array.from(container!.querySelectorAll("h4")).map((node) => node.textContent);
    expect(headings.slice(0, 3)).toEqual(["Сегодня", "Завтра", expect.stringMatching(/^пт, 25 сентября/)]);
    expect(link("Утренняя сводка").getAttribute("href")).toBe("/cron");
    expect(text()).toContain("Запуск агента · secretary");
    expect(text()).toContain("Задача · Главный агент");
  });

  it("без Google не прикидывается пустой неделей и ведёт подключать", async () => {
    api.getDashboardCalendar.mockResolvedValue(
      feed([], { state: "not_connected", action: "connect", fetched_at: null, account: "" }),
    );
    await mount("m");
    expect(text()).toContain("Календарь не подключён");
    expect(text()).toContain("агенты смогут отвечать о вашем расписании");
    expect(link("Подключить").getAttribute("href")).toBe(
      "/connections?connect=calendar&profile=default",
    );
  });

  it("без Google всё равно показывает задачи и запуски, а подключение — строкой", async () => {
    api.getDashboardCalendar.mockResolvedValue(
      feed([RUN], { state: "not_connected", action: "connect", fetched_at: null }),
    );
    await mount("l");
    expect(text()).toContain("Утренняя сводка");
    const notice = container!.querySelector("[data-calendar-notice='not_connected']");
    expect(notice?.textContent).toContain("Календарь не подключён");
    expect(link("Подключить").getAttribute("href")).toContain("/connections?connect=calendar");
  });

  it("S без Google ведёт подключать, а не показывает запуск вместо встречи", async () => {
    api.getDashboardCalendar.mockResolvedValue(
      feed([RUN], { state: "not_connected", action: "connect", fetched_at: null }),
    );
    await mount("s");
    expect(container!.querySelector("[data-calendar-next]")).toBeNull();
    expect(text()).toContain("Календарь не подключён");
    expect(link("Подключить").getAttribute("href")).toContain("/connections?connect=calendar");
  });

  it("истёкший доступ просит переподключить у источника", async () => {
    api.getDashboardCalendar.mockResolvedValue(
      feed([], { state: "reauthorization_required", action: "reconnect", source_profile: "assistant" }),
    );
    await mount("s");
    expect(text()).toContain("Доступ к Google истёк");
    expect(link("Переподключить").getAttribute("href")).toBe(
      "/connections?connect=calendar&profile=assistant",
    );
  });

  it("сбой Google: встречи помечены как старые, «Повторить» просит свежие", async () => {
    api.getDashboardCalendar.mockResolvedValue(
      feed([MEETING], {
        stale: true,
        error: { code: "google_unavailable", message: "x", action: "retry" },
        action: "retry",
      }),
    );
    await mount("m");
    expect(text()).toContain("Созвон с поставщиком");
    expect(text()).toContain("Google не ответил");
    const retry = Array.from(container!.querySelectorAll("button")).find((node) =>
      node.textContent?.includes("Повторить"),
    );
    expect(retry).toBeTruthy();
    await act(async () => retry!.click());
    expect(api.getDashboardCalendar).toHaveBeenLastCalledWith(true);
  });

  it("если панель не ответила — говорит об этом и даёт повторить", async () => {
    api.getDashboardCalendar.mockRejectedValue(new Error("offline"));
    await mount("m");
    expect(text()).toContain("Не удалось прочитать календарь");
    expect(text()).not.toContain("Неделя свободна");
  });

  it("свободный день — это ответ, а не пустота", async () => {
    api.getDashboardCalendar.mockResolvedValue(feed([RUN]));
    await mount("m");
    expect(text()).toContain("Сегодня ничего не запланировано");
    expect(text()).toContain("Дальше: Завтра, 08:00 · Утренняя сводка");
  });
});
