import { useStore } from "@nanostores/react";
import { Link } from "react-router";
import { CheckCircle2, Pause, Play, Upload, X } from "lucide-react";
import { ProductButton } from "@/components/ProductButton";
import { $folderUpload, dismissFolderUpload, pauseFolderUpload, resumeFolderUpload } from "@/store/calc-folder-upload";

function bytesLabel(bytes: number): string {
  return bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} ГБ` : `${(bytes / 1024 ** 2).toFixed(1)} МБ`;
}

export function CalcFolderUploadPanel() {
  const job = useStore($folderUpload);
  if (!job) return null;
  const { status, progress } = job;
  const done = status === "complete";
  const label = done ? "Папка загружена. Черновик сохранён"
    : status === "paused" ? "Загрузка приостановлена"
      : status === "pausing" ? "Приостанавливаю загрузку…"
        : status === "failed" ? "Загрузка прервана"
          : progress.completed === progress.total ? "Сохраняю черновик…" : "Загружаю папку заказа";
  return <section aria-label="Загрузка заказа" className="sticky top-2 z-20 mb-4 shrink-0 space-y-3 rounded-xl border border-border bg-background p-3 shadow-sm sm:p-4">
    <div className="flex items-start gap-3">
      {done ? <CheckCircle2 aria-hidden className="mt-1 size-5 shrink-0 text-success" /> : <Upload aria-hidden className="mt-1 size-5 shrink-0 text-primary" />}
      <div className="min-w-0 flex-1">
        <p className="break-words text-sm font-semibold [overflow-wrap:anywhere]">{job.name}</p>
        <p role="status" className="text-sm text-muted-foreground">{label}</p>
      </div>
      {done && <ProductButton ghost size="icon" aria-label="Скрыть уведомление о загрузке" onClick={dismissFolderUpload}><X /></ProductButton>}
    </div>
    {!done && <>
      <progress aria-label="Прогресс загрузки заказа" className="block h-2 w-full accent-primary" value={progress.completed} max={Math.max(1, progress.total)} />
      <p className="text-sm tabular-nums">Загружено {progress.completed} из {progress.total} · {bytesLabel(progress.bytes)} из {bytesLabel(progress.totalBytes)}</p>
    </>}
    {job.error && <p role="alert" className="text-sm text-destructive">{job.error}</p>}
    <div className="flex flex-wrap items-center gap-3">
      {status === "uploading" && <ProductButton outlined size="sm" prefix={<Pause />} onClick={pauseFolderUpload}>Приостановить</ProductButton>}
      {status === "pausing" && <ProductButton outlined size="sm" disabled>Приостанавливаю…</ProductButton>}
      {(status === "paused" || status === "failed") && <>
        <ProductButton size="sm" prefix={<Play />} onClick={resumeFolderUpload}>Продолжить загрузку</ProductButton>
        <ProductButton ghost size="sm" onClick={dismissFolderUpload}>Отменить</ProductButton>
      </>}
      {done && job.orderId ? <>
        <Link className="inline-flex min-h-11 items-center rounded-lg border border-primary bg-primary px-3 text-sm font-semibold text-primary-foreground" to={`/orders?order=${encodeURIComponent(job.orderId)}`}>Открыть заказ</Link>
        <Link className="inline-flex min-h-11 items-center rounded-lg border border-border px-3 text-sm font-semibold" to={`/files?order=${encodeURIComponent(job.orderId)}`}>Открыть папку</Link>
      </> : <span className="text-xs text-muted-foreground">Можно переходить между разделами кабинета.</span>}
    </div>
  </section>;
}
