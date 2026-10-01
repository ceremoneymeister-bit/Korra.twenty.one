// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getDashboardView: vi.fn(),
  setDashboardView: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, ...apiMocks } };
});

import ViewSettingsPage from "./ViewSettingsPage";
import { resetAgentsViewForTests } from "@/lib/agents-view";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  localStorage.clear();
  resetAgentsViewForTests();
  const pref = { version: 1, revision: 1, agents_mobile: "tabs", pinned: [], scope: "0123456789abcdef" };
  apiMocks.getDashboardView.mockReset().mockResolvedValue(pref);
  apiMocks.setDashboardView.mockReset().mockImplementation(async (body) => ({ ...pref, ...body, revision: 2 }));
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

it("«Вид»: тема и две карточки способа переключать агентов, выбор сохраняется сразу", async () => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<ViewSettingsPage />));

  const radios = [...container.querySelectorAll<HTMLButtonElement>(".theme-choices button")];
  expect(radios.map((radio) => radio.textContent?.trim().split("Агенты")[0])).toEqual(
    expect.arrayContaining(["Светлая", "Тёмная", "Цвет"]),
  );
  const tabs = container.querySelector<HTMLButtonElement>("[data-agents-mode=tabs]")!;
  const list = container.querySelector<HTMLButtonElement>("[data-agents-mode=list]")!;
  expect(tabs.getAttribute("aria-checked")).toBe("true");
  expect(list.getAttribute("aria-checked")).toBe("false");
  // Мини-превью — картинка для глаз, диктор слышит название и описание.
  expect(list.querySelector(".k-mini")?.getAttribute("aria-hidden")).toBe("true");
  expect(container.textContent).toContain("На компьютере агенты остаются вкладками");

  await act(async () => list.click());
  expect(list.getAttribute("aria-checked")).toBe("true");
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  expect(apiMocks.setDashboardView).toHaveBeenCalledWith({ revision: 1, agents_mobile: "list", pinned: [] });
  expect(container.querySelector("[role=status]")?.textContent).toContain("Сохранено");
});
