import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { ArrowLeft, ArrowUpRight, FolderOpen, Search, Upload } from "lucide-react";
import { useStore } from "@nanostores/react";
import { Input } from "@nous-research/ui/ui/components/input";
import { ProductButton } from "@/components/ProductButton";
import { fetchJSON } from "@/lib/api";
import { formatMoment } from "@/lib/calc-orders";
import { ownerFacingError } from "@/lib/owner-facing-error";
import { readDirectoryHandle, readDroppedFolder, selectedFolder, type FolderSelection } from "@/lib/calc-folder-upload";
import { FileTrash } from "@/pages/FilesPage";
import { CalcOrderDocuments, type CalcOrderDocument } from "@/components/CalcOrderDocuments";
import { $folderUpload, startFolderUpload } from "@/store/calc-folder-upload";

interface FolderSummary {
  order_id: string;
  folder_name: string;
  file_count: number;
  total_bytes: number;
  created_at: string;
  status: string;
}
interface FolderDetail extends FolderSummary { source_files: CalcOrderDocument[]; result_files: CalcOrderDocument[]; directories?: string[] }
const PAGE_SIZE = 50;
const inputStyle = "min-h-11 w-full rounded-lg border border-border bg-background px-3 text-sm";

function bytesLabel(size: number): string {
  if (size < 1024) return `${size} Б`;
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} КБ`;
  if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} МБ`;
  return `${(size / 1024 ** 3).toFixed(1)} ГБ`;
}

export default function CalcFilesPage() {
  const [params] = useSearchParams();
  const orderId = params.get("order");
  const [folders, setFolders] = useState<FolderSummary[]>([]);
  const [detail, setDetail] = useState<FolderDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [visible, setVisible] = useState(PAGE_SIZE);
  const [selection, setSelection] = useState<FolderSelection | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const upload = useStore($folderUpload);
  const [reading, setReading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const loadVersion = useRef(0);
  const blocked = Boolean(upload && upload.status !== "complete") || reading;
  const completedOrderId = upload?.status === "complete" ? upload.orderId : null;
  const seenCompletion = useRef(completedOrderId);

  const load = useCallback(async () => {
    const version = ++loadVersion.current;
    setLoading(true); setError(null); setDetail(null);
    try {
      if (orderId) {
        const result = await fetchJSON<FolderDetail>(`/api/calc/folders/${encodeURIComponent(orderId)}`);
        if (version === loadVersion.current) setDetail(result);
      } else {
        const result = await fetchJSON<{ orders: FolderSummary[] }>("/api/calc/folders");
        if (version === loadVersion.current) setFolders(result.orders);
      }
    } catch (cause) {
      if (version === loadVersion.current) setError(ownerFacingError(cause, "Не удалось открыть файлы заказов."));
    } finally { if (version === loadVersion.current) setLoading(false); }
  }, [orderId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (completedOrderId && completedOrderId !== seenCompletion.current) {
      seenCompletion.current = completedOrderId;
      if (!orderId) void load();
    }
  }, [load, completedOrderId, orderId]);

  function choose(folder: FolderSelection) {
    setSelection(folder);
    setUploadError(null);
  }
  async function drop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault(); setDragging(false);
    if (blocked) return;
    setReading(true); setUploadError(null);
    try { choose(await readDroppedFolder(event.dataTransfer.items)); }
    catch (cause) { setUploadError(ownerFacingError(cause, "Не удалось прочитать папку.")); }
    finally { setReading(false); }
  }
  async function pickFolder() {
    const picker = (window as Window & { showDirectoryPicker?: (options: { mode: "read" }) => Promise<FileSystemDirectoryHandle> }).showDirectoryPicker;
    if (!picker) { input.current?.click(); return; }
    setReading(true); setUploadError(null);
    try { choose(await readDirectoryHandle(await picker.call(window, { mode: "read" }))); }
    catch (cause) {
      if (!(cause instanceof DOMException && cause.name === "AbortError")) {
        setUploadError(ownerFacingError(cause, "Не удалось открыть папку. Попробуйте перетащить её в область загрузки."));
      }
    } finally { setReading(false); }
  }
  function startUpload() {
    if (!selection || blocked) return;
    if (startFolderUpload(selection)) { setSelection(null); setUploadError(null); }
  }
  const filtered = folders.filter((folder) => folder.folder_name.toLocaleLowerCase("ru").includes(query.toLocaleLowerCase("ru")));

  return <div className="mx-auto w-full max-w-5xl space-y-8 pb-8">
    {orderId ? <>
      <Link to="/files" className="inline-flex min-h-11 items-center gap-2 rounded-lg text-sm underline underline-offset-4"><ArrowLeft className="size-4" aria-hidden />Все папки заказов</Link>
      {detail?.order_id === orderId && <>
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 space-y-2">
            <h1 className="break-words text-2xl font-semibold [overflow-wrap:anywhere]">{detail.folder_name}</h1>
            <p className="text-sm text-muted-foreground">Файлов: {detail.file_count} · {bytesLabel(detail.total_bytes)} · {formatMoment(detail.created_at)}</p>
          </div>
          <Link className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-border px-4 text-sm font-semibold hover:bg-muted/40" to={`/orders?order=${encodeURIComponent(detail.order_id)}`}>Открыть в заказах<ArrowUpRight className="size-4" aria-hidden /></Link>
        </header>
        <CalcOrderDocuments key={`${orderId}-sources`} title="Исходные документы" files={detail.source_files} directories={detail.directories} />
        <CalcOrderDocuments key={`${orderId}-results`} title="Результаты расчёта" files={detail.result_files} />
      </>}
    </> : <>
      <header className="space-y-2"><h1 className="text-2xl font-semibold">Файлы заказов</h1><p className="text-sm text-muted-foreground">Заявка, чертежи и остальные материалы — одной папкой.</p></header>
      <div onDrop={(event) => void drop(event)} onDragOver={(event) => { event.preventDefault(); if (!blocked) setDragging(true); }}
        onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false); }}
        className={`flex flex-col items-center gap-4 rounded-2xl border-2 border-dashed px-5 py-7 text-center ${dragging ? "border-primary bg-primary/10" : "border-border bg-muted/20"}`}>
        <FolderOpen className="size-7 text-muted-foreground" aria-hidden />
        <ProductButton disabled={blocked} prefix={<Upload className="size-4" />} onClick={() => void pickFolder()}>Загрузить папку заказа</ProductButton>
        <p className="text-sm text-muted-foreground">{reading ? "Читаю список файлов…" : "Или перетащите папку сюда вместе с вложенными папками"}</p>
        <input ref={input} type="file" multiple {...{ webkitdirectory: "" }} className="hidden" aria-label="Выбрать папку заказа" onChange={(event) => {
          if (blocked || !event.target.files?.length) return;
          try { choose(selectedFolder(Array.from(event.target.files))); }
          catch (cause) { setUploadError(ownerFacingError(cause, "Не удалось выбрать папку.")); }
          event.target.value = "";
        }} />
      </div>
      {selection && <section aria-label="Загрузка папки" className="space-y-4 rounded-xl border border-border p-4 sm:p-5">
        <label className="block max-w-xl space-y-2 text-sm font-medium">Номер или название заказа
          <Input value={selection.name} disabled={blocked} onChange={(event) => setSelection({ ...selection, name: event.target.value })} className="min-h-11" />
        </label>
        <p className="text-sm text-muted-foreground">Файлов: {selection.files.length} · Папок: {selection.directories?.length ?? 0} · {bytesLabel(selection.files.reduce((sum, { file }) => sum + file.size, 0))}</p>
        {selection.directoryCapture === "files-only" && <p className="text-sm text-muted-foreground">Чтобы сохранить и пустые подпапки, перетащите папку заказа в область загрузки.</p>}
        <div className="flex flex-wrap gap-3">
          <ProductButton disabled={blocked || !selection.name.trim()} onClick={startUpload}>Загрузить и создать черновик</ProductButton>
          <ProductButton ghost disabled={blocked} onClick={() => { setSelection(null); setUploadError(null); }}>Выбрать другую папку</ProductButton>
        </div>
      </section>}
      {uploadError && <p role="alert" className="rounded-xl border border-destructive/30 p-4 text-sm text-destructive">{uploadError}</p>}
      <section className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-semibold">Папки заказов</h2><span className="text-sm text-muted-foreground">Всего: {folders.length}</span></div>
        <label className="flex max-w-xl items-center gap-3"><Search className="size-4 shrink-0 text-muted-foreground" aria-hidden /><input className={inputStyle} value={query} onChange={(event) => { setQuery(event.target.value); setVisible(PAGE_SIZE); }} placeholder="Номер или название заказа" aria-label="Поиск заказов" /></label>
        {!loading && !error && filtered.length === 0 && <p className="py-5 text-sm text-muted-foreground">{query ? "По этому запросу папок не найдено." : "Загрузите первую папку. Она появится здесь, а её черновик — в заказах."}</p>}
        <ul className="divide-y divide-border">
          {filtered.slice(0, visible).map((folder) => <li key={folder.order_id}><Link to={`/files?order=${encodeURIComponent(folder.order_id)}`} className="flex min-w-0 items-center gap-4 rounded-lg px-2 py-4 hover:bg-muted/30 focus-visible:outline focus-visible:outline-primary">
            <FolderOpen className="size-5 shrink-0 text-primary" aria-hidden />
            <div className="min-w-0 flex-1 space-y-1"><p className="break-words font-medium [overflow-wrap:anywhere]">{folder.folder_name}</p><p className="text-sm text-muted-foreground">Файлов: {folder.file_count} · {bytesLabel(folder.total_bytes)}</p></div>
            <span className="hidden text-sm text-muted-foreground sm:block">{formatMoment(folder.created_at)}</span><ArrowUpRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          </Link></li>)}
        </ul>
        {visible < filtered.length && <ProductButton outlined onClick={() => setVisible((count) => count + PAGE_SIZE)}>Показать ещё 50</ProductButton>}
      </section>
      <FileTrash onRestored={() => void load()} refreshVersion={0} />
    </>}
    {loading && <p role="status" className="text-sm text-muted-foreground">Загружаю список…</p>}
    {error && <div role="alert" className="space-y-3"><p className="text-sm text-destructive">{error}</p><ProductButton outlined onClick={() => void load()}>Повторить</ProductButton></div>}
  </div>;
}
