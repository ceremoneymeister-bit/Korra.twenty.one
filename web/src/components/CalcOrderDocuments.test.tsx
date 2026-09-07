// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import CalcOrderDocuments, { type CalcOrderDocument } from "./CalcOrderDocuments";

vi.mock("@/lib/api", () => ({ withBasePath: (path: string) => `/c/calc21${path}` }));
const file = (path: string, index = 0): CalcOrderDocument => ({
  name: path.split("/").at(-1)!, relative_path: path, bytes: 5, download_url: `/api/calc/folders/order/files/${index}`,
});
let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement("div"); document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount()); container.remove();
  vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers();
});
async function click(label: string) {
  const button = [...container.querySelectorAll("button")].find((item) => item.getAttribute("aria-label") === label || item.textContent === label);
  expect(button, label).toBeDefined();
  await act(async () => button!.click());
}
async function search(query: string) {
  const input = container.querySelector("input")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, query);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
const rows = () => [...container.querySelectorAll("ul > li")];

it("opens nested and empty folders, navigates breadcrumbs/back and searches the whole order", async () => {
  await act(async () => root.render(<CalcOrderDocuments title="Исходные документы"
    files={[file("А/Сборка/деталь.pdf"), file("Б/деталь.pdf", 1)]} directories={["Пусто/Архив"]} />));
  expect(rows()).toHaveLength(3);
  await click("Открыть папку А");
  expect(rows()).toHaveLength(1);
  await click("Открыть папку А/Сборка");
  expect(rows()[0].textContent).toContain("деталь.pdf");
  expect(container.querySelector('nav [aria-current="page"]')?.textContent).toBe("Сборка");
  await search("ДЕТАЛЬ");
  expect(rows()).toHaveLength(2);
  expect(rows()[0].textContent).toContain("А/Сборка/деталь.pdf");
  expect(rows()[1].textContent).toContain("Б/деталь.pdf");
  await click("Закрыть поиск");
  expect(rows()).toHaveLength(1);
  await click("Назад");
  expect(container.querySelector('nav [aria-current="page"]')?.textContent).toBe("А");
  await click("Все документы");
  await click("Открыть папку Пусто");
  await click("Открыть папку Пусто/Архив");
  expect(container.textContent).toContain("В этой папке пока нет файлов.");
});

it("keeps at most 50 file rows mounted and resets pagination for a search", async () => {
  await act(async () => root.render(<CalcOrderDocuments title="Исходные документы"
    files={Array.from({ length: 1001 }, (_, index) => file(`деталь ${index}.pdf`, index))} />));
  expect(rows()).toHaveLength(50);
  expect(container.textContent).toContain("Показано 1–50 из 1001");
  await click("Следующие 50");
  expect(rows()).toHaveLength(50);
  expect(container.textContent).toContain("Показано 51–100 из 1001");
  await search("деталь 1000");
  expect(rows()).toHaveLength(1);
  expect(container.textContent).toContain("Показано 1–1 из 1");
  await search("нет такого");
  expect(container.textContent).toContain("По этому запросу файлов и папок не найдено.");
});

it("downloads the selected duplicate basename through the cabinet prefix with authentication", async () => {
  vi.useFakeTimers();
  const fetch = vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(["12345"]) });
  vi.stubGlobal("fetch", fetch);
  vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:download"), revokeObjectURL: vi.fn() });
  window.__HERMES_SESSION_TOKEN__ = "test-token";
  const anchor = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  await act(async () => root.render(<CalcOrderDocuments title="Исходные документы" files={[file("А/деталь.pdf"), file("Б/деталь.pdf", 1)]} />));
  await search("деталь");
  await click("Скачать Б/деталь.pdf");
  expect(fetch).toHaveBeenCalledWith("/c/calc21/api/calc/folders/order/files/1", {
    credentials: "include", headers: { "X-Hermes-Session-Token": "test-token" },
  });
  expect((anchor.mock.instances[0] as HTMLAnchorElement).download).toBe("деталь.pdf");
  await vi.runAllTimersAsync();
  delete window.__HERMES_SESSION_TOKEN__;
});
