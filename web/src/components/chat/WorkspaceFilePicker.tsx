import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { FolderOpen, X } from "lucide-react";
import { api, type ManagedFilesResponse } from "@/lib/api";
import { describeAttachment, formatSize, type UploadedAttachment } from "@/lib/chat-attachments";

export function WorkspaceFilePicker({ disabled, onPick }: {
  disabled: boolean;
  onPick: (file: UploadedAttachment) => void;
}) {
  const [open, setOpen] = useState(false);
  const [path, setPath] = useState<string>();
  const [listing, setListing] = useState<ManagedFilesResponse>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [params, setParams] = useSearchParams();
  const incoming = params.get("attach");
  useEffect(() => {
    if (!incoming || disabled) return;
    let cancelled = false;
    void describeAttachment(incoming).then(file => {
      if (!cancelled) onPick(file);
    }).catch(() => {
      if (!cancelled) { setOpen(true); setError("Файл недоступен. Выберите его в рабочей папке."); }
    }).finally(() => {
      if (!cancelled) setParams(previous => { const next = new URLSearchParams(previous); next.delete("attach"); return next; }, { replace: true });
    });
    return () => { cancelled = true; };
  }, [incoming, disabled, onPick, setParams]);
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
    {open && <div role="dialog" aria-modal="true" aria-label="Выбрать из Файлов"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => setOpen(false)}>
      <div className="w-full max-w-lg rounded-2xl bg-[var(--neo-surface)] p-5 shadow-[var(--neo-depth-1)]" onClick={event => event.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between"><strong>Файлы рабочей папки</strong>
          <button type="button" onClick={() => setOpen(false)} aria-label="Закрыть выбор файла"><X size={20} /></button></div>
        <p className="mb-3 text-xs text-muted-foreground">Файл прикрепится к сообщению без повторной загрузки.</p>
        {error && <p role="alert" className="mb-2 text-sm text-destructive">{error}</p>}
        {listing?.parent && <button type="button" className="mb-2 text-sm" onClick={() => setPath(listing.parent!)}>← На уровень выше</button>}
        <div className="max-h-80 overflow-y-auto">
          {!listing ? <p role="status">Открываем папку…</p> : listing.entries.length === 0 ? <p>Папка пуста</p> : listing.entries.map(entry =>
            <button key={entry.path} type="button" disabled={busy} className="flex w-full items-center gap-2 rounded-lg px-2 py-3 text-left text-sm hover:shadow-[var(--neo-inset-compact)]"
              onClick={() => {
                setError("");
                if (entry.is_directory) { setPath(entry.path); return; }
                setBusy(true);
                void describeAttachment(entry.path).then(file => { onPick(file); setOpen(false); })
                  .catch(() => setError("Файл недоступен или больше 100 МБ. Выберите другой."))
                  .finally(() => setBusy(false));
              }}>
              {entry.is_directory && <FolderOpen size={16} aria-hidden />}<span className="min-w-0 flex-1 truncate">{entry.name}</span>
              {!entry.is_directory && <span className="text-xs text-muted-foreground">{entry.size === null ? "" : formatSize(entry.size)}</span>}
            </button>)}
        </div>
      </div>
    </div>}
  </>;
}
