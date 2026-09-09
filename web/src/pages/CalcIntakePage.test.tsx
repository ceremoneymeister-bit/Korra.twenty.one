// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { IntakeHandoff } from "@/lib/calc-intake-handoff";
import type { IntakePreparation } from "@/lib/calc-intake-preparation";
import type { OrderCard } from "@/lib/calc-orders";
import { PageHeaderProvider } from "@/contexts/PageHeaderProvider";
import App from "@/App";
import CalcIntakePage from "./CalcIntakePage";

const mocks = vi.hoisted(() => ({
  fetch: vi.fn(), find: vi.fn(), create: vi.fn(), get: vi.fn(),
  preparation: vi.fn(), save: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  fetchJSON: mocks.fetch,
}));
vi.mock("@/lib/calc-intake-handoff", () => ({
  findIntakeHandoff: mocks.find,
  createIntakeHandoff: mocks.create,
  getIntakeHandoff: mocks.get,
}));
vi.mock("@/lib/calc-intake-preparation", () => ({
  getIntakePreparation: mocks.preparation,
  saveIntakeAnswers: mocks.save,
}));
vi.mock("@/components/IntakeAnalysisPanel", () => ({ IntakeAnalysisPanel: () => <div data-testid="analysis-controls" /> }));
vi.mock("@/plugins", () => ({
  usePlugins: () => ({ manifests: [], loading: false }),
  PluginSlot: () => null,
  PluginPage: () => null,
}));
vi.mock("@/themes", () => ({ useTheme: () => ({ theme: { name: "test", layoutVariant: "standard" } }) }));
vi.mock("@/i18n", () => ({ useI18n: () => ({ t: {
  app: { openNavigation: "Открыть", closeNavigation: "Закрыть", navigation: "Навигация", pluginNavSection: "Плагины", system: "Система", nav: {} },
  common: { expand: "Развернуть", collapse: "Свернуть", cancel: "Отмена", loading: "Загрузка" },
  status: { updateHermesConfirmMessage: "", restartGateway: "Перезапустить", restartingGateway: "Перезапуск", updateHermes: "Обновить", updatingHermes: "Обновление" },
  theme: { switchTheme: "Тема" },
} }) }));
vi.mock("@/contexts/ProfileProvider", () => ({ ProfileProvider: ({ children }: { children: ReactNode }) => children }));
vi.mock("@/contexts/useProfileScope", () => ({ useProfileScope: () => ({ profile: "" }) }));
vi.mock("@/contexts/useSystemActions", () => ({ useSystemActions: () => ({ activeAction: null, isBusy: false, isRunning: false, pendingAction: null, runAction: vi.fn() }) }));
vi.mock("@/hooks/useSidebarStatus", () => ({ useSidebarStatus: () => ({ status: null, reachable: true }) }));
vi.mock("@nous-research/ui/hooks/use-below-breakpoint", () => ({ useBelowBreakpoint: () => false }));
vi.mock("@nous-research/ui/ui/components/selection-switcher", () => ({ SelectionSwitcher: () => null }));
vi.mock("@nous-research/ui/ui/components/confirm-dialog", () => ({ ConfirmDialog: () => null }));
vi.mock("@/components/KorraLoader", () => ({ KorraLoader: () => <span>Загрузка</span> }));
vi.mock("@/components/ThemeSwitcher", () => ({ ThemeSwitcher: () => null }));
vi.mock("@/components/AuthWidget", () => ({ AuthWidget: () => null }));
vi.mock("@/components/MemoryPressureBanner", () => ({ MemoryPressureBanner: () => null }));
vi.mock("@/components/CalcFolderUploadPanel", () => ({ CalcFolderUploadPanel: () => null }));
vi.mock("@/components/KorraBrand", () => ({ KorraBrand: () => <span>Korra</span> }));
vi.mock("@/components/TechstkomBrand", () => ({ TechstkomBrand: () => <span>Techstkom</span> }));
vi.mock("@/components/SidebarFooter", () => ({ SidebarFooter: () => null }));
vi.mock("@/components/SidebarStatusStrip", () => ({ SidebarStatusStrip: () => null, gatewayLine: () => ({ label: "", tone: "" }) }));

const order: OrderCard = {
  kind: "draft", order_id: "folder-124", folder_name: "Сделка 124", file_count: 3,
  status: "draft", revision: 1, customer: null, current_stage: null,
  created_at: "2026-09-07T08:00:00Z", updated_at: "2026-09-07T08:00:00Z",
  stages: {}, warnings: [], provisional: false, price: null,
};

function handoff(overrides: Partial<IntakeHandoff> = {}): IntakeHandoff {
  return {
    handoff_id: `intake_${"a".repeat(40)}`, order_id: order.order_id,
    order_name: "Сделка 124", session_id: `intake_${"a".repeat(40)}`,
    profile: "default", snapshot_id: "snapshot-1", status: "received",
    initial_run_active: false, chat_blocked: false, received_at: 1,
    ...overrides,
  };
}

function preparation(handoffId: string): IntakePreparation {
  return {
    handoff_id: handoffId, order_id: order.order_id, snapshot_id: "snapshot-1",
    document_set_revision: 1, editable: true,
    summary: { files_total: 3, engineering_documents: 2, service_files: 1, cached_engineering_documents: 2, documents_without_observation: 0, unsupported_documents: 0, service_file_names: ["Thumbs.db"] },
    initial_answers: { revision: 1, answers: { scope: "whole", scope_note: "", more_documents: "no", quantity_source: "in_documents", notes: "" }, receipt: { actor: "panel", source: "operator", recorded_at: 1, decision_id: "decision-1" } },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function RouteSwitch({ to }: { to: string }) {
  const navigate = useNavigate();
  return <button type="button" onClick={() => navigate(to)}>switch</button>;
}

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

beforeEach(() => {
  window.__KORRA_UI_MODE__ = "calc";
  window.matchMedia = vi.fn().mockImplementation((media: string) => ({
    matches: false, media, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  }));
  mocks.fetch.mockReset().mockResolvedValue(order);
  mocks.find.mockReset().mockResolvedValue({ handoff: handoff() });
  mocks.create.mockReset();
  mocks.get.mockReset();
  mocks.preparation.mockReset().mockImplementation((id: string) => Promise.resolve(preparation(id)));
  mocks.save.mockReset();
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

async function mountPage(key = "page") {
  await act(async () => root.render(
    <MemoryRouter key={key} initialEntries={["/orders/folder-124/intake"]}>
      <PageHeaderProvider pluginTabs={[]}>
        <Routes><Route path="/orders/:orderId/intake" element={<CalcIntakePage />} /></Routes>
      </PageHeaderProvider>
    </MemoryRouter>,
  ));
}

it("restores the saved order handoff on direct link and reload without POST", async () => {
  mocks.find.mockResolvedValueOnce({ handoff: handoff({ order_name: "Старое имя" }) });
  await mountPage();
  await vi.waitFor(() => expect(container.querySelector("h1")?.textContent).toBe("Заказы / Сделка 124 / Приёмка"));
  expect(container.textContent).toContain("Комплект и исходные ответы");
  expect(container.querySelectorAll("select")).toHaveLength(3);
  expect(container.querySelector("details")?.className).not.toContain("max-h-[45vh]");
  expect(mocks.create).not.toHaveBeenCalled();
  expect(mocks.find).toHaveBeenCalledWith("folder-124");
  expect(container.querySelector("a")?.getAttribute("href")).toBe("/orders?order=folder-124");

  await mountPage("reload");
  await vi.waitFor(() => expect(mocks.find).toHaveBeenCalledTimes(2));
  expect(mocks.find.mock.calls.every(([id]) => id === "folder-124")).toBe(true);
  expect(mocks.create).not.toHaveBeenCalled();
});

it("discards a late lookup after navigation to another order", async () => {
  const oldLookup = deferred<{ handoff: IntakeHandoff | null }>();
  const secondOrder = { ...order, order_id: "folder-125", folder_name: "Сделка 125" };
  const secondHandoff = handoff({
    handoff_id: `intake_${"c".repeat(40)}`, session_id: `intake_${"c".repeat(40)}`,
    order_id: secondOrder.order_id, order_name: secondOrder.folder_name,
  });
  mocks.fetch.mockImplementation((url: string) => Promise.resolve(
    url.includes("folder-125") ? secondOrder : order,
  ));
  mocks.find.mockImplementation((id: string) => id === order.order_id
    ? oldLookup.promise
    : Promise.resolve({ handoff: secondHandoff }));
  await act(async () => root.render(
    <MemoryRouter initialEntries={["/orders/folder-124/intake"]}>
      <PageHeaderProvider pluginTabs={[]}>
        <RouteSwitch to="/orders/folder-125/intake" />
        <Routes><Route path="/orders/:orderId/intake" element={<CalcIntakePage />} /></Routes>
      </PageHeaderProvider>
    </MemoryRouter>,
  ));
  await act(async () => [...container.querySelectorAll("button")]
    .find((button) => button.textContent === "switch")!.click());
  await vi.waitFor(() => expect(container.querySelector("h1")?.textContent).toBe("Заказы / Сделка 125 / Приёмка"));
  await act(async () => oldLookup.resolve({ handoff: handoff() }));
  expect(container.querySelector("h1")?.textContent).toBe("Заказы / Сделка 125 / Приёмка");
  expect(mocks.preparation).not.toHaveBeenCalledWith(handoff().handoff_id);
});

it("starts an empty intake once by explicit action and polls the same binding", async () => {
  mocks.find.mockResolvedValueOnce({ handoff: null });
  const pending = deferred<IntakeHandoff>();
  mocks.create.mockReturnValueOnce(pending.promise);
  const running = handoff({ status: "running", initial_run_active: true, received_at: null });
  const received = handoff();
  mocks.get.mockResolvedValueOnce(received);
  await mountPage();
  const start = await vi.waitFor(() => [...container.querySelectorAll("button")].find((button) => button.textContent === "Начать приёмку")!);
  await act(async () => { start.click(); start.click(); });
  expect(mocks.create).toHaveBeenCalledTimes(1);
  await act(async () => pending.resolve(running));
  await vi.waitFor(() => expect(mocks.get).toHaveBeenCalledWith(running.handoff_id));
  expect(mocks.preparation).toHaveBeenCalledWith(received.handoff_id);
});

it("recovers the saved handoff after a lost POST response without a second POST", async () => {
  const saved = handoff();
  mocks.find
    .mockResolvedValueOnce({ handoff: null })
    .mockResolvedValueOnce({ handoff: saved });
  mocks.create.mockRejectedValueOnce(new Error("network response lost"));
  await mountPage();
  const start = await vi.waitFor(() => [...container.querySelectorAll("button")]
    .find((button) => button.textContent === "Начать приёмку")!);
  await act(async () => start.click());
  await vi.waitFor(() => expect(mocks.find).toHaveBeenCalledTimes(2));
  expect(mocks.create).toHaveBeenCalledTimes(1);
  expect(mocks.preparation).toHaveBeenCalledWith(saved.handoff_id);
  expect(container.textContent).not.toContain("Начать приёмку");
});

it("keeps a lost POST uncertain when reconciliation GET also fails", async () => {
  mocks.find
    .mockResolvedValueOnce({ handoff: null })
    .mockRejectedValue(new Error("gateway unavailable"));
  mocks.create.mockRejectedValueOnce(new Error("network response lost"));
  await mountPage();
  const start = await vi.waitFor(() => [...container.querySelectorAll("button")]
    .find((button) => button.textContent === "Начать приёмку")!);
  await act(async () => start.click());
  await vi.waitFor(() => expect(container.textContent).toContain("Новый запуск недоступен"));
  expect(container.textContent).not.toContain("Начать приёмку");
  const refresh = [...container.querySelectorAll("button")]
    .find((button) => button.textContent === "Обновить состояние")!;
  await act(async () => refresh.click());
  expect(mocks.find).toHaveBeenCalledTimes(3);
  expect(mocks.create).toHaveBeenCalledTimes(1);
});

it("permits another explicit start only after reconciliation confirms no handoff", async () => {
  mocks.find.mockResolvedValue({ handoff: null });
  mocks.create
    .mockRejectedValueOnce(new Error("422: Нельзя запустить этот заказ."))
    .mockResolvedValueOnce(handoff());
  await mountPage();
  let start = await vi.waitFor(() => [...container.querySelectorAll("button")]
    .find((button) => button.textContent === "Начать приёмку")!);
  await act(async () => start.click());
  await vi.waitFor(() => expect(mocks.find).toHaveBeenCalledTimes(2));
  expect(mocks.create).toHaveBeenCalledTimes(1);
  start = [...container.querySelectorAll("button")]
    .find((button) => button.textContent === "Начать приёмку")!;
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("Нельзя запустить этот заказ");
  await act(async () => start.click());
  expect(mocks.create).toHaveBeenCalledTimes(2);
});

it("rejects a handoff returned for another order", async () => {
  mocks.find.mockResolvedValueOnce({ handoff: handoff({ order_id: "folder-other" }) });
  await mountPage();
  await vi.waitFor(() => expect(container.querySelector('[role="alert"]')?.textContent).toContain("другому заказу"));
  expect(mocks.preparation).not.toHaveBeenCalled();
  expect(mocks.create).not.toHaveBeenCalled();
});

it("shows stale state and refreshes it only by explicit POST", async () => {
  mocks.find.mockResolvedValueOnce({ handoff: handoff({ status: "stale" }) });
  const current = handoff({ handoff_id: `intake_${"b".repeat(40)}`, session_id: `intake_${"b".repeat(40)}` });
  mocks.create.mockResolvedValueOnce(current);
  await mountPage();
  const refresh = await vi.waitFor(() => [...container.querySelectorAll("button")].find((button) => button.textContent === "Передать актуальный комплект")!);
  expect(mocks.create).not.toHaveBeenCalled();
  await act(async () => refresh.click());
  expect(mocks.create).toHaveBeenCalledTimes(1);
  await vi.waitFor(() => expect(mocks.preparation).toHaveBeenCalledWith(current.handoff_id));
});

it("offers POST retry only for a confirmed pre-run failure", async () => {
  mocks.find.mockResolvedValueOnce({ handoff: handoff({
    status: "needs_attention", error_code: "session_create_failed",
    session_created: false, run_id: null,
  }) });
  mocks.create.mockResolvedValueOnce(handoff());
  await mountPage();
  const retry = await vi.waitFor(() => [...container.querySelectorAll("button")]
    .find((button) => button.textContent === "Повторить начало приёмки")!);
  expect(mocks.create).not.toHaveBeenCalled();
  await act(async () => retry.click());
  expect(mocks.create).toHaveBeenCalledTimes(1);
});

it("rechecks an uncertain outcome with GET and never posts a duplicate run", async () => {
  const uncertain = handoff({
    status: "needs_attention", error_code: "dispatch_unknown",
    chat_blocked: true, run_id: null,
  });
  mocks.find.mockResolvedValueOnce({ handoff: uncertain });
  mocks.get.mockResolvedValueOnce(handoff());
  await mountPage();
  await vi.waitFor(() => expect(container.textContent).toContain("Новый запуск не создаётся"));
  expect(container.textContent).not.toContain("Повторить начало приёмки");
  const refresh = [...container.querySelectorAll("button")]
    .find((button) => button.textContent === "Обновить состояние")!;
  await act(async () => refresh.click());
  expect(mocks.get).toHaveBeenCalledWith(uncertain.handoff_id);
  expect(mocks.create).not.toHaveBeenCalled();
});

it("surfaces an unknown order without starting intake", async () => {
  mocks.fetch.mockRejectedValueOnce(new Error("404: Заказ не найден"));
  await mountPage();
  await vi.waitFor(() => expect(container.querySelector('[role="alert"]')?.textContent).toContain("Заказ не найден"));
  expect(mocks.create).not.toHaveBeenCalled();
});

it("registers the nested route only in the actual calc App and keeps Orders active", async () => {
  await act(async () => root.render(<MemoryRouter initialEntries={["/orders/folder-124/intake"]}><App /></MemoryRouter>));
  await vi.waitFor(() => expect(container.querySelector("h1")?.textContent).toBe("Заказы / Сделка 124 / Приёмка"));
  const ordersLink = [...container.querySelectorAll("a")].find((link) => link.getAttribute("href") === "/orders");
  expect(ordersLink?.getAttribute("aria-current")).toBe("page");

  window.__KORRA_UI_MODE__ = "fleet";
  await act(async () => root.render(<MemoryRouter key="fleet" initialEntries={["/orders/folder-124/intake"]}><App /></MemoryRouter>));
  await vi.waitFor(() => expect(container.textContent).toContain("Такого раздела нет"));
  expect(container.textContent).not.toContain("Заказы / Сделка 124 / Приёмка");
});
