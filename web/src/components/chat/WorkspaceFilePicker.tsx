import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { ChevronRight, FileIcon, Folder, FolderOpen, MessageSquare } from "lucide-react";
import { api, type ManagedFilesResponse } from "@/lib/api";
import { describeAttachment, formatSize, MAX_ATTACHMENTS, type UploadedAttachment } from "@/lib/chat-attachments";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@nous-research/ui/ui/components/dialog";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Button } from "@/components/ProductButton";
import { buildFileBreadcrumbs, buildWorkspaceBreadcrumbs, chatUploadsPath, workspaceEntryLabel, workspaceEntryTarget, workspaceParentPath } from "@/lib/file-manager";
import { productUiMode } from "@/lib/dashboard-flags";
import { ownerFacingError } from "@/lib/owner-facing-error";
import { $uploadJobs } from "@/store/upload-jobs";
import { useStore } from "@nanostores/react";

/**
 * Выбор файла из рабочей папки для вложения в сообщение.
 *
 * Один штатный «Закрыть» от общего Dialog, штатные отступы header/body, видна
 * текущая папка, различимы загрузка / пустота / ошибка с повтором. В fleet
 * поверх физических `client/inbox` и дат показываются «Мои файлы» и
 * «Загрузки из чатов» — те же имена, что на странице «Файлы».
 */
export function WorkspaceFilePicker({ disabled, onPick, remaining = MAX_ATTACHMENTS, profile }: {
  disabled: boolean;
  remaining?: number;
  profile?: string;
  onPick: (file: UploadedAttachment) => void;
}) {
  const [open, setOpen] = useState(false);
  const [path, setPath] = useState<string>();
  const [listing, setListing] = useState<ManagedFilesResponse>();
  const [listError, setListError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [params, setParams] = useSearchParams();
  const jobs = useStore($uploadJobs);
  const fleetMode = productUiMode() === "fleet";
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
    setListError("");
    void api.listFiles(path).then(value => { if (!cancelled) setListing(value); })
      .catch((cause: unknown) => { if (!cancelled) setListError(ownerFacingError(cause, "Не удалось открыть папку.")); });
    return () => { cancelled = true; };
  }, [open, path, attempt]);

  const root = listing?.locked_root ?? listing?.root;
  const currentPath = listing?.path ?? path;
  const breadcrumbs = listing
    ? (fleetMode ? buildWorkspaceBreadcrumbs(root, listing.path) : buildFileBreadcrumbs(root, listing.path))
    : [];
  const label = useCallback(
    (entryPath: string, name: string) => (fleetMode ? workspaceEntryLabel(root, entryPath, name) : name),
    [fleetMode, root],
  );
  const parent = listing ? (fleetMode ? workspaceParentPath(root, listing.path, listing.parent) : listing.parent) : null;
  const atRoot = Boolean(listing && root && listing.path === root);
  const uploadsPath = fleetMode && root ? chatUploadsPath(root) : null;
  const showUploadsShortcut = Boolean(uploadsPath && atRoot && !listing?.entries.some(entry => workspaceEntryTarget(root, entry.path) === uploadsPath));

  const pick = (target: string, failure: string) => {
    setError("");
    setBusy(true);
    void describeAttachment(target).then(file => { onPick(file); setOpen(false); })
      .catch(() => setError(failure))
      .finally(() => setBusy(false));
  };
  const openDialog = (next: boolean) => {
    setOpen(next);
    if (!next) setError("");
  };

  return <>
    <button type="button" disabled={disabled} onClick={() => setOpen(true)}
      className="korra-chat-composer__control" aria-label="Выбрать из Файлов" title="Выбрать из Файлов">
      <FolderOpen size={20} aria-hidden />
    </button>
    <Dialog open={open} onOpenChange={openDialog}>
      <DialogContent className="flex max-h-[85vh] w-[calc(100vw-1.5rem)] max-w-lg flex-col overflow-hidden" aria-label="Выбрать из Файлов">
        <DialogHeader className="pr-12">
          <DialogTitle>Файлы рабочей папки</DialogTitle>
          <DialogDescription>Файл или папка прикрепится к сообщению без повторной загрузки.</DialogDescription>
        </DialogHeader>
        <div className="flex min-h-0 flex-1 flex-col gap-3 px-5 pb-5 pt-4">
          {Object.values(jobs).some(job => job.result) && <details className="text-sm"><summary className="cursor-pointer py-1">Недавние загрузки</summary>
            {Object.values(jobs).filter(job => job.result).slice(-8).flatMap<{ path: string; name: string }>(job => job.result!.folder ? [job.result!.folder] : job.result!.files).map(file =>
              <button key={file.path} type="button" className="block max-w-full truncate rounded-lg px-2 py-2 text-left hover:shadow-[var(--neo-inset-compact)] disabled:opacity-60" disabled={busy}
                onClick={() => pick(file.path, "Вложение недоступно. Выберите его в рабочей папке.")}>{file.name}</button>)}
          </details>}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <nav aria-label="Текущая папка" className="flex min-w-0 flex-wrap items-center gap-1 text-xs text-muted-foreground">
            {breadcrumbs.length === 0 ? <span>{currentPath ? currentPath : "Рабочая папка"}</span> : breadcrumbs.map((crumb, index) => (
              <span key={crumb.path} className="flex items-center gap-1">
                {index > 0 && <ChevronRight size={12} aria-hidden />}
                <button type="button" disabled={busy || index === breadcrumbs.length - 1}
                  className="max-w-[12rem] truncate rounded px-1 py-1 hover:text-foreground disabled:text-foreground"
                  onClick={() => setPath(crumb.path)}>{crumb.label}</button>
              </span>
            ))}
          </nav>
          <div className="flex flex-wrap items-center gap-2">
            {parent && <Button type="button" size="xs" outlined disabled={busy} onClick={() => setPath(parent)}>← На уровень выше</Button>}
            {showUploadsShortcut && <Button type="button" size="xs" outlined disabled={busy} prefix={<MessageSquare />} onClick={() => setPath(uploadsPath!)}>Загрузки из чатов</Button>}
            {listing && !atRoot && <Button type="button" size="xs" ghost disabled={busy} prefix={<FolderOpen />}
              onClick={() => pick(listing.path, "Эту папку нельзя прикрепить.")}>Прикрепить эту папку</Button>}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto rounded-xl" role="list" aria-label="Содержимое папки" aria-busy={!listing && !listError}>
            {listError ? (
              <div className="flex flex-col items-start gap-2 px-2 py-6 text-sm" role="alert">
                <span>{listError}</span>
                <Button type="button" size="xs" outlined onClick={() => setAttempt(value => value + 1)}>Повторить</Button>
              </div>
            ) : !listing ? (
              <p role="status" className="flex items-center gap-2 px-2 py-6 text-sm text-muted-foreground"><Spinner /> Открываем папку…</p>
            ) : listing.entries.length === 0 ? (
              <div className="px-2 py-6 text-sm text-muted-foreground" role="status">
                <p>Папка пуста.</p>
                <p className="mt-1">Загрузите материалы на странице <Link to="/files" className="underline" onClick={() => setOpen(false)}>«Файлы»</Link> или прикрепите файл с устройства кнопкой «+».</p>
              </div>
            ) : listing.entries.map(entry => (
              <button key={entry.path} type="button" role="listitem" disabled={busy}
                className="flex min-h-[44px] w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-sm hover:shadow-[var(--neo-inset-compact)] focus-visible:shadow-[var(--neo-focus-visible)] disabled:opacity-60"
                onClick={() => {
                  setError("");
                  if (entry.is_directory) { setPath(fleetMode ? workspaceEntryTarget(root, entry.path) : entry.path); return; }
                  pick(entry.path, "Файл недоступен или больше 2 ГБ. Выберите другой.");
                }}>
                {entry.is_directory ? <Folder size={16} className="shrink-0 text-warning" aria-hidden /> : <FileIcon size={16} className="shrink-0 text-muted-foreground" aria-hidden />}
                <span className="min-w-0 flex-1 truncate">{label(entry.path, entry.name)}</span>
                {!entry.is_directory && <span className="shrink-0 text-xs text-muted-foreground">{entry.size === null ? "" : formatSize(entry.size)}</span>}
              </button>
            ))}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  </>;
}
