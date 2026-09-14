/**
 * Задачи загрузки на уровне вкладки (K21-059).
 *
 * Владеет задачей вкладка, а не страница: уход с экрана «Файлы» в чат не должен
 * обрывать передачу и терять объекты File, без которых дозагрузка невозможна.
 * Компоненты только подписываются.
 *
 * Порт `store/calc-folder-upload.ts` расчётчика, расширенный до нескольких
 * одновременных задач: в чате и в «Файлах» они идут параллельно и независимо.
 */

import { atom } from "nanostores";
import { ownerFacingError } from "@/lib/owner-facing-error";
import {
  DEFAULT_PART_BYTES,
  cancelUpload,
  completeUpload,
  createUpload,
  uploadStatus,
  UploadError,
  type UploadManifest,
  type UploadResult,
} from "@/lib/upload-session";
import {
  runUploadQueue,
  type UploadQueueFile,
  type UploadQueueProgress,
} from "@/lib/upload-queue";

export type UploadJobStatus =
  | "preparing"
  | "uploading"
  | "publishing"
  | "pausing"
  | "paused"
  | "failed"
  | "complete";

export interface UploadJob {
  uploadId: string;
  title: string;
  origin: "chat" | "files";
  status: UploadJobStatus;
  progress: UploadQueueProgress;
  result?: UploadResult;
  error?: string;
}

export interface StartUploadInput {
  manifest: UploadManifest;
  /** Тела файлов по порядку манифеста. */
  blobs: Blob[];
  title: string;
}

export const $uploadJobs = atom<Record<string, UploadJob>>({});

interface Running {
  controller: AbortController;
  input: StartUploadInput;
  excluded: Set<number>;
  promise: Promise<void>;
}

const running = new Map<string, Running>();
// Последний вход задачи нужен кнопке «Продолжить»: объекты File из состояния
// не восстановить, а перекачивать дошедшее незачем.
const lastInput = new Map<string, { input: StartUploadInput; excluded: Set<number> }>();

export function uploadJobIsActive(job: UploadJob | undefined): boolean {
  return job?.status === "preparing" || job?.status === "uploading" || job?.status === "publishing" || job?.status === "pausing";
}

export function activeUploadJobs(): UploadJob[] {
  return Object.values($uploadJobs.get()).filter(uploadJobIsActive);
}

function patch(uploadId: string, changes: Partial<UploadJob>): void {
  const jobs = $uploadJobs.get();
  const current = jobs[uploadId];
  if (!current) return;
  $uploadJobs.set({ ...jobs, [uploadId]: { ...current, ...changes } });
}

function beforeUnload(event: BeforeUnloadEvent) {
  event.preventDefault();
}

function guardPageUnload(): void {
  if (typeof window === "undefined") return;
  if (Object.values($uploadJobs.get()).some(job => job.status !== "complete")) window.addEventListener("beforeunload", beforeUnload);
  else window.removeEventListener("beforeunload", beforeUnload);
}

async function transfer(uploadId: string, job: Running): Promise<void> {
  const { manifest, blobs } = job.input;
  const signal = job.controller.signal;
  try {
    const status = await createUpload(manifest, signal);
    if (status.published && status.result) {
      patch(uploadId, { status: "complete", result: status.result });
      return;
    }
    const done = new Set(status.already_present);
    for (const receipt of status.received) if (receipt.complete) done.add(receipt.index);
    const files: UploadQueueFile[] = blobs
      .map((blob, index) => ({
        index,
        blob,
        offset: status.received.find((item) => item.index === index)?.bytes ?? 0,
        complete: done.has(index),
      }))
      .filter((file) => !job.excluded.has(file.index));

    patch(uploadId, { status: "uploading", error: undefined });
    const outcome = await runUploadQueue({
      uploadId,
      files,
      signal,
      partBytes: status.limits?.part_bytes ?? DEFAULT_PART_BYTES,
      onProgress: (progress) => patch(uploadId, { progress }),
    });
    if (signal.aborted) {
      patch(uploadId, { status: "paused" });
      return;
    }
    if (outcome.failed.length) {
      patch(uploadId, {
        status: "failed",
        error: ownerFacingError(
          outcome.failed[0].error,
          "Загрузка прервалась. Нажмите «Продолжить».",
        ),
      });
      return;
    }
    patch(uploadId, { status: "publishing" });
    const result = await completeUpload(uploadId, [...job.excluded], signal);
    patch(uploadId, { status: "complete", result, error: undefined });
  } catch (cause) {
    patch(uploadId, {
      status: signal.aborted ? "paused" : "failed",
      error: signal.aborted
        ? undefined
        : ownerFacingError(cause, "Загрузка прервалась. Нажмите «Продолжить»."),
    });
  } finally {
    running.delete(uploadId);
    guardPageUnload();
  }
}

function run(uploadId: string, input: StartUploadInput, excluded: Set<number>): void {
  const controller = new AbortController();
  const job: Running = { controller, input, excluded, promise: Promise.resolve() };
  lastInput.set(uploadId, { input, excluded });
  running.set(uploadId, job);
  patch(uploadId, { status: "preparing", error: undefined });
  guardPageUnload();
  job.promise = transfer(uploadId, job);
}

export function startUploadJob(input: StartUploadInput): string {
  const uploadId = input.manifest.upload_id;
  if (running.has(uploadId)) return uploadId;
  const totalBytes = input.blobs.reduce((sum, blob) => sum + blob.size, 0);
  $uploadJobs.set({
    ...$uploadJobs.get(),
    [uploadId]: {
      uploadId,
      title: input.title,
      origin: input.manifest.origin,
      status: "preparing",
      progress: { completed: 0, total: input.blobs.length, bytes: 0, totalBytes },
    },
  });
  run(uploadId, input, new Set());
  return uploadId;
}

export function pauseUploadJob(uploadId: string): void {
  const job = running.get(uploadId);
  if (!job) return;
  patch(uploadId, { status: "pausing" });
  job.controller.abort();
}

export function resumeUploadJob(uploadId: string): void {
  if (running.has(uploadId)) return;
  const current = $uploadJobs.get()[uploadId];
  const previous = lastInput.get(uploadId);
  if (!current || !previous || !["paused", "failed"].includes(current.status)) return;
  run(uploadId, previous.input, previous.excluded);
}

/** Отменить один файл в пакете: остальные дойдут, его сервер не опубликует. */
export function excludeUploadFile(uploadId: string, index: number): void {
  lastInput.get(uploadId)?.excluded.add(index);
}

export async function cancelUploadJob(uploadId: string): Promise<void> {
  const pending = running.get(uploadId)?.promise;
  pauseUploadJob(uploadId);
  await pending;
  try {
    await cancelUpload(uploadId);
  } catch (cause) {
    if (!(cause instanceof UploadError) || ![404, 410].includes(cause.status)) {
      // A lost completion response does not mean the files were not saved.
      const status = await uploadStatus(uploadId);
      if (status.published && status.result) {
        patch(uploadId, { status: "complete", result: status.result, error: undefined });
        guardPageUnload();
        return;
      }
      throw cause;
    }
  }
  dismissUploadJob(uploadId);
}

export function dismissUploadJob(uploadId: string): void {
  if (running.has(uploadId)) return;
  const jobs = { ...$uploadJobs.get() };
  delete jobs[uploadId];
  lastInput.delete(uploadId);
  $uploadJobs.set(jobs);
  guardPageUnload();
}
