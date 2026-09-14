import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { FolderOpen, X } from "lucide-react";
import { api, type ManagedFilesResponse } from "@/lib/api";
import { describeAttachment, formatSize, MAX_ATTACHMENTS, type UploadedAttachment } from "@/lib/chat-attachments";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@nous-research/ui/ui/components/dialog";
import { $uploadJobs } from "@/store/upload-jobs";
import { useStore } from "@nanostores/react";

export function WorkspaceFilePicker({ disabled, onPick, remaining = MAX_ATTACHMENTS, profile }: {
  disabled: boolean;
  remaining?: number;
  profile?: string;
  onPick: (file: UploadedAttachment) => void;
}) {
  const [open, setOpen] = useState(false);
  const [path, setPath] = useState<string>();
  const [listing, setListing] = useState<ManagedFilesResponse>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [params, setParams] = useSearchParams();
  const jobs = useStore($uploadJobs);
  const incoming = JSON.stringify([...new Set(params.getAll("attach"))]);
  const recipient = params.get("agent");
  useEffect(() => {
    const paths: string[] = JSON.parse(incoming);
    if (!paths.length || disabled) return;
    if (recipient && recipient !== (profile || "default")) return;
    let cancelled = false;
    if (paths.length > remaining) {
      setOpen(true); setError(`Свободных мест: ${remaining}. Передайте содержащую файлы папку или уменьшите выбор.`);
      return;
    }
    void Promise.all(paths.map(path => describeAttachment(path))).then(files => {
      if (!cancelled) files.forEach(onPick);
    }).catch(() => {
      if (!cancelled) { setOpen(true); setError("Файл недоступен. Выберите его в рабочей папке."); }
    }).finally(() => {
      if (!cancelled) setParams(previous => { const next = new URLSearchParams(previous); next.delete("attach"); return next; }, { replace: true });
    });
    return () => { cancelled = true; };
  }, [incoming, disabled, remaining, recipient, profile, onPick, setParams]);
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setListing(undefined);
    void api.listFiles(path).then(value => { if (!cancelled) setListing(value); })
      .catch(() => { if (!cancelled) setError("Не удалось открыть папку. Попробуйте ещё раз."); });
    return () => { cancelled = true; };
  }, [open, path]);
  return <>
    <button type="button" disabled={disabled} onClick={() => setOpen(true)}
      className="korra-chat-composer__control" aria-label="Выбрать из Файлов" title="Выбрать из Файлов">
      <FolderOpen size={20} aria-hidden />
    </button>
    <Dialog open={open} onOpenChange={setOpen}><DialogContent className="max-w-lg" aria-label="Выбрать из Файлов">
        <div className="mb-3 flex items-center justify-between"><DialogTitle>Файлы рабочей папки</DialogTitle>
          <button type="button" onClick={() => setOpen(false)} aria-label="Закрыть выбор файла"><X size={20} /></button></div>
        <DialogDescription>Файл или папка прикрепится к сообщению без повторной загрузки.</DialogDescription>
        {Object.values(jobs).some(job => job.result) && <details className="my-2 text-sm"><summary>Недавние загрузки</summary>
          {Object.values(jobs).filter(job => job.result).slice(-8).flatMap<{ path: string; name: string }>(job => job.result!.folder ? [job.result!.folder] : job.result!.files).map(file =>
            <button key={file.path} type="button" className="block max-w-full truncate py-2" disabled={busy} onClick={() => {
              setBusy(true); void describeAttachment(file.path).then(value => { onPick(value); setOpen(false); })
                .catch(() => setError("Вложение недоступно. Выберите его в рабочей папке.")).finally(() => setBusy(false));
            }}>{file.name}</button>)}
        </details>}
        {error && <p role="alert" className="mb-2 text-sm text-destructive">{error}</p>}
        {listing?.parent && <button type="button" className="mb-2 text-sm" onClick={() => setPath(listing.parent!)}>← На уровень выше</button>}
        {listing && <button type="button" disabled={busy} className="mb-2 ml-3 rounded-lg px-3 py-2 text-sm" onClick={() => {
          setBusy(true); void describeAttachment(listing.path).then(file => { onPick(file); setOpen(false); })
            .catch(() => setError("Эту папку нельзя прикрепить.")).finally(() => setBusy(false));
        }}>Прикрепить эту папку</button>}
        <div className="max-h-80 overflow-y-auto">
          {!listing ? <p role="status">Открываем папку…</p> : listing.entries.length === 0 ? <p>Папка пуста</p> : listing.entries.map(entry =>
            <button key={entry.path} type="button" disabled={busy} className="flex w-full items-center gap-2 rounded-lg px-2 py-3 text-left text-sm hover:shadow-[var(--neo-inset-compact)]"
              onClick={() => {
                setError("");
                if (entry.is_directory) { setPath(entry.path); return; }
                setBusy(true);
                void describeAttachment(entry.path).then(file => { onPick(file); setOpen(false); })
                  .catch(() => setError("Файл недоступен или больше 2 ГБ. Выберите другой."))
                  .finally(() => setBusy(false));
              }}>
              {entry.is_directory && <FolderOpen size={16} aria-hidden />}<span className="min-w-0 flex-1 truncate">{entry.name}</span>
              {!entry.is_directory && <span className="text-xs text-muted-foreground">{entry.size === null ? "" : formatSize(entry.size)}</span>}
            </button>)}
        </div>
    </DialogContent></Dialog>
  </>;
}
