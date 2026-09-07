import { useEffect, useState } from "react";
import { Download, FileText, FolderOpen } from "lucide-react";
import { describeAttachment, formatSize, isImageKind, type UploadedAttachment } from "@/lib/chat-attachments";
import { artifactUrl } from "@/lib/chat-artifacts";
import { withBasePath } from "@/lib/api";

/** Metadata and bytes both pass through the Files server's access checks. */
export function FileAttachment({ path, name }: { path: string; name?: string }) {
  const [file, setFile] = useState<UploadedAttachment | null>(null);
  const [failed, setFailed] = useState(false);
  const [imageFailed, setImageFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void describeAttachment(path, controller.signal).then(setFile).catch(() => {
      if (!controller.signal.aborted) setFailed(true);
    });
    return () => controller.abort();
  }, [path]);
  const label = name || file?.name || path.split("/").pop() || "Файл";
  const download = file ? `${artifactUrl(file.path, false)}&chat=1` : undefined;
  return (
    <div className="my-3 max-w-lg rounded-xl bg-[var(--neo-surface)] p-3 shadow-[var(--neo-depth-1)]" aria-label={`Вложение: ${label}`}>
      {file && isImageKind(file.kind) && !imageFailed && (
        <img src={`${artifactUrl(file.path)}&chat=1`} alt={label} loading="lazy"
          onError={() => setImageFailed(true)} className="mb-3 max-h-80 rounded-lg object-contain" />
      )}
      <div className="flex items-center gap-3">
        <FileText size={22} className="shrink-0 text-muted-foreground" aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm" title={label}>{label}</div>
          <div className="text-xs text-muted-foreground" role="status">
            {file ? `${file.kind.toUpperCase()} · ${formatSize(file.size)}` : failed
              ? "Файл недоступен: удалён, перемещён, больше 100 МБ или вне рабочей папки." : "Проверяем файл…"}
          </div>
        </div>
        {download && <a href={download} download={label} className="inline-flex items-center gap-1 rounded-lg px-3 py-2 text-xs shadow-[var(--neo-depth-1)]" aria-label={`Скачать ${label}`}>
          <Download size={14} aria-hidden /> Скачать
        </a>}
      </div>
      {file && <a href={withBasePath(`/files?${new URLSearchParams({ path: file.path.slice(0, file.path.lastIndexOf("/")) })}`)}
        className="mt-2 inline-flex items-center gap-1 text-xs text-muted-foreground">
        <FolderOpen size={13} aria-hidden /> Показать в «Файлах»
      </a>}
    </div>
  );
}
