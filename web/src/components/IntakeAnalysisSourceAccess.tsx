import { useEffect, useRef, useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@nous-research/ui/ui/components/dialog";
import { authedFetch } from "@/lib/api";
import type { AnalysisSource } from "@/lib/calc-intake-analysis";
import { ownerFacingError } from "@/lib/owner-facing-error";

export function IntakeAnalysisSourceAccess({ source }: { source: AnalysisSource }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ url: string; page: string } | null>(null);
  const imageUrl = useRef<string | null>(null);
  const request = useRef<AbortController | null>(null);
  const pages = Object.entries(source.render_jobs ?? {}).filter(([page, job]) => /^[1-9]\d*$/.test(page) && Boolean(job)).sort(([left], [right]) => Number(left) - Number(right));

  useEffect(() => () => {
    request.current?.abort();
    if (imageUrl.current) URL.revokeObjectURL(imageUrl.current);
  }, []);

  function closePreview() {
    if (imageUrl.current) URL.revokeObjectURL(imageUrl.current);
    imageUrl.current = null;
    setPreview(null);
  }

  async function open(jobId: string, page?: string) {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError(null);
    try {
      const response = await authedFetch(`/api/calc/document-jobs/${encodeURIComponent(jobId)}/sources/${encodeURIComponent(source.source_id)}/${page ? "image" : "download"}`, { signal: controller.signal });
      if (!response.ok) throw new Error(page ? "Изображение страницы пока недоступно. Обновите состояние разбора и попробуйте снова." : "Не удалось скачать исходник. Обновите состояние разбора и попробуйте снова.");
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      if (page && blob.type !== "image/png") throw new Error("Сервис вернул неподходящий формат изображения страницы.");
      const url = URL.createObjectURL(blob);
      if (page) {
        if (imageUrl.current) URL.revokeObjectURL(imageUrl.current);
        imageUrl.current = url;
        setPreview({ url, page });
      } else {
        const link = document.createElement("a");
        link.href = url;
        link.download = source.relative_path.split("/").at(-1) || "исходник";
        link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
      }
    } catch (cause) {
      if (!controller.signal.aborted) setError(ownerFacingError(cause, "Не удалось открыть источник."));
    } finally {
      request.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  if (!source.inspect_job_id && pages.length === 0) return null;
  return <div className="space-y-2 px-3 pb-3">
    <div className="flex flex-wrap gap-2">
      {source.inspect_job_id && <button type="button" disabled={busy} className="min-h-10 rounded-lg border border-border px-3 text-sm text-primary disabled:opacity-50" onClick={() => void open(source.inspect_job_id!)}>Скачать исходник</button>}
      {pages.map(([page, jobId]) => <button key={page} type="button" disabled={busy} className="min-h-10 rounded-lg border border-border px-3 text-sm text-primary disabled:opacity-50" onClick={() => void open(jobId, page)}>Страница {page}</button>)}
    </div>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <Dialog open={preview !== null} onOpenChange={(open) => { if (!open) closePreview(); }}>
      <DialogContent className="flex max-h-[90vh] max-w-5xl flex-col overflow-hidden">
        <DialogHeader>
          <DialogTitle className="break-words">{source.relative_path} · Страница {preview?.page}</DialogTitle>
          <DialogDescription>Изображение исходной страницы для проверки предложенного состава.</DialogDescription>
        </DialogHeader>
        <div className="min-h-0 overflow-auto">{preview && <img src={preview.url} alt={`${source.relative_path}, страница ${preview.page}`} className="h-auto w-full" />}</div>
        <button type="button" className="min-h-10 self-end rounded-lg border border-border px-3 font-semibold" onClick={closePreview}>Закрыть страницу</button>
      </DialogContent>
    </Dialog>
  </div>;
}
