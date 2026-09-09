// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fetchJSON } from "@/lib/api";
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
  const actions = [...documents.querySelectorAll("a")];
  const intake = actions.find((action) => action.textContent === "Приёмка")!;
  const documentsLink = actions.find((action) => action.textContent === "Открыть документы")!;
  expect(intake.getAttribute("href")).toBe("/c/calc21/orders/folder-124/intake");
  expect(documentsLink.getAttribute("href")).toBe("/c/calc21/files?order=folder-124");
  expect(container.textContent).not.toContain("Проверить комплект");
  expect(container.textContent).not.toContain("Открыть у расчётчика");
  expect(container.textContent).not.toContain("Запустить стадию");
  expect(container.textContent?.toLowerCase()).not.toContain("автоматическ");
  expect(request.mock.calls.map(([url]) => url).sort()).toEqual(["/api/calc/orders", "/api/calc/orders/folder-124"]);
  await act(async () => documentsLink.click());
  expect(container.querySelector('[data-testid="location"]')?.textContent).toBe(
    "/files?order=folder-124",
  );
});

it("opens intake by navigation without dispatching from the order card", async () => {
  await act(async () => root.render(
    <MemoryRouter initialEntries={["/orders?order=folder-124"]}>
      <CalcOrdersPage />
      <LocationProbe />
    </MemoryRouter>,
  ));
  const intake = [...container.querySelectorAll("a")]
    .find((action) => action.textContent === "Приёмка")!;
  await act(async () => intake.click());
  expect(container.querySelector('[data-testid="location"]')?.textContent).toBe(
    "/orders/folder-124/intake",
  );
  expect(request.mock.calls.some(([, options]) => options?.method === "POST")).toBe(false);
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
