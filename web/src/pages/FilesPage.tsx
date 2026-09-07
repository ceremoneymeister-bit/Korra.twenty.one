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
  CheckCircle2,
  ChevronRight,
  Copy,
  Download,
  Eye,
  FileIcon,
  Folder,
  FolderOpen,
  FolderPlus,
  Pencil,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  X,
  XCircle,
} from "lucide-react";
import { useSearchParams } from "react-router";
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
import type { ManagedFileEntry, ManagedFilesResponse, ManagedTrashEntry } from "@/lib/api";
import {
  getOwnerTimeZone,
  isClientUiMode,
  productUiMode,
} from "@/lib/dashboard-flags";
import { productNavLabel } from "@/lib/product-nav";
import { artifactUrl } from "@/lib/chat-artifacts";
import { ownerFacingError } from "@/lib/owner-facing-error";
import {
  availableCopyName,
  buildFileBreadcrumbs,
  filterAndSortFileEntries,
  type FileSortMode,
} from "@/lib/file-manager";
import { PluginSlot } from "@/plugins";

type UploadStatus = "waiting" | "uploading" | "choice" | "done" | "failed" | "cancelled";
const TRASH_PAGE_SIZE = 50;

interface UploadItem {
  id: string;
  file: File;
  targetDirectory: string;
  existingNames: string[];
  existingRevision?: string;
  status: UploadStatus;
  uploadedName?: string;
  message?: string;
}

function joinPath(base: string, name: string): string {
  const cleanName = name.trim().replace(/^[\\/]+/, "");
  if (!cleanName) return base;
  const separator = base.includes("\\") && !base.includes("/") ? "\\" : "/";
  if (!base || base.endsWith("/") || base.endsWith("\\")) return `${base}${cleanName}`;
  return `${base}${separator}${cleanName}`;
}

function formatBytes(size: number | null): string {
  if (size === null) return "-";
  if (size < 1024) return `${size} Б`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} КБ`;
  if (size < 1024 * 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} МБ`;
  return `${(size / (1024 * 1024 * 1024)).toFixed(1)} ГБ`;
}

function filesRootLabel(): string {
  return productNavLabel(productUiMode(), "/files") ?? "Файлы";
}

function displayPath(path: string | null | undefined): string {
  return path?.trim() || filesRootLabel();
}

export function clientRelativeParts(
  path: string | null | undefined,
  root?: string | null,
): string[] {
  const normalized = (path ?? "").trim().replaceAll("\\", "/").replace(/\/$/, "");
  const normalizedRoot = (root ?? "").trim().replaceAll("\\", "/").replace(/\/$/, "");
  if (normalizedRoot && normalized === normalizedRoot) return [];
  if (normalizedRoot && normalized.startsWith(`${normalizedRoot}/`)) {
    return normalized.slice(normalizedRoot.length + 1).split("/").filter(Boolean);
  }
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

function clientDisplayPath(path: string | null | undefined, root?: string | null): string {
  const parts = clientRelativeParts(path, root).map(clientEntryLabel);
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
 * Корзина файлового менеджера.
 *
 * До этого файл уходил в `.trash` и исчезал из интерфейса навсегда: вернуть
 * его или удалить окончательно было нельзя ничем, кроме доступа к серверу.
 * «Удалить» без видимой корзины — это обещание, которое продукт не выполнял.
 */
export function FileTrash({
  onRestored,
  refreshVersion,
}: {
  onRestored: () => void;
  refreshVersion: number;
}) {
  const [entries, setEntries] = useState<ManagedTrashEntry[] | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [pendingPurge, setPendingPurge] = useState<ManagedTrashEntry | null>(null);

  const reload = useCallback(async () => {
    try {
      const payload = await api.listTrash(0, TRASH_PAGE_SIZE);
      setEntries(payload.entries);
      setTotal(payload.total);
      setError(null);
    } catch (exception) {
      setError(ownerFacingError(exception, "Не удалось открыть корзину."));
    }
  }, []);

  const loadMore = async () => {
    if (!entries || entries.length >= total || loadingMore) return;
    setLoadingMore(true);
    setError(null);
    try {
      const payload = await api.listTrash(entries.length, TRASH_PAGE_SIZE);
      setEntries((current) => [...(current ?? []), ...payload.entries]);
      setTotal(payload.total);
    } catch (exception) {
      setError(ownerFacingError(exception, "Не удалось загрузить остальные файлы."));
    } finally {
      setLoadingMore(false);
    }
  };

  useEffect(() => {
    void reload();
  }, [reload, refreshVersion]);

  const act = async (entry: ManagedTrashEntry, action: "restore" | "purge") => {
    setBusy(entry.trash_id);
    setError(null);
    try {
      if (action === "restore") {
        await api.restoreTrash(entry.trash_id);
        onRestored();
      } else {
        await api.purgeTrash(entry.trash_id);
      }
      await reload();
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

  if ((!entries || entries.length === 0) && !error) return null;
  const trashCount = total;

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
            {trashCount === 0 ? "Нет данных" : trashCount === 1 ? "1 объект" : `${trashCount} объектов`}
          </span>
        </button>
        {open ? (
          <div className="border-t border-border">
            {error ? (
              <p className="px-4 py-3 text-sm text-destructive" role="alert">{error}</p>
            ) : null}
            <p className="px-4 pt-3 text-xs text-muted-foreground">
              Объекты можно вернуть на прежнее место или удалить окончательно.
            </p>
            {(entries ?? []).map((entry) => (
              <div key={entry.trash_id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm">{entry.name}</span>
                  <span className="block truncate text-xs text-muted-foreground">{entry.original_path}</span>
                </span>
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
            {(entries?.length ?? 0) < total ? (
              <div className="border-t border-border/60 px-4 py-3 text-center">
                <Button
                  type="button"
                  size="xs"
                  outlined
                  disabled={loadingMore}
                  onClick={() => void loadMore()}
                  prefix={loadingMore ? <Spinner /> : undefined}
                >
                  Показать ещё
                </Button>
              </div>
            ) : null}
          </div>
        ) : null}
      </CardContent>
      <DeleteConfirmDialog
        open={Boolean(pendingPurge)}
        loading={busy !== null}
        onCancel={() => setPendingPurge(null)}
        title="Удалить навсегда?"
        description={`«${pendingPurge?.name ?? ""}» будет удалён без возможности восстановления.`}
        confirmLabel="Удалить навсегда"
        onConfirm={() => { if (pendingPurge) void act(pendingPurge, "purge"); }}
      />
    </Card>
  );
}

export default function FilesPage() {
  // Calculator files use the same inbox/artifacts boundary as owner cabinets.
  // Fleet keeps its full workspace manager.
  const clientMode = isClientUiMode() || productUiMode() === "calc";
  const { toast, showToast } = useToast();
  const { setAfterTitle, setEnd } = usePageHeader();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const dragDepthRef = useRef(0);
  const listRequestRef = useRef(0);
  // Открытая папка живёт в адресе (`/files?path=…`), а не только в состоянии:
  // «Назад» в браузере поднимает на уровень выше, ссылку на папку можно
  // отправить, а перезагрузка страницы возвращает туда же (QA 03.09).
  const [searchParams, setSearchParams] = useSearchParams();
  const searchParamsRef = useRef(searchParams);
  searchParamsRef.current = searchParams;
  const requestedPath =
    searchParams.get("path")?.trim() || (isClientUiMode() ? "client" : undefined);
  // Что реально показано на экране. Стартует пустым, поэтому первая загрузка
  // случается всегда, даже когда адрес уже содержит нужную папку.
  const currentPathRef = useRef<string | undefined>(undefined);
  const [pathInput, setPathInput] = useState("");
  const [listing, setListing] = useState<ManagedFilesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [resolvingCollision, setResolvingCollision] = useState(false);
  const [uploadItems, setUploadItems] = useState<UploadItem[]>([]);
  const [draggingFiles, setDraggingFiles] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [sortMode, setSortMode] = useState<FileSortMode>("name");
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
  const [trashRefreshVersion, setTrashRefreshVersion] = useState(0);
  const dateFormat = useMemo(() => new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: getOwnerTimeZone(),
  }), []);

  const activePath = listing?.path ?? requestedPath ?? "";
  const canChangePath = listing?.can_change_path ?? false;
  const managedRoot = listing?.locked_root ?? listing?.root;
  const breadcrumbs = useMemo(
    () => buildFileBreadcrumbs(managedRoot, activePath, filesRootLabel()),
    [activePath, managedRoot],
  );
  const rawHeaderPath = displayPath(listing?.locked_root ?? listing?.path ?? requestedPath);
  const headerPath = clientMode
    ? clientDisplayPath(activePath, managedRoot)
    : canChangePath
      ? rawHeaderPath
      : breadcrumbs.map((item) => item.label).join(" / ");
  const normalizedActivePath = activePath.replaceAll("\\", "/").replace(/\/$/, "");
  const normalizedClientRoot = (managedRoot ?? "").replaceAll("\\", "/").replace(/\/$/, "");
  const isAtClientRoot =
    clientMode && Boolean(normalizedClientRoot) && normalizedActivePath === normalizedClientRoot;
  const isInClientInbox =
    clientMode && Boolean(normalizedClientRoot) && (
      normalizedActivePath === `${normalizedClientRoot}/inbox` ||
      normalizedActivePath.startsWith(`${normalizedClientRoot}/inbox/`)
    );
  const currentCollision = uploadItems.find((item) => item.status === "choice") ?? null;
  const canUpload =
    Boolean(activePath) && !uploading && !currentCollision && (!clientMode || isInClientInbox);
  const baseEntries = useMemo(() => (listing?.entries ?? []).filter((entry) => {
    if (!clientMode) return true;
    if (entry.name.startsWith(".") || entry.name.endsWith(".meta.json")) return false;
    return !isAtClientRoot || ["artifacts", "inbox"].includes(entry.name);
  }), [clientMode, isAtClientRoot, listing?.entries]);
  const visibleEntries = useMemo(
    () => filterAndSortFileEntries(baseEntries, searchQuery, sortMode),
    [baseEntries, searchQuery, sortMode],
  );
  const uploadActive = uploadItems.some((item) =>
    ["waiting", "uploading", "choice"].includes(item.status),
  );

  /** Переход в папку — новая запись в истории: «Назад» вернёт на уровень выше. */
  const navigateTo = useCallback(
    (path: string | undefined) => {
      const next = new URLSearchParams(searchParamsRef.current);
      if (path) next.set("path", path);
      else next.delete("path");
      setSearchParams(next);
    },
    [setSearchParams],
  );

  /**
   * Сервер отвечает каноническим путём (корень контура, `client`, результат
   * подъёма на «..»), и адрес обязан показывать именно его — иначе ссылка из
   * адресной строки ведёт не туда, куда смотрит человек. Замена без новой
   * записи в истории: один переход — одна кнопка «Назад».
   */
  const syncPathParam = useCallback(
    (path: string) => {
      if ((searchParamsRef.current.get("path") ?? "").trim() === path) return;
      const next = new URLSearchParams(searchParamsRef.current);
      next.set("path", path);
      setSearchParams(next, { replace: true });
    },
    [setSearchParams],
  );

  const load = useCallback(
    async (path?: string) => {
      const target = path === undefined ? currentPathRef.current : path;
      const requestId = ++listRequestRef.current;
      setLoading(true);
      setError(null);
      try {
        const result = await api.listFiles(target);
        if (requestId !== listRequestRef.current) return;
        setListing(result);
        currentPathRef.current = result.path;
        setPathInput(result.path);
        syncPathParam(result.path);
      } catch (e) {
        if (requestId !== listRequestRef.current) return;
        setError(ownerFacingError(e, "Не удалось загрузить список файлов."));
      } finally {
        if (requestId === listRequestRef.current) setLoading(false);
      }
    },
    [syncPathParam],
  );

  useEffect(() => {
    // Existing dashboard data pages fetch from effects; keep this local and explicit
    // until the shared lint profile is updated for async page loaders.
    //
    // Эта папка уже на экране — второй запрос не нужен. Сюда мы приходим и
    // сразу после того, как `load` сам переписал адрес каноническим путём:
    // без проверки каждый переход стоил бы двух обращений к серверу.
    if (requestedPath !== undefined && requestedPath === currentPathRef.current) {
      return;
    }
    void load(requestedPath);
  }, [requestedPath, load]);

  // Два эффекта, а не один: раньше общий эффект зависел и от `loading`, и на
  // каждый запрос снимал ОБА слота шапки и ставил их заново — путь рядом с
  // заголовком мигал при любом обновлении списка. Теперь дёргается только та
  // часть шапки, которая действительно изменилась.
  useEffect(() => {
    // В корне путь совпадает с названием экрана, и бейдж рядом с заголовком
    // повторял «Файлы» вторым словом — шапка сообщала одно и то же дважды.
    // Показываем путь только когда человек ушёл вглубь.
    const atRoot = breadcrumbs.length === 1 && !canChangePath;
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
    return () => setAfterTitle(null);
  }, [breadcrumbs.length, canChangePath, headerPath, setAfterTitle]);

  useEffect(() => {
    setEnd(
      <div className="flex items-center gap-2">
        <Button
          ghost
          size="icon"
          type="button"
          onClick={() => void load()}
          disabled={loading}
          aria-label="Обновить файлы"
        >
          {loading ? <Spinner /> : <RefreshCw />}
        </Button>
      </div>,
    );
    return () => setEnd(null);
  }, [load, loading, setEnd]);

  const openDirectory = (entry: ManagedFileEntry) => {
    if (entry.is_directory) {
      navigateTo(entry.path);
    }
  };

  const goToPath = () => {
    const nextPath = pathInput.trim();
    if (!nextPath) {
      showToast("Укажите путь", "error");
      return;
    }
    // Через адрес, а не через `load` напрямую: ручной ввод пути — такой же
    // переход, как клик по папке, и «Назад» должен его отменять.
    navigateTo(nextPath);
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
    const uploadPath = activePath;
    const batch = Array.from(files).map((file, index): UploadItem => ({
      id: `${file.name}-${file.size}-${file.lastModified}-${index}`,
      file,
      targetDirectory: uploadPath,
      existingNames: baseEntries.map((entry) => entry.name),
      existingRevision: baseEntries.find((entry) => entry.name === file.name)?.revision ?? undefined,
      status: "waiting",
    }));
    setUploadItems(batch);
    setUploading(true);
    let succeeded = 0;
    for (const item of batch) {
      setUploadItems((current) => current.map((candidate) =>
        candidate.id === item.id ? { ...candidate, status: "uploading" } : candidate,
      ));
      try {
        await api.uploadFile(joinPath(uploadPath, item.file.name), item.file, false);
        succeeded += 1;
        setUploadItems((current) => current.map((candidate) =>
          candidate.id === item.id
            ? { ...candidate, status: "done", uploadedName: item.file.name }
            : candidate,
        ));
      } catch (exception) {
        const collision = exception instanceof Error && /^409:/.test(exception.message);
        let existingRevision = item.existingRevision;
        if (collision) {
          try {
            const freshListing = await api.listFiles(uploadPath);
            existingRevision = freshListing.entries.find(
              (entry) => entry.name === item.file.name,
            )?.revision ?? undefined;
          } catch {
            // The collision remains actionable as cancel/copy. Replacement is
            // refused server-side unless we have a fresh optimistic revision.
          }
        }
        setUploadItems((current) => current.map((candidate) =>
          candidate.id === item.id
            ? {
                ...candidate,
                status: collision ? "choice" : "failed",
                existingRevision,
                message: collision
                  ? "Файл с таким именем уже есть"
                  : ownerFacingError(exception, "Не удалось загрузить файл."),
              }
            : candidate,
        ));
      }
    }
    try {
      if (succeeded > 0) showToast(`Загружено файлов: ${succeeded}`, "success");
      await load();
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const resolveCollision = async (action: "replace" | "copy" | "cancel") => {
    if (!currentCollision || resolvingCollision) return;
    if (action === "cancel") {
      setUploadItems((current) => current.map((item) =>
        item.id === currentCollision.id
          ? { ...item, status: "cancelled", message: "Загрузка отменена" }
          : item,
      ));
      return;
    }
    setResolvingCollision(true);
    const knownNames = [
      ...currentCollision.existingNames,
      ...uploadItems.flatMap((item) => item.uploadedName ? [item.uploadedName] : []),
    ];
    const nextName = action === "copy"
      ? availableCopyName(currentCollision.file.name, knownNames)
      : currentCollision.file.name;
    try {
      await api.uploadFile(
        joinPath(currentCollision.targetDirectory, nextName),
        currentCollision.file,
        action === "replace",
        action === "replace" ? currentCollision.existingRevision : undefined,
      );
      setUploadItems((current) => current.map((item) =>
        item.id === currentCollision.id
          ? { ...item, status: "done", uploadedName: nextName, message: undefined }
          : item,
      ));
      showToast(action === "replace" ? "Файл заменён" : `Сохранено как «${nextName}»`, "success");
      await load();
    } catch (exception) {
      setUploadItems((current) => current.map((item) =>
        item.id === currentCollision.id
          ? { ...item, status: "failed", message: ownerFacingError(exception, "Не удалось загрузить файл.") }
          : item,
      ));
    } finally {
      setResolvingCollision(false);
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
    });
  };

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    if (!pendingDelete.capabilities?.trash || !pendingDelete.revision) {
      showToast("Этот объект нельзя переместить в корзину.", "error");
      setPendingDelete(null);
      return;
    }
    setDeleting(true);
    try {
      await api.trashFile(pendingDelete.path, pendingDelete.revision);
      showToast("Перемещено в корзину", "success");
      setPendingDelete(null);
      setTrashRefreshVersion((version) => version + 1);
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
      await api.renameFile(renameEntry.path, nextName, renameEntry.revision);
      showToast(renameEntry.is_directory ? "Папка переименована" : "Файл переименован", "success");
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
              goToPath();
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
          <nav className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto text-sm" aria-label="Путь к папке">
            {breadcrumbs.map((item, index) => (
              <span key={item.path} className="flex shrink-0 items-center gap-1">
                {index > 0 ? <ChevronRight className="size-3.5 text-text-tertiary" aria-hidden /> : null}
                <button
                  type="button"
                  onClick={() => navigateTo(item.path)}
                  disabled={index === breadcrumbs.length - 1}
                  className="min-h-9 rounded-md px-2 font-medium text-text-secondary hover:bg-background/45 hover:text-foreground disabled:text-foreground"
                >
                  {clientEntryLabel(item.label)}
                </button>
              </span>
            ))}
          </nav>
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
              <span className="block truncate text-xs text-text-secondary" title={headerPath}>
                {headerPath} · до 100 МБ на файл
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

      {uploadItems.length > 0 ? (
        <Card className="min-w-0 max-w-full overflow-hidden rounded-xl" aria-live="polite">
          <CardContent className="p-0">
            <div className="flex min-h-12 items-center justify-between gap-3 border-b border-border px-4 py-2">
              <span className="text-sm font-semibold">Загрузки</span>
              {!uploadActive ? (
                <Button ghost size="icon" type="button" onClick={() => setUploadItems([])} aria-label="Скрыть список загрузок">
                  <X />
                </Button>
              ) : null}
            </div>
            <div className="divide-y divide-border/60">
              {uploadItems.map((item) => (
                <div key={item.id} className="flex min-h-12 items-center gap-3 px-4 py-2 text-sm">
                  {item.status === "done" ? (
                    <CheckCircle2 className="size-4 shrink-0 text-success" aria-hidden />
                  ) : item.status === "failed" || item.status === "cancelled" ? (
                    <XCircle className="size-4 shrink-0 text-destructive" aria-hidden />
                  ) : item.status === "uploading" ? (
                    <Spinner />
                  ) : (
                    <Upload className="size-4 shrink-0 text-text-tertiary" aria-hidden />
                  )}
                  <span className="min-w-0 flex-1 truncate">{item.uploadedName ?? item.file.name}</span>
                  <span className="shrink-0 text-xs text-text-secondary">
                    {item.status === "waiting" ? "Ожидает" :
                      item.status === "uploading" ? "Загружается" :
                        item.status === "choice" ? "Нужен выбор" :
                          item.status === "done" ? "Готово" : item.message ?? "Не загружено"}
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      ) : null}

      <FileTrash
        onRestored={() => void load()}
        refreshVersion={trashRefreshVersion}
      />

      <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center">
        <label className="relative min-w-0 flex-1">
          <span className="sr-only">Поиск файлов</span>
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-text-tertiary" aria-hidden />
          <Input
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="Найти файл или папку"
            className="h-10 pl-9"
          />
        </label>
        <label className="flex min-h-10 items-center gap-2 rounded-lg border border-border bg-background px-3 text-sm text-text-secondary">
          <span>Сортировка</span>
          <select
            value={sortMode}
            onChange={(event) => setSortMode(event.target.value as FileSortMode)}
            className="min-h-8 rounded bg-transparent font-medium text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
          >
            <option value="name">По имени</option>
            <option value="modified">Сначала новые</option>
            <option value="size">По размеру</option>
          </select>
        </label>
      </div>

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

          <div className="hidden grid-cols-[minmax(12rem,1fr)_7rem_10rem_11rem] items-center gap-3 border-b border-border px-4 py-3 text-xs font-semibold text-text-tertiary md:grid">
            <span>Название</span>
            <span>Размер</span>
            <span>Изменено</span>
            <span className="text-right">Действия</span>
          </div>

          {listing?.parent && !isAtClientRoot && (
            <button
              type="button"
              onClick={() => navigateTo(listing.parent ?? undefined)}
              className="grid min-h-12 w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-border/60 px-4 py-3 text-left text-sm transition-colors hover:bg-background/40 md:grid-cols-[minmax(12rem,1fr)_7rem_10rem_11rem]"
            >
              <span className="flex min-w-0 items-center gap-2 font-mono text-text-secondary">
                <ArrowUp className="h-4 w-4 shrink-0 text-text-tertiary" />
                ..
              </span>
              <span className="hidden md:block" />
              <span className="hidden md:block" />
              <span className="hidden md:block" />
            </button>
          )}

          {loading && !listing ? (
            <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
              <Spinner />
              Загрузка файлов...
            </div>
          ) : !error && listing && visibleEntries.length === 0 ? (
            <div className="px-5 py-12 text-center text-sm text-muted-foreground">
              {searchQuery.trim()
                ? `По запросу «${searchQuery.trim()}» ничего не найдено.`
                : isInClientInbox
                ? "Здесь появятся ваши исходники. Нажмите «Загрузить» или перетащите файлы в область выше."
                : "Папка пуста. Загрузите файлы или создайте новую папку."}
            </div>
          ) : (
            visibleEntries.map((entry) => (
              <div
                key={entry.path}
                className="relative grid min-h-16 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-border/60 px-4 py-3 text-sm last:border-b-0 hover:bg-background/35 md:grid-cols-[minmax(12rem,1fr)_7rem_10rem_11rem]"
              >
                {/* `after:inset-0` растягивает область нажатия на всю строку:
                    владелец жаловался, что папка открывается только по имени,
                    а промах по размеру или дате не делает ничего (QA 03.09).
                    Колонка действий поднята над этим слоем ниже. */}
                <button
                  type="button"
                  onClick={() => (entry.is_directory ? openDirectory(entry) : previewEntry(entry))}
                  className="flex min-w-0 cursor-pointer items-center gap-3 text-left text-foreground after:absolute after:inset-0 after:content-['']"
                >
                  {entry.is_directory ? (
                    <Folder className="h-4 w-4 shrink-0 text-warning" />
                  ) : (
                    <FileIcon className="h-4 w-4 shrink-0 text-text-tertiary" />
                  )}
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{clientEntryLabel(entry.name)}</span>
                    <span className="mt-0.5 block truncate text-xs text-muted-foreground md:hidden">
                      {formatBytes(entry.size)} · {Number.isFinite(entry.mtime) ? dateFormat.format(entry.mtime * 1000) : "Дата неизвестна"}
                    </span>
                  </span>
                </button>
                <span className="hidden text-xs tabular-nums text-text-secondary md:block">{formatBytes(entry.size)}</span>
                <span className="hidden truncate text-xs text-text-secondary md:block">
                  {Number.isFinite(entry.mtime) ? dateFormat.format(entry.mtime * 1000) : "-"}
                </span>
                <span className="relative z-10 flex justify-end gap-1">
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
                    </>
                  )}
                  {!isAtClientRoot && entry.capabilities?.rename && entry.revision ? (
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
                  ) : null}
                  {!isAtClientRoot && entry.capabilities?.trash && entry.revision ? (
                    <Button
                      ghost
                      size="icon"
                      type="button"
                      onClick={() => setPendingDelete(entry)}
                      aria-label={entry.capabilities?.trash
                        ? `Переместить ${entry.name} в корзину`
                        : `Удалить ${entry.name}`}
                      className="text-destructive hover:text-destructive"
                    >
                      <Trash2 />
                    </Button>
                  ) : null}
                </span>
              </div>
            ))
          )}
        </CardContent>
      </Card>

      <PluginSlot name="files:bottom" />

      <Dialog
        open={Boolean(currentCollision)}
        onOpenChange={(open) => {
          if (!open && !resolvingCollision) void resolveCollision("cancel");
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Файл с таким именем уже есть</DialogTitle>
            <DialogDescription>
              {currentCollision?.existingRevision
                ? `Выберите, что сделать с «${currentCollision.file.name}».`
                : "Не удалось подтвердить текущую версию файла. Сохраните копию или отмените загрузку."}
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-2 p-4">
            <Button
              type="button"
              outlined
              disabled={resolvingCollision}
              onClick={() => void resolveCollision("copy")}
              prefix={<Copy />}
            >
              Сохранить копию
            </Button>
            <Button
              type="button"
              disabled={resolvingCollision || !currentCollision?.existingRevision}
              onClick={() => void resolveCollision("replace")}
              prefix={resolvingCollision ? <Spinner /> : <RefreshCw />}
            >
              Заменить файл
            </Button>
          </div>
          <DialogFooter>
            <Button type="button" ghost disabled={resolvingCollision} onClick={() => void resolveCollision("cancel")}>
              Отмена
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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
              В папке: {headerPath || filesRootLabel()}
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
        title={pendingDelete ? `Переместить «${pendingDelete.name}» в корзину?` : "Переместить объект?"}
        description="Объект переместится в корзину. Его можно будет вернуть на прежнее место."
        confirmLabel="Переместить в корзину"
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
            <DialogTitle>
              {renameEntry?.is_directory ? "Переименовать папку" : "Переименовать файл"}
            </DialogTitle>
            <DialogDescription>
              Содержимое останется без изменений.
            </DialogDescription>
          </DialogHeader>
          <div className="p-4">
            <label htmlFor="rename-owner-file" className="sr-only">
              Новое имя
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
