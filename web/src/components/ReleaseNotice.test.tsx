// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ReleaseNotice } from "./ReleaseNotice";
import { $seenReleaseIds, markReleaseSeen } from "@/store/release-notice";
const calls = vi.hoisted(() => ({ getUpdatesState: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: calls, HERMES_BASE_PATH: "/c/demo" }));
const release = { release_id: "release-one", title: "Браузер и файлы" };
let root: Root;
let container: HTMLDivElement;
async function render(path = "/") {
  await act(async () => root.render(<MemoryRouter initialEntries={[path]}><ReleaseNotice /></MemoryRouter>));
}
beforeEach(() => {
  localStorage.clear(); $seenReleaseIds.set([]);
  calls.getUpdatesState.mockReset().mockResolvedValue({ installed: release });
  vi.useFakeTimers();
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.useRealTimers(); });
it("закрытое приглашение не возвращается после повторного входа", async () => {
  await render();
  expect(container.textContent).toContain("Что нового в Korra");
  await act(async () => container.querySelector("button")?.click());
  expect(container.querySelector("aside")).toBeNull();
  expect(JSON.parse(localStorage.getItem("korra.seen-releases:/c/demo") ?? "[]")).toContain(release.release_id);
  await act(async () => root.unmount()); root = createRoot(container); await render();
  expect(container.querySelector("aside")).toBeNull();
});
it("ссылка открывает обновления и отмечает выпуск просмотренным", async () => {
  await render();
  expect(container.querySelector("a")?.getAttribute("href")).toBe("/updates");
  await act(async () => container.querySelector("a")?.click());
  expect($seenReleaseIds.get()).toContain(release.release_id);
  expect(container.querySelector("aside")).toBeNull();
});
it("просмотр страницы отмечается тем же механизмом", async () => {
  await render(); await act(async () => markReleaseSeen(release.release_id));
  expect(container.querySelector("aside")).toBeNull();
  markReleaseSeen(release.release_id);
  expect($seenReleaseIds.get()).toEqual([release.release_id]);
});
it("новый выпуск получает одно приглашение, а при откате старое не повторяется", async () => {
  await render(); await act(async () => markReleaseSeen(release.release_id));
  calls.getUpdatesState.mockResolvedValue({ installed: { ...release, release_id: "release-two" } });
  await act(async () => vi.advanceTimersByTimeAsync(60_000));
  expect(container.querySelector("aside")).not.toBeNull();
  await act(async () => container.querySelector("button")?.click());
  calls.getUpdatesState.mockResolvedValue({ installed: release });
  await act(async () => vi.advanceTimersByTimeAsync(60_000));
  expect(container.querySelector("aside")).toBeNull();
});
it("не мешает человеку на странице обновлений", async () => {
  await render("/updates/"); expect(container.querySelector("aside")).toBeNull();
});
it("не показывает ошибки уведомления при недоступном сервере", async () => {
  calls.getUpdatesState.mockRejectedValue(new Error("network")); await render(); expect(container.textContent).toBe("");
});
