import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  Check,
  Download,
  ExternalLink,
  FileQuestion,
  Loader2,
  Pencil,
  RefreshCw,
  Save,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@nous-research/ui/ui/components/dialog";

import { Markdown } from "@/components/Markdown";
import { api, type ManagedFileTextResponse, type OfficePreview } from "@/lib/api";
import { artifactUrl } from "@/lib/chat-artifacts";
import { ownerFacingError } from "@/lib/owner-facing-error";
import { cn } from "@/lib/utils";

export interface PreviewFile {
  name: string;
  path: string;
  mimeType?: string | null;
  expectedSha256?: string | null;
}

type PreviewKind = "image" | "pdf" | "video" | "audio" | "text" | "office" | "unsupported";
type TextMode = "preview" | "edit" | "source";

const TEXT_EXTENSIONS = new Set(["csv", "json", "md", "markdown", "txt", "yaml", "yml"]);
/** Форматы, которые сервер умеет пересказать текстом. */
const OFFICE_EXTENSIONS = new Set(["docx", "xlsx", "pptx"]);

function extension(name: string): string {
  return name.split(".").pop()?.toLowerCase() ?? "";
}

function previewKind(file: PreviewFile): PreviewKind {
  const mime = (file.mimeType ?? "").toLowerCase();
  const ext = extension(file.name);
  // SVG показываем как картинку: в <img> скрипты внутри файла не исполняются,
  // а ответ сервера несёт CSP sandbox и на случай прямого открытия вкладкой.
  if (mime.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"].includes(ext)) return "image";
  if (OFFICE_EXTENSIONS.has(ext)) return "office";
  if (mime === "application/pdf" || ext === "pdf") return "pdf";
  if (mime.startsWith("video/") || ["mp4", "webm", "mov", "mkv"].includes(ext)) return "video";
  if (mime.startsWith("audio/") || ["mp3", "wav", "m4a", "ogg", "opus", "flac"].includes(ext)) return "audio";
  if (mime.startsWith("text/") || TEXT_EXTENSIONS.has(ext)) return "text";
  return "unsupported";
}

function fileTypeLabel(file: PreviewFile, kind: PreviewKind): string {
  if (kind === "text" && ["md", "markdown"].includes(extension(file.name))) return "Markdown";
  const labels: Record<PreviewKind, string> = {
    image: "Изображение",
    pdf: "PDF",
    video: "Видео",
    audio: "Аудио",
    text: "Текст",
    office: { docx: "Документ Word", xlsx: "Таблица Excel", pptx: "Презентация" }[
      extension(file.name)
    ] ?? "Документ",
    unsupported: extension(file.name).toUpperCase() || "Файл",
  };
  return labels[kind];
}

function ActionButton({
  children,
  primary,
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { primary?: boolean }) {
  return (
    <button
      className={cn(
        "inline-flex min-h-[44px] items-center justify-center gap-2 rounded-lg border px-4 py-2 text-sm font-semibold tracking-normal transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 disabled:cursor-not-allowed disabled:opacity-50",
        primary
          ? "border-primary bg-primary text-primary-foreground hover:bg-primary/90"
          : "border-border bg-background text-foreground hover:border-primary/40 hover:bg-primary/[0.06]",
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}

export function FilePreviewDialog({
  file,
  open,
  onOpenChange,
  initialText,
  allowEdit = true,
  reviewed = false,
  onReviewed,
  onSaved,
}: {
  file: PreviewFile | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialText?: string | null;
  allowEdit?: boolean;
  reviewed?: boolean;
  onReviewed?: (path: string) => void;
  onSaved?: (file: ManagedFileTextResponse) => void;
}) {
  const kind = file ? previewKind(file) : "unsupported";
  const [textFile, setTextFile] = useState<ManagedFileTextResponse | null>(null);
  const [officeFile, setOfficeFile] = useState<OfficePreview | null>(null);
  const [textFileKey, setTextFileKey] = useState("");
  const [originalOpenedKey, setOriginalOpenedKey] = useState("");
  const [draft, setDraft] = useState("");
  const [textMode, setTextMode] = useState<TextMode>("preview");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tabsId = useId();
  const filePath = file?.path ?? "";
  const fileName = file?.name ?? "";
  const expectedSha256 = file?.expectedSha256 ?? null;
  const fileKey = `${filePath}\u0000${expectedSha256 ?? ""}`;
  const markdown = ["md", "markdown"].includes(extension(fileName));
  const textTabs = useMemo<Array<[TextMode, string]>>(() => [
    ...(markdown && !textFile?.truncated ? [["preview", "Читать"]] as Array<[TextMode, string]> : []),
    ["source", "Исходник"],
    ...(textFile?.editable && allowEdit ? [["edit", "Редактировать"]] as Array<[TextMode, string]> : []),
  ], [allowEdit, markdown, textFile?.editable, textFile?.truncated]);
  const onReviewedRef = useRef(onReviewed);
  useEffect(() => {
    onReviewedRef.current = onReviewed;
  }, [onReviewed]);
  const href = useMemo(
    () => filePath ? artifactUrl(filePath, true, expectedSha256) : "",
    [expectedSha256, filePath],
  );
  const downloadHref = useMemo(
    () => filePath ? artifactUrl(filePath, false, expectedSha256) : "",
    [expectedSha256, filePath],
  );

  const markReviewed = useCallback(() => {
    if (filePath) onReviewedRef.current?.(filePath);
  }, [filePath]);

  useEffect(() => {
    if (
      open &&
      kind === "text" &&
      textFileKey === fileKey &&
      textFile &&
      !textFile.binary &&
      !textFile.truncated &&
      !loading &&
      !error
    ) {
      markReviewed();
    }
  }, [error, fileKey, kind, loading, markReviewed, open, textFile, textFileKey]);

  useEffect(() => {
    if (!open || !filePath) return;
    setError(null);
    setTextMode("preview");
    setTextFile(null);
    setTextFileKey("");
    setDraft("");
    setOfficeFile(null);
    if (kind === "office") {
      let alive = true;
      setLoading(true);
      api.readOfficeFile(filePath)
        .then((payload) => {
          if (!alive) return;
          setOfficeFile(payload);
          markReviewed();
        })
        .catch((exception) => {
          if (!alive) return;
          setError(ownerFacingError(exception, "Не удалось открыть файл."));
        })
        .finally(() => {
          if (alive) setLoading(false);
        });
      return () => { alive = false; };
    }
    if (kind !== "text") {
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);
    api.readFileText(filePath, expectedSha256)
      .then((payload) => {
        if (!active) return;
        setTextFile(payload);
        setTextFileKey(fileKey);
        setDraft(payload.text);
        setTextMode(payload.truncated || !markdown ? "source" : "preview");
      })
      .catch((exception) => {
        if (!active) return;
        if (initialText !== undefined && initialText !== null) {
          setTextFile(null);
          setTextFileKey("");
          setDraft(initialText);
        } else {
          setError(ownerFacingError(exception, "Не удалось открыть текст. Повторите попытку."));
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [expectedSha256, fileKey, filePath, initialText, kind, markdown, markReviewed, open]);

  const save = async () => {
    if (!file || !allowEdit || !textFile?.editable || !textFile.sha256 || saving) return;
    setSaving(true);
    setError(null);
    try {
      const saved = await api.writeFileText(file.path, draft, textFile.sha256);
      setTextFile(saved);
      setTextFileKey(fileKey);
      setDraft(saved.text);
      setTextMode(saved.truncated || !markdown ? "source" : "preview");
      onSaved?.(saved);
    } catch (exception) {
      setError(ownerFacingError(exception, "Не удалось сохранить изменения."));
    } finally {
      setSaving(false);
    }
  };

  const reloadText = async () => {
    if (!file) return;
    setLoading(true);
    setError(null);
    setTextFile(null);
    setTextFileKey("");
    setDraft("");
    try {
      const payload = await api.readFileText(file.path, file.expectedSha256);
      setTextFile(payload);
      setTextFileKey(fileKey);
      setDraft(payload.text);
      setTextMode(payload.truncated || !markdown ? "source" : "preview");
    } catch (exception) {
      setError(ownerFacingError(exception, "Не удалось обновить файл."));
    } finally {
      setLoading(false);
    }
  };

  const manualReviewRequired = Boolean(onReviewed) && (
    kind === "pdf" ||
    kind === "unsupported" ||
    (kind === "text" && Boolean(textFile?.binary || textFile?.truncated))
  );
  const originalRequired = kind !== "pdf";
  const canConfirmManualReview =
    manualReviewRequired &&
    !loading &&
    !error &&
    (!originalRequired || originalOpenedKey === fileKey);

  return (
    <Dialog open={open} onOpenChange={(next) => !saving && onOpenChange(next)}>
      <DialogContent className="flex h-[min(92dvh,58rem)] w-[min(96vw,78rem)] max-w-none flex-col overflow-hidden rounded-2xl">
        <DialogHeader className="shrink-0 gap-2 px-4 py-4 pr-12 sm:px-6">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-primary/12 px-2.5 py-1 text-xs font-semibold text-primary">
              {file ? fileTypeLabel(file, kind) : "Файл"}
            </span>
            {textFile?.editable && allowEdit ? (
              <span className="rounded-full bg-success/12 px-2.5 py-1 text-xs font-semibold text-success">Можно редактировать</span>
            ) : null}
          </div>
          <DialogTitle className="truncate font-sans text-base font-semibold normal-case tracking-normal sm:text-lg" title={file?.name}>
            {file?.name ?? "Предпросмотр"}
          </DialogTitle>
          <DialogDescription className="font-sans text-xs leading-5 tracking-normal">
            Просмотр внутри платформы. Скачивание оригинала — отдельное действие.
          </DialogDescription>
        </DialogHeader>

        {kind === "text" && !loading && !error && !textFile?.binary ? (
          <div className="flex shrink-0 gap-1 overflow-x-auto border-b border-border px-3 pt-2 sm:px-5" role="tablist" aria-label="Режим просмотра текста">
            {textTabs.map(([value, label], index) => (
              <button
                key={value}
                id={`${tabsId}-tab-${value}`}
                type="button"
                role="tab"
                aria-selected={textMode === value}
                aria-controls={`${tabsId}-panel`}
                tabIndex={textMode === value ? 0 : -1}
                onClick={() => setTextMode(value)}
                onKeyDown={(event) => {
                  const last = textTabs.length - 1;
                  const nextIndex = event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? last
                      : event.key === "ArrowRight"
                        ? (index + 1) % textTabs.length
                        : event.key === "ArrowLeft"
                          ? (index - 1 + textTabs.length) % textTabs.length
                          : null;
                  if (nextIndex === null) return;
                  event.preventDefault();
                  const nextMode = textTabs[nextIndex][0];
                  setTextMode(nextMode);
                  document.getElementById(`${tabsId}-tab-${nextMode}`)?.focus();
                }}
                className={cn(
                  "min-h-11 shrink-0 border-b-2 px-3 text-sm font-medium tracking-normal focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40",
                  textMode === value ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground",
                )}
              >
                {value === "edit" ? <Pencil className="mr-2 inline size-4" aria-hidden /> : null}
                {label}
              </button>
            ))}
          </div>
        ) : null}

        <div
          id={kind === "text" ? `${tabsId}-panel` : undefined}
          role={kind === "text" && !loading && !error && !textFile?.binary ? "tabpanel" : undefined}
          aria-labelledby={kind === "text" && !loading && !error && !textFile?.binary ? `${tabsId}-tab-${textMode}` : undefined}
          className="min-h-0 flex-1 overflow-auto bg-muted/[0.08]"
        >
          {loading ? (
            <div className="grid h-full min-h-64 place-items-center" role="status"><span className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />Открываем файл…</span></div>
          ) : error ? (
            <div className="grid h-full min-h-64 place-items-center p-6 text-center" role="alert"><div><FileQuestion className="mx-auto size-8 text-warning" /><p className="mt-3 max-w-lg text-sm leading-6 text-muted-foreground">{error}</p><ActionButton className="mt-4" onClick={() => kind === "text" && void reloadText()}><RefreshCw className="size-4" />Повторить</ActionButton></div></div>
          ) : kind === "text" && textFile?.binary ? (
            <div className="grid h-full min-h-64 place-items-center p-6 text-center" role="status"><div><FileQuestion className="mx-auto size-9 text-muted-foreground" /><h3 className="mt-3 font-semibold">Этот файл не является обычным текстом</h3><p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">Откройте оригинал в приложении для этого формата.</p></div></div>
          ) : kind === "image" ? (
            <div className="grid h-full min-h-64 place-items-center p-3"><img src={href} alt={file?.name ?? "Изображение"} onLoad={markReviewed} className="max-h-full max-w-full rounded-lg object-contain" /></div>
          ) : kind === "pdf" ? (
            <iframe src={href} title={file?.name ?? "PDF"} className="h-full min-h-[30rem] w-full bg-white" />
          ) : kind === "video" ? (
            <div className="grid h-full min-h-64 place-items-center bg-black p-3"><video controls preload="metadata" onLoadedMetadata={markReviewed} className="max-h-full max-w-full"><source src={href} />Ваш браузер не смог открыть видео.</video></div>
          ) : kind === "audio" ? (
            <div className="grid h-full min-h-64 place-items-center p-6"><audio controls preload="metadata" onLoadedMetadata={markReviewed} className="w-full max-w-2xl"><source src={href} />Ваш браузер не смог открыть аудио.</audio></div>
          ) : kind === "office" ? (
            officeFile ? (
              <article className="mx-auto max-w-3xl space-y-4 p-5 text-[0.95rem] leading-7 sm:p-8">
                {officeFile.truncated ? (
                  <p className="rounded-lg border border-warning/30 bg-warning/[0.08] px-4 py-3 text-sm text-warning" role="status">
                    Показано начало документа. Скачайте оригинал, чтобы увидеть его целиком.
                  </p>
                ) : null}
                {officeFile.blocks.map((block, index) =>
                  block.type === "heading" ? (
                    <h3 key={index} className="mt-6 border-b border-border pb-2 text-lg font-semibold">
                      {block.text}
                    </h3>
                  ) : block.type === "paragraph" ? (
                    <p key={index}>{block.text}</p>
                  ) : (
                    <div key={index} className="overflow-x-auto">
                      <table className="w-full border-collapse text-left text-sm">
                        <tbody>
                          {block.rows.map((row, rowIndex) => (
                            <tr key={rowIndex} className="border-b border-border/60 last:border-0">
                              {row.map((cell, cellIndex) => (
                                <td key={cellIndex} className="px-3 py-2 align-top">{cell}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ),
                )}
                <p className="border-t border-border pt-4 text-xs text-muted-foreground">
                  Это текст из файла: оформление, формулы и картинки не показываются.
                  Чтобы увидеть оригинал целиком, скачайте его.
                </p>
              </article>
            ) : null
          ) : kind === "text" ? (
            textMode === "edit" ? (
              <div className="h-full p-3 sm:p-5"><label htmlFor="file-preview-editor" className="sr-only">Текст файла</label><textarea id="file-preview-editor" value={draft} onChange={(event) => setDraft(event.target.value)} className="h-full min-h-[26rem] w-full resize-none rounded-xl border border-border bg-background p-4 font-mono text-sm leading-6 outline-none focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-primary/25" spellCheck /></div>
            ) : textMode === "source" ? (
              <div className="min-h-full">
                {textFile?.truncated ? <p className="border-b border-warning/30 bg-warning/[0.08] px-5 py-3 text-sm text-warning" role="status">Показано начало большого файла. Скачайте оригинал, чтобы увидеть его целиком.</p> : null}
                <pre className="whitespace-pre-wrap break-words p-5 font-mono text-sm leading-6 text-foreground sm:p-8">{draft}</pre>
              </div>
            ) : (
              <article className="mx-auto max-w-3xl p-5 sm:p-8"><Markdown content={draft} variant="document" /></article>
            )
          ) : (
            <div className="grid h-full min-h-64 place-items-center p-6 text-center"><div><FileQuestion className="mx-auto size-9 text-muted-foreground" /><h3 className="mt-3 font-semibold">Предпросмотр этого формата пока недоступен</h3><p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">Скачайте оригинал и откройте его в приложении для этого типа файлов.</p></div></div>
          )}
        </div>

        <DialogFooter className="shrink-0 flex-wrap justify-between border-t border-border px-4 py-3 sm:px-6">
          <a href={downloadHref} download onClick={() => setOriginalOpenedKey(fileKey)} className="inline-flex min-h-[44px] items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-muted-foreground hover:bg-muted/40 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40">
            <Download className="size-4" aria-hidden />Скачать оригинал
          </a>
          <div className="flex flex-wrap justify-end gap-2">
            {kind !== "text" && kind !== "unsupported" ? (
              <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-[44px] items-center gap-2 rounded-lg border border-border px-4 py-2 text-sm font-semibold hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"><ExternalLink className="size-4" />Открыть отдельно</a>
            ) : null}
            {textMode === "edit" && textFile?.editable && allowEdit ? (
              <ActionButton primary disabled={saving || draft === textFile.text} onClick={() => void save()}>{saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}Сохранить изменения</ActionButton>
            ) : null}
            {manualReviewRequired ? (
              <ActionButton
                primary
                disabled={reviewed || !canConfirmManualReview}
                onClick={markReviewed}
              >
                <Check className="size-4" aria-hidden />
                {reviewed ? "Просмотр подтверждён" : "Подтвердить просмотр"}
              </ActionButton>
            ) : null}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
