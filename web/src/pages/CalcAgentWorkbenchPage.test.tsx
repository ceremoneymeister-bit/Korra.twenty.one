// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AgentWorkbenchPage from "./AgentWorkbenchPage";

const agentTabs = vi.hoisted(() => ({
  tabs: [{ profile: "", label: "Корра" }, { profile: "calc-norm", label: "Расчётчик" }],
  hiddenTabs: [],
  refresh: vi.fn(async () => {}),
  updateDisplayName: vi.fn(),
  hideTab: vi.fn(),
  showTab: vi.fn(),
  moveTab: vi.fn(),
}));

vi.mock("@/hooks/useAgentTabs", () => ({ useAgentTabs: () => agentTabs }));
vi.mock("@/pages/BubbleChatPage", () => ({
  default: ({ agentProfile, draft, active }: { agentProfile: string; draft: string | null; active: boolean }) => (
    <div data-profile={agentProfile} data-active={active}>{draft}</div>
  ),
}));
vi.mock("@/components/DeleteConfirmDialog", () => ({ DeleteConfirmDialog: () => null }));
vi.mock("@nous-research/ui/ui/components/toast", () => ({ Toast: () => null }));

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  delete window.__KORRA_UI_MODE__;
});

async function mount(mode: string, path = "/agents") {
  window.__KORRA_UI_MODE__ = mode;
  await act(async () => {
    root.render(<MemoryRouter initialEntries={[path]}><AgentWorkbenchPage /></MemoryRouter>);
  });
}

async function openCalculatorMenu() {
  const button = container.querySelector<HTMLButtonElement>('[aria-label="Меню агента «Расчётчик»"]');
  expect(button).not.toBeNull();
  await act(async () => button!.click());
  return document.querySelector('[role="menu"]')!;
}

describe("calculator agent workbench", () => {
  it("сохраняет рабочие действия и скрывает изменение управляемых ролей", async () => {
    await mount("calc");
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toContain("Приёмщик");
    expect(container.querySelector('[data-profile=""][data-active="true"]')).not.toBeNull();
    const menu = await openCalculatorMenu();
    expect(menu.textContent).toContain("Новый чат");
    expect(menu.textContent).toContain("Модель");
    for (const action of ["Переименовать", "Роль и поведение", "Навыки", "Расписание", "Удалить агента"]) {
      expect(menu.textContent).not.toContain(action);
    }
    expect(container.querySelector('[aria-label="Добавить вкладку агента"]')).toBeNull();
  });

  it("сохраняет управление агентами в основном интерфейсе Korra21", async () => {
    await mount("fleet");
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toContain("Корра");
    const menu = await openCalculatorMenu();
    for (const action of ["Переименовать", "Роль и поведение", "Навыки", "Расписание", "Удалить агента"]) {
      expect(menu.textContent).toContain(action);
    }
    expect(container.querySelector('[aria-label="Добавить вкладку агента"]')).not.toBeNull();
  });

  it("передаёт действие заказа только выбранному расчётчику", async () => {
    await mount("calc", "/agents?agent=calc-norm&draft=" + encodeURIComponent("Проверь заказ TEST-01"));
    const calculator = container.querySelector('[data-profile="calc-norm"]')!;
    const main = container.querySelector('[data-profile=""]')!;
    expect(calculator.getAttribute("data-active")).toBe("true");
    expect(calculator.textContent).toBe("Проверь заказ TEST-01");
    expect(main.getAttribute("data-active")).toBe("false");
    expect(main.textContent).toBe("");
    expect(container.querySelectorAll('[role="tabpanel"]')).toHaveLength(2);
  });
});
