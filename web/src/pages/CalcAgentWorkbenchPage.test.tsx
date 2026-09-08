// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useNavigate } from "react-router";
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
const intakeMocks = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock("@/hooks/useAgentTabs", () => ({ useAgentTabs: () => agentTabs }));
vi.mock("@/lib/calc-intake-handoff", async () => {
  const actual = await vi.importActual<typeof import("@/lib/calc-intake-handoff")>(
    "@/lib/calc-intake-handoff",
  );
  return { ...actual, getIntakeHandoff: intakeMocks.get };
});
vi.mock("@/pages/BubbleChatPage", () => ({
  default: ({
    agentProfile,
    draft,
    active,
    sessionRequest,
    sessionGuard,
    composeLocked,
    onStreamingChange,
    onConversationChange,
  }: {
    agentProfile: string;
    draft: string | null;
    active: boolean;
    sessionRequest?: { sessionId: string; refreshKey: number } | null;
    sessionGuard?: { sessionId: string; locked: boolean } | null;
    composeLocked?: boolean;
    onStreamingChange?: (profile: string, streaming: boolean) => void;
    onConversationChange?: (sessionId: string | null) => void;
  }) => (
    <div
      data-profile={agentProfile}
      data-active={active}
      data-session-request={sessionRequest?.sessionId}
      data-refresh-key={sessionRequest?.refreshKey}
      data-guard-session={sessionGuard?.sessionId}
      data-guard-locked={sessionGuard?.locked}
      data-compose-locked={composeLocked}
    >
      {draft}
      <button aria-label="stream" data-testid={`stream-${agentProfile || "root"}`} onClick={() => onStreamingChange?.(agentProfile, true)} />
      <button aria-label="idle" data-testid={`idle-${agentProfile || "root"}`} onClick={() => onStreamingChange?.(agentProfile, false)} />
      <button aria-label="switch" data-testid={`switch-${agentProfile || "root"}`} onClick={() => onConversationChange?.("ordinary-session")} />
      <button aria-label="restore intake" data-testid={`restore-${agentProfile || "root"}`} onClick={() => onConversationChange?.(`intake_${"b".repeat(40)}`)} />
    </div>
  ),
}));
vi.mock("@/components/DeleteConfirmDialog", () => ({ DeleteConfirmDialog: () => null }));
vi.mock("@/components/IntakePreparationPanel", () => ({
  IntakePreparationPanel: ({ handoffId }: { handoffId: string }) => <div data-preparation={handoffId} />,
}));
vi.mock("@nous-research/ui/ui/components/toast", () => ({ Toast: () => null }));

let container: HTMLDivElement;
let root: Root;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

beforeEach(() => {
  intakeMocks.get.mockReset();
  intakeMocks.get.mockRejectedValue(new Error("unexpected intake lookup"));
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  delete window.__KORRA_UI_MODE__;
  vi.useRealTimers();
});

async function mount(mode: string, path = "/agents") {
  window.__KORRA_UI_MODE__ = mode;
  await act(async () => {
    root.render(<MemoryRouter initialEntries={[path]}><AgentWorkbenchPage /></MemoryRouter>);
  });
}

function NavigateButton({ to }: { to: string }) {
  const navigate = useNavigate();
  return <button data-testid="navigate" onClick={() => navigate(to)}>navigate</button>;
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
    expect(container.querySelector('[data-preparation]')).toBeNull();
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

  it("ignores Calc21 intake links outside managed calculator mode", async () => {
    const id = `intake_${"f".repeat(40)}`;
    await mount("fleet", `/agents?agent=default&intake=${id}`);
    expect(intakeMocks.get).not.toHaveBeenCalled();
    expect(container.querySelector('[aria-label="Передача заказа приёмщику"]')).toBeNull();
    expect(container.querySelector('[data-profile=""]')?.getAttribute("data-session-request")).toBeNull();
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

  it("polls through an early receipt, refreshes the bound session and then unlocks it", async () => {
    vi.useFakeTimers();
    const id = `intake_${"a".repeat(40)}`;
    const base = {
      handoff_id: id,
      order_id: "folder-124",
      order_name: "Сделка 124",
      session_id: id,
      profile: "default" as const,
      snapshot_id: "snapshot-1",
      received_at: null,
    };
    intakeMocks.get
      .mockResolvedValueOnce({ ...base, status: "running", initial_run_active: true, chat_blocked: true })
      .mockResolvedValueOnce({ ...base, status: "received", initial_run_active: true, chat_blocked: true, received_at: 1_788_800_000 })
      .mockResolvedValueOnce({ ...base, status: "received", initial_run_active: false, chat_blocked: false, received_at: 1_788_800_000 });

    await mount("calc", `/agents?agent=default&intake=${id}`);
    await vi.waitFor(() => expect(intakeMocks.get).toHaveBeenCalledTimes(1));
    let rootChat = container.querySelector('[data-profile=""]')!;
    expect(rootChat.getAttribute("data-session-request")).toBe(id);
    expect(rootChat.getAttribute("data-refresh-key")).toBe("0");
    expect(rootChat.getAttribute("data-guard-locked")).toBe("true");

    await act(async () => { await vi.advanceTimersByTimeAsync(1_500); });
    expect(intakeMocks.get).toHaveBeenCalledTimes(2);
    rootChat = container.querySelector('[data-profile=""]')!;
    expect(rootChat.getAttribute("data-refresh-key")).toBe("1");
    expect(rootChat.getAttribute("data-guard-locked")).toBe("true");

    await act(async () => { await vi.advanceTimersByTimeAsync(1_500); });
    expect(intakeMocks.get).toHaveBeenCalledTimes(3);
    rootChat = container.querySelector('[data-profile=""]')!;
    expect(rootChat.getAttribute("data-refresh-key")).toBe("2");
    expect(rootChat.getAttribute("data-guard-locked")).toBe("false");
    expect(container.textContent).toContain("Заказ передан");
    expect(container.textContent).toContain("Сделка 124");
    expect(container.querySelectorAll('[data-preparation]')).toHaveLength(1);
    expect(container.querySelector('[data-preparation]')?.getAttribute("data-preparation")).toBe(id);
    expect(container.querySelector('[data-profile="calc-norm"]')?.getAttribute("data-session-request")).toBeNull();
  });

  it("defers the intake view during an existing root stream and stops pulling after a normal session switch", async () => {
    const id = `intake_${"a".repeat(40)}`;
    intakeMocks.get.mockResolvedValue({
      handoff_id: id, order_id: "folder-124", order_name: "Сделка 124",
      session_id: id, profile: "default", snapshot_id: "snapshot-1",
      status: "running", initial_run_active: true, chat_blocked: true, received_at: null,
    });
    window.__KORRA_UI_MODE__ = "calc";
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={["/agents"]}>
          <AgentWorkbenchPage />
          <NavigateButton to={`/agents?agent=default&intake=${id}`} />
        </MemoryRouter>,
      );
    });
    await act(async () => container.querySelector<HTMLButtonElement>('[data-testid="stream-root"]')!.click());
    await act(async () => container.querySelector<HTMLButtonElement>('[data-testid="navigate"]')!.click());
    await vi.waitFor(() => expect(intakeMocks.get).toHaveBeenCalled());
    expect(container.textContent).toContain("В этом чате уже идёт ответ");
    expect(container.textContent).toContain("текущий ответ сохранится");

    await act(async () => container.querySelector<HTMLButtonElement>('[data-testid="switch-root"]')!.click());
    expect(container.textContent).not.toContain("Передача заказа приёмщику");
    expect(container.querySelector('[data-preparation]')).toBeNull();
    const rootChat = container.querySelector('[data-profile=""]')!;
    expect(rootChat.getAttribute("data-session-request")).toBeNull();
    // Guard remains attached to the original durable session in case it is
    // selected again before the uncertain external run reconciles.
    expect(rootChat.getAttribute("data-guard-session")).toBe(id);
    expect(rootChat.getAttribute("data-guard-locked")).toBe("true");
  });

  it("restores a guarded intake when its root session is selected from history", async () => {
    vi.useFakeTimers();
    const id = `intake_${"b".repeat(40)}`;
    intakeMocks.get.mockResolvedValue({
      handoff_id: id, order_id: "folder-125", order_name: "Сделка 125",
      session_id: id, profile: "default", snapshot_id: "snapshot-2",
      status: "needs_attention", initial_run_active: false, chat_blocked: true,
      received_at: null, error_code: "dispatch_unknown",
    });
    await mount("calc", "/agents");
    await act(async () => container.querySelector<HTMLButtonElement>('[data-testid="restore-root"]')!.click());
    await vi.waitFor(() => expect(intakeMocks.get).toHaveBeenCalledWith(id));
    const rootChat = container.querySelector('[data-profile=""]')!;
    expect(rootChat.getAttribute("data-guard-session")).toBe(id);
    expect(rootChat.getAttribute("data-guard-locked")).toBe("true");
    expect(container.textContent).toContain("Доставка пока не подтверждена");
    expect(container.querySelector('[aria-label="Закрыть передачу заказа"]')).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(intakeMocks.get).toHaveBeenCalledTimes(1);
  });

  it("does not load or guard a session that the backend failed to create", async () => {
    const id = `intake_${"c".repeat(40)}`;
    intakeMocks.get.mockResolvedValue({
      handoff_id: id, order_id: "folder-126", order_name: "Сделка 126",
      session_id: id, profile: "default", snapshot_id: "snapshot-3",
      status: "needs_attention", initial_run_active: false, chat_blocked: false,
      session_created: false, received_at: null, error_code: "session_create_failed",
    });
    await mount("calc", `/agents?agent=default&intake=${id}`);
    await vi.waitFor(() => expect(intakeMocks.get).toHaveBeenCalledWith(id));
    const rootChat = container.querySelector('[data-profile=""]')!;
    expect(rootChat.getAttribute("data-session-request")).toBeNull();
    expect(rootChat.getAttribute("data-guard-session")).toBeNull();
    expect(rootChat.getAttribute("data-compose-locked")).toBe("true");
    expect(container.textContent).toContain("Чат приёмщика не создан");
    expect(container.textContent).toContain("Откройте заказ и повторите передачу");
    expect(container.textContent).not.toContain("продолжить вручную");
  });
});
