/**
 * Планировщик частей загрузки (K21-059).
 *
 * Все запросы идут в одном H2-соединении, поэтому скорость упирается в канал
 * отдачи, а не в число потоков: два-три параллельных PUT держат канал занятым
 * в паузах между частями, каждый следующий только удлиняет все запросы и
 * увеличивает потерю при одном разрыве. На телефоне берём два, на десктопе три.
 *
 * Обрыв здесь — норма, а не ошибка: часть повторяется с задержкой, 409 с
 * фактическим размером просто сдвигает смещение, а возврат сети сбрасывает
 * счётчик попыток. Уже принятые сервером файлы не перекачиваются никогда.
 */

import {
  DEFAULT_PART_BYTES,
  UploadError,
  uploadPart as defaultUploadPart,
  type PartReceipt,
} from "@/lib/upload-session";

export interface UploadQueueFile {
  index: number;
  blob: Blob;
  /** Сколько байт уже принял сервер (из `create`/`status`). */
  offset: number;
  complete: boolean;
}

export interface UploadQueueProgress {
  completed: number;
  total: number;
  bytes: number;
  totalBytes: number;
}

export interface UploadQueueFailure {
  index: number;
  error: UploadError;
}

export interface UploadQueueOutcome {
  completed: number[];
  failed: UploadQueueFailure[];
}

/**
 * Фон и сеть. Скрытая вкладка не начинает новых частей (те, что в полёте,
 * доходят), а возврат сети будит ожидающие немедленно.
 */
export interface UploadGate {
  canStart(): boolean;
  waitUntilStartable(signal: AbortSignal): Promise<void>;
  /** Пауза backoff. `true` — её прервал возврат сети, счётчик попыток обнуляем. */
  sleep(ms: number, signal: AbortSignal): Promise<boolean>;
}

export interface UploadQueueOptions {
  uploadId: string;
  files: UploadQueueFile[];
  signal: AbortSignal;
  partBytes?: number;
  concurrency?: number;
  gate?: UploadGate;
  onProgress?: (progress: UploadQueueProgress) => void;
  uploadPart?: typeof defaultUploadPart;
}

export const MAX_PART_ATTEMPTS = 6;
const BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000];

export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  const base = BACKOFF_MS[Math.min(Math.max(attempt, 0), BACKOFF_MS.length - 1)];
  return base + Math.round(random() * 500);
}

/** Мобильный канал уже и рвётся чаще — на нём третий поток только мешает. */
export function deviceConcurrency(): number {
  if (typeof window === "undefined") return 3;
  const coarse = window.matchMedia?.("(pointer: coarse)").matches ?? false;
  const connection = (navigator as { connection?: { type?: string } }).connection;
  return coarse || connection?.type === "cellular" ? 2 : 3;
}

function listen(target: EventTarget | undefined, event: string, handler: () => void): () => void {
  target?.addEventListener(event, handler);
  return () => target?.removeEventListener(event, handler);
}

export function createBrowserGate(): UploadGate {
  const visible = () =>
    typeof document === "undefined" || document.visibilityState !== "hidden";
  return {
    canStart: visible,
    waitUntilStartable(signal) {
      if (visible() || signal.aborted) return Promise.resolve();
      return new Promise<void>((resolve) => {
        const settle = () => {
          if (!visible() && !signal.aborted) return;
          offVisibility();
          offAbort();
          resolve();
        };
        const offVisibility = listen(document, "visibilitychange", settle);
        const offAbort = listen(signal, "abort", settle);
      });
    },
    sleep(ms, signal) {
      if (signal.aborted) return Promise.resolve(false);
      return new Promise<boolean>((resolve) => {
        let woken = false;
        const finish = () => {
          window.clearTimeout(timer);
          offOnline();
          offAbort();
          resolve(woken);
        };
        const timer = window.setTimeout(finish, ms);
        const offOnline = listen(window, "online", () => {
          woken = true;
          finish();
        });
        const offAbort = listen(signal, "abort", finish);
      });
    },
  };
}

function asUploadError(cause: unknown): UploadError | null {
  return cause instanceof UploadError ? cause : null;
}

function isAbort(cause: unknown): boolean {
  return cause instanceof DOMException && cause.name === "AbortError";
}

/**
 * Довести объявленные файлы до сервера. Возвращает, что дошло и что нет; сам
 * `complete` — забота вызывающего, планировщик про публикацию не знает.
 */
export async function runUploadQueue(options: UploadQueueOptions): Promise<UploadQueueOutcome> {
  const {
    uploadId,
    files,
    signal,
    partBytes = DEFAULT_PART_BYTES,
    concurrency = deviceConcurrency(),
    gate = createBrowserGate(),
    onProgress,
    uploadPart = defaultUploadPart,
  } = options;

  const totalBytes = files.reduce((sum, file) => sum + file.blob.size, 0);
  const inFlight = new Map<number, number>();
  const failed: UploadQueueFailure[] = [];
  let cursor = 0;

  const report = () => {
    if (!onProgress) return;
    const settled = files.reduce((sum, file) => sum + file.offset, 0);
    const flying = [...inFlight.values()].reduce((sum, value) => sum + value, 0);
    onProgress({
      completed: files.filter((file) => file.complete).length,
      total: files.length,
      bytes: Math.min(settled + flying, totalBytes),
      totalBytes,
    });
  };
  report();

  const take = (): UploadQueueFile | null => {
    while (cursor < files.length) {
      const file = files[cursor++];
      if (!file.complete) return file;
    }
    return null;
  };

  async function transfer(file: UploadQueueFile): Promise<void> {
    let attempt = 0;
    while (!file.complete && !signal.aborted) {
      if (!gate.canStart()) {
        await gate.waitUntilStartable(signal);
        if (signal.aborted) return;
      }
      const end = Math.min(file.offset + partBytes, file.blob.size);
      const chunk = file.blob.slice(file.offset, end);
      try {
        const receipt: PartReceipt = await uploadPart(
          uploadId,
          file.index,
          file.offset,
          chunk,
          (loaded) => {
            inFlight.set(file.index, loaded);
            report();
          },
          signal,
        );
        inFlight.delete(file.index);
        if (!Number.isSafeInteger(receipt.bytes) || receipt.bytes < 0 || receipt.bytes > file.blob.size
          || (receipt.complete && receipt.bytes !== file.blob.size)
          || (!receipt.complete && receipt.bytes <= file.offset)) {
          throw new UploadError(422, "Сервер вернул неверное смещение. Продолжите загрузку, чтобы сверить состояние.");
        }
        file.offset = receipt.bytes;
        file.complete = receipt.complete;
        attempt = 0;
        report();
      } catch (cause) {
        inFlight.delete(file.index);
        if (signal.aborted || isAbort(cause)) return;
        const failure = asUploadError(cause);
        if (failure?.status === 409 && failure.bytes !== undefined
          && Number.isSafeInteger(failure.bytes) && failure.bytes >= 0 && failure.bytes <= file.blob.size
          && failure.bytes !== file.offset && attempt + 1 < MAX_PART_ATTEMPTS) {
          // Не ошибка: сервер назвал фактический размер, продолжаем с него.
          file.offset = failure.bytes;
          attempt++;
          report();
          continue;
        }
        if (!failure?.retryable || attempt + 1 >= MAX_PART_ATTEMPTS) {
          failed.push({ index: file.index, error: failure ?? new UploadError(0, "") });
          return;
        }
        const woken = await gate.sleep(backoffDelay(attempt), signal);
        attempt = woken ? 0 : attempt + 1;
      }
    }
  }

  async function worker(): Promise<void> {
    for (;;) {
      if (signal.aborted) return;
      const file = take();
      if (!file) return;
      await transfer(file);
    }
  }

  await Promise.all(
    Array.from({ length: Math.max(1, Math.min(concurrency, files.length || 1)) }, worker),
  );
  report();
  return {
    completed: files.filter((file) => file.complete).map((file) => file.index),
    failed,
  };
}
