// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FilePreviewDialog } from "./FilePreviewDialog";

let host: HTMLDivElement;
let root: Root;
const fetchMock = vi.fn();
const pageData = (page: number) => ({ page, pages: 3, data_url: `data:image/png;base64,page${page}` });
const response = (page: number) => new Response(JSON.stringify(pageData(page)));
function button(label: string) { return document.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!; }
async function render(path = "/work/test.pdf") {
  await act(async () => root.render(<FilePreviewDialog open file={{ name: "test.pdf", path, expectedSha256: "a".repeat(64) }} onOpenChange={() => {}} />));
}
beforeEach(() => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  fetchMock.mockReset(); vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation(async (url: string) => response(Number(new URL(url, "http://localhost").searchParams.get("pdf_page"))));
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });

it("lets a browser without a PDF plugin read every page and keeps the original download", async () => {
  await render();
  expect(document.querySelector('img[alt="Страница 1 из 3"]')?.getAttribute("src")).toBe(pageData(1).data_url);
  expect(button("Предыдущая страница").disabled).toBe(true);
  for (const page of [2, 3]) {
    await act(async () => button("Следующая страница").click());
    expect(document.querySelector(`img[alt="Страница ${page} из 3"]`)).not.toBeNull();
  }
  expect(button("Следующая страница").disabled).toBe(true);
  await act(async () => button("Предыдущая страница").click());
  expect(document.querySelector('img[alt="Страница 2 из 3"]')).not.toBeNull();
  expect(fetchMock.mock.calls.every(([url]) => new URL(url, "http://localhost").searchParams.get("expected_sha256") === "a".repeat(64))).toBe(true);
  expect(document.querySelector('a[download]')?.getAttribute("href")).toContain("/api/files/download?");
});

it("explains a failed PDF preview, offers retry and preserves download", async () => {
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: "PDF защищён паролем. Скачайте оригинал." }), { status: 422 }));
  await render();
  expect(document.querySelector('[role="alert"]')?.textContent).toContain("Скачайте оригинал");
  expect(document.querySelector('a[download]')).not.toBeNull();
  await act(async () => button("Повторить просмотр PDF").click());
  expect(document.querySelector('img[alt="Страница 1 из 3"]')).not.toBeNull();
});

it("shows an HTML proxy failure as text and retries the same PDF page", async () => {
  await render();
  await act(async () => button("Следующая страница").click());
  const downloadHref = document.querySelector('a[download]')?.getAttribute("href");
  fetchMock.mockResolvedValueOnce(new Response("<h1>Панель агента не ответила</h1>", {
    status: 502,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  }));

  await act(async () => button("Предыдущая страница").click());

  expect(document.querySelector('[role="alert"] p')?.textContent).toBe("Панель агента не ответила");
  expect(document.querySelector('a[download]')?.getAttribute("href")).toBe(downloadHref);
  const failedUrl = fetchMock.mock.lastCall?.[0];
  await act(async () => button("Повторить просмотр PDF").click());
  expect(fetchMock.mock.lastCall?.[0]).toBe(failedUrl);
  expect(document.querySelector('[role="alert"]')).toBeNull();
  expect(document.querySelector('img[alt="Страница 1 из 3"]')).not.toBeNull();
});

it("starts another PDF on page one and ignores a late response from the old file", async () => {
  await render();
  let finish!: (value: Response) => void;
  fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }));
  await act(async () => button("Следующая страница").click());
  await render("/work/other.pdf");
  await act(async () => finish(response(2)));
  expect(document.querySelector('img[alt="Страница 1 из 3"]')).not.toBeNull();
  expect(document.querySelector('img[alt="Страница 2 из 3"]')).toBeNull();
});
