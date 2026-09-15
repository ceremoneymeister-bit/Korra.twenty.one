// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";
import { beforeEach, afterEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ listFiles: vi.fn(), listTrash: vi.fn(), getProfiles: vi.fn(),
  prepareUploadBatch: vi.fn(), startUploadJob: vi.fn(), setEnd: vi.fn(), setAfterTitle: vi.fn(),
  cabinet: { restrictedFiles: false, canCreateFolders: true } }));
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
vi.mock("@/hooks/useCabinetSession", () => ({ useCabinetSession: () => ({ logout: "", canManageSkills: true, canBrowseSkillsHub: true, canConfigureToolsets: true, ...mocks.cabinet }) }));
import FilesPage from "./FilesPage";

let host: HTMLDivElement;
let root: Root;
const entry = (name: string, directory = false) => ({ name, path: `/w/${name}`, is_directory: directory, size: directory ? null : 5, mtime: 1 });
function Location() { const location = useLocation(); return <output>{location.pathname}{location.search}</output>; }
async function render(entries = [entry("a.txt"), entry("Папка", true)], initial = "/files") {
  mocks.listFiles.mockResolvedValue({ path: "/w", root: "/w", locked_root: "/w", parent: null, entries });
  await act(async () => root.render(<MemoryRouter initialEntries={[initial]}><FilesPage /><Location /></MemoryRouter>));
}
function button(text: string) { return [...host.querySelectorAll("button")].find(item => item.textContent?.trim() === text)!; }
beforeEach(() => {
  vi.clearAllMocks(); localStorage.clear(); sessionStorage.clear();
  mocks.cabinet = { restrictedFiles: false, canCreateFolders: true };
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

it("offers «Создать папку» only on the exact cabinet hint, never on the wider files_manage alone", async () => {
  mocks.cabinet = { restrictedFiles: true, canCreateFolders: true };
  await render();
  expect(button("Создать папку")).toBeDefined();
  await act(async () => root.unmount());
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  mocks.cabinet = { restrictedFiles: false, canCreateFolders: false };
  await render();
  expect(button("Создать папку")).toBeUndefined();
});

it("shows chat uploads by a human name in the fleet workspace and opens the inbox directly", async () => {
  await render([entry("client", true), entry("Проекты", true)]);
  const labels = [...host.querySelectorAll("span.font-medium")].map(item => item.textContent);
  expect(labels).toEqual(["Загрузки из чатов", "Проекты"]);
  expect(host.querySelector('button[aria-label="Открыть Загрузки из чатов"]')).not.toBeNull();
  mocks.listFiles.mockResolvedValue({ path: "/w/client/inbox", root: "/w", locked_root: "/w", parent: "/w/client",
    entries: [{ ...entry("2026-09-15", true), path: "/w/client/inbox/2026-09-15" }] });
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Открыть Загрузки из чатов"]')!.click());
  expect(host.querySelector("output")!.textContent).toContain("path=%2Fw%2Fclient%2Finbox");
  expect(mocks.listFiles).toHaveBeenLastCalledWith("/w/client/inbox");
  const crumbs = [...host.querySelectorAll('nav[aria-label="Путь к папке"] button')].map(item => item.textContent);
  expect(crumbs).toEqual(["Мои файлы", "Загрузки из чатов"]);
  expect([...host.querySelectorAll("span.font-medium")].map(item => item.textContent)).toEqual(["15 сентября 2026"]);
  // «..» из «Загрузок из чатов» ведёт в корень, а не в служебный `client`.
  const up = [...host.querySelectorAll("button")].find(item => item.textContent?.trim() === "..")!;
  mocks.listFiles.mockResolvedValue({ path: "/w", root: "/w", locked_root: "/w", parent: null, entries: [] });
  await act(async () => up.click());
  expect(mocks.listFiles).toHaveBeenLastCalledWith("/w");
});

it("highlights the file named by «Показать в папке» and drops the highlight on the next navigation", async () => {
  await render([entry("отчёт.xlsx"), entry("другой.txt"), entry("Папка", true)], "/files?path=%2Fw&highlight=%D0%BE%D1%82%D1%87%D1%91%D1%82.xlsx");
  const rows = [...host.querySelectorAll("[data-highlighted]")];
  expect(rows).toHaveLength(1);
  expect(rows[0].textContent).toContain("отчёт.xlsx");
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Открыть Папка"]')!.click());
  expect(host.querySelector("output")!.textContent).not.toContain("highlight=");
});
