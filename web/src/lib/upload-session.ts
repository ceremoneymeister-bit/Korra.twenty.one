/**
 * Клиент сессии загрузки (K21-059).
 *
 * Сервер (`korra_cli/web_routers/uploads.py`) принимает список файлов, потом
 * части каждого файла по смещению, потом один `complete`. Здесь — только
 * транспорт и классификация ответов: что можно повторить, что нужно
 * пересинхронизировать, а что бесполезно повторять. Планировщик частей и
 * состояние задачи живут отдельно (`upload-queue.ts`, `store/upload-jobs.ts`).
 *
 * Части идут через XHR, а не fetch: нужен настоящий прогресс отправки и
 * отмена, а `fetch` про отправленные байты не рассказывает.
 */

import { SESSION_HEADER, authedFetch, withBasePath } from "@/lib/api";

export type UploadOrigin = "chat" | "files";
export type ConflictPolicy = "skip" | "replace" | "copy";

export interface UploadManifestFile {
  path: string;
  size: number;
  sha256?: string | null;
  last_modified?: number;
}

export interface UploadManifest {
  upload_id: string;
  origin: UploadOrigin;
  target?: { kind: "inbox" } | { kind: "path"; path: string };
  profile?: string | null;
  name?: string | null;
  directories?: string[];
  files: UploadManifestFile[];
  on_conflict?: ConflictPolicy;
  client?: { optimized_images: boolean; ua: string };
}

export interface UploadLimits {
  part_bytes: number;
  max_files: number;
  max_directories: number;
  max_file_bytes: number;
  max_total_bytes: number;
  chat_max_files: number;
  chat_folder_threshold: number;
}

export interface PartReceipt {
  index: number;
  bytes: number;
  complete: boolean;
}

export interface UploadedFile {
  index: number;
  path: string;
  name: string;
  kind: string;
  size: number;
  sha256?: string | null;
  reader: string;
  deduplicated: boolean;
  skipped: boolean;
}

export interface UploadedFolder {
  path: string;
  name: string;
  file_count: number;
  total_bytes: number;
}

export interface UploadResult {
  published: true;
  files: UploadedFile[];
  folder: UploadedFolder | null;
  skipped: number[];
  excluded: number[];
}

export interface UploadStatus {
  upload_id: string;
  origin: UploadOrigin;
  target: string | null;
  published: boolean;
  received: PartReceipt[];
  already_present: number[];
  limits: UploadLimits;
  result?: UploadResult;
}

/** Значение по умолчанию до первого ответа сервера; дальше — `limits.part_bytes`. */
export const DEFAULT_PART_BYTES = 4 * 1024 * 1024;

// Повторяем то, что проходит само: таймауты, троттлинг и пятисотки. Плюс
// пустой 400 — так кабинет за nginx закрывает поток, не дойдя до панели
// (вывод из журналов 12.09), и для клиента это ровно обрыв связи.
const RETRYABLE_STATUSES = new Set([408, 425, 429, 500, 502, 503, 504]);

export function isRetryableStatus(status: number, detail: string): boolean {
  if (status === 0) return true;
  if (RETRYABLE_STATUSES.has(status)) return true;
  return status === 400 && detail.trim() === "";
}

export class UploadError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly retryable: boolean;
  /** Фактический размер части на сервере: ответ 409, клиент продолжает с него. */
  readonly bytes?: number;
  /** Номера файлов, которых серверу не хватило для публикации. */
  readonly missing?: number[];

  constructor(status: number, detail: string, extra: { bytes?: number; missing?: number[] } = {}) {
    super(detail || `Загрузка не удалась (HTTP ${status}).`);
    this.name = "UploadError";
    this.status = status;
    this.detail = detail;
    this.retryable = isRetryableStatus(status, detail);
    this.bytes = extra.bytes;
    this.missing = extra.missing;
  }
}

function parseBody(text: string): Record<string, unknown> {
  try {
    const value = JSON.parse(text) as unknown;
    return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

function errorFrom(status: number, text: string): UploadError {
  const body = parseBody(text);
  const detail = typeof body.detail === "string" ? body.detail : "";
  return new UploadError(status, detail, {
    bytes: typeof body.bytes === "number" ? body.bytes : undefined,
    missing: Array.isArray(body.missing) ? (body.missing as number[]) : undefined,
  });
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await authedFetch(url, init);
  const text = await response.text();
  if (!response.ok) throw errorFrom(response.status, text);
  return (text ? JSON.parse(text) : null) as T;
}

export function createUpload(manifest: UploadManifest, signal?: AbortSignal): Promise<UploadStatus> {
  return request<UploadStatus>("/api/uploads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(manifest),
    signal,
  });
}

export function uploadStatus(uploadId: string, signal?: AbortSignal): Promise<UploadStatus> {
  return request<UploadStatus>(`/api/uploads/${encodeURIComponent(uploadId)}`, { signal });
}

export function completeUpload(
  uploadId: string,
  exclude: number[] = [],
  signal?: AbortSignal,
): Promise<UploadResult> {
  return request<UploadResult>(`/api/uploads/${encodeURIComponent(uploadId)}/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(exclude.length ? { exclude } : {}),
    signal,
  });
}

export async function cancelUpload(uploadId: string): Promise<void> {
  await request<null>(`/api/uploads/${encodeURIComponent(uploadId)}`, { method: "DELETE" });
}

/** Отправить одну часть. Прогресс — по отправленным байтам, отмена — через signal. */
export function uploadPart(
  uploadId: string,
  index: number,
  offset: number,
  chunk: Blob,
  onProgress?: (loaded: number) => void,
  signal?: AbortSignal,
): Promise<PartReceipt> {
  return new Promise<PartReceipt>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Загрузка отменена", "AbortError"));
      return;
    }
    const xhr = new XMLHttpRequest();
    const stop = () => {
      signal?.removeEventListener("abort", onAbort);
    };
    const onAbort = () => {
      xhr.abort();
    };
    signal?.addEventListener("abort", onAbort, { once: true });

    const query = new URLSearchParams({ offset: String(offset) });
    xhr.open(
      "PUT",
      withBasePath(`/api/uploads/${encodeURIComponent(uploadId)}/files/${index}?${query}`),
    );
    // Cookie-путь кабинета и заголовочный путь панели должны работать оба.
    xhr.withCredentials = true;
    const token = typeof window !== "undefined" ? (window.__HERMES_SESSION_TOKEN__ ?? "") : "";
    if (token) xhr.setRequestHeader(SESSION_HEADER, token);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");

    if (onProgress) {
      xhr.upload.onprogress = (event) => onProgress(event.loaded);
    }
    xhr.onload = () => {
      stop();
      if (xhr.status >= 200 && xhr.status < 300) {
        const body = parseBody(xhr.responseText || "{}");
        resolve({
          index,
          bytes: typeof body.bytes === "number" ? body.bytes : offset + chunk.size,
          complete: body.complete === true,
        });
        return;
      }
      reject(errorFrom(xhr.status, xhr.responseText || ""));
    };
    // status 0 — сеть оборвалась, а не сервер отказал: это повторяемо.
    xhr.onerror = () => {
      stop();
      reject(new UploadError(0, ""));
    };
    xhr.ontimeout = () => {
      stop();
      reject(new UploadError(0, ""));
    };
    xhr.onabort = () => {
      stop();
      reject(new DOMException("Загрузка отменена", "AbortError"));
    };
    xhr.send(chunk);
  });
}
