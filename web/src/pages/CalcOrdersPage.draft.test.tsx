// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fetchJSON } from "@/lib/api";
import type { IntakeHandoff } from "@/lib/calc-intake-handoff";
import type { OrderCard } from "@/lib/calc-orders";
import { $folderUpload } from "@/store/calc-folder-upload";
import CalcOrdersPage from "./CalcOrdersPage";

vi.mock("@/lib/api", () => ({ fetchJSON: vi.fn() }));
const request = vi.mocked(fetchJSON);
const draft: OrderCard = {
  kind: "draft", order_id: "folder-124", folder_name: "Сделка 124", file_count: 3,
  status: "draft", revision: 1, customer: null, current_stage: null,
  created_at: "2026-09-07T08:00:00Z", updated_at: "2026-09-07T08:00:00Z",
  stages: {}, warnings: [], provisional: false, price: null,
  detail: {
    stages: {}, events: [], provenance: {}, source_files: [
      { name: "заявка.XLSX" }, { relative_path: "Чертежи/деталь.PDF" }, { name: "примечание.txt" },
    ],
  },
};
let container: HTMLDivElement;
let root: Root;
const handoff = {
  handoff_id: `intake_${"a".repeat(40)}`, order_id: draft.order_id, order_name: "Сделка 124",
  session_id: `intake_${"a".repeat(40)}`, profile: "default", snapshot_id: "snapshot-1",
  status: "running", initial_run_active: true, chat_blocked: true,
  session_created: true, received_at: null,
} satisfies IntakeHandoff;
function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}
beforeEach(() => {
  window.__KORRA_UI_MODE__ = "calc";
  $folderUpload.set(null);
  container = document.createElement("div"); document.body.appendChild(container);
  root = createRoot(container);
  request.mockReset();
  request.mockImplementation(async (url) => {
    if (url === "/api/calc/orders") return { orders: [draft] };
    if (url.endsWith("/intake-handoff")) return handoff;
    return draft;
  });
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); $folderUpload.set(null); delete window.__KORRA_UI_MODE__; });

it("shows a truthful uploaded-document state and a working link to the same order", async () => {
  await act(async () => root.render(
    <MemoryRouter basename="/c/calc21" initialEntries={["/c/calc21/orders?order=folder-124"]}>
      <CalcOrdersPage />
      <LocationProbe />
    </MemoryRouter>,
  ));
  expect(container.querySelector("h2")?.textContent).toBe("Сделка 124");
  expect(container.textContent).toContain("Документы загружены");
  expect(container.textContent).toContain("Приёмщик получит этот комплект и сохранённые результаты подготовки.");
  expect(container.textContent).toContain("Состав и готовность к расчёту сотрудник подтверждает отдельно.");
  const documents = container.querySelector('section[aria-label="Документы заказа"]')!;
  expect(documents.textContent).toContain("Файлов: 3 · PDF: 1 · Excel: 1 · другие: 1");
  const action = documents.querySelector("a")!;
  expect(action.textContent).toBe("Открыть документы");
  expect(action.getAttribute("href")).toBe("/c/calc21/files?order=folder-124");
  expect(documents.textContent).toContain("Передать приёмщику");
  expect(container.textContent).not.toContain("Проверить комплект");
  expect(container.textContent).not.toContain("Открыть у расчётчика");
  expect(container.textContent).not.toContain("Запустить стадию");
  expect(container.textContent?.toLowerCase()).not.toContain("автоматическ");
  expect(request.mock.calls.map(([url]) => url).sort()).toEqual(["/api/calc/orders", "/api/calc/orders/folder-124"]);
  await act(async () => action.click());
  expect(container.querySelector('[data-testid="location"]')?.textContent).toBe(
    "/files?order=folder-124",
  );
});

it("posts once while pending and opens the durable intake handoff", async () => {
  let finishHandoff!: (value: typeof handoff) => void;
  const pendingHandoff = new Promise<typeof handoff>((resolve) => {
    finishHandoff = resolve;
  });
  request.mockImplementation(async (url) => {
    if (url === "/api/calc/orders") return { orders: [draft] };
    if (url.endsWith("/intake-handoff")) return pendingHandoff;
    return draft;
  });
  await act(async () => root.render(
    <MemoryRouter initialEntries={["/orders?order=folder-124"]}>
      <CalcOrdersPage />
      <LocationProbe />
    </MemoryRouter>,
  ));
  const transfer = Array.from(container.querySelectorAll("button"))
    .find((button) => button.textContent?.includes("Передать приёмщику"))!;
  await act(async () => {
    transfer.click();
    transfer.click();
    await Promise.resolve();
  });
  const posts = request.mock.calls.filter(([url]) => url.endsWith("/intake-handoff"));
  expect(posts).toEqual([
    ["/api/calc/orders/folder-124/intake-handoff", { method: "POST" }],
  ]);
  expect(transfer.disabled).toBe(true);
  expect(transfer.textContent).toContain("Передаём");

  await act(async () => {
    finishHandoff(handoff);
    await pendingHandoff;
  });
  expect(container.querySelector('[data-testid="location"]')?.textContent).toBe(
    `/agents?agent=default&intake=${handoff.handoff_id}`,
  );
});

it("shows a handoff error and lets the user retry", async () => {
  let attempts = 0;
  request.mockImplementation(async (url) => {
    if (url === "/api/calc/orders") return { orders: [draft] };
    if (url.endsWith("/intake-handoff")) {
      attempts++;
      if (attempts === 1) throw new Error("503: Приёмщик временно недоступен.");
      return handoff;
    }
    return draft;
  });
  await act(async () => root.render(
    <MemoryRouter initialEntries={["/orders?order=folder-124"]}>
      <CalcOrdersPage />
      <LocationProbe />
    </MemoryRouter>,
  ));
  const transfer = Array.from(container.querySelectorAll("button"))
    .find((button) => button.textContent?.includes("Передать приёмщику"))!;
  await act(async () => {
    transfer.click();
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(attempts).toBe(1);
  expect(container.querySelector('[role="alert"]')?.textContent).toContain(
    "Приёмщик временно недоступен",
  );

  await act(async () => {
    transfer.click();
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(attempts).toBe(2);
  const posts = request.mock.calls.filter(([url]) => url.endsWith("/intake-handoff"));
  expect(posts).toHaveLength(2);
  expect(posts.every(([, options]) => options?.method === "POST")).toBe(true);
  expect(container.querySelector('[data-testid="location"]')?.textContent).toBe(
    `/agents?agent=default&intake=${handoff.handoff_id}`,
  );
});

it("refreshes the order list on upload completion and ignores a stale initial response", async () => {
  let finishInitial!: (value: unknown) => void;
  const initial = new Promise((resolve) => { finishInitial = resolve; });
  const second = { ...draft, order_id: "folder-125", folder_name: "Сделка 125" };
  let lists = 0;
  request.mockImplementation(async (url) => {
    if (url !== "/api/calc/orders") return draft;
    lists++;
    return lists === 1 ? initial : { orders: [second, draft] };
  });
  await act(async () => root.render(<MemoryRouter initialEntries={["/orders?order=folder-124"]}><CalcOrdersPage /></MemoryRouter>));
  const progress = { completed: 1, total: 2, bytes: 5, totalBytes: 10 };
  await act(async () => $folderUpload.set({ uploadId: "upload-125", name: "Сделка 125", status: "uploading", selection: null, progress }));
  expect(lists).toBe(1);
  await act(async () => $folderUpload.set({ uploadId: "upload-125", name: "Сделка 125", status: "complete", selection: null, progress, orderId: second.order_id }));
  expect(lists).toBe(2);
  expect(container.querySelector("aside")?.textContent).toContain("Сделка 125");
  await act(async () => finishInitial({ orders: [draft] }));
  expect(container.querySelector("aside")?.textContent).toContain("Сделка 125");
  expect(container.querySelector("h2")?.textContent).toBe("Сделка 124");
  await act(async () => $folderUpload.set(null));
  expect(lists).toBe(2);
});
