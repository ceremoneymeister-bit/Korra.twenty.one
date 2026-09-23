// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ICloudCalendarCard } from "./ICloudCalendarCard";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ getICloudCalendarStatus: vi.fn(), connectICloudCalendar: vi.fn(), disconnectICloudCalendar: vi.fn() }));
vi.mock("@/lib/api", () => ({ api }));

let root: Root;
let host: HTMLDivElement;

beforeEach(() => {
  for (const method of Object.values(api)) method.mockReset();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

async function change(id: string, value: string) {
  const input = host.querySelector<HTMLInputElement>(`#${id}`)!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

it("проверяет пароль до сохранения и показывает общий доступ", async () => {
  api.getICloudCalendarStatus.mockResolvedValue({ state: "not_connected", account: null });
  api.connectICloudCalendar.mockResolvedValue({ state: "connected", account: "person@icloud.com", calendars: 2 });
  await act(async () => { root.render(<ICloudCalendarCard />); await Promise.resolve(); });
  expect(host.textContent).toContain("создайте отдельный пароль приложения");
  await change("icloud-username", "person@icloud.com");
  await change("icloud-app-password", "app-secret");
  await act(async () => { host.querySelector<HTMLButtonElement>("button:not([disabled])")?.click(); await Promise.resolve(); });
  // The first enabled button is the connection action in the unconnected card.
  expect(api.connectICloudCalendar).toHaveBeenCalledWith("person@icloud.com", "app-secret");
  expect(host.textContent).toContain("Все агенты и виджет используют это подключение");
  expect(host.textContent).toContain("Проверка прошла: календарь отвечает");
  expect(host.textContent).not.toContain("app-secret");
});
