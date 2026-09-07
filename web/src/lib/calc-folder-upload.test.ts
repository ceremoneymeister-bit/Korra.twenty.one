// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchJSON } from "@/lib/api";
import { readDroppedFolder, selectedFolder, uploadFolder, type FolderSelection } from "./calc-folder-upload";

vi.mock("@/lib/api", () => ({ fetchJSON: vi.fn() }));
const request = vi.mocked(fetchJSON);
const selection: FolderSelection = { name: "Сделка 124", files: Array.from({ length: 1001 }, (_, index) => ({
  path: index === 0 ? "Заявка.xlsx" : `Чертежи/Деталь ${index}.pdf`,
  file: new File([String(index)], index === 0 ? "Заявка.xlsx" : `Деталь ${index}.pdf`),
})) };
beforeEach(() => { request.mockReset(); });

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
});
