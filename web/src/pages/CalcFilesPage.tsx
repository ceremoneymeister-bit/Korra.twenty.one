import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { ArrowLeft, ArrowUpRight, CheckCircle2, Download, FileText, FolderOpen, Search, Upload } from "lucide-react";
import { Input } from "@nous-research/ui/ui/components/input";
import { ProductButton } from "@/components/ProductButton";
import { fetchJSON, withBasePath } from "@/lib/api";
import { formatMoment } from "@/lib/calc-orders";
import { ownerFacingError } from "@/lib/owner-facing-error";
import { folderUploadKey, readDroppedFolder, selectedFolder, uploadFolder, type FolderSelection, type UploadProgress } from "@/lib/calc-folder-upload";
import { FileTrash } from "@/pages/FilesPage";

interface FolderSummary {
  order_id: string;
  folder_name: string;
  file_count: number;
  total_bytes: number;
  created_at: string;
  status: string;
}
interface FolderDocument { name: string; relative_path: string; bytes: number; download_url: string }
interface FolderDetail extends FolderSummary { source_files: FolderDocument[]; result_files: FolderDocument[] }
const PAGE_SIZE = 50;
const inputStyle = "min-h-11 w-full rounded-lg border border-border bg-background px-3 text-sm";

function bytesLabel(size: number): string {
  if (size < 1024) return `${size} Б`;
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} КБ`;
  if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} МБ`;
  return `${(size / 1024 ** 3).toFixed(1)} ГБ`;
}

function Documents({ title, files }: { title: string; files: FolderDocument[] }) {
  const [query, setQuery] = useState("");
  const [visible, setVisible] = useState(PAGE_SIZE);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const filtered = files.filter((file) => (file.relative_path || file.name).toLocaleLowerCase("ru").includes(query.toLocaleLowerCase("ru")));
  async function download(file: FolderDocument) {
    setBusy(file.download_url); setError(null);
    try {
      const response = await fetch(withBasePath(file.download_url), {
        credentials: "include", headers: { "X-Hermes-Session-Token": window.__HERMES_SESSION_TOKEN__ ?? "" },
      });
      if (!response.ok) throw new Error("Не удалось скачать файл. Обновите страницу и попробуйте снова.");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = file.name;
      link.click(); setTimeout(() => URL.revokeObjectURL(url), 30_000);
    } catch (cause) { setError(ownerFacingError(cause, "Не удалось скачать файл.")); }
    finally { setBusy(null); }
  }
  return <section className="min-w-0 space-y-4">
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <h2 className="text-lg font-semibold">{title}</h2>
      <span className="text-sm text-muted-foreground">Файлов: {files.length}</span>
    </div>
    {files.length > 0 ? <>
      <label className="flex max-w-xl items-center gap-3">
        <Search className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        <input className={inputStyle} value={query} onChange={(event) => { setQuery(event.target.value); setVisible(PAGE_SIZE); }}
          placeholder="Найти файл или подпапку" aria-label={`Поиск: ${title.toLowerCase()}`} />
      </label>
      <ul className="divide-y divide-border rounded-xl border border-border">
        {filtered.slice(0, visible).map((file) => <li key={file.relative_path || file.name} className="flex min-w-0 items-center gap-3 px-3 py-2 sm:px-4">
          <FileText className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="break-words text-sm font-medium [overflow-wrap:anywhere]">{file.relative_path || file.name}</p>
            <p className="text-xs text-muted-foreground">{bytesLabel(file.bytes)}</p>
          </div>
          <ProductButton ghost size="icon" disabled={busy !== null} aria-label={`Скачать ${file.relative_path || file.name}`} onClick={() => void download(file)}><Download /></ProductButton>
        </li>)}
      </ul>
      <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-muted-foreground">
        <span>Показано {Math.min(visible, filtered.length)} из {filtered.length}{query ? ` · всего ${files.length}` : ""}</span>
        {visible < filtered.length && <ProductButton outlined onClick={() => setVisible((count) => count + PAGE_SIZE)}>Показать ещё 50</ProductButton>}
      </div>
    </> : <p className="rounded-xl bg-muted/30 p-5 text-sm text-muted-foreground">{title === "Результаты расчёта" ? "Здесь появятся документы после расчёта заказа." : "Исходные документы отсутствуют."}</p>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
  </section>;
}

export default function CalcFilesPage() {
  const [params, setParams] = useSearchParams();
  const orderId = params.get("order");
  const [folders, setFolders] = useState<FolderSummary[]>([]);
  const [detail, setDetail] = useState<FolderDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [visible, setVisible] = useState(PAGE_SIZE);
  const [selection, setSelection] = useState<FolderSelection | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [uploading, setUploading] = useState(false);
  const [reading, setReading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [saved, setSaved] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | null>(null);
  const uploadId = useRef<string | null>(null);
  const loadVersion = useRef(0);
  const blocked = uploading || reading;

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
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    if (!uploading) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [uploading]);

  function choose(folder: FolderSelection) {
    setSelection(folder); uploadId.current = null;
    setUploadError(null); setProgress(null); setSaved(false);
  }
  async function drop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault(); setDragging(false);
    if (blocked) return;
    setReading(true); setUploadError(null);
    try { choose(await readDroppedFolder(event.dataTransfer.items)); }
    catch (cause) { setUploadError(ownerFacingError(cause, "Не удалось прочитать папку.")); }
    finally { setReading(false); }
  }
  async function startUpload() {
    if (!selection || blocked) return;
    controller.current = new AbortController();
    uploadId.current ??= folderUploadKey(selection);
    setUploading(true); setUploadError(null);
    try {
      const id = await uploadFolder(selection, uploadId.current, setProgress, controller.current.signal);
      setSelection(null); setProgress(null); setSaved(true); setParams({ order: id });
    } catch (cause) {
      if (!controller.current.signal.aborted) setUploadError(ownerFacingError(cause, "Загрузка прервана. Повторите загрузку, чтобы продолжить."));
    } finally { setUploading(false); }
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
        {saved && <p role="status" className="flex items-center gap-2 rounded-xl bg-success/10 p-4 text-sm"><CheckCircle2 className="size-4 shrink-0 text-success" aria-hidden />Папка загружена полностью. Черновик заказа сохранён.</p>}
        <Documents key={`${orderId}-sources`} title="Исходные документы" files={detail.source_files} />
        <Documents key={`${orderId}-results`} title="Результаты расчёта" files={detail.result_files} />
      </>}
    </> : <>
      <header className="space-y-2"><h1 className="text-2xl font-semibold">Файлы заказов</h1><p className="text-sm text-muted-foreground">Заявка, чертежи и остальные материалы — одной папкой.</p></header>
      <div onDrop={(event) => void drop(event)} onDragOver={(event) => { event.preventDefault(); if (!blocked) setDragging(true); }}
        onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false); }}
        className={`flex flex-col items-center gap-4 rounded-2xl border-2 border-dashed px-5 py-7 text-center ${dragging ? "border-primary bg-primary/10" : "border-border bg-muted/20"}`}>
        <FolderOpen className="size-7 text-muted-foreground" aria-hidden />
        <ProductButton disabled={blocked} prefix={<Upload className="size-4" />} onClick={() => input.current?.click()}>Загрузить папку заказа</ProductButton>
        <p className="text-sm text-muted-foreground">{reading ? "Читаю список файлов…" : "Или перетащите папку сюда вместе с вложенными папками"}</p>
        <input ref={input} type="file" multiple {...{ webkitdirectory: "" }} className="hidden" aria-label="Выбрать папку заказа" onChange={(event) => {
          if (!event.target.files?.length) return;
          try { choose(selectedFolder(Array.from(event.target.files))); }
          catch (cause) { setUploadError(ownerFacingError(cause, "Не удалось выбрать папку.")); }
          event.target.value = "";
        }} />
      </div>
      {selection && <section aria-label="Загрузка папки" className="space-y-4 rounded-xl border border-border p-4 sm:p-5">
        <label className="block max-w-xl space-y-2 text-sm font-medium">Номер или название заказа
          <Input value={selection.name} disabled={blocked || progress !== null} onChange={(event) => { setSelection({ ...selection, name: event.target.value }); uploadId.current = null; }} className="min-h-11" />
        </label>
        <p className="text-sm text-muted-foreground">Файлов: {selection.files.length} · {bytesLabel(selection.files.reduce((sum, { file }) => sum + file.size, 0))} · структура подпапок сохранится</p>
        {progress && <div className="space-y-2" role="status" aria-live="polite">
          <progress className="h-2 w-full accent-primary" value={progress.completed} max={progress.total} aria-label="Прогресс загрузки" />
          <p className="text-sm">Загружено {progress.completed} из {progress.total} · {bytesLabel(progress.bytes)} из {bytesLabel(progress.totalBytes)}{progress.completed === progress.total && uploading ? " · Сохраняю черновик…" : ""}</p>
        </div>}
        <div className="flex flex-wrap gap-3">
          <ProductButton disabled={blocked || !selection.name.trim()} onClick={() => void startUpload()}>{uploading ? "Загрузка…" : progress ? "Продолжить загрузку" : "Загрузить и создать черновик"}</ProductButton>
          {uploading && <ProductButton outlined onClick={() => controller.current?.abort()}>Приостановить</ProductButton>}
          {!uploading && <ProductButton ghost disabled={reading} onClick={() => { setSelection(null); setUploadError(null); setProgress(null); }}>Выбрать другую папку</ProductButton>}
        </div>
      </section>}
      {uploadError && <p role="alert" className="rounded-xl border border-destructive/30 p-4 text-sm text-destructive">{uploadError}</p>}
      <section className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-semibold">Папки заказов</h2><span className="text-sm text-muted-foreground">Всего: {folders.length}</span></div>
        <label className="flex max-w-xl items-center gap-3"><Search className="size-4 shrink-0 text-muted-foreground" aria-hidden /><input className={inputStyle} value={query} onChange={(event) => { setQuery(event.target.value); setVisible(PAGE_SIZE); }} placeholder="Номер или название заказа" aria-label="Поиск заказов" /></label>
        {!loading && !error && filtered.length === 0 && <p className="py-5 text-sm text-muted-foreground">{query ? "По этому запросу папок не найдено." : "Загрузите первую папку. Она появится здесь, а её черновик — в заказах."}</p>}
        <ul className="divide-y divide-border">
          {filtered.slice(0, visible).map((folder) => <li key={folder.order_id}><Link to={`/files?order=${encodeURIComponent(folder.order_id)}`} aria-disabled={blocked} onClick={(event) => { if (blocked) event.preventDefault(); }} className="flex min-w-0 items-center gap-4 rounded-lg px-2 py-4 hover:bg-muted/30 focus-visible:outline focus-visible:outline-primary">
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
