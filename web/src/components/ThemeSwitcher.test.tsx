import { THEME_COLORS } from "@/themes/color";
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
  color: "#5275d9",
  scheme: "light",
  previewColor: vi.fn(),
}));

vi.mock("@/themes", () => ({ useTheme: () => state }));

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.clearAllMocks();
  state.themeName = "light";
  state.scheme = "light";
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

it("offers three explicit choices and opens a palette without losing the selected theme", async () => {
  await act(async () => root.render(<ThemeSwitcher />));
  const light = host.querySelector<HTMLButtonElement>('[aria-label="Светлая тема"]')!;
  const dark = host.querySelector<HTMLButtonElement>('[aria-label="Тёмная тема"]')!;
  const color = host.querySelector<HTMLButtonElement>('[aria-label="Цвет"]')!;
  expect(light.getAttribute("aria-pressed")).toBe("true");
  expect(dark.getAttribute("aria-pressed")).toBe("false");
  expect(color.getAttribute("aria-expanded")).toBe("false");
  await act(async () => dark.click());
  expect(state.setTheme).toHaveBeenCalledWith("dark");
  state.setTheme.mockClear();
  await act(async () => color.click());
  expect(state.setTheme).not.toHaveBeenCalled();
  expect(document.querySelector('[role="dialog"]')).not.toBeNull();
  expect(document.querySelector('input[aria-label="Код цвета HEX"]')).not.toBeNull();
  await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Розовый"]')!.click());
  expect(state.setTheme).toHaveBeenCalledWith("color", THEME_COLORS.find(([label]) => label === "Розовый")![1], "light");
});

it.each(["light", "dark", "color"])("collapsed %s opens both schemes and the palette without silently cycling", async name => {
  state.themeName = name;
  await act(async () => root.render(<ThemeSwitcher collapsed />));
  const toggle = host.querySelector<HTMLButtonElement>('[aria-label="Выбрать тему"]')!;
  expect(host.querySelectorAll("button")).toHaveLength(1);
  await act(async () => toggle.click());
  expect(state.setTheme).not.toHaveBeenCalled();
  const dialog = document.querySelector('[role="dialog"]')!;
  expect(dialog.querySelector('[aria-label="Светлая тема"]')).not.toBeNull();
  expect(dialog.querySelector('[aria-label="Тёмная тема"]')).not.toBeNull();
  expect(dialog.querySelector('[aria-label="Цветовой тон"]')).not.toBeNull();
  await act(async () => dialog.querySelector<HTMLButtonElement>('[aria-label="Закрыть палитру"]')!.click());
  expect(document.querySelector('[role="dialog"]')).toBeNull();
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

it("retries a failed save from inside the Appearance palette", async () => {
  state.themeName = "color";
  state.saveState = "error";
  await act(async () => root.render(<ThemeSwitcher labeled />));
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Цвет"]')!.click());
  await act(async () => document.querySelector<HTMLButtonElement>('.theme-palette-popover > .theme-color-reset')!.click());
  expect(state.retryTheme).toHaveBeenCalledOnce();
});

it("keeps the color when switching the light/dark basis", async () => {
  state.themeName = "color";
  await act(async () => root.render(<ThemeSwitcher />));
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Тёмная тема"]')!.click());
  expect(state.setTheme).toHaveBeenCalledWith("color", state.color, "dark");
});
it("does not save the seed when focusing and leaving an unchanged hue slider", async () => {
  await act(async () => root.render(<ThemeSwitcher />));
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Цвет"]')!.click());
  const hue = document.querySelector<HTMLInputElement>('[aria-label="Цветовой тон"]')!;
  await act(async () => { hue.focus(); hue.blur(); });
  expect(state.setTheme).not.toHaveBeenCalled();
});
