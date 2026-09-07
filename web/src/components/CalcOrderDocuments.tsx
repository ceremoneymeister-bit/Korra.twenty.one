import { useId, useMemo, useRef, useState } from "react";
import { ArrowLeft, ChevronRight, Download, FileText, FolderOpen, Search } from "lucide-react";
import { ProductButton } from "@/components/ProductButton";
import { withBasePath } from "@/lib/api";
import { buildDocumentTree, documentEntries, parentDocumentPath, type CalcOrderDocument } from "@/lib/calc-document-tree";
import { ownerFacingError } from "@/lib/owner-facing-error";

export type { CalcOrderDocument } from "@/lib/calc-document-tree";
const PAGE_SIZE = 50;

function bytesLabel(size: number): string {
  if (size < 1024) return `${size} Б`;
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} КБ`;
  if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} МБ`;
  return `${(size / 1024 ** 3).toFixed(1)} ГБ`;
}

export function CalcOrderDocuments({ title, files, directories }: {
  title: string;
  files: CalcOrderDocument[];
  directories?: string[];
}) {
  const titleId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const [folder, setFolder] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const tree = useMemo(() => buildDocumentTree(files, directories), [files, directories]);
  const currentFolder = tree.children.has(folder) ? folder : "";
  const searching = query.trim().length > 0;
  const entries = documentEntries(tree, currentFolder, query);
  const currentPage = Math.min(page, Math.max(0, Math.ceil(entries.length / PAGE_SIZE) - 1));
  const offset = currentPage * PAGE_SIZE;
  const breadcrumbs = currentFolder ? currentFolder.split("/") : [];

  function openFolder(path: string) {
    setFolder(path); setQuery(""); setPage(0);
    heading.current?.focus();
  }

  async function download(file: CalcOrderDocument) {
    setBusy(file.download_url); setError(null);
    try {
      const response = await fetch(withBasePath(file.download_url), {
        credentials: "include", headers: { "X-Hermes-Session-Token": window.__HERMES_SESSION_TOKEN__ ?? "" },
      });
      if (!response.ok) throw new Error("Не удалось скачать файл. Обновите страницу и попробуйте снова.");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url; link.download = file.name;
      link.click(); setTimeout(() => URL.revokeObjectURL(url), 30_000);
    } catch (cause) {
      setError(ownerFacingError(cause, "Не удалось скачать файл."));
    } finally { setBusy(null); }
  }

  return <section aria-labelledby={titleId} className="min-w-0 space-y-4">
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <h2 id={titleId} className="text-lg font-semibold">{title}</h2>
      <span className="text-sm text-muted-foreground">Всего файлов: {files.length}</span>
    </div>
    {tree.entries.length > 0 && <>
      <label className="flex max-w-xl items-center gap-3">
        <Search className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        <input type="search" value={query} onChange={(event) => { setQuery(event.target.value); setPage(0); }}
          className="min-h-11 w-full rounded-lg border border-border bg-background px-3 text-sm"
          placeholder="Поиск по всем файлам и папкам" aria-label={`Поиск: ${title.toLowerCase()}`} />
      </label>
      <nav aria-label={`Путь: ${title.toLowerCase()}`} className="flex min-w-0 flex-wrap items-center gap-x-1 text-sm">
        <button type="button" onClick={() => openFolder("")} aria-current={!currentFolder ? "page" : undefined}
          className="min-h-11 rounded-lg px-2 underline underline-offset-4 hover:bg-muted/40 focus-visible:outline focus-visible:outline-primary">Все документы</button>
        {breadcrumbs.map((name, index) => <span key={index} className="inline-flex min-w-0 max-w-full items-center gap-1">
          <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <button type="button" onClick={() => openFolder(breadcrumbs.slice(0, index + 1).join("/"))}
            aria-current={index === breadcrumbs.length - 1 ? "page" : undefined}
            className="min-h-11 min-w-0 rounded-lg px-2 text-left underline underline-offset-4 [overflow-wrap:anywhere] hover:bg-muted/40 focus-visible:outline focus-visible:outline-primary">{name}</button>
        </span>)}
      </nav>
    </>}
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h3 ref={heading} tabIndex={-1} className="min-w-0 text-sm font-semibold outline-none [overflow-wrap:anywhere]">
        {searching ? "Результаты поиска по всему заказу" : currentFolder.split("/").at(-1) || "Все документы"}
      </h3>
      {searching
        ? <ProductButton ghost onClick={() => { setQuery(""); setPage(0); }}>Закрыть поиск</ProductButton>
        : currentFolder && <ProductButton ghost prefix={<ArrowLeft className="size-4" />} onClick={() => openFolder(parentDocumentPath(currentFolder))}>Назад</ProductButton>}
    </div>
    {entries.length > 0 ? <>
      <ul aria-label={`Содержимое: ${title.toLowerCase()}`} className="divide-y divide-border rounded-xl border border-border">
        {entries.slice(offset, offset + PAGE_SIZE).map((entry) => <li key={`${entry.kind}:${entry.path}`} className="min-w-0">
          {entry.kind === "folder" ? <button type="button" onClick={() => openFolder(entry.path)} aria-label={`Открыть папку ${entry.path}`}
            className="flex min-h-14 w-full min-w-0 items-center gap-3 rounded-lg px-3 py-2 text-left hover:bg-muted/40 focus-visible:outline focus-visible:outline-primary sm:px-4">
            <FolderOpen className="size-5 shrink-0 text-primary" aria-hidden />
            <span className="min-w-0 flex-1"><span className="block text-sm font-medium [overflow-wrap:anywhere]">{searching ? entry.path : entry.name}</span>
              <span className="block text-xs text-muted-foreground">Файлов: {entry.fileCount}</span></span>
            <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          </button> : <div className="flex min-h-14 min-w-0 items-center gap-3 px-3 py-2 sm:px-4">
            <FileText className="size-4 shrink-0 text-muted-foreground" aria-hidden />
            <div className="min-w-0 flex-1"><p className="text-sm font-medium [overflow-wrap:anywhere]">{searching ? entry.path : entry.name}</p>
              <p className="text-xs text-muted-foreground">{bytesLabel(entry.file.bytes)}</p></div>
            <ProductButton ghost size="icon" disabled={busy !== null} aria-label={`Скачать ${entry.path}`} onClick={() => void download(entry.file)}><Download /></ProductButton>
          </div>}
        </li>)}
      </ul>
      <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-muted-foreground">
        <span role="status">Показано {offset + 1}–{Math.min(offset + PAGE_SIZE, entries.length)} из {entries.length}</span>
        {entries.length > PAGE_SIZE && <div className="flex flex-wrap gap-2">
          <ProductButton outlined disabled={currentPage === 0} onClick={() => { setPage(currentPage - 1); heading.current?.focus(); }}>Предыдущие 50</ProductButton>
          <ProductButton outlined disabled={offset + PAGE_SIZE >= entries.length} onClick={() => { setPage(currentPage + 1); heading.current?.focus(); }}>Следующие 50</ProductButton>
        </div>}
      </div>
    </> : <p role="status" className="rounded-xl bg-muted/30 p-5 text-sm text-muted-foreground">
      {searching ? "По этому запросу файлов и папок не найдено."
        : currentFolder ? "В этой папке пока нет файлов."
        : title === "Результаты расчёта" ? "Здесь появятся документы после расчёта заказа." : "Исходные документы отсутствуют."}
    </p>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
  </section>;
}

export default CalcOrderDocuments;
