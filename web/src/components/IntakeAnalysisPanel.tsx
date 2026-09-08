import { useCallback, useEffect, useRef, useState } from "react";
import { actOnIntakeAnalysis, getIntakeAnalysis, planIntakeAnalysis, type IntakeAnalysis } from "@/lib/calc-intake-analysis";
import { ownerFacingError } from "@/lib/owner-facing-error";
import { IntakeAnalysisResults } from "./IntakeAnalysisResults";

interface Props {
  handoffId: string;
  snapshotId: string;
  answersRevision: number;
  answersReady: boolean;
}

const buttonClass = "min-h-10 rounded-lg border border-border px-3 py-2 font-semibold disabled:opacity-50";
const statuses = { queued: "Ожидает обработки", running: "Разбор выполняется", completed: "Разбор завершён", partial: "Разбор завершён частично", blocked: "Разбор требует проверки", cancelled: "Разбор остановлен", stale: "Комплект или исходные ответы изменились" };
const stages = { inventory: "Чтение и проверка объёма документов", rendering: "Подготовка страниц", analysis: "Разбор содержания", review: "Предварительный состав и вопросы" };
const blockerLabels: Record<string, string> = {
  meaningful_files_limit: "Превышен лимит документов для пилотного разбора.",
  no_engineering_documents: "В комплекте отсутствуют документы для разбора.",
  pdf_page_limit: "PDF превышает лимит страниц на один документ.",
  order_page_limit: "Превышен общий лимит страниц PDF в заказе.",
  xlsx_cell_limit: "Таблица превышает лимит ячеек для пилотного разбора.",
  model_context_limit: "Объём извлечённых данных документа превышает предел этого разбора. Модель для него не запускалась.",
  model_image_limit: "Изображение страницы слишком большое для этого разбора. Нужен рендер меньшего размера.",
};
const issueLabels: Record<string, string> = {
  ...blockerLabels,
  inventory_failed: "Не удалось прочитать документ.",
  inventory_incomplete: "Чтение документа завершилось частично.",
  unsupported_document: "Формат документа требует отдельной проверки.",
  inspection_unavailable: "Результат чтения документа недоступен.",
  render_failed: "Не удалось подготовить изображение страницы.",
  render_unavailable: "Изображение страницы недоступно.",
  inventory_pending: "Ожидается проверка объёма документов.",
  model_dispatch_unknown: "Получение задания агентом пока не подтверждено. Повторный запуск требует проверки.",
  model_dispatch_rejected: "Агент не смог принять задание.",
  model_run_failed: "Разбор документа завершился с ошибкой.",
  model_run_interrupted: "Разбор документа прерван.",
  model_run_cancelled: "Разбор документа остановлен.",
  model_proposal_missing: "Агент завершил работу без сохранённого предложения по составу.",
  native_unavailable: "Сервис разбора временно недоступен.",
  model_binding_mismatch: "Результат агента относится к другому заданию и требует проверки.",
  native_session_failed: "Не удалось подготовить рабочий сеанс разбора.",
  model_run_unavailable: "Не удалось проверить состояние задания агента.",
  native_terminal_after_publication: "Предложение сохранено; завершение работы агента требует проверки.",
  analysis_cancelled: "Обработка остановлена сотрудником.",
};

type Action = "start" | "retry" | "cancel";
interface PendingAction {
  action: Action;
  fingerprint: string;
  payload: { plan_id?: string; job_id?: string; request_id?: string };
}

export function IntakeAnalysisPanel(props: Props) {
  return <AnalysisPanel key={props.handoffId} {...props} />;
}

function AnalysisPanel({ handoffId, snapshotId, answersRevision, answersReady }: Props) {
  const [view, setView] = useState<IntakeAnalysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [uncertain, setUncertain] = useState<Action | null>(null);
  const alive = useRef(false);
  const busyRef = useRef(false);
  const sequence = useRef(0);
  const pending = useRef<PendingAction | null>(null);

  const refresh = useCallback(async () => {
    if (busyRef.current) return;
    const serial = ++sequence.current;
    try {
      const next = await getIntakeAnalysis(handoffId);
      if (!alive.current || serial !== sequence.current) return;
      if ((next.plan && next.plan.handoff_id !== handoffId) || (next.job && next.job.handoff_id !== handoffId)) {
        throw new Error("Результат относится к другому заказу.");
      }
      setView(next);
      setLoadError(null);
      if (pending.current?.action === "start" && next.job?.plan_id === pending.current.payload.plan_id) {
        pending.current = null;
        setUncertain(null);
        setError(null);
      }
      if (pending.current?.action === "cancel" && next.job?.status === "cancelled") {
        pending.current = null;
        setUncertain(null);
        setError(null);
      }
      if (next.job?.status === "stale" && next.job.plan_id === next.plan?.plan_id) {
        pending.current = null;
        setUncertain(null);
        setConflict(true);
      }
    } catch (cause) {
      if (alive.current && serial === sequence.current) setLoadError(ownerFacingError(cause, "Не удалось проверить разбор заказа."));
    }
  }, [handoffId]);

  useEffect(() => {
    alive.current = true;
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3_000);
    const focus = () => void refresh();
    window.addEventListener("focus", focus);
    return () => {
      alive.current = false;
      sequence.current += 1;
      window.clearInterval(timer);
      window.removeEventListener("focus", focus);
    };
  }, [refresh]);

  const plan = view?.plan;
  const job = view?.job;
  const planCurrent = Boolean(plan && plan.snapshot_id === snapshotId && plan.answers_revision === answersRevision);
  const active = job?.status === "queued" || job?.status === "running";
  const canPlan = answersReady && answersRevision > 0 && !busy && !active && !uncertain;
  const canStart = Boolean(plan?.can_start && planCurrent && answersReady && !busy && !active && !conflict && (!job || job.plan_id !== plan.plan_id) && (!uncertain || uncertain === "start"));

  async function preparePlan() {
    if (!canPlan || busyRef.current) return;
    busyRef.current = true;
    sequence.current += 1;
    setBusy(true);
    setError(null);
    try {
      const next = await planIntakeAnalysis(handoffId);
      if (!alive.current) return;
      if (next.handoff_id !== handoffId) throw new Error("План относится к другому заказу.");
      setView((previous) => ({ plan: next, job: previous?.job ?? null }));
      setConflict(false);
    } catch (cause) {
      if (alive.current) setError(ownerFacingError(cause, "Не удалось подготовить план. Проверьте сохранение исходных ответов."));
    } finally {
      busyRef.current = false;
      if (alive.current) setBusy(false);
    }
  }

  async function action(kind: Action) {
    if (busyRef.current || (kind === "start" && !canStart)) return;
    if (kind === "cancel" && !job?.cancellable) return;
    if (kind === "retry" && ((!job?.retryable && uncertain !== "retry") || !answersReady || !planCurrent || conflict)) return;
    const target = kind === "start" ? { plan_id: plan!.plan_id } : { job_id: job!.job_id };
    const fingerprint = JSON.stringify([kind, target]);
    if (pending.current && pending.current.fingerprint !== fingerprint) return;
    pending.current ??= { action: kind, fingerprint, payload: { ...target, ...(kind === "cancel" ? {} : { request_id: crypto.randomUUID() }) } };
    busyRef.current = true;
    sequence.current += 1;
    setBusy(true);
    setError(null);
    try {
      const next = await actOnIntakeAnalysis(handoffId, kind, pending.current.payload);
      if (!alive.current) return;
      if (next.handoff_id !== handoffId || (kind === "start" ? next.plan_id !== target.plan_id : next.job_id !== target.job_id)) {
        throw new Error("Результат относится к другому запуску.");
      }
      pending.current = null;
      setUncertain(null);
      setView((previous) => ({ plan: previous?.plan ?? null, job: next }));
    } catch (cause) {
      if (!alive.current) return;
      if (cause instanceof Error && /^409\b/.test(cause.message)) {
        pending.current = null;
        setUncertain(null);
        setConflict(true);
        setError("План или состояние запуска изменились. Проверьте исходные ответы и подготовьте актуальный план.");
        busyRef.current = false;
        await refresh();
      } else {
        setUncertain(kind);
        setError(ownerFacingError(cause, "Результат действия пока неизвестен. Повторите это же действие, чтобы проверить результат."));
      }
    } finally {
      busyRef.current = false;
      if (alive.current) setBusy(false);
    }
  }

  return <section aria-label="Разбор документов" className="space-y-3 border-t border-border pt-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="font-semibold">Разбор документов</h3>
      <button type="button" disabled={!canPlan} className={buttonClass} onClick={() => void preparePlan()}>Подготовить план</button>
    </div>
    {!answersReady && <p className="text-text-secondary">Сохраните исходные ответы для актуального комплекта, чтобы подготовить план и запустить разбор.</p>}
    {!view && !error && !loadError && <p role="status">Проверяем сохранённый разбор…</p>}
    {plan && <div className="space-y-2 rounded-lg bg-muted/30 p-3">
      <p className="font-medium">План разбора</p>
      <p>Документов: {plan.source_ids.length}. Служебных файлов: {plan.service_files}. Готовое чтение: {plan.cached_documents}. Предстоит прочитать: {plan.documents_to_read}.</p>
      <p>Ограничения пилота: до {plan.limits.max_files} документов, до {plan.limits.max_pages_per_pdf} страниц в одном PDF, до {plan.limits.max_total_pdf_pages} страниц PDF на заказ. Для XLSX — до {plan.limits.max_xlsx_cells.toLocaleString("ru-RU")} ячеек.</p>
      {plan.limits.max_source_context_bytes && <p className="text-xs text-text-secondary">До {Math.floor(plan.limits.max_source_context_bytes / 1024)} КиБ извлечённых данных на документ{plan.limits.max_model_image_bytes ? ` и до ${Math.floor(plan.limits.max_model_image_bytes / 1024 / 1024)} МиБ на изображение страницы` : ""}. Превышение проверяется до отправки модели.</p>}
      <p>Известно страниц PDF: {plan.known_pages}.{plan.pages_unknown > 0 && ` У ${plan.pages_unknown} документов число страниц пока неизвестно. После запуска сначала проверим объём; разбор содержания начнётся после проверки ограничений.`}</p>
      <p className="text-text-secondary">Результат: предварительные позиции, количества, материалы, требования и вопросы с источниками.</p>
      {!planCurrent && <p role="status" className="text-warning">План относится к предыдущей версии исходных ответов или комплекта. Подготовьте актуальный план.</p>}
      {plan.blockers.length > 0 && <ul className="list-disc pl-5">{plan.blockers.map((blocker) => <li key={blocker}>{blockerLabels[blocker] ?? "Для запуска требуется проверить ограничения комплекта."}</li>)}</ul>}
      <button type="button" className={`${buttonClass} bg-primary text-primary-foreground`} disabled={!canStart} onClick={() => void action("start")}>{uncertain === "start" ? "Повторить запрос запуска" : "Начать разбор"}</button>
    </div>}
    {job && <div className="space-y-3">
      <div role="status" className="space-y-1">
        <p className="font-medium">{statuses[job.status]} · {stages[job.stage]}</p>
        <p>Предложения сохранены: {job.summary.sources_complete} из {job.summary.sources_total}. Требуют проверки: {job.summary.sources_failed}.</p>
        {job.summary.sources_total > 0 && <progress aria-label="Обработано документов" className="w-full" value={job.summary.sources_complete + job.summary.sources_failed} max={job.summary.sources_total} />}
      </div>
      <p className="text-xs text-text-secondary">Состав предварительный. Подтверждение сотрудником и допуск к калькуляции — следующие этапы.</p>
      {(job.cancellable || job.status === "cancelled") && <p className="text-xs text-text-secondary">Остановка прекращает разбор содержания агентом. Уже запущенное чтение файлов может завершиться; сохранённые результаты чтения остаются в комплекте.</p>}
      <div className="flex flex-wrap gap-2">
        {job.cancellable && <button type="button" disabled={busy || Boolean(uncertain && uncertain !== "cancel")} className={buttonClass} onClick={() => void action("cancel")}>{uncertain === "cancel" ? "Повторить запрос остановки" : "Остановить разбор"}</button>}
        {(job.retryable || uncertain === "retry") && <button type="button" disabled={busy || !answersReady || !planCurrent || conflict || Boolean(uncertain && uncertain !== "retry")} className={buttonClass} onClick={() => void action("retry")}>{uncertain === "retry" ? "Повторить запрос повтора" : "Повторить неудавшиеся документы"}</button>}
      </div>
      {job.issues.length > 0 && <ul className="list-disc space-y-1 pl-5">{job.issues.map((issue) => <li key={`${issue.source_id}:${issue.code}`} className="break-words">{issue.relative_path}: {issueLabels[issue.code] ?? "Обработка документа требует проверки."} {issue.next_action}</li>)}</ul>}
      <IntakeAnalysisResults rows={job.rows} />
    </div>}
    {(error || loadError) && <div role="alert" className="space-y-1"><p>{error || loadError}</p><button type="button" disabled={busy} className={buttonClass} onClick={() => void refresh()}>Обновить состояние разбора</button></div>}
  </section>;
}
