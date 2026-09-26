import { useEffect, useState } from "react";
import { api, type CronJob, type CronJobHistory } from "@/lib/api";
import { agentChatHref } from "@/lib/agent-tabs";
import { cronDeliveryLabel } from "@/lib/cron-job";
import { getOwnerTimeZone } from "@/lib/dashboard-flags";

/** History is fetched only when opened, scoped to one task and one profile. */
export function CronHistory({ job }: { job: CronJob }) {
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState<CronJobHistory | null>(null);
  const [error, setError] = useState(false);
  const profile = job.profile || "default";

  useEffect(() => {
    if (!open) return;
    let active = true;
    setHistory(null);
    setError(false);
    api.getCronJobHistory(job.id, profile).then(
      (result) => { if (active) setHistory(result); },
      () => { if (active) setError(true); },
    );
    return () => { active = false; };
  }, [open, job.id, profile, job.last_run_at]);

  return (
    <details className="mt-3 text-xs" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="cursor-pointer text-muted-foreground">История выполнения</summary>
      {open && (
        <div className="mt-2 space-y-2" aria-live="polite">
          {error ? <p>Не удалось загрузить историю. Закройте и откройте её снова.</p> : !history ? <p>Загружаю…</p> : <>
            {history.executions.length === 0 && <p>Сохранённых сведений о выполнении пока нет.</p>}
            {history.executions.map((run) => (
              <div key={run.id} className="flex flex-wrap gap-x-3 gap-y-1">
                {run.result_text && <p className="w-full whitespace-pre-wrap break-words">{run.result_text}</p>}
                <time dateTime={run.claimed_at}>{run.claimed_at ? new Date(run.claimed_at).toLocaleString("ru-RU", { timeZone: getOwnerTimeZone() }) : "—"}</time>
                <span>{(run.source === "missed" ? "Пропущено" : ({ claimed: "Ожидает запуска", running: "Выполняется", completed: "Выполнено", failed: "Ошибка выполнения", unknown: "Выполнение не подтверждено" } as Record<string, string>)[run.status] || "Статус неизвестен")}</span>
                <span className="text-muted-foreground">{cronDeliveryLabel({ ...job, latest_execution: run }) || "Сведения о доставке не сохранены"}</span>
              </div>
            ))}
            {history.runs.length > 0 && <div className="pt-2">
              <p className="mb-1 text-muted-foreground">Подробности работы агента</p>
              {history.runs.map((run) => <a key={run.id} className="block underline py-1" href={agentChatHref(profile, run.id)}>{run.title || "Открыть выполнение"}</a>)}
            </div>}
          </>}
        </div>
      )}
    </details>
  );
}
