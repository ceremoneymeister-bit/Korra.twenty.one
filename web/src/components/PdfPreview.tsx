import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Loader2, RefreshCw } from "lucide-react";
import { api, type PdfPagePreview } from "@/lib/api";
import { ownerFacingError } from "@/lib/owner-facing-error";

interface PdfPreviewProps {
  path: string;
  expectedSha256?: string | null;
}

/** Page images work in browsers that cannot embed a PDF, including mobile ones. */
export function PdfPreview({ path, expectedSha256 }: PdfPreviewProps) {
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<PdfPagePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [zoom, setZoom] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    api.readPdfPage(path, page, expectedSha256, controller.signal)
      .then(payload => {
        if (controller.signal.aborted) return;
        setResult(payload);
        scroller.current?.scrollTo?.(0, 0);
      })
      .catch(cause => {
        if (!controller.signal.aborted) setError(ownerFacingError(cause, "Не удалось открыть PDF. Скачайте оригинал или повторите попытку."));
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [path, expectedSha256, page, retry]);

  const control = "inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg border border-border px-3 text-sm disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40";
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 flex-wrap items-center justify-center gap-2 border-b border-border bg-background px-2 py-2" aria-label="Страницы PDF">
        <button type="button" className={control} aria-label="Предыдущая страница" disabled={loading || page <= 1} onClick={() => setPage(page - 1)}><ChevronLeft className="size-4" /></button>
        <span className="text-sm" aria-live="polite">{result ? `Страница ${page} из ${result.pages}` : "PDF"}</span>
        <button type="button" className={control} aria-label="Следующая страница" disabled={loading || !result || page >= result.pages} onClick={() => setPage(page + 1)}><ChevronRight className="size-4" /></button>
        <button type="button" className={control} aria-pressed={zoom} onClick={() => setZoom(!zoom)}>{zoom ? "По ширине" : "Увеличить"}</button>
      </div>
      <div ref={scroller} className="min-h-0 flex-1 overflow-auto p-2 sm:p-4">
        {loading ? (
          <div role="status" className="flex min-h-48 items-center justify-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />Открываем страницу…</div>
        ) : error ? (
          <div role="alert" className="p-5 text-center text-sm"><p>{error}</p><button type="button" className={`${control} mt-4 gap-2`} aria-label="Повторить просмотр PDF" onClick={() => setRetry(retry + 1)}><RefreshCw className="size-4" />Повторить</button></div>
        ) : result ? (
          <img src={result.data_url} alt={`Страница ${result.page} из ${result.pages}`} className={zoom ? "mx-auto max-w-none bg-white" : "mx-auto h-auto max-w-full bg-white"} />
        ) : null}
      </div>
    </div>
  );
}
