// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";
import { beforeEach, afterEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ listFiles: vi.fn(), listTrash: vi.fn(), getProfiles: vi.fn(),
  prepareUploadBatch: vi.fn(), startUploadJob: vi.fn(), setEnd: vi.fn(), setAfterTitle: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, listFiles: mocks.listFiles, listTrash: mocks.listTrash, getProfiles: mocks.getProfiles } };
});
vi.mock("@/lib/upload-batch", () => ({ prepareUploadBatch: mocks.prepareUploadBatch }));
vi.mock("@/store/upload-jobs", async () => ({
  ...await vi.importActual<typeof import("@/store/upload-jobs")>("@/store/upload-jobs"), startUploadJob: mocks.startUploadJob,
}));
vi.mock("@/contexts/usePageHeader", () => ({ usePageHeader: () => ({ setEnd: mocks.setEnd, setAfterTitle: mocks.setAfterTitle }) }));
vi.mock("@/plugins", () => ({ PluginSlot: () => null }));
vi.mock("@/components/FilePreviewDialog", () => ({ FilePreviewDialog: () => null }));
import FilesPage from "./FilesPage";

let host: HTMLDivElement;
let root: Root;
const entry = (name: string, directory = false) => ({ name, path: `/w/${name}`, is_directory: directory, size: directory ? null : 5, mtime: 1 });
function Location() { const location = useLocation(); return <output>{location.pathname}{location.search}</output>; }
async function render(entries = [entry("a.txt"), entry("Папка", true)]) {
  mocks.listFiles.mockResolvedValue({ path: "/w", root: "/w", locked_root: "/w", parent: null, entries });
  await act(async () => root.render(<MemoryRouter initialEntries={["/files"]}><FilesPage /><Location /></MemoryRouter>));
}
function button(text: string) { return [...host.querySelectorAll("button")].find(item => item.textContent?.trim() === text)!; }
beforeEach(() => {
  vi.clearAllMocks(); localStorage.clear(); sessionStorage.clear();
  window.__KORRA_UI_MODE__ = "fleet";
  mocks.listTrash.mockResolvedValue({ entries: [], total: 0 });
  mocks.getProfiles.mockResolvedValue({ profiles: [{ name: "default", is_default: true }, { name: "designer", display_name: "Дизайнер" }] });
  mocks.prepareUploadBatch.mockResolvedValue({ manifest: { upload_id: "test" }, blobs: [], title: "Файлы" });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); delete window.__KORRA_UI_MODE__; vi.unstubAllGlobals(); });

it("transfers every selected path to the chosen agent without sending a message", async () => {
  await render();
  await act(async () => {
    host.querySelector<HTMLInputElement>('input[aria-label="Выбрать a.txt"]')!.click();
    host.querySelector<HTMLInputElement>('input[aria-label="Выбрать Папка"]')!.click();
    const select = host.querySelector<HTMLSelectElement>('select[aria-label="Агент для файлов"]')!;
    select.value = "designer"; select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await act(async () => button("Передать агенту").click());
  const location = new URL(host.querySelector("output")!.textContent!, "https://local.test");
  expect(location.pathname).toBe("/agents");
  expect(location.searchParams.get("agent")).toBe("designer");
  expect(location.searchParams.getAll("attach")).toEqual(["/w/a.txt", "/w/Папка"]);
  expect(location.searchParams.has("draft")).toBe(false);
  expect(mocks.startUploadJob).not.toHaveBeenCalled();
});

it("does not silently truncate a selection larger than 30", async () => {
  await render(Array.from({ length: 31 }, (_, index) => entry(`${index}.txt`)));
  await act(async () => (host.querySelector('input[type="checkbox"]') as HTMLInputElement).click());
  await act(async () => button("Передать агенту").click());
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("содержащую их папку");
  expect(host.querySelector("output")!.textContent).toContain("/files");
});

it("starts the shared session queue with originals and the actual directory", async () => {
  await render();
  const file = new File(["data"], "new.txt");
  const input = host.querySelector<HTMLInputElement>('input[type="file"]')!;
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
  expect(mocks.prepareUploadBatch).toHaveBeenCalledWith([file], { origin: "files", target: "/w", folder: undefined, conflict: "copy" });
  expect(mocks.startUploadJob).toHaveBeenCalledOnce();
});

it("requires a conflict choice before starting replacement", async () => {
  await render();
  const input = host.querySelector<HTMLInputElement>('input[type="file"]')!;
  Object.defineProperty(input, "files", { value: [new File(["new"], "a.txt")], configurable: true });
  await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
  expect(mocks.prepareUploadBatch).not.toHaveBeenCalled();
  const replace = [...document.querySelectorAll("button")].find(item => item.textContent === "Заменить файлы")!;
  await act(async () => replace.click());
  expect(mocks.prepareUploadBatch.mock.calls[0][1].conflict).toBe("replace");
});
