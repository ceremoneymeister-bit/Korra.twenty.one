// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ORIGINALS_STORAGE_KEY,
  optimizeAll,
  optimizeImage,
  sendOriginals,
  setSendOriginals,
} from "./image-optimize";

/** Файл заданного размера: сама картинка неважна, декодер подменён. */
function photo(name: string, bytes: number, type = "image/jpeg"): File {
  return new File([new Uint8Array(bytes)], name, { type, lastModified: 1_757_650_000_000 });
}

/** Подменяем декодер и кодировщик: jsdom не умеет ни того, ни другого. */
function decoder(width: number, height: number, encodedBytes: number) {
  const drawn: { width: number; height: number }[] = [];
  vi.stubGlobal("createImageBitmap", async () => ({ width, height, close: () => {} }));
  vi.stubGlobal("OffscreenCanvas", class {
    constructor(width: number, height: number) {
      drawn.push({ width, height });
    }
    getContext() {
      return { drawImage: () => {} };
    }
    async convertToBlob() {
      return new Blob([new Uint8Array(encodedBytes)], { type: "image/jpeg" });
    }
  });
  return drawn;
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("сжатие фотографий", () => {
  it("ужимает снимок с телефона до 2048 px и переименовывает в .jpg", async () => {
    const drawn = decoder(4000, 3000, 600_000);
    const result = await optimizeImage(photo("IMG_2581.jpeg", 3 * 1024 * 1024));
    expect(result.optimized).toBe(true);
    expect(result.file.name).toBe("IMG_2581.jpg");
    expect(result.file.type).toBe("image/jpeg");
    expect(result.file.lastModified).toBe(1_757_650_000_000);
    expect(drawn).toEqual([{ width: 2048, height: 1536 }]);
  });

  it("не трогает небольшую картинку разумного размера", async () => {
    decoder(1200, 800, 1);
    const original = photo("схема.png", 800 * 1024, "image/png");
    const result = await optimizeImage(original);
    expect(result.optimized).toBe(false);
    expect(result.file).toBe(original);
  });

  it("оставляет оригинал, когда выигрыш меньше 15 %", async () => {
    decoder(4000, 3000, Math.round(3 * 1024 * 1024 * 0.9));
    const original = photo("IMG.jpg", 3 * 1024 * 1024);
    const result = await optimizeImage(original);
    expect(result.optimized).toBe(false);
    expect(result.file).toBe(original);
  });

  it("отказ декодера — это HEIC в Chrome, а не ошибка: уходит оригинал", async () => {
    vi.stubGlobal("createImageBitmap", async () => {
      throw new Error("The source image could not be decoded");
    });
    const original = photo("IMG_2581.heic", 4 * 1024 * 1024, "image/heic");
    const result = await optimizeImage(original);
    expect(result.optimized).toBe(false);
    expect(result.file).toBe(original);
  });

  it("пропускает анимацию и не-картинки", async () => {
    const calls = vi.fn();
    vi.stubGlobal("createImageBitmap", calls);
    for (const name of ["баннер.gif", "смета.pdf", "запись.mp4"]) {
      const original = photo(name, 5 * 1024 * 1024);
      expect((await optimizeImage(original)).file).toBe(original);
    }
    expect(calls).not.toHaveBeenCalled();
  });
});

describe("переключатель «Отправлять оригиналы»", () => {
  it("по умолчанию выключен и хранится глобально для панели", () => {
    expect(sendOriginals()).toBe(false);
    setSendOriginals(true);
    expect(localStorage.getItem(ORIGINALS_STORAGE_KEY)).toBe("1");
    expect(sendOriginals()).toBe(true);
    setSendOriginals(false);
    expect(sendOriginals()).toBe(false);
  });

  it("с включёнными оригиналами пакет не декодируется вовсе", async () => {
    const calls = vi.fn();
    vi.stubGlobal("createImageBitmap", calls);
    setSendOriginals(true);
    const batch = [photo("a.jpg", 3 * 1024 * 1024), photo("b.jpg", 3 * 1024 * 1024)];
    const done: number[] = [];
    const result = await optimizeAll(batch, { onProgress: (value) => done.push(value) });
    expect(result.map((item) => item.file)).toEqual(batch);
    expect(result.every((item) => !item.optimized)).toBe(true);
    expect(done).toEqual([1, 2]);
    expect(calls).not.toHaveBeenCalled();
  });

  it("без оригиналов сжимает весь пакет по одному файлу", async () => {
    decoder(4000, 3000, 600_000);
    const batch = [photo("a.jpg", 3 * 1024 * 1024), photo("b.jpg", 3 * 1024 * 1024)];
    const result = await optimizeAll(batch, { originals: false });
    expect(result.map((item) => item.file.name)).toEqual(["a.jpg", "b.jpg"]);
    expect(result.every((item) => item.optimized)).toBe(true);
    expect(result.every((item) => item.file.size === 600_000)).toBe(true);
  });
});
