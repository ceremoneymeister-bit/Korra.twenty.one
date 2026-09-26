import type { CronJob, CronJobMutation } from "./api";

/** Delivery is separate from successful preparation; old rows stay unknown. */
export function cronDeliveryLabel(job: CronJob): string | null {
  const labels: Record<string, string> = {
    delivered: "Результат отправлен",
    failed: "Результат не доставлен",
    waiting_decision: "Отправка ожидает решения",
    unknown: "Отправка не подтверждена",
    not_configured: "Нужно настроить получателя",
    suppressed: "Без уведомления",
    suppressed_acked: "Сбой уже отмечен",
    expired: "Результат устарел",
  };
  const outcome = job.latest_execution?.delivery_outcome;
  if (outcome === "not_configured" && job.deliver === "local") return "Результат сохранён в платформе";
  return outcome ? labels[outcome] ?? "Отправка не подтверждена" : null;
}

export interface CronJobFormState {
  name: string;
  prompt: string;
  mode?: "agent" | "reminder";
  schedule: string;
  deliver: string;
  skills: string[];
  provider: string;
  model: string;
  base_url: string;
  script: string;
  no_agent: boolean;
  context_from: string;
  continuity: boolean;
  enabled_toolsets: string[];
  workdir: string;
}

/** Split a comma/newline list (or array) into trimmed, non-empty items. */
export function splitCronList(value: unknown): string[] {
  const items = Array.isArray(value)
    ? value
    : typeof value === "string"
      ? value.split(/[\n,]/)
      : [];
  return items.map((item) => String(item).trim()).filter(Boolean);
}

/** Trim to a non-empty string, or null. Optionally strip trailing slashes
 * (base URLs). Mirrors the backend's `_cron_optional_text`. */
function optionalText(value: string, stripTrailingSlash = false): string | null {
  const text = stripTrailingSlash ? value.trim().replace(/\/+$/, "") : value.trim();
  return text || null;
}

/** Read a stored string field as a plain string ("" when absent). */
function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** Build the create/update payload. Optional fields collapse to null so an
 * update explicitly clears them rather than leaving stale values. */
export function buildCronJobPayload(form: CronJobFormState): CronJobMutation {
  if (form.mode === "reminder") {
    return {
      name: form.name.trim(), reminder: form.prompt.trim(), prompt: "",
      schedule: form.schedule.trim(), deliver: form.deliver.trim() || "local",
      skills: [], script: null, no_agent: false, context_from: null,
      monitor_script: null, monitor_url: null,
      enabled_toolsets: null, workdir: null, provider: null, model: null, base_url: null,
    };
  }
  // The `continuity` toggle is stored as the reserved "self" entry in
  // context_from (the job's own previous output). Users never type "self" —
  // the checkbox is the surface; strip any hand-typed variant first.
  const contextFrom = splitCronList(form.context_from).filter(
    (item) => item.toLowerCase() !== "self",
  );
  if (form.continuity) contextFrom.push("self");
  const enabledToolsets = form.enabled_toolsets.filter(Boolean);
  return {
    name: form.name.trim(),
    prompt: form.prompt.trim(),
    reminder: null,
    schedule: form.schedule.trim(),
    deliver: form.deliver.trim() || "local",
    skills: form.skills.filter(Boolean),
    provider: optionalText(form.provider),
    model: optionalText(form.model),
    base_url: optionalText(form.base_url, true),
    script: optionalText(form.script),
    no_agent: Boolean(form.no_agent),
    context_from: contextFrom.length > 0 ? contextFrom : null,
    enabled_toolsets: enabledToolsets.length > 0 ? enabledToolsets : null,
    workdir: optionalText(form.workdir),
  };
}

export function cronJobHasExecutionContent(
  job: Pick<CronJobMutation, "prompt" | "skills" | "script" | "reminder">,
): boolean {
  const skills = Array.isArray(job.skills) ? job.skills.filter(Boolean) : [];
  return Boolean(asString(job.reminder).trim() || asString(job.prompt).trim() || asString(job.script).trim() || skills.length);
}

export function cronJobFormFromJob(job: CronJob): CronJobFormState {
  const storedRefs = splitCronList(job.context_from);
  // Raw store records carry the reserved "self" entry inside context_from;
  // tool/RPC-formatted records strip it and set an explicit continuity flag.
  const continuity =
    Boolean((job as { continuity?: boolean }).continuity) ||
    storedRefs.some((item) => item.toLowerCase() === "self");
  const externalRefs = storedRefs.filter((item) => item.toLowerCase() !== "self");
  return {
    name: asString(job.name),
    prompt: asString(job.reminder) || asString(job.prompt),
    mode: job.reminder ? "reminder" : "agent",
    schedule:
      asString(job.schedule?.expr) ||
      asString(job.schedule?.run_at) ||
      asString(job.schedule_display),
    deliver: asString(job.deliver) || "local",
    skills: Array.isArray(job.skills) ? job.skills.filter(Boolean) : [],
    provider: asString(job.provider),
    model: asString(job.model),
    base_url: asString(job.base_url),
    script: asString(job.script),
    no_agent: Boolean(job.no_agent),
    context_from: externalRefs.join("\n"),
    continuity,
    enabled_toolsets: splitCronList(job.enabled_toolsets),
    workdir: asString(job.workdir),
  };
}
