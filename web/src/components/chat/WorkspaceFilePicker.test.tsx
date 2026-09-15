// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ listFiles: vi.fn(), fetchJSON: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, fetchJSON: mocks.fetchJSON, api: { ...actual.api, listFiles: mocks.listFiles } };
});
import { WorkspaceFilePicker } from "./WorkspaceFilePicker";

const root = "/opt/data/workspace";
const entry = (name: string, directory = false, base = root) => ({ name, path: `${base}/${name}`, is_directory: directory, size: directory ? null : 2048, mtime: 1, mime_type: null });
let host: HTMLDivElement;
let reactRoot: Root;
const onPick = vi.fn();

async function open() {
  await act(async () => reactRoot.render(<MemoryRouter><WorkspaceFilePicker disabled={false} onPick={onPick} /></MemoryRouter>));
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Выбрать из Файлов"]')!.click());
}
const dialog = () => document.querySelector('[role="dialog"]')!;
const buttons = () => [...dialog().querySelectorAll("button")];
const byText = (text: string) => buttons().find(item => item.textContent?.trim() === text);

beforeEach(() => {
  vi.clearAllMocks();
  window.__KORRA_UI_MODE__ = "fleet";
  host = document.createElement("div"); document.body.append(host); reactRoot = createRoot(host);
});
afterEach(async () => { await act(async () => reactRoot.unmount()); host.remove(); delete window.__KORRA_UI_MODE__; });

it("shows one close control, the current folder by human names and opens chat uploads directly", async () => {
  let resolve!: (value: unknown) => void;
  mocks.listFiles.mockReturnValueOnce(new Promise(done => { resolve = done; }));
  await open();
  expect(dialog().querySelector('[role="status"]')?.textContent).toContain("Открываем папку");
  await act(async () => resolve({ path: root, root, locked_root: root, parent: null, entries: [entry("Проекты", true), entry("client", true), entry("бриф.docx")] }));
  const closers = buttons().filter(item => /закрыть/i.test(item.textContent ?? "") || /закрыть/i.test(item.getAttribute("aria-label") ?? ""));
  expect(closers).toHaveLength(1);
  expect(dialog().querySelector('nav[aria-label="Текущая папка"]')?.textContent).toBe("Мои файлы");
  expect([...dialog().querySelectorAll('[role="listitem"]')].map(item => item.textContent)).toEqual(["Проекты", "Загрузки из чатов", "бриф.docx2 КБ"]);
  expect(byText("Прикрепить эту папку")).toBeUndefined();
  mocks.listFiles.mockResolvedValueOnce({ path: `${root}/client/inbox`, root, locked_root: root, parent: `${root}/client`, entries: [entry("2026-09-15", true, `${root}/client/inbox`)] });
  await act(async () => byText("Загрузки из чатов")!.click());
  expect(mocks.listFiles).toHaveBeenLastCalledWith(`${root}/client/inbox`);
  expect(dialog().querySelector('nav[aria-label="Текущая папка"]')?.textContent).toBe("Мои файлыЗагрузки из чатов");
  expect([...dialog().querySelectorAll('[role="listitem"]')].map(item => item.textContent)).toEqual(["15 сентября 2026"]);
  expect(byText("Прикрепить эту папку")).toBeDefined();
  mocks.listFiles.mockResolvedValueOnce({ path: root, root, locked_root: root, parent: null, entries: [] });
  await act(async () => byText("← На уровень выше")!.click());
  expect(mocks.listFiles).toHaveBeenLastCalledWith(root);
});

it("explains a failed listing and retries on request", async () => {
  mocks.listFiles.mockRejectedValueOnce(new Error("500: boom"));
  await open();
  const alert = dialog().querySelector('[role="alert"]');
  expect(alert?.textContent).toContain("Сервис временно недоступен");
  mocks.listFiles.mockResolvedValueOnce({ path: root, root, locked_root: root, parent: null, entries: [] });
  await act(async () => byText("Повторить")!.click());
  expect(mocks.listFiles).toHaveBeenCalledTimes(2);
  expect(dialog().querySelector('[role="status"]')?.textContent).toContain("Папка пуста");
  expect(dialog().querySelector('a[href="/files"]')).not.toBeNull();
});

it("picks a file through the server descriptor and closes; a rejected file stays explained", async () => {
  mocks.listFiles.mockResolvedValue({ path: root, root, locked_root: root, parent: null, entries: [entry("бриф.docx"), entry("huge.zip")] });
  await open();
  mocks.fetchJSON.mockRejectedValueOnce(new Error("413: Файл больше 2 ГБ."));
  await act(async () => byText("huge.zip2 КБ")!.click());
  expect(dialog().querySelector('[role="alert"]')?.textContent).toContain("Файл недоступен или больше 2 ГБ");
  expect(onPick).not.toHaveBeenCalled();
  const descriptor = { path: `${root}/бриф.docx`, name: "бриф.docx", kind: "docx", size: 2048, reader: "docx" };
  mocks.fetchJSON.mockResolvedValueOnce(descriptor);
  await act(async () => byText("бриф.docx2 КБ")!.click());
  expect(onPick).toHaveBeenCalledWith(descriptor);
  expect(document.querySelector('[role="dialog"]')).toBeNull();
});
