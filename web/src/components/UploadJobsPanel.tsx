import { useState } from "react";
import { useStore } from "@nanostores/react";
import { $uploadJobs, pauseUploadJob, resumeUploadJob, cancelUploadJob, dismissUploadJob } from "@/store/upload-jobs";
import { formatSize } from "@/lib/chat-attachments";
import { ownerFacingError } from "@/lib/owner-facing-error";

export function UploadJobsPanel({ origin, ids }: { origin: "chat" | "files"; ids?: string[] }) {
  const jobs = useStore($uploadJobs);
  const [error, setError] = useState("");
  const visible = Object.values(jobs).filter(job => job.origin === origin && (!ids || ids.includes(job.uploadId)));
  if (!visible.length) return null;
  return <section aria-label="Загрузки" className="my-3 space-y-2 text-sm">
    {error && <p role="alert" className="text-destructive">{error}</p>}
    {visible.map(job => <div key={job.uploadId} className="rounded-xl bg-[var(--neo-surface)] p-3 shadow-[var(--neo-inset-compact)]">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 truncate">{job.title}</span>
        <span role="status">{job.status === "complete" ? "Сохранено" : job.status === "publishing" ? "Сохраняем файлы…" : job.status === "paused" ? "Пауза" : job.status === "pausing" ? "Останавливаем…" : job.status === "failed" ? "Нужен повтор" : job.status === "preparing" ? "Сверяем загрузку…" : `${job.progress.completed}/${job.progress.total} · ${formatSize(job.progress.bytes)} / ${formatSize(job.progress.totalBytes)}`}</span>
        {["preparing", "uploading"].includes(job.status) && <button type="button" onClick={() => pauseUploadJob(job.uploadId)} className="rounded-lg px-3 py-2">Пауза</button>}
        {["paused", "failed"].includes(job.status) && <button type="button" onClick={() => resumeUploadJob(job.uploadId)} className="rounded-lg px-3 py-2">Продолжить</button>}
        {job.status === "complete" ? <button type="button" onClick={() => dismissUploadJob(job.uploadId)} className="rounded-lg px-3 py-2">Скрыть</button>
          : job.status !== "publishing" && <button type="button" onClick={() => { setError(""); void cancelUploadJob(job.uploadId).catch(cause => setError(ownerFacingError(cause, "Не удалось отменить. Продолжите загрузку для проверки состояния."))); }} className="rounded-lg px-3 py-2">Отменить загрузку</button>}
      </div>
      {job.status !== "complete" && <progress className="mt-2 h-1 w-full" aria-label={`Прогресс: ${job.title}`} max={job.progress.totalBytes || 1} value={job.progress.bytes} />}
      {job.error && <p role="alert" className="mt-1 text-xs text-destructive">{job.error}</p>}
      {["paused", "failed"].includes(job.status) && <p className="mt-1 text-xs text-muted-foreground">Принятые части сохранены. После обновления вкладки выберите тот же набор в той же папке — передадим только недостающее.</p>}
    </div>)}
  </section>;
}
