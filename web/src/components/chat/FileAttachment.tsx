import { useEffect, useState } from "react";
import { Download, FileText, FolderOpen } from "lucide-react";
import { describeAttachment, downloadWorkspaceFile, formatSize, isImageKind, type UploadedAttachment } from "@/lib/chat-attachments";
import { artifactUrl } from "@/lib/chat-artifacts";
import { authedFetch, withBasePath } from "@/lib/api";

/** Metadata and bytes both pass through the Files server's access checks. */
export function FileAttachment({ path, name }: { path: string; name?: string }) {
  const [file, setFile] = useState<UploadedAttachment | null>(null);
  const [failed, setFailed] = useState(false);
  const [imageFailed, setImageFailed] = useState(false);
  const [preview, setPreview] = useState<string>();
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setFile(null); setFailed(false); setImageFailed(false); setPreview(undefined);
    void describeAttachment(path, controller.signal).then(value => {
      if (!controller.signal.aborted) setFile(value);
    }).catch(() => {
      if (!controller.signal.aborted) setFailed(true);
    });
    return () => controller.abort();
  }, [path]);
  useEffect(() => {
    if (!file || !isImageKind(file.kind) || file.size > 25 * 1024 * 1024 || !window.__HERMES_SESSION_TOKEN__) return;
    const controller = new AbortController();
    let objectUrl: string | undefined;
    void authedFetch(`/api/files/download?${new URLSearchParams({ path: file.path, inline: "1", chat: "1" })}`, { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error("preview");
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setPreview(objectUrl);
      }).catch(() => { if (!controller.signal.aborted) setImageFailed(true); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [file]);
  const label = name || file?.name || path.split("/").pop() || "Файл";
  const folder = file?.kind === "folder";
  const download = file && !folder ? `${artifactUrl(file.path, false)}&chat=1` : undefined;
  return (
    <div className="my-3 max-w-lg rounded-xl bg-[var(--neo-surface)] p-3 shadow-[var(--neo-depth-1)]" aria-label={`Вложение: ${label}`}>
      {file && isImageKind(file.kind) && file.size <= 25 * 1024 * 1024 && !imageFailed && (!window.__HERMES_SESSION_TOKEN__ || preview) && (
        <img src={preview || `${artifactUrl(file.path)}&chat=1`} alt={label} loading="lazy"
          onError={() => setImageFailed(true)} className="mb-3 max-h-80 rounded-lg object-contain" />
      )}
      <div className="flex items-center gap-3">
        {folder ? <FolderOpen size={22} className="shrink-0 text-muted-foreground" aria-hidden /> : <FileText size={22} className="shrink-0 text-muted-foreground" aria-hidden />}
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm" title={label}>{label}</div>
          <div className="text-xs text-muted-foreground" role="status">
            {file ? `${folder ? `${file.truncated ? "Не менее " : ""}${file.file_count} файлов` : file.kind.toUpperCase()} · ${formatSize(file.size)}` : failed
              ? "Вложение недоступно: удалено, перемещено, больше 2 ГБ или вне рабочей папки." : "Проверяем вложение…"}
          </div>
        </div>
        {download && <a href={download} download={label} className="inline-flex items-center gap-1 rounded-lg px-3 py-2 text-xs shadow-[var(--neo-depth-1)]" aria-label={`Скачать ${label}`}
          aria-disabled={downloading} onClick={event => {
            if (!window.__HERMES_SESSION_TOKEN__) return;
            event.preventDefault();
            if (downloading || !file) return;
            setDownloading(true); setDownloadError(false);
            void downloadWorkspaceFile(file.path, label, true).catch(() => setDownloadError(true)).finally(() => setDownloading(false));
          }}>
          <Download size={14} aria-hidden /> {downloading ? "Скачиваем…" : "Скачать"}
        </a>}
      </div>
      {downloadError && <p role="alert" className="mt-2 text-xs text-destructive">Не удалось скачать файл. Проверьте соединение или обновите страницу.</p>}
      {file && <a href={withBasePath(`/files?${new URLSearchParams({ path: folder ? file.path : file.path.slice(0, file.path.lastIndexOf("/")) })}`)}
        className="mt-2 inline-flex items-center gap-1 text-xs text-muted-foreground">
        <FolderOpen size={13} aria-hidden /> Показать в «Файлах»
      </a>}
    </div>
  );
}
