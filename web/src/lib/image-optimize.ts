/**
 * Сжатие фотографий перед отправкой агенту (K21-059).
 *
 * Снимок с телефона — 3–5 МБ и 4000 px по длинной стороне, а движок всё равно
 * ужимает изображение до 1568 px, когда показывает его модели
 * (`agent/vision_tools.py`). Отправлять оригинал целиком значит тратить минуты
 * мобильного канала на байты, которые никто не увидит. Поэтому по умолчанию
 * сжимаем, а переключатель «Отправлять оригиналы фото» остаётся для случая,
 * когда важен именно исходный файл.
 *
 * Декодируем в главном потоке по одному файлу, уступая кадр между файлами:
 * Worker с OffscreenCanvas в iOS Safari появился поздно, и проверка его
 * поддержки — лишний риск ради 100–300 мс на снимок.
 *
 * Любой отказ декодера — не ошибка: уходит оригинал. HEIC Chrome и Firefox не
 * читают, и это нормально: сервер откроет его через pillow-heif.
 */

import { isImageKind, kindOf } from "@/lib/chat-attachments";

export const OPTIMIZE_MIN_BYTES = Math.round(1.5 * 1024 * 1024);
export const OPTIMIZE_MAX_EDGE = 2048;
export const OPTIMIZE_QUALITY = 0.85;
/** Выигрыш меньше 15 % не стоит потери оригинала. */
export const OPTIMIZE_MIN_GAIN = 0.15;
export const ORIGINALS_STORAGE_KEY = "korra-upload-originals";

export interface OptimizedImage {
  /** Что отправлять: сжатая копия или сам оригинал. */
  file: File;
  original: File;
  optimized: boolean;
}

/** Привычка человека, а не свойство агента: ключ общий для всей панели. */
export function sendOriginals(): boolean {
  try {
    return localStorage.getItem(ORIGINALS_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function setSendOriginals(value: boolean): void {
  try {
    if (value) localStorage.setItem(ORIGINALS_STORAGE_KEY, "1");
    else localStorage.removeItem(ORIGINALS_STORAGE_KEY);
  } catch {
    /* Приватный режим: настройка проживёт до перезагрузки. */
  }
}

/** GIF пропускаем: перекодировать анимацию в JPEG значит её потерять. */
export function isOptimizableKind(name: string): boolean {
  const kind = kindOf(name);
  return isImageKind(kind) && kind !== "gif";
}

function jpegName(name: string): string {
  const dot = name.lastIndexOf(".");
  const stem = dot > 0 ? name.slice(0, dot) : name;
  return `${stem}.jpg`;
}

async function encode(bitmap: ImageBitmap, width: number, height: number): Promise<Blob | null> {
  if (typeof OffscreenCanvas !== "undefined") {
    const canvas = new OffscreenCanvas(width, height);
    const context = canvas.getContext("2d");
    if (!context) return null;
    context.drawImage(bitmap, 0, 0, width, height);
    return canvas.convertToBlob({ type: "image/jpeg", quality: OPTIMIZE_QUALITY });
  }
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) return null;
  context.drawImage(bitmap, 0, 0, width, height);
  return new Promise<Blob | null>((resolve) =>
    canvas.toBlob(resolve, "image/jpeg", OPTIMIZE_QUALITY));
}

export async function optimizeImage(file: File): Promise<OptimizedImage> {
  const untouched: OptimizedImage = { file, original: file, optimized: false };
  if (!isOptimizableKind(file.name) || typeof createImageBitmap !== "function") return untouched;

  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    // Chrome и Firefox так отвечают на HEIC. Оригинал сервер прочитает сам.
    return untouched;
  }
  try {
    const edge = Math.max(bitmap.width, bitmap.height);
    if (file.size <= OPTIMIZE_MIN_BYTES && edge <= OPTIMIZE_MAX_EDGE) return untouched;
    const scale = edge > OPTIMIZE_MAX_EDGE ? OPTIMIZE_MAX_EDGE / edge : 1;
    const width = Math.max(1, Math.round(bitmap.width * scale));
    const height = Math.max(1, Math.round(bitmap.height * scale));
    const blob = await encode(bitmap, width, height);
    if (!blob || blob.size > file.size * (1 - OPTIMIZE_MIN_GAIN)) return untouched;
    return {
      file: new File([blob], jpegName(file.name), {
        type: "image/jpeg",
        lastModified: file.lastModified,
      }),
      original: file,
      optimized: true,
    };
  } catch {
    return untouched;
  } finally {
    bitmap.close?.();
  }
}

function nextFrame(): Promise<void> {
  if (typeof requestAnimationFrame !== "function") return Promise.resolve();
  return new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
}

export interface OptimizeAllOptions {
  originals?: boolean;
  onProgress?: (done: number, total: number) => void;
}

/** По одному файлу, уступая кадр между ними — интерфейс не замирает на пакете. */
export async function optimizeAll(
  files: File[],
  options: OptimizeAllOptions = {},
): Promise<OptimizedImage[]> {
  const originals = options.originals ?? sendOriginals();
  const result: OptimizedImage[] = [];
  for (const [position, file] of files.entries()) {
    result.push(originals ? { file, original: file, optimized: false } : await optimizeImage(file));
    options.onProgress?.(position + 1, files.length);
    if (!originals && position + 1 < files.length) await nextFrame();
  }
  return result;
}
