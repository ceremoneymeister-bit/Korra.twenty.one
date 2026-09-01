import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent as ReactDragEvent,
} from "react";
import {
  ArrowUp,
  Download,
  Eye,
  FileIcon,
  Folder,
  FolderOpen,
  FolderPlus,
  Pencil,
  RefreshCw,
  Trash2,
  Upload,
} from "lucide-react";
import { Button } from "@/components/ProductButton";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@nous-research/ui/ui/components/dialog";
import { Input } from "@nous-research/ui/ui/components/input";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";
import { FilePreviewDialog, type PreviewFile } from "@/components/FilePreviewDialog";
import { usePageHeader } from "@/contexts/usePageHeader";
import { api } from "@/lib/api";
import type { ManagedFileEntry, ManagedFilesResponse, OwnerTrashEntry } from "@/lib/api";
import {
  getOwnerTimeZone,
  isProductUiMode,
  productUiMode,
} from "@/lib/dashboard-flags";
import { productNavLabel } from "@/lib/product-nav";
import { artifactUrl } from "@/lib/chat-artifacts";
import { ownerFacingError } from "@/lib/owner-facing-error";
import { PluginSlot } from "@/plugins";

function joinPath(base: string, name: string): string {
  const cleanName = name.trim().replace(/^[\\/]+/, "");
  if (!cleanName) return base;
  const separator = base.includes("\\") && !base.includes("/") ? "\\" : "/";
  if (!base || base.endsWith("/") || base.endsWith("\\")) return `${base}${cleanName}`;
  return `${base}${separator}${cleanName}`;
}

function formatBytes(size: number | null): string {
  if (size === null) return "-";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  if (size < 1024 * 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  return `${(size / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function filesRootLabel(): string {
  return productNavLabel(productUiMode(), "/files") ?? "Материалы";
}

function displayPath(path: string | null | undefined): string {
  return path?.trim() || filesRootLabel();
}

export function clientRelativeParts(path: string | null | undefined): string[] {
  const normalized = (path ?? "").trim().replaceAll("\\", "/").replace(/\/$/, "");
  const marker = normalized.lastIndexOf("/home/client");
  if (marker >= 0) {
    return normalized.slice(marker + "/home/client".length).split("/").filter(Boolean);
  }
  // Корень не опознан — показываем только последний сегмент. Отдать наружу
  // абсолютный путь хуже, чем показать меньше: канон прямо запрещает путь как
  // основной текст, а человеку он ничего не объясняет.
  const parts = normalized.split("/").filter(Boolean);
  return parts.length ? [parts[parts.length - 1]] : [];
}

function clientDisplayPath(path: string | null | undefined): string {
  const parts = clientRelativeParts(path).map(clientEntryLabel);
  return [filesRootLabel(), ...parts].join(" / ");
}

function transferHasFiles(event: ReactDragEvent<HTMLElement>): boolean {
  return Array.from(event.dataTransfer.types).includes("Files");
}

function clientEntryLabel(name: string): string {
  if (name === "artifacts") return "Готовые материалы";
  if (name === "inbox") return "Мои загрузки";
  return name;
}

/**
 * Корзина владельца.
 *
 * До этого файл уходил в `.trash` и исчезал из интерфейса навсегда: вернуть
 * его или удалить окончательно было нельзя ничем, кроме доступа к серверу.
 * «Удалить» без видимой корзины — это обещание, которое продукт не выполнял.
 */
function OwnerTrash({ onRestored }: { onRestored: () => void }) {
  const [entries, setEntries] = useState<OwnerTrashEntry[] | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingPurge, setPendingPurge] = useState<OwnerTrashEntry | null>(null);

  const reload = useCallback(() => {
    api.listOwnerTrash()
      .then((payload) => setEntries(payload.entries))
      .catch((exception) => setError(ownerFacingError(exception, "Не удалось открыть корзину.")));
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const act = async (entry: OwnerTrashEntry, action: "restore" | "purge") => {
    setBusy(entry.trash_id);
    setError(null);
    try {
      if (action === "restore") {
        await api.restoreOwnerTrash(entry.trash_id);
        onRestored();
      } else {
        await api.purgeOwnerTrash(entry.trash_id);
      }
      reload();
    } catch (exception) {
      setError(
        ownerFacingError(
          exception,
          action === "restore" ? "Не удалось вернуть файл." : "Не удалось удалить файл.",
        ),
      );
    } finally {
      setBusy(null);
      setPendingPurge(null);
    }
  };

  if (!entries || entries.length === 0) return null;

  return (
    <Card className="min-w-0 max-w-full overflow-hidden rounded-xl">
      <CardContent className="p-0">
        <button
          type="button"
          className="flex min-h-14 w-full items-center gap-3 px-4 py-3 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-primary"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
        >
          <Trash2 className="size-4 text-muted-foreground" aria-hidden />
          <span className="flex-1 text-sm font-medium">Корзина</span>
          <span className="text-xs text-muted-foreground">
            {entries.length === 1 ? "1 файл" : `${entries.length} файла(ов)`}
          </span>
        </button>
        {open ? (
          <div className="border-t border-border">
            {error ? (
              <p className="px-4 py-3 text-sm text-destructive" role="alert">{error}</p>
            ) : null}
            <p className="px-4 pt-3 text-xs text-muted-foreground">
              Отсюда файл можно вернуть в «Мои загрузки» или удалить окончательно.
            </p>
            {entries.map((entry) => (
              <div key={entry.trash_id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <span className="min-w-0 flex-1 truncate text-sm">{entry.name}</span>
                <Button
                  type="button"
                  size="xs"
                  outlined
                  disabled={busy !== null}
                  onClick={() => void act(entry, "restore")}
                >
                  Вернуть
                </Button>
                <Button
                  type="button"
                  size="xs"
                  ghost
                  destructive
                  disabled={busy !== null}
                  onClick={() => setPendingPurge(entry)}
                >
                  Удалить навсегда
                </Button>
              </div>
            ))}
          </div>
        ) : null}
      </CardContent>
      <DeleteConfirmDialog
        open={Boolean(pendingPurge)}
        loading={busy !== null}
        onCancel={() => setPendingPurge(null)}
        title="Удалить файл навсегда?"
        description={`«${pendingPurge?.name ?? ""}» будет удалён без возможности восстановления.`}
        confirmLabel="Удалить навсегда"
        onConfirm={() => { if (pendingPurge) void act(pendingPurge, "purge"); }}
      />
    </Card>
  );
}

export default function FilesPage() {
  const clientMode = isProductUiMode();
  const { toast, showToast } = useToast();
  const { setAfterTitle, setEnd } = usePageHeader();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const dragDepthRef = useRef(0);
  const [currentPath, setCurrentPath] = useState<string | undefined>(() =>
    clientMode ? "client" : undefined,
  );
  const [pathInput, setPathInput] = useState("");
  const [listing, setListing] = useState<ManagedFilesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [draggingFiles, setDraggingFiles] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [folderName, setFolderName] = useState("");
  const [pendingDelete, setPendingDelete] = useState<ManagedFileEntry | null>(null);
  const [renameEntry, setRenameEntry] = useState<ManagedFileEntry | null>(null);
  const [renameName, setRenameName] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [previewFile, setPreviewFile] = useState<PreviewFile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const dateFormat = useMemo(() => new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: getOwnerTimeZone(),
  }), []);

  const activePath = listing?.path ?? currentPath ?? "";
  const canChangePath = listing?.can_change_path ?? false;
  const rawHeaderPath = displayPath(listing?.locked_root ?? listing?.path ?? currentPath);
  const headerPath = clientMode ? clientDisplayPath(activePath) : rawHeaderPath;
  const normalizedActivePath = activePath.replaceAll("\\", "/").replace(/\/$/, "");
  const isAtClientRoot =
    clientMode && /(?:^|\/)home\/client$/.test(normalizedActivePath);
  const isInClientInbox =
    clientMode && /(?:^|\/)home\/client\/inbox(?:\/|$)/.test(normalizedActivePath);
  const canUpload =
    Boolean(activePath) && !uploading && (!clientMode || isInClientInbox);
  const visibleEntries = listing?.entries.filter((entry) => {
    if (!clientMode) return true;
    if (entry.name.startsWith(".") || entry.name.endsWith(".meta.json")) return false;
    return !isAtClientRoot || ["artifacts", "inbox"].includes(entry.name);
  });

  const load = useCallback(
    async (path = currentPath) => {
      setLoading(true);
      setError(null);
      try {
        const result = await api.listFiles(path);
        setListing(result);
        setCurrentPath(result.path);
        setPathInput(result.path);
      } catch (e) {
        setError(ownerFacingError(e, "Не удалось загрузить список материалов."));
      } finally {
        setLoading(false);
      }
    },
    [currentPath],
  );

  useEffect(() => {
    // Existing dashboard data pages fetch from effects; keep this local and explicit
    // until the shared lint profile is updated for async page loaders.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load(currentPath);
  }, [currentPath]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    // В корне путь совпадает с названием экрана, и бейдж рядом с заголовком
    // повторял «Файлы» вторым словом — шапка сообщала одно и то же дважды.
    // Показываем путь только когда человек ушёл вглубь.
    const atRoot = clientMode && headerPath === filesRootLabel();
    setAfterTitle(
      atRoot ? null : (
        <span
          className="max-w-[22rem] truncate rounded-lg border border-border px-2.5 py-1 text-sm text-text-secondary"
          title={headerPath}
        >
          {headerPath}
        </span>
      ),
    );
    setEnd(
      <div className="flex items-center gap-2">
        <Button
          ghost
          size="icon"
          type="button"
          onClick={() => void load()}
          disabled={loading}
          aria-label="Обновить материалы"
        >
          {loading ? <Spinner /> : <RefreshCw />}
        </Button>
      </div>,
    );
    return () => {
      setAfterTitle(null);
      setEnd(null);
    };
  }, [clientMode, headerPath, load, loading, setAfterTitle, setEnd]);

  const openDirectory = (entry: ManagedFileEntry) => {
    if (entry.is_directory) {
      setCurrentPath(entry.path);
    }
  };

  const goToPath = async () => {
    const nextPath = pathInput.trim();
    if (!nextPath) {
      showToast("Укажите путь", "error");
      return;
    }
    await load(nextPath);
  };

  const createDirectory = async () => {
    const name = folderName.trim();
    if (!activePath) {
      showToast("Папка недоступна", "error");
      return;
    }
    if (!name) {
      showToast("Укажите название папки", "error");
      return;
    }
    setCreating(true);
    try {
      await api.createDirectory(joinPath(activePath, name));
      setFolderName("");
      setCreateDialogOpen(false);
      showToast("Папка создана", "success");
      await load();
    } catch (e) {
      showToast(ownerFacingError(e, "Не удалось создать папку."), "error");
    } finally {
      setCreating(false);
    }
  };

  const uploadFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    const succeeded: string[] = [];
    const failed: string[] = [];
    for (const file of Array.from(files)) {
      try {
        await api.uploadFile(joinPath(activePath, file.name), file, !clientMode);
        succeeded.push(file.name);
      } catch {
        failed.push(file.name);
      }
    }
    try {
      if (succeeded.length > 0) {
        showToast(`Загружено файлов: ${succeeded.length}`, "success");
      }
      if (failed.length > 0) {
        showToast(
          `Не загружено: ${failed.join(", ")}. Проверьте имена или совпадения.`,
          "error",
        );
      }
      await load();
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleDragEnter = (event: ReactDragEvent<HTMLElement>) => {
    if (!canUpload || !transferHasFiles(event)) return;
    event.preventDefault();
    dragDepthRef.current += 1;
    setDraggingFiles(true);
  };

  const handleDragOver = (event: ReactDragEvent<HTMLElement>) => {
    if (!canUpload || !transferHasFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  };

  const handleDragLeave = (event: ReactDragEvent<HTMLElement>) => {
    if (!canUpload || !transferHasFiles(event)) return;
    event.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) {
      setDraggingFiles(false);
    }
  };

  const handleDrop = (event: ReactDragEvent<HTMLElement>) => {
    if (!canUpload) return;
    event.preventDefault();
    dragDepthRef.current = 0;
    setDraggingFiles(false);
    void uploadFiles(event.dataTransfer.files);
  };

  const downloadFile = (entry: ManagedFileEntry) => {
    if (entry.is_directory) return;
    const link = document.createElement("a");
    link.href = artifactUrl(entry.path, false);
    link.download = entry.name || "download";
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

  const previewEntry = (entry: ManagedFileEntry) => {
    if (entry.is_directory) return;
    setPreviewFile({
      name: clientEntryLabel(entry.name),
      path: entry.path,
      mimeType: entry.mime_type,
      expectedSha256: entry.revision,
    });
  };

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      if (clientMode) {
        if (!pendingDelete.revision) throw new Error("Обновите список и повторите.");
        await api.trashOwnerFile(pendingDelete.path, pendingDelete.revision);
        showToast("Файл перемещён в корзину", "success");
      } else {
        await api.deleteFile(pendingDelete.path, pendingDelete.is_directory);
        showToast("Удалено", "success");
      }
      setPendingDelete(null);
      await load();
    } catch (e) {
      showToast(ownerFacingError(e, "Не удалось удалить файл."), "error");
    } finally {
      setDeleting(false);
    }
  };

  const confirmRename = async () => {
    if (!renameEntry?.revision) return;
    const nextName = renameName.trim();
    if (!nextName) {
      showToast("Укажите новое имя файла", "error");
      return;
    }
    setRenaming(true);
    try {
      await api.renameOwnerFile(renameEntry.path, nextName, renameEntry.revision);
      showToast("Файл переименован", "success");
      setRenameEntry(null);
      setRenameName("");
      await load();
    } catch (e) {
      showToast(ownerFacingError(e, "Не удалось переименовать файл."), "error");
    } finally {
      setRenaming(false);
    }
  };

  return (
    <div className="flex min-w-0 max-w-full flex-col gap-4">
      <Toast toast={toast} />
      <PluginSlot name="files:top" />
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(event) => void uploadFiles(event.currentTarget.files)}
      />

      <div className="flex min-w-0 flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
        {canChangePath ? (
          <form
            className="flex min-w-0 flex-1 items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void goToPath();
            }}
          >
            <Input
              value={pathInput}
              onChange={(event) => setPathInput(event.target.value)}
              aria-label="Путь"
              placeholder="Путь"
              className="h-9 min-w-0 flex-1 font-mono"
            />
            <Button type="submit" size="sm" outlined>
              Перейти
            </Button>
          </form>
        ) : (
          <div className="min-w-0 truncate text-sm text-text-secondary" title={headerPath}>
            {headerPath}
          </div>
        )}
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {(!clientMode || isInClientInbox) && (
            <Button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={!canUpload}
              size="sm"
              outlined
              prefix={uploading ? <Spinner /> : <Upload />}
            >
              Загрузить
            </Button>
          )}
          {!clientMode && (
            <Button
              type="button"
              onClick={() => setCreateDialogOpen(true)}
              disabled={!activePath}
              size="sm"
              outlined
              prefix={<FolderPlus />}
            >
              Создать папку
            </Button>
          )}
        </div>
      </div>

      {(!clientMode || isInClientInbox) && (
        <button
          type="button"
          onClick={() => canUpload && fileInputRef.current?.click()}
          onDragEnter={handleDragEnter}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          disabled={!canUpload}
          aria-label="Загрузить файлы"
          className={`flex min-h-24 w-full min-w-0 items-center justify-between gap-4 rounded-xl border border-dashed px-4 py-4 text-left transition-colors ${
            draggingFiles
              ? "border-primary bg-primary/10 text-foreground"
              : "border-border bg-background/20 text-text-secondary hover:border-text-tertiary hover:bg-background/35"
          } disabled:cursor-not-allowed disabled:opacity-60`}
        >
          <span className="flex min-w-0 items-center gap-3">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-lg border border-border bg-background/45 text-text-tertiary">
              {uploading ? <Spinner /> : <Upload className="h-4 w-4" />}
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-semibold text-foreground">
                {uploading ? "Загрузка" : draggingFiles ? "Отпустите файлы" : "Перетащите файлы сюда"}
              </span>
              <span className="block truncate text-xs text-text-secondary" title={clientDisplayPath(activePath)}>
                {clientDisplayPath(activePath)}
              </span>
            </span>
          </span>
          <span className="hidden shrink-0 text-xs font-semibold text-text-tertiary sm:block">
            Выбрать файлы
          </span>
        </button>
      )}

      {clientMode && isAtClientRoot && (
        <p className="text-sm leading-6 text-muted-foreground">
          В «Готовых материалах» лежат результаты Корры. В «Моих загрузках» можно
          добавить исходники для работы.
        </p>
      )}

      {clientMode && isInClientInbox ? <OwnerTrash onRestored={() => void load()} /> : null}

      <Card className="min-w-0 max-w-full overflow-hidden rounded-xl">
        <CardContent className="p-0">
          {error && (
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-destructive/20 bg-destructive/10 p-3 text-sm text-destructive" role="alert">
              <span>{error}</span>
              <Button type="button" size="xs" outlined onClick={() => void load()}>
                Повторить
              </Button>
            </div>
          )}

          <div className="hidden grid-cols-[minmax(12rem,1fr)_7rem_10rem_8rem] items-center gap-3 border-b border-border px-4 py-3 text-xs font-semibold text-text-tertiary sm:grid">
            <span>Название</span>
            <span>Размер</span>
            <span>Изменено</span>
            <span className="text-right">Действия</span>
          </div>

          {listing?.parent && !isAtClientRoot && (
            <button
              type="button"
              onClick={() => setCurrentPath(listing.parent ?? undefined)}
              className="grid min-h-12 w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-border/60 px-4 py-3 text-left text-sm transition-colors hover:bg-background/40 sm:grid-cols-[minmax(12rem,1fr)_7rem_10rem_8rem]"
            >
              <span className="flex min-w-0 items-center gap-2 font-mono text-text-secondary">
                <ArrowUp className="h-4 w-4 shrink-0 text-text-tertiary" />
                ..
              </span>
              <span className="hidden sm:block" />
              <span className="hidden sm:block" />
              <span className="hidden sm:block" />
            </button>
          )}

          {loading && !listing ? (
            <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
              <Spinner />
              Загрузка файлов...
            </div>
          ) : !error && listing && visibleEntries?.length === 0 ? (
            <div className="px-5 py-12 text-center text-sm text-muted-foreground">
              {isInClientInbox
                ? "Здесь появятся ваши исходники. Нажмите «Загрузить» или перетащите файлы в область выше."
                : "Здесь появятся готовые материалы после следующей сборки Корры."}
            </div>
          ) : (
            visibleEntries?.map((entry) => (
              <div
                key={entry.path}
                className="grid min-h-16 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-border/60 px-4 py-3 text-sm last:border-b-0 hover:bg-background/35 sm:grid-cols-[minmax(12rem,1fr)_7rem_10rem_8rem]"
              >
                <button
                  type="button"
                  onClick={() => (entry.is_directory ? openDirectory(entry) : previewEntry(entry))}
                  className="flex min-w-0 items-center gap-3 text-left text-foreground"
                >
                  {entry.is_directory ? (
                    <Folder className="h-4 w-4 shrink-0 text-warning" />
                  ) : (
                    <FileIcon className="h-4 w-4 shrink-0 text-text-tertiary" />
                  )}
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{clientEntryLabel(entry.name)}</span>
                    <span className="mt-0.5 block truncate text-xs text-muted-foreground sm:hidden">
                      {formatBytes(entry.size)} · {Number.isFinite(entry.mtime) ? dateFormat.format(entry.mtime * 1000) : "Дата неизвестна"}
                    </span>
                  </span>
                </button>
                <span className="hidden text-xs tabular-nums text-text-secondary sm:block">{formatBytes(entry.size)}</span>
                <span className="hidden truncate text-xs text-text-secondary sm:block">
                  {Number.isFinite(entry.mtime) ? dateFormat.format(entry.mtime * 1000) : "-"}
                </span>
                <span className="flex justify-end gap-1">
                  {entry.is_directory ? (
                    <Button
                      ghost
                      size="icon"
                      type="button"
                      onClick={() => openDirectory(entry)}
                      aria-label={`Открыть ${clientEntryLabel(entry.name)}`}
                    >
                      <FolderOpen />
                    </Button>
                  ) : (
                    <>
                      <Button
                        ghost
                        size="icon"
                        type="button"
                        onClick={() => previewEntry(entry)}
                        aria-label={`Просмотреть ${clientEntryLabel(entry.name)}`}
                      >
                        <Eye />
                      </Button>
                      <Button
                        ghost
                        size="icon"
                        type="button"
                        onClick={() => downloadFile(entry)}
                        aria-label={`Скачать ${clientEntryLabel(entry.name)}`}
                      >
                        <Download />
                      </Button>
                      {clientMode && entry.capabilities?.rename && entry.revision && (
                        <Button
                          ghost
                          size="icon"
                          type="button"
                          onClick={() => {
                            setRenameEntry(entry);
                            setRenameName(entry.name);
                          }}
                          aria-label={`Переименовать ${entry.name}`}
                        >
                          <Pencil />
                        </Button>
                      )}
                    </>
                  )}
                  {(!clientMode || (entry.capabilities?.trash && entry.revision)) && (
                    <Button
                      ghost
                      size="icon"
                      type="button"
                      onClick={() => setPendingDelete(entry)}
                      aria-label={
                        clientMode
                          ? `Переместить ${entry.name} в корзину`
                          : `Удалить ${entry.name}`
                      }
                      className="text-destructive hover:text-destructive"
                    >
                      <Trash2 />
                    </Button>
                  )}
                </span>
              </div>
            ))
          )}
        </CardContent>
      </Card>

      <PluginSlot name="files:bottom" />

      <Dialog
        open={createDialogOpen}
        onOpenChange={(open) => {
          if (creating) return;
          setCreateDialogOpen(open);
          if (!open) setFolderName("");
        }}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Создать папку</DialogTitle>
            <DialogDescription>
              Путь: {activePath || "Загрузка"}
            </DialogDescription>
          </DialogHeader>
          <div className="p-4">
            <Input
              autoFocus
              value={folderName}
              onChange={(event) => setFolderName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void createDirectory();
              }}
              placeholder="Название папки"
              disabled={creating}
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              outlined
              onClick={() => {
                setCreateDialogOpen(false);
                setFolderName("");
              }}
              disabled={creating}
            >
              Отмена
            </Button>
            <Button
              type="button"
              onClick={() => void createDirectory()}
              disabled={creating}
              prefix={creating ? <Spinner /> : <FolderPlus />}
            >
              Создать
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <DeleteConfirmDialog
        open={Boolean(pendingDelete)}
        loading={deleting}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => void confirmDelete()}
        title={
          clientMode
            ? pendingDelete
              ? `Убрать ${pendingDelete.name}?`
              : "Убрать файл?"
            : pendingDelete
              ? `Удалить ${pendingDelete.name}?`
              : "Удалить объект?"
        }
        description={
          clientMode
            ? "Файл пропадёт из «Моих загрузок», но не удалится: он останется в корзине, и его можно вернуть."
            : pendingDelete?.is_directory
            ? "Папка и всё её содержимое будут удалены."
            : "Файл будет удалён."
        }
        confirmLabel={clientMode ? "Переместить в корзину" : undefined}
      />

      <Dialog
        open={Boolean(renameEntry)}
        onOpenChange={(open) => {
          if (renaming || open) return;
          setRenameEntry(null);
          setRenameName("");
        }}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Переименовать файл</DialogTitle>
            <DialogDescription>
              Изменится только имя файла в «Моих загрузках».
            </DialogDescription>
          </DialogHeader>
          <div className="p-4">
            <label htmlFor="rename-owner-file" className="sr-only">
              Новое имя файла
            </label>
            <Input
              id="rename-owner-file"
              autoFocus
              value={renameName}
              onChange={(event) => setRenameName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void confirmRename();
              }}
              disabled={renaming}
            />
          </div>
          <DialogFooter>
            <Button
              outlined
              onClick={() => {
                setRenameEntry(null);
                setRenameName("");
              }}
              disabled={renaming}
            >
              Отмена
            </Button>
            <Button
              onClick={() => void confirmRename()}
              disabled={renaming || !renameName.trim()}
              prefix={renaming ? <Spinner /> : <Pencil />}
            >
              Переименовать
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <FilePreviewDialog
        file={previewFile}
        open={Boolean(previewFile)}
        onOpenChange={(open) => {
          if (!open) setPreviewFile(null);
        }}
        onSaved={(saved) => {
          setPreviewFile((current) => current ? {
            ...current,
            expectedSha256: saved.sha256,
          } : current);
          void load();
        }}
      />
    </div>
  );
}
