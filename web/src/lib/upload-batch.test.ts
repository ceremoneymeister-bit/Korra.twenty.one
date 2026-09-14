// @vitest-environment jsdom
import { webcrypto, randomUUID } from "node:crypto";
import { File as NodeFile } from "node:buffer";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { prepareUploadBatch } from "./upload-batch";

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  vi.stubGlobal("crypto", { subtle: webcrypto.subtle, randomUUID });
  vi.stubGlobal("File", NodeFile);
});
afterEach(() => vi.unstubAllGlobals());

it("publishes a completely empty selected folder without inventing a file", async () => {
  const batch = await prepareUploadBatch([], { origin: "files", target: "/w", folder: {
    name: "Пустая", files: [], directories: [], directoryCapture: "complete",
  } });
  expect(batch.manifest.name).toBe("Пустая");
  expect(batch.manifest.files).toEqual([]);
  expect(batch.blobs).toEqual([]);
  await expect(prepareUploadBatch([], { origin: "files", target: "/w" })).rejects.toThrow();
});

it("same content resumes, changed content with identical metadata starts a new session", async () => {
  const first = new File(["abc"], "data.txt", { lastModified: 1 });
  const changed = new File(["abd"], "data.txt", { lastModified: 1 });
  const a = await prepareUploadBatch([first], { origin: "files", target: "/w" });
  const b = await prepareUploadBatch([first], { origin: "files", target: "/w" });
  const c = await prepareUploadBatch([changed], { origin: "files", target: "/w" });
  expect(a.manifest.upload_id).toBe(b.manifest.upload_id);
  expect(a.manifest.upload_id).not.toBe(c.manifest.upload_id);
  expect(a.manifest.files[0].sha256).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
});

it("never reuses an upload in another destination or profile", async () => {
  const file = new File(["x"], "x.txt", { lastModified: 1 });
  const a = await prepareUploadBatch([file], { origin: "files", target: "/one" });
  const b = await prepareUploadBatch([file], { origin: "files", target: "/two" });
  const c = await prepareUploadBatch([file], { origin: "chat", profile: "designer" });
  expect(new Set([a, b, c].map(item => item.manifest.upload_id)).size).toBe(3);
  const again = await prepareUploadBatch([file], { origin: "files", target: "/one" });
  expect(again.manifest.upload_id).toBe(a.manifest.upload_id);
});

it("preserves 1001 files and empty nested directories without optimisation in Files", async () => {
  const files = Array.from({ length: 1001 }, (_, index) => ({
    path: `Фото/${index}.txt`, file: new File([String(index)], `${index}.txt`, { lastModified: 1 }),
  }));
  const batch = await prepareUploadBatch([], { origin: "files", target: "/w", folder: {
    name: "Проект", files, directories: ["Пустая/Внутри"], directoryCapture: "complete",
  } });
  expect(batch.manifest.files).toHaveLength(1001);
  expect(batch.manifest.directories).toEqual(["Фото", "Пустая", "Пустая/Внутри"].sort());
  expect(batch.manifest.client?.optimized_images).toBe(false);
  expect(batch.manifest.files[0].path).toBe("Фото/0.txt");
  expect(await batch.blobs[0].text()).toBe("0");
});

it("groups 31 chat files but leaves a flat Files selection in its chosen directory", async () => {
  const files = Array.from({ length: 31 }, (_, index) => new File([String(index)], `${index}.txt`));
  const chat = await prepareUploadBatch(files, { origin: "chat" });
  const browser = await prepareUploadBatch(files, { origin: "files", target: "/w" });
  expect(chat.manifest.name).toBe("Файлы");
  expect(browser.manifest.name).toBeUndefined();
});

it("reads large files in bounded pieces and keeps the original upload blob", async () => {
  const file = new File([new Uint8Array(9 * 1024 * 1024)], "large.bin");
  const slice = vi.spyOn(file, "slice");
  const batch = await prepareUploadBatch([file], { origin: "files", target: "/w" });
  expect(batch.blobs[0]).toBe(file);
  expect(slice).toHaveBeenCalledTimes(3);
  expect(slice.mock.calls.every(([start, end]) => end! - start! <= 4 * 1024 * 1024)).toBe(true);
});
