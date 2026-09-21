// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ThemeSwitcher } from "./ThemeSwitcher";

const state = vi.hoisted(() => ({
  retryTheme: vi.fn(async () => true),
  saveError: "",
  saveState: "idle" as "idle" | "pending" | "saved" | "error",
  setTheme: vi.fn(async () => true),
  themeName: "light",
}));

vi.mock("@/themes", () => ({ useTheme: () => state }));

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.clearAllMocks();
  state.themeName = "light";
  state.saveState = "idle";
  state.saveError = "";
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

it("renders two centered icon-only choices in the expanded sidebar", async () => {
  await act(async () => root.render(<ThemeSwitcher />));

  const group = host.querySelector<HTMLElement>('[role="group"]');
  const light = host.querySelector<HTMLButtonElement>('[aria-label="Светлая тема"]');
  const dark = host.querySelector<HTMLButtonElement>('[aria-label="Тёмная тема"]');

  expect(group?.className).toContain("grid-cols-2");
  expect(light?.className).toContain("place-items-center");
  expect(dark?.className).toContain("place-items-center");
  expect(light?.textContent).toBe("");
  expect(dark?.textContent).toBe("");
  expect(light?.getAttribute("aria-pressed")).toBe("true");
  expect(dark?.getAttribute("aria-pressed")).toBe("false");

  await act(async () => dark?.click());
  expect(state.setTheme).toHaveBeenCalledWith("dark");
});

it("uses one 44 px direct toggle in the collapsed sidebar", async () => {
  await act(async () => root.render(<ThemeSwitcher collapsed />));

  const toggle = host.querySelector<HTMLButtonElement>('[aria-label="Включить тёмную тему"]');
  expect(host.querySelectorAll("button")).toHaveLength(1);
  expect(toggle?.className).toContain("size-[44px]");

  await act(async () => toggle?.click());
  expect(state.setTheme).toHaveBeenCalledWith("dark");
});

it("shows a portaled save error without changing sidebar geometry", async () => {
  state.saveState = "error";
  state.saveError = "Ошибка сохранения";
  await act(async () => root.render(<ThemeSwitcher />));

  const alert = document.body.querySelector<HTMLElement>('[role="alert"]');
  expect(alert?.textContent).toContain("Ошибка сохранения");
  expect(host.querySelector('[role="alert"]')).toBeNull();

  await act(async () => alert?.querySelector<HTMLButtonElement>("button")?.click());
  expect(state.retryTheme).toHaveBeenCalledOnce();
});

it("keeps the retry target at 44 px regardless of theme density", async () => {
  state.saveState = "error";
  await act(async () => root.render(<ThemeSwitcher />));

  const retry = document.body.querySelector<HTMLButtonElement>('[role="alert"] button');

  // Утилиты Tailwind отмеряны от `--spacing`, а его у нас умножает плотность
  // темы, поэтому цель пальца задаётся абсолютной величиной.
  expect(retry?.className).toContain("min-h-[44px]");
  expect(retry?.className).not.toMatch(/\bmin-h-\d+\b/);
});
