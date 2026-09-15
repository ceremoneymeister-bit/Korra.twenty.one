import { useCallback, useEffect, useState } from "react";
import { Download, FileText, FolderOpen } from "lucide-react";
import { describeAttachment, downloadWorkspaceFile, formatSize, isImageKind, type UploadedAttachment } from "@/lib/chat-attachments";
import { artifactUrl } from "@/lib/chat-artifacts";
import { authedFetch, withBasePath } from "@/lib/api";
import { ownerFacingError } from "@/lib/owner-facing-error";
import { ImageViewer } from "@/components/chat/ImageViewer";

const UNAVAILABLE_FALLBACK = "Вложение недоступно: удалено, перемещено, больше 2 ГБ или вне рабочей папки.";

/** Metadata and bytes both pass through the Files server's access checks. */
export function FileAttachment({ path, name }: { path: string; name?: string }) {
  const [file, setFile] = useState<UploadedAttachment | null>(null);
  // Причина недоступности приходит с сервера вместе с продолжением («попросите
  // агента сохранить в workspace»); общий текст — только когда причины нет.
  const [failed, setFailed] = useState<string | null>(null);
  const [imageFailed, setImageFailed] = useState(false);
  const [preview, setPreview] = useState<string>();
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState(false);
  const [viewing, setViewing] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setFile(null); setFailed(null); setImageFailed(false); setPreview(undefined); setViewing(false);
    let pending = false;
    let checkedAt = 0;
    let loaded = false;
    const refresh = async (force = false) => {
      if (pending || (!force && (document.hidden || Date.now() - checkedAt < 30_000))) return;
      pending = true;
      checkedAt = Date.now();
      try {
        const value = await describeAttachment(path, controller.signal);
        if (controller.signal.aborted) return;
        loaded = true;
        setFailed(null);
        // Retain the same object and <img> for unchanged files. Only a new
        // revision triggers the existing blob effect and its URL cleanup.
        setFile(previous => JSON.stringify(previous) === JSON.stringify(value) ? previous : value);
      } catch (error) {
        if (controller.signal.aborted) return;
        // Offline/5xx must not erase already visible content. An explicit
        // access/deletion refusal invalidates it, including an open viewer.
        if (!loaded || (error instanceof Error && /^(400|401|403|404|410|413):/.test(error.message))) {
          setFile(null); setPreview(undefined); setViewing(false);
          setFailed(ownerFacingError(error, UNAVAILABLE_FALLBACK));
        }
      } finally { pending = false; }
    };
    void refresh(true);
    const onReturn = () => { void refresh(); };
    const onOnline = () => { void refresh(true); };
    const timer = window.setInterval(onReturn, 30_000);
    window.addEventListener("focus", onReturn);
    document.addEventListener("visibilitychange", onReturn);
    window.addEventListener("online", onOnline);
    return () => {
      controller.abort(); window.clearInterval(timer);
      window.removeEventListener("focus", onReturn);
      document.removeEventListener("visibilitychange", onReturn);
      window.removeEventListener("online", onOnline);
    };
  }, [path]);
  useEffect(() => {
    setImageFailed(false);
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
  // Просмотр показывает ровно то, что уже получила карточка: object URL при
  // токене сессии, иначе тот же проверяемый маршрут Files. Ограничение 25 МБ
  // остаётся здесь — большой файл по-прежнему только скачивается.
  const imageSrc = file && isImageKind(file.kind) && file.size <= 25 * 1024 * 1024 && !imageFailed && (!window.__HERMES_SESSION_TOKEN__ || preview)
    ? preview || `${artifactUrl(file.path)}&chat=1&revision=${encodeURIComponent(file.revision ?? "")}`
    : undefined;
  // Токен сессии нельзя класть в ссылку, поэтому в таком контуре и карточка,
  // и просмотр скачивают одним и тем же авторизованным запросом.
  const authedDownload = useCallback(() => {
    if (downloading || !file) return;
    setDownloading(true); setDownloadError(false);
    void downloadWorkspaceFile(file.path, label, true).catch(() => setDownloadError(true)).finally(() => setDownloading(false));
  }, [downloading, file, label]);
  return (
    <div className="my-3 max-w-lg rounded-xl bg-[var(--neo-surface)] p-3 shadow-[var(--neo-depth-1)]" aria-label={`Вложение: ${label}`}>
      {imageSrc && (
        <button type="button" onClick={() => setViewing(true)} aria-label={`Открыть изображение крупно: ${label}`}
          className="mb-3 block cursor-zoom-in rounded-lg border-0 bg-transparent p-0 outline-none focus-visible:shadow-[var(--neo-inset-compact)]">
          <img src={imageSrc} alt={label} loading="lazy"
            onError={() => setImageFailed(true)} className="max-h-80 rounded-lg object-contain" />
        </button>
      )}
      {viewing && imageSrc && (
        <ImageViewer src={imageSrc} name={label} downloadHref={download} downloading={downloading}
          onDownload={window.__HERMES_SESSION_TOKEN__ ? authedDownload : undefined}
          onClose={() => setViewing(false)} />
      )}
      <div className="flex items-center gap-3">
        {folder ? <FolderOpen size={22} className="shrink-0 text-muted-foreground" aria-hidden /> : <FileText size={22} className="shrink-0 text-muted-foreground" aria-hidden />}
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm" title={label}>{label}</div>
          <div className="text-xs text-muted-foreground" role="status">
            {file ? `${folder ? `${file.truncated ? "Не менее " : ""}${file.file_count} файлов` : file.kind.toUpperCase()} · ${formatSize(file.size)}` : failed
              ? (failed === UNAVAILABLE_FALLBACK || /^Вложение недоступно/.test(failed) ? failed : `Вложение недоступно. ${failed}`) : "Проверяем вложение…"}
          </div>
        </div>
        {download && <a href={download} download={label} className="inline-flex items-center gap-1 rounded-lg px-3 py-2 text-xs shadow-[var(--neo-depth-1)]" aria-label={`Скачать ${label}`}
          aria-disabled={downloading} onClick={event => {
            if (!window.__HERMES_SESSION_TOKEN__) return;
            event.preventDefault();
            authedDownload();
          }}>
          <Download size={14} aria-hidden /> {downloading ? "Скачиваем…" : "Скачать"}
        </a>}
      </div>
      {downloadError && <p role="alert" className="mt-2 text-xs text-destructive">Не удалось скачать файл. Проверьте соединение или обновите страницу.</p>}
      {file && <a href={withBasePath(`/files?${new URLSearchParams(folder
          ? { path: file.path }
          : { path: file.path.slice(0, file.path.lastIndexOf("/")), highlight: file.name })}`)}
        className="mt-2 inline-flex items-center gap-1 text-xs text-muted-foreground">
        <FolderOpen size={13} aria-hidden /> Показать в папке
      </a>}
    </div>
  );
}
