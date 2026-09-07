import { atom } from "nanostores";
import { folderUploadKey, uploadFolder, type FolderSelection, type UploadProgress } from "@/lib/calc-folder-upload";
import { ownerFacingError } from "@/lib/owner-facing-error";

export type FolderUploadStatus = "uploading" | "pausing" | "paused" | "failed" | "complete";
export interface FolderUploadJob {
  uploadId: string;
  name: string;
  status: FolderUploadStatus;
  progress: UploadProgress;
  selection: FolderSelection | null;
  orderId?: string;
  error?: string;
}

// The browser tab owns this job. Route components only subscribe; unmounting a
// page cannot cancel the request or drop the File handles needed for resume.
export const $folderUpload = atom<FolderUploadJob | null>(null);
let controller: AbortController | null = null;
let running: Promise<void> | null = null;

function beforeUnload(event: BeforeUnloadEvent) {
  event.preventDefault();
}

function guardPageUnload(enabled: boolean) {
  if (typeof window === "undefined") return;
  if (enabled) window.addEventListener("beforeunload", beforeUnload);
  else window.removeEventListener("beforeunload", beforeUnload);
}

export function folderUploadIsActive(job: FolderUploadJob | null): boolean {
  return job?.status === "uploading" || job?.status === "pausing";
}

async function transfer(job: FolderUploadJob, signal: AbortSignal): Promise<void> {
  if (!job.selection) return;
  guardPageUnload(true);
  try {
    const orderId = await uploadFolder(job.selection, job.uploadId, (progress) => {
      const current = $folderUpload.get();
      if (current?.uploadId === job.uploadId) $folderUpload.set({ ...current, progress });
    }, signal);
    const current = $folderUpload.get();
    if (current?.uploadId === job.uploadId) {
      $folderUpload.set({ ...current, status: "complete", orderId, selection: null,
        progress: { ...current.progress, completed: current.progress.total, bytes: current.progress.totalBytes } });
    }
  } catch (cause) {
    const current = $folderUpload.get();
    if (current?.uploadId === job.uploadId) {
      $folderUpload.set({ ...current, status: signal.aborted ? "paused" : "failed",
        error: signal.aborted ? undefined : ownerFacingError(cause, "Загрузка прервана. Нажмите «Продолжить загрузку»."),
      });
    }
  } finally {
    guardPageUnload(false);
    controller = null;
    running = null;
  }
}

function run(job: FolderUploadJob) {
  controller = new AbortController();
  $folderUpload.set({ ...job, status: "uploading", error: undefined });
  running = transfer(job, controller.signal);
}

export function startFolderUpload(selection: FolderSelection): boolean {
  const current = $folderUpload.get();
  if (running || (current && current.status !== "complete")) return false;
  const job: FolderUploadJob = {
    uploadId: folderUploadKey(selection), name: selection.name, status: "uploading", selection,
    progress: { completed: 0, total: selection.files.length, bytes: 0,
      totalBytes: selection.files.reduce((sum, item) => sum + item.file.size, 0) },
  };
  run(job);
  return true;
}

export function pauseFolderUpload(): void {
  const current = $folderUpload.get();
  if (!current || current.status !== "uploading" || !controller) return;
  $folderUpload.set({ ...current, status: "pausing" });
  controller.abort();
}

export function resumeFolderUpload(): void {
  const current = $folderUpload.get();
  if (running || !current?.selection || !["paused", "failed"].includes(current.status)) return;
  run(current);
}

export function dismissFolderUpload(): void {
  if (!running) $folderUpload.set(null);
}
