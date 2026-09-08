import { useCallback, useEffect, useRef, useState } from "react";
import {
  getIntakePreparation,
  saveIntakeAnswers,
  type IntakeInitialAnswers,
  type IntakePreparation,
  type SaveIntakeAnswersRequest,
} from "@/lib/calc-intake-preparation";
import { ownerFacingError } from "@/lib/owner-facing-error";
import { IntakeAnalysisPanel } from "./IntakeAnalysisPanel";

interface Props {
  handoffId: string;
}

interface FormState {
  data: IntakePreparation;
  answers: IntakeInitialAnswers;
  revision: number;
  dirty: boolean;
  conflict: boolean;
}

const fieldClass = "mt-1 min-h-10 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground disabled:opacity-60";

// Key the complete request/form lifecycle to the order's handoff, including
// when its parent is reused while navigating between chats.
export function IntakePreparationPanel({ handoffId }: Props) {
  return <PreparationForm key={handoffId} handoffId={handoffId} />;
}

function PreparationForm({ handoffId }: Props) {
  const [form, setForm] = useState<FormState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const mounted = useRef(false);
  const savingRef = useRef(false);
  const sequence = useRef(0);
  const pending = useRef<{ fingerprint: string; request: SaveIntakeAnswersRequest } | null>(null);

  const reload = useCallback(async () => {
    if (savingRef.current) return;
    const requestSequence = ++sequence.current;
    try {
      const data = await getIntakePreparation(handoffId);
      if (!mounted.current || requestSequence !== sequence.current) return;
      if (data.handoff_id !== handoffId) throw new Error("Ответ относится к другому заказу.");
      setForm((previous) => {
        if (previous?.dirty || previous?.conflict) {
          return {
            ...previous,
            data,
            conflict: previous.conflict || previous.revision !== data.initial_answers.revision || previous.data.snapshot_id !== data.snapshot_id,
          };
        }
        return { data, answers: data.initial_answers.answers, revision: data.initial_answers.revision, dirty: false, conflict: false };
      });
      setError(null);
    } catch (cause) {
      if (mounted.current && requestSequence === sequence.current) {
        setError(ownerFacingError(cause, "Не удалось загрузить сведения о комплекте."));
      }
    }
  }, [handoffId]);

  useEffect(() => {
    mounted.current = true;
    void reload();
    const interval = window.setInterval(() => void reload(), 30_000);
    const focus = () => void reload();
    window.addEventListener("focus", focus);
    return () => {
      mounted.current = false;
      sequence.current += 1;
      window.clearInterval(interval);
      window.removeEventListener("focus", focus);
    };
  }, [reload]);

  function change<K extends keyof IntakeInitialAnswers>(key: K, value: IntakeInitialAnswers[K]) {
    setForm((previous) => previous ? { ...previous, answers: { ...previous.answers, [key]: value }, dirty: true } : previous);
    setSaved(false);
  }

  async function save() {
    if (!form || savingRef.current || form.conflict || !form.data.editable) return;
    if (form.answers.scope === "selected" && !form.answers.scope_note.trim()) {
      setError("Укажите, какие позиции нужно рассчитать.");
      return;
    }
    const payload = { snapshot_id: form.data.snapshot_id, expected_revision: form.revision, answers: form.answers };
    const fingerprint = JSON.stringify(payload);
    if (pending.current?.fingerprint !== fingerprint) {
      pending.current = { fingerprint, request: { ...payload, request_id: crypto.randomUUID() } };
    }
    savingRef.current = true;
    sequence.current += 1; // A GET started before this save cannot replace its receipt.
    setSaving(true);
    setForm((previous) => previous ? { ...previous, dirty: true } : previous);
    setSaved(false);
    setError(null);
    try {
      const data = await saveIntakeAnswers(handoffId, pending.current.request);
      if (!mounted.current) return;
      if (data.handoff_id !== handoffId || data.snapshot_id !== payload.snapshot_id) {
        throw new Error("Ответ относится к другому комплекту документов.");
      }
      pending.current = null;
      setForm({ data, answers: data.initial_answers.answers, revision: data.initial_answers.revision, dirty: false, conflict: false });
      setSaved(true);
    } catch (cause) {
      if (!mounted.current) return;
      if (cause instanceof Error && /^409\b/.test(cause.message)) {
        pending.current = null;
        setForm((previous) => previous ? { ...previous, conflict: true } : previous);
        savingRef.current = false;
        await reload();
      } else {
        setError(ownerFacingError(cause, "Сохранение не подтверждено. Повторите попытку."));
      }
    } finally {
      savingRef.current = false;
      if (mounted.current) setSaving(false);
    }
  }

  return (
    <details open className="mx-1 mb-2 max-h-[45vh] shrink-0 overflow-auto rounded-xl border border-border bg-background text-sm">
      <summary className="cursor-pointer px-4 py-3 font-semibold">Комплект и исходные ответы</summary>
      <div className="space-y-3 px-4 pb-4">
        {!form && !error && <p role="status">Загружаем сведения о комплекте…</p>}
        {form && <>
          <p>
            Документы для разбора: <strong>{form.data.summary.engineering_documents}</strong>
            {" · "}Служебные файлы: <strong>{form.data.summary.service_files}</strong>
            {" · "}Всего файлов: {form.data.summary.files_total}
          </p>
          <p className="text-text-secondary">
            Сохранённые результаты чтения: {form.data.summary.cached_engineering_documents}.
            {" "}Ещё без результатов: {form.data.summary.documents_without_observation}.
            {(form.data.summary.partial_documents ?? 0) > 0 && ` Прочитаны частично: ${form.data.summary.partial_documents}.`}
            {(form.data.summary.failed_documents ?? 0) > 0 && ` Ошибки чтения: ${form.data.summary.failed_documents}.`}
            {form.data.summary.unsupported_documents > 0 && ` Требуют отдельной проверки формата: ${form.data.summary.unsupported_documents}.`}
          </p>
          {form.data.summary.service_file_names.length > 0 && <p className="break-words text-xs text-text-secondary">Служебные: {form.data.summary.service_file_names.join(", ")}</p>}
          {form.data.summary.classification_pending && <p role="status" className="text-text-secondary">Проверка служебных файлов ожидает завершения. До проверки кандидаты остаются в документах для разбора.</p>}
          {(form.data.summary.classification_issues?.length ?? 0) > 0 && <div className="space-y-1 text-text-secondary">
            <p className="font-medium">Файлы, требующие проверки:</p>
            <ul className="list-disc space-y-1 pl-5">
              {form.data.summary.classification_issues!.map((issue) => <li key={`${issue.source_id}:${issue.reason}`} className="break-words">{issue.relative_path}: {issue.message}</li>)}
            </ul>
          </div>}
          {!form.data.editable && <p role="status" className="text-text-secondary">Ответы доступны для просмотра. Для изменений откройте заказ и проверьте передачу актуального комплекта.</p>}
          <form onSubmit={(event) => { event.preventDefault(); void save(); }}>
            <fieldset disabled={saving || !form.data.editable || form.conflict} className="space-y-3">
              <div className="grid gap-3 md:grid-cols-3">
                <label>Что рассчитываем?
                  <select className={fieldClass} value={form.answers.scope} onChange={(event) => change("scope", event.target.value as IntakeInitialAnswers["scope"])}>
                    <option value="unknown">Пока не определено</option>
                    <option value="whole">Весь заказ</option>
                    <option value="selected">Отдельные позиции</option>
                  </select>
                </label>
                <label>Будут ещё документы?
                  <select className={fieldClass} value={form.answers.more_documents} onChange={(event) => change("more_documents", event.target.value as IntakeInitialAnswers["more_documents"])}>
                    <option value="unknown">Пока неизвестно</option>
                    <option value="no">Дополнений не ожидается</option>
                    <option value="yes">Да, будут дополнения</option>
                  </select>
                </label>
                <label>Где указано количество?
                  <select className={fieldClass} value={form.answers.quantity_source} onChange={(event) => change("quantity_source", event.target.value as IntakeInitialAnswers["quantity_source"])}>
                    <option value="unknown">Нужно уточнить</option>
                    <option value="in_documents">В документах</option>
                    <option value="separate">Сообщу отдельно</option>
                  </select>
                </label>
              </div>
              {(form.answers.scope === "selected" || form.answers.scope_note) && <label className="block">Какие позиции включить в расчёт?
                <textarea className={fieldClass} rows={2} maxLength={2000} required={form.answers.scope === "selected"} value={form.answers.scope_note} onChange={(event) => change("scope_note", event.target.value)} />
              </label>}
              <label className="block">Дополнения к заказу
                <textarea className={fieldClass} rows={2} maxLength={2000} placeholder="Количество, приоритеты или другие исходные сведения" value={form.answers.notes} onChange={(event) => change("notes", event.target.value)} />
              </label>
              <button type="submit" disabled={saving || !form.data.editable || form.conflict} className="min-h-10 rounded-lg bg-primary px-4 py-2 font-semibold text-primary-foreground disabled:opacity-50">
                {saving ? "Сохраняем…" : "Сохранить ответы"}
              </button>
            </fieldset>
          </form>
          {form.conflict && <div role="alert" className="space-y-2">
            <p>Ответы или комплект уже изменились. Ваш черновик сохранён на экране. Загрузите актуальные ответы перед следующим изменением.</p>
            <button type="button" disabled={saving || Boolean(error)} className="min-h-10 rounded-lg border border-border px-3 font-semibold disabled:opacity-50" onClick={() => {
              pending.current = null;
              setForm({ data: form.data, answers: form.data.initial_answers.answers, revision: form.data.initial_answers.revision, dirty: false, conflict: false });
              setSaved(false);
            }}>Загрузить сохранённые ответы</button>
          </div>}
          {(saved || (!form.dirty && form.data.initial_answers.receipt)) && <p role="status" className="text-primary">Ответы сохранены для этого комплекта.</p>}
          <IntakeAnalysisPanel handoffId={handoffId} snapshotId={form.data.snapshot_id} answersRevision={form.revision} answersReady={form.data.editable && !form.dirty && !form.conflict && !saving && form.revision > 0} />
        </>}
        {error && <div role="alert"><p>{error}</p><button type="button" className="min-h-10 text-primary" onClick={() => void reload()}>Обновить сведения</button></div>}
      </div>
    </details>
  );
}
