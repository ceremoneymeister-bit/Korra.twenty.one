// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchJSON } from "@/lib/api";
import { folderUploadKey, readDirectoryHandle, readDroppedFolder, selectedFolder, uploadFolder, validateSelection, type FolderSelection } from "./calc-folder-upload";

vi.mock("@/lib/api", () => ({ fetchJSON: vi.fn() }));
const request = vi.mocked(fetchJSON);
const selection: FolderSelection = { name: "Сделка 124", files: Array.from({ length: 1001 }, (_, index) => ({
  path: index === 0 ? "Заявка.xlsx" : `Чертежи/Деталь ${index}.pdf`,
  file: new File([String(index)], index === 0 ? "Заявка.xlsx" : `Деталь ${index}.pdf`),
})) };
beforeEach(() => { request.mockReset(); sessionStorage.clear(); });

describe("whole-folder intake", () => {
  it("collects every directory-reader batch and nested path beyond 500 files", async () => {
    let cursor = 0;
    const entries = selection.files.map(({ file }) => ({ name: file.name, isFile: true, file: (done: (file: File) => void) => done(file) }));
    const directory = { name: "Чертежи", isDirectory: true, createReader: () => ({
      readEntries: (done: (entries: unknown[]) => void) => { const batch = entries.slice(cursor, cursor + 100); cursor += 100; done(batch); },
    }) };
    let first = true;
    const root = { name: selection.name, isDirectory: true, createReader: () => ({
      readEntries: (done: (entries: unknown[]) => void) => { done(first ? [directory] : []); first = false; },
    }) };
    const result = await readDroppedFolder([{ kind: "file", webkitGetAsEntry: () => root }] as unknown as DataTransferItemList);
    expect(result.files).toHaveLength(1001);
    expect(result.files.every(({ path }) => path.startsWith("Чертежи/"))).toBe(true);
  });

  it("requires a real folder selection", () => {
    expect(() => selectedFolder([new File(["x"], "Заявка.xlsx")])).toThrow("папку");
  });

  it("captures empty directories from every dropped directory", async () => {
    function directory(name: string, entries: unknown[] = []): unknown {
      return { name, isDirectory: true, createReader: () => {
        let pending = true;
        return { readEntries: (done: (entries: unknown[]) => void) => {
          done(pending ? entries : []); pending = false;
        } };
      } };
    }
    const root = directory("Сделка", [directory("Чертежи", [directory("Корпус")]), directory("Результаты")]);
    const result = await readDroppedFolder([{ kind: "file", webkitGetAsEntry: () => root }] as unknown as DataTransferItemList);
    expect(result.files).toEqual([]);
    expect(result.directories).toEqual(["Результаты", "Чертежи", "Чертежи/Корпус"]);
    expect(result.directoryCapture).toBe("complete");
  });

  it("reads directory handles with both files and empty subdirectories", async () => {
    const file = new File(["pdf"], "Деталь.pdf");
    function directory(name: string, entries: unknown[] = []): FileSystemDirectoryHandle {
      return { name, kind: "directory", async *values() { yield* entries; } } as FileSystemDirectoryHandle;
    }
    const root = directory("Сделка", [directory("Результаты"), directory("Чертежи", [
      { name: file.name, kind: "file", getFile: async () => file }, directory("Корпус"),
    ])]);
    const result = await readDirectoryHandle(root);
    expect(result.files).toEqual([{ file, path: "Чертежи/Деталь.pdf" }]);
    expect(result.directories).toEqual(["Результаты", "Чертежи", "Чертежи/Корпус"]);
    expect(result.directoryCapture).toBe("complete");
    await expect(readDirectoryHandle(directory("Пустая"))).rejects.toThrow("полностью пуста");
  });

  it("marks the FileList fallback as files-only and infers every non-empty parent", () => {
    const file = new File(["pdf"], "Деталь.pdf");
    Object.defineProperty(file, "webkitRelativePath", { value: "Сделка/Чертежи/Корпус/Деталь.pdf" });
    expect(selectedFolder([file])).toMatchObject({ name: "Сделка", directoryCapture: "files-only",
      directories: ["Чертежи", "Чертежи/Корпус"] });
    expect(() => selectedFolder([])).toThrow("Перетащите папку");
  });

  it("preserves old resume keys for inferred parents and distinguishes empty directories", () => {
    const oldSignature = JSON.stringify([selection.name,
      selection.files.map(({ path, file }) => [path, file.size, file.lastModified])]);
    sessionStorage.setItem("calc.folder-upload", JSON.stringify({ id: "legacy-upload", signature: oldSignature }));
    expect(folderUploadKey({ ...selection, directories: ["Чертежи"] })).toBe("legacy-upload");
    const next = folderUploadKey({ ...selection, directories: ["Чертежи", "Пустая"] });
    expect(next).not.toBe("legacy-upload");
    expect(folderUploadKey({ ...selection, directories: ["Пустая", "Чертежи"] })).toBe(next);
  });

  it("registers a directory-only order without sending file requests", async () => {
    request.mockImplementation(async (url) => url.endsWith("/complete")
      ? { order_id: "directory-order" } : { upload_id: "upload", received: [] });
    const dirs = validateSelection({ name: "Сделка", files: [], directories: ["Чертежи/Корпус"] });
    expect(await uploadFolder(dirs, "upload", vi.fn(), new AbortController().signal)).toBe("directory-order");
    expect(JSON.parse(String(request.mock.calls[0][1]?.body))).toMatchObject({ files: [],
      directories: ["Чертежи", "Чертежи/Корпус"] });
    expect(request).toHaveBeenCalledTimes(2);
    expect(request.mock.calls.every(([, init]) => init?.method === "POST")).toBe(true);
  });

  it("streams 1001 files with at most three in flight, then finalizes exactly once", async () => {
    let active = 0;
    let maximum = 0;
    let completed = 0;
    request.mockImplementation(async (url, init) => {
      if (init?.method === "PUT") {
        active++; maximum = Math.max(maximum, active);
        await new Promise((done) => setTimeout(done, 0));
        active--; completed++;
        return { ok: true };
      }
      if (url.endsWith("/complete")) { expect(completed).toBe(1001); return { order_id: "one-order" }; }
      return { upload_id: "upload", received: [] };
    });
    const progress = vi.fn();
    const order = await uploadFolder(selection, "upload", progress, new AbortController().signal);
    expect(order).toBe("one-order");
    expect(maximum).toBe(3);
    expect(request.mock.calls.filter(([url]) => url.endsWith("/complete"))).toHaveLength(1);
    expect(progress.mock.lastCall?.[0].completed).toBe(1001);
  });

  it("leaves a failed batch unfinalized and retries only missing files", async () => {
    const received = new Set<number>();
    let fail = true;
    request.mockImplementation(async (url, init) => {
      if (init?.method === "PUT") {
        const index = Number(url.split("/").at(-1));
        if (index === 1 && fail) throw new Error("Сеть недоступна");
        expect(received.has(index)).toBe(false);
        received.add(index); return { ok: true };
      }
      if (url.endsWith("/complete")) return { order_id: "same-order" };
      return { received: [...received], upload_id: "upload" };
    });
    const small = { ...selection, files: selection.files.slice(0, 8) };
    await expect(uploadFolder(small, "upload", vi.fn(), new AbortController().signal)).rejects.toThrow("Сеть");
    expect(request.mock.calls.some(([url]) => url.endsWith("/complete"))).toBe(false);
    fail = false;
    expect(await uploadFolder(small, "upload", vi.fn(), new AbortController().signal)).toBe("same-order");
    expect(received.size).toBe(8);
  });

  it("recovers a lost final response by returning the existing order", async () => {
    request.mockResolvedValue({ upload_id: "upload", received: [], order_id: "existing" });
    expect(await uploadFolder(selection, "upload", vi.fn(), new AbortController().signal)).toBe("existing");
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("can pause while auth recovery waits for a reload that the operator cancelled", async () => {
    request.mockImplementation(() => new Promise(() => {}));
    const controller = new AbortController();
    const pending = uploadFolder(selection, "upload", vi.fn(), controller.signal);
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(request).toHaveBeenCalledTimes(1);
  });
});
