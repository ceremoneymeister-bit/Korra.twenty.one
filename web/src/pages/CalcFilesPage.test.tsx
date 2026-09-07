// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import CalcFilesPage from "./CalcFilesPage";
import { fetchJSON } from "@/lib/api";
import { $folderUpload } from "@/store/calc-folder-upload";

vi.mock("@/lib/api", () => ({ fetchJSON: vi.fn(), withBasePath: (path: string) => path }));
vi.mock("@/pages/FilesPage", () => ({ FileTrash: () => null }));
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root | undefined;
let container: HTMLDivElement;
afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove(); $folderUpload.set(null); vi.clearAllMocks();
});

it("keeps the opened subfolder when upload notifications change", async () => {
  vi.mocked(fetchJSON).mockResolvedValue({
    order_id: "folder-1", folder_name: "Заказ 123", file_count: 1, total_bytes: 3,
    created_at: "2026-09-07T08:00:00Z", status: "draft", directories: ["До передачи"], result_files: [],
    source_files: [{ name: "деталь.pdf", relative_path: "До передачи/деталь.pdf", bytes: 3, download_url: "/api/calc/folders/folder-1/files/0" }],
  });
  $folderUpload.set({ uploadId: "upload", name: "Заказ 123", status: "complete", orderId: "folder-1", selection: null,
    progress: { completed: 1, total: 1, bytes: 3, totalBytes: 3 } });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => root!.render(<MemoryRouter initialEntries={["/files?order=folder-1"]}><CalcFilesPage /></MemoryRouter>));
  const open = container.querySelector<HTMLButtonElement>('button[aria-label="Открыть папку До передачи"]');
  expect(open).not.toBeNull();
  await act(async () => open!.click());
  const heading = () => [...container.querySelectorAll("h3")].some((element) => element.textContent === "До передачи");
  expect(heading()).toBe(true);
  const calls = vi.mocked(fetchJSON).mock.calls.length;
  await act(async () => $folderUpload.set(null));
  expect(heading()).toBe(true);
  expect(fetchJSON).toHaveBeenCalledTimes(calls);
  await act(async () => $folderUpload.set({ uploadId: "upload-2", name: "Другой заказ", status: "complete", orderId: "folder-2", selection: null,
    progress: { completed: 1, total: 1, bytes: 3, totalBytes: 3 } }));
  expect(heading()).toBe(true);
  expect(fetchJSON).toHaveBeenCalledTimes(calls);
});
