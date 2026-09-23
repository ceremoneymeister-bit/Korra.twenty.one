// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ICLOUD_CALENDAR_WIDGET } from "./widgets/ICloudCalendarWidget";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ getICloudCalendarFeed: vi.fn() }));
vi.mock(import("@/lib/api"), async (importOriginal) => ({ ...(await importOriginal()), api: api as unknown as typeof import("@/lib/api").api }));

let root: Root;
let host: HTMLDivElement;

beforeEach(() => {
  api.getICloudCalendarFeed.mockReset();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

async function mount(size: "s" | "m" | "l") {
  await act(async () => {
    root.render(<MemoryRouter><ICLOUD_CALENDAR_WIDGET.Body size={size} /></MemoryRouter>);
    await Promise.resolve();
  });
}

it("объясняет первый шаг и ведёт к настройке доступа", async () => {
  api.getICloudCalendarFeed.mockResolvedValue({ state: "not_connected", account: null, fetched_at: null, events: [] });
  await mount("m");
  expect(host.textContent).toContain("iCloud Calendar не подключён");
  expect(host.querySelector("a")?.getAttribute("href")).toBe("/env#section-icloud-calendar");
  expect(host.textContent).toContain("Подключить iCloud календарь");
});

it("после отказа авторизации скрывает старые встречи и предлагает переподключение", async () => {
  api.getICloudCalendarFeed.mockResolvedValueOnce({
    state: "connected", account: "person@icloud.com", timezone: "UTC",
    now: "2026-09-23T08:00:00Z", fetched_at: "2026-09-23T08:00:00Z",
    events: [{ id: "one", title: "Старая встреча", start: "2026-09-23T09:00:00Z", end: "2026-09-23T10:00:00Z", all_day: false, calendar: "Работа", location: "" }],
  }).mockRejectedValueOnce(new (await import("@/lib/api")).ApiError(424, "access expired", { detail: { code: "auth_error" } }));
  await mount("m");
  expect(host.textContent).toContain("Старая встреча");
  await act(async () => { host.querySelector<HTMLButtonElement>("button[aria-label='Обновить iCloud Calendar']")!.click(); await Promise.resolve(); });
  expect(host.textContent).not.toContain("Старая встреча");
  expect(host.textContent).toContain("Переподключить iCloud");
});

it("показывает настоящую встречу и честно помечает сбой повторного чтения", async () => {
  api.getICloudCalendarFeed.mockResolvedValueOnce({
    state: "connected", account: "person@icloud.com", timezone: "UTC",
    now: "2026-09-23T08:00:00Z", fetched_at: "2026-09-23T08:00:00Z",
    events: [{ id: "one", title: "Разговор с клиентом", start: "2026-09-23T09:00:00Z", end: "2026-09-23T10:00:00Z", all_day: false, calendar: "Работа", location: "" }],
  }).mockRejectedValueOnce(new Error("offline"));
  await mount("m");
  expect(host.textContent).toContain("Разговор с клиентом");
  expect(host.textContent).toContain("09:00");
  await act(async () => { host.querySelector<HTMLButtonElement>("button[aria-label='Обновить iCloud Calendar']")!.click(); await Promise.resolve(); });
  expect(host.textContent).toContain("Не удалось обновить · данные 08:00");
  expect(host.textContent).toContain("Разговор с клиентом");
  expect(api.getICloudCalendarFeed).toHaveBeenLastCalledWith(true);
});

it("выделяет ближайшую встречу и показывает следующие на большой карточке", async () => {
  api.getICloudCalendarFeed.mockResolvedValue({
    state: "connected", account: "person@icloud.com", timezone: "UTC",
    now: "2026-09-23T08:00:00Z", fetched_at: "2026-09-23T08:00:00Z",
    events: [
      { id: "one", title: "Первая встреча", start: "2026-09-23T09:00:00Z", end: "2026-09-23T10:00:00Z", all_day: false, calendar: "Работа", location: "" },
      { id: "two", title: "Вторая встреча", start: "2026-09-24T11:00:00Z", end: "2026-09-24T12:00:00Z", all_day: false, calendar: "Личное", location: "" },
    ],
  });
  await mount("l");
  expect(host.querySelector(".kdw-icloud-featured")?.textContent).toContain("Первая встреча");
  expect(host.querySelector(".kdw-icloud-followups")?.textContent).toContain("Вторая встреча");
  expect(host.textContent).toContain("Завтра");
});

it("показывает старую ленту сразу, пока сервер обновляет iCloud", async () => {
  api.getICloudCalendarFeed.mockResolvedValue({
    state: "connected", account: "person@icloud.com", timezone: "UTC",
    now: "2026-09-23T08:05:00Z", fetched_at: "2026-09-23T08:00:00Z",
    stale: true, refreshing: true, events: [
      { id: "one", title: "Встреча", start: "2026-09-23T09:00:00Z", end: null, all_day: false, calendar: "Работа", location: "" },
    ],
  });
  await mount("m");
  expect(host.textContent).toContain("Встреча");
  expect(host.textContent).toContain("Обновляем · данные 08:00");
  expect(host.querySelector<HTMLButtonElement>("button[aria-label='Обновить iCloud Calendar']")?.disabled).toBe(true);
});
