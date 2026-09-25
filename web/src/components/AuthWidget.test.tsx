// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { beforeEach, afterEach, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ base: "", getAuthMe: vi.fn(), logout: vi.fn() }));
vi.mock("@/lib/api", () => ({ get HERMES_BASE_PATH() { return state.base; }, api: state,
  fetchJSON: async () => ({ kind: "cabinet", mode: "client", logout_url: "/cab/logout" }) }));
vi.mock("@/i18n", () => ({ useI18n: () => ({ tr: (text: string) => text }) }));
import { AuthWidget } from "./AuthWidget";
let root: Root;
let host: HTMLDivElement;
beforeEach(() => { state.base = ""; vi.clearAllMocks(); host = document.createElement("div"); root = createRoot(host); });
afterEach(async () => { await act(async () => root.unmount()); delete window.__HERMES_AUTH_REQUIRED__; });

it("shows cabinet logout even when internal dashboard OAuth is disabled", async () => {
  state.base = "/c/probe";
  await act(async () => root.render(<AuthWidget />));
  expect(host.textContent).toContain("Выйти из кабинета");
  expect(host.querySelector("a")?.getAttribute("href")).toBe("/cab/logout");
  expect(state.getAuthMe).not.toHaveBeenCalled();
  expect(state.logout).not.toHaveBeenCalled();
});

it("does not invent an authenticated session for a local panel", async () => {
  await act(async () => root.render(<AuthWidget />));
  expect(host.textContent).toBe("");
});

it("keeps the full accessible name and cabinet route in the compact footer", async () => {
  state.base = "/c/probe";
  await act(async () => root.render(<AuthWidget compact />));
  const link = host.querySelector("a");
  expect(link?.textContent).toBe("Выйти");
  expect(link?.getAttribute("aria-label")).toBe("Выйти из кабинета");
  expect(link?.getAttribute("href")).toBe("/cab/logout");
});

it("keeps OAuth logout failures visible in the compact footer", async () => {
  window.__HERMES_AUTH_REQUIRED__ = true;
  state.getAuthMe.mockResolvedValue({ user_id: "test-user", provider: "test" });
  state.logout.mockRejectedValue(new Error("offline"));
  await act(async () => root.render(<AuthWidget compact />));
  await act(async () => host.querySelector("button")?.click());
  expect(state.logout).toHaveBeenCalledOnce();
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("Не удалось выйти");
});
