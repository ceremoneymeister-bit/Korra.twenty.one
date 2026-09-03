import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { KorraLoader } from "@/components/KorraLoader";
import {
  type CronTriggerController,
  createCronTriggerController,
} from "@hermes/shared";
import { Clock, Pause, Pencil, Play, Trash2, X, Zap } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@/components/ProductButton";
import { Select, SelectOption } from "@nous-research/ui/ui/components/select";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { api } from "@/lib/api";
import type {
  CronJob,
  CronDeliveryTarget,
  ModelOptionsResponse,
  ProfileInfo,
  SkillInfo,
  ToolsetInfo,
} from "@/lib/api";
import {
  buildCronJobPayload,
  cronJobHasExecutionContent,
  cronJobFormFromJob,
  type CronJobFormState,
} from "@/lib/cron-job";
import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";
import {
  DEFAULT_SCHEDULE_STATE,
  ScheduleBuilder,
} from "@/components/ScheduleBuilder";
import {
  buildScheduleString,
  describeSchedule,
  englishOrdinal,
  parseScheduleString,
  type ScheduleBuilderState,
  type ScheduleDescribeStrings,
} from "@/lib/schedule";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { useConfirmDelete } from "@nous-research/ui/hooks/use-confirm-delete";
import { useModalBehavior } from "@/hooks/useModalBehavior";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import { useI18n } from "@/i18n";
import { usePageHeader } from "@/contexts/usePageHeader";
import { PluginSlot } from "@/plugins";
import { Segmented } from "@nous-research/ui/ui/components/segmented";
import { AutomationBlueprints } from "@/components/AutomationBlueprints";
import { getOwnerTimeZone, getScheduleTimeZone, isProductUiMode } from "@/lib/dashboard-flags";
import { ownerFacingError } from "@/lib/owner-facing-error";
import { cn, themedBody } from "@/lib/utils";

const OWNER_TIME_ZONE = getOwnerTimeZone();
const SCHEDULE_TIME_ZONE = getScheduleTimeZone();

function formatTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("ru-RU", {
    timeZone: OWNER_TIME_ZONE,
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function ownerTimeZoneLabel(): string {
  try {
    const zone = new Intl.DateTimeFormat("ru-RU", {
      timeZone: OWNER_TIME_ZONE,
      timeZoneName: "shortOffset",
    }).formatToParts(new Date()).find((part) => part.type === "timeZoneName")?.value;
    return zone ? `${OWNER_TIME_ZONE} (${zone.replace("GMT", "UTC")})` : OWNER_TIME_ZONE;
  } catch {
    return OWNER_TIME_ZONE;
  }
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function truncateText(value: string, maxLength: number): string {
  return value.length > maxLength
    ? value.slice(0, maxLength) + "..."
    : value;
}

function getJobPrompt(job: CronJob): string {
  return asText(job.prompt);
}

function NameCheckboxPicker({
  id,
  available,
  selected,
  onChange,
  emptyLabel,
}: {
  id: string;
  available: Array<{ name: string; description?: string | null }>;
  selected: string[];
  onChange: (names: string[]) => void;
  emptyLabel: string;
}) {
  const names = available.map((item) => item.name);
  const orphaned = selected.filter((s) => !names.includes(s));
  const all = [...orphaned.map((name) => ({ name, description: "" })), ...available];

  if (all.length === 0) {
    return <p className="text-xs text-muted-foreground">{emptyLabel}</p>;
  }

  const toggle = (name: string, checked: boolean) => {
    if (checked) onChange([...selected, name]);
    else onChange(selected.filter((s) => s !== name));
  };

  return (
    <div
      id={id}
      className="max-h-36 overflow-y-auto border border-border bg-background/40 p-1"
    >
      {all.map((item) => (
        <label
          key={item.name}
          className="flex cursor-pointer items-center gap-2 px-2 py-1 text-xs hover:bg-muted/40"
          title={item.description || undefined}
        >
          <input
            type="checkbox"
            className="accent-foreground"
            checked={selected.includes(item.name)}
            onChange={(e) => toggle(item.name, e.target.checked)}
          />
          <span className="font-mono-ui truncate">{item.name}</span>
        </label>
      ))}
    </div>
  );
}

interface CronJobEditorState extends CronJobFormState {
  scheduleState: ScheduleBuilderState;
}

interface CronJobFormResources {
  availableSkills: SkillInfo[];
  availableToolsets: ToolsetInfo[];
  modelOptions: ModelOptionsResponse | null;
  deliveryTargets: CronDeliveryTarget[];
}

function emptyCronJobForm(): CronJobEditorState {
  return {
    name: "",
    prompt: "",
    schedule: "",
    deliver: "local",
    skills: [],
    provider: "",
    model: "",
    base_url: "",
    script: "",
    no_agent: false,
    context_from: "",
    continuity: false,
    enabled_toolsets: [],
    workdir: "",
    scheduleState: { ...DEFAULT_SCHEDULE_STATE },
  };
}

function editorFormFromJob(job: CronJob): CronJobEditorState {
  const form = cronJobFormFromJob(job);
  return { ...form, scheduleState: parseScheduleString(form.schedule) };
}

function buildCronJobPayloadFromEditor(form: CronJobEditorState) {
  const { scheduleState, ...payloadForm } = form;
  return buildCronJobPayload({
    ...payloadForm,
    schedule: buildScheduleString(scheduleState),
  });
}

function selectOptions(
  current: string,
  options: Array<{ value: string; label: string }>,
) {
  const known = new Set(options.map((option) => option.value));
  return [
    ...options.map((option) => (
      <SelectOption key={option.value} value={option.value}>
        {option.label}
      </SelectOption>
    )),
    ...(current && !known.has(current)
      ? [
          <SelectOption key={current} value={current}>
            {current}
          </SelectOption>,
        ]
      : []),
  ];
}

function CronAdvancedFields({
  idPrefix,
  form,
  onChange,
  modelOptions,
  availableToolsets,
}: {
  idPrefix: string;
  form: CronJobEditorState;
  onChange: (form: CronJobEditorState) => void;
  modelOptions: ModelOptionsResponse | null;
  availableToolsets: ToolsetInfo[];
}) {
  const { tr } = useI18n();
  const update = <K extends keyof CronJobEditorState,>(
    key: K,
    next: CronJobEditorState[K],
  ) => {
    onChange({ ...form, [key]: next });
  };

  const providers = (modelOptions?.providers ?? []).filter(
    (p) => p.authenticated !== false,
  );
  const selectedProvider = providers.find((p) => p.slug === form.provider);
  const models = selectedProvider?.models ?? [];

  return (
    <details className="border border-border bg-background/30 p-3" open>
      <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {tr("Advanced fields")}
      </summary>
      <div className="mt-3 grid gap-3">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="grid gap-1">
            <Label htmlFor={`${idPrefix}-provider`}>{tr("Provider")}</Label>
            <Select
              id={`${idPrefix}-provider`}
              value={form.provider}
              onValueChange={(v) => {
                onChange({ ...form, provider: v, model: "" });
              }}
            >
              <SelectOption value="">{tr("Default")}</SelectOption>
              {selectOptions(
                form.provider,
                providers.map((p) => ({ value: p.slug, label: p.name })),
              )}
            </Select>
          </div>
          <div className="grid gap-1">
            <Label htmlFor={`${idPrefix}-model`}>{tr("Model")}</Label>
            <Select
              id={`${idPrefix}-model`}
              value={form.model}
              onValueChange={(v) => update("model", v)}
            >
              <SelectOption value="">{tr("Default")}</SelectOption>
              {selectOptions(
                form.model,
                models.map((model) => ({ value: model, label: model })),
              )}
            </Select>
          </div>
        </div>

        <div className="grid gap-1">
          <Label htmlFor={`${idPrefix}-base-url`}>{tr("Base URL override")}</Label>
          <Input
            id={`${idPrefix}-base-url`}
            placeholder="https://api.example.com/v1"
            value={form.base_url}
            onChange={(e) => update("base_url", e.target.value)}
          />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 items-end">
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              className="accent-foreground"
              checked={form.no_agent}
              onChange={(e) => update("no_agent", e.target.checked)}
            />
            no_agent: {tr("run the script only and deliver stdout verbatim")}
          </label>
          <div className="grid gap-1">
            <Label htmlFor={`${idPrefix}-script`}>{tr("Script")}</Label>
            <Input
              id={`${idPrefix}-script`}
              value={form.script}
              onChange={(e) => update("script", e.target.value)}
              placeholder="relative/path/in/scripts"
            />
          </div>
        </div>

        <div className="grid gap-1">
          <Label htmlFor={`${idPrefix}-workdir`}>{tr("Workdir")}</Label>
          <Input
            id={`${idPrefix}-workdir`}
            value={form.workdir}
            onChange={(e) => update("workdir", e.target.value)}
            placeholder="/absolute/project/path"
          />
        </div>

        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            className="accent-foreground"
            checked={form.continuity}
            onChange={(e) => update("continuity", e.target.checked)}
          />
          continuity: {tr("each run sees the previous run's output (dedupe, pick up where it left off)")}
        </label>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="grid gap-1">
            <Label htmlFor={`${idPrefix}-context-from`}>context_from {tr("job IDs")}</Label>
            <textarea
              id={`${idPrefix}-context-from`}
              className="flex min-h-[64px] w-full border border-border bg-background/40 px-3 py-2 text-xs font-courier shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-foreground/30 focus-visible:border-foreground/25"
              placeholder={tr("one job id per line")}
              value={form.context_from}
              onChange={(e) => update("context_from", e.target.value)}
            />
          </div>
          <div className="grid gap-1">
            <Label htmlFor={`${idPrefix}-toolsets`}>enabled_toolsets</Label>
            <NameCheckboxPicker
              id={`${idPrefix}-toolsets`}
              available={availableToolsets}
              selected={form.enabled_toolsets}
              onChange={(v) => update("enabled_toolsets", v)}
              emptyLabel={tr("No toolsets available.")}
            />
          </div>
        </div>
      </div>
    </details>
  );
}

interface CronJobFormFieldsProps {
  idPrefix: string;
  autoFocus?: boolean;
  ownerMode?: boolean;
  form: CronJobEditorState;
  resources: CronJobFormResources;
  onChange: (form: CronJobEditorState) => void;
}

function CronJobFormFields({
  idPrefix,
  autoFocus,
  ownerMode = false,
  form,
  resources,
  onChange,
}: CronJobFormFieldsProps) {
  const { t, tr } = useI18n();
  const { availableSkills, availableToolsets, deliveryTargets, modelOptions } = resources;
  const update = <K extends keyof CronJobEditorState,>(
    key: K,
    next: CronJobEditorState[K],
  ) => {
    onChange({ ...form, [key]: next });
  };
  const onlyLocalAvailable =
    deliveryTargets.filter((target) => target.id !== "local").length === 0;

  const deliveryOptions = selectOptions(
    form.deliver,
    deliveryTargets.map((target) => {
      const base = target.id === "local" ? t.cron.delivery.local : target.name;
      if (target.id !== "local" && !target.home_target_set) {
        const hint = t.cron.delivery.needsHomeChannel ?? tr("set a home channel first");
        return { value: target.id, label: `${base} — ${hint}` };
      }
      return { value: target.id, label: base };
    }),
  );

  return (
    <>
      <div className="grid gap-2">
        <Label htmlFor={`${idPrefix}-name`}>
          {ownerMode ? "Название задачи" : t.cron.nameOptional}
        </Label>
        <Input
          id={`${idPrefix}-name`}
          autoFocus={autoFocus}
          required={ownerMode}
          placeholder={t.cron.namePlaceholder}
          value={form.name}
          onChange={(e) => update("name", e.target.value)}
        />
      </div>

      <div className="grid gap-2">
        <Label htmlFor={`${idPrefix}-prompt`}>{t.cron.prompt}</Label>
        <textarea
          id={`${idPrefix}-prompt`}
          required
          className="flex min-h-[80px] w-full border border-border bg-background/40 px-3 py-2 text-sm font-courier shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-foreground/30 focus-visible:border-foreground/25"
          placeholder={t.cron.promptPlaceholder}
          value={form.prompt}
          onChange={(e) => update("prompt", e.target.value)}
        />
      </div>

      <ScheduleBuilder
        value={form.scheduleState}
        onChange={(state) => update("scheduleState", state)}
      />

      <div className="grid gap-2">
        <Label htmlFor={`${idPrefix}-deliver`}>{t.cron.deliverTo}</Label>
        <Select
          id={`${idPrefix}-deliver`}
          value={form.deliver}
          onValueChange={(v) => update("deliver", v)}
        >
          {deliveryOptions}
        </Select>
        {onlyLocalAvailable && (
          <p className="text-xs text-muted-foreground">
            {t.cron.delivery.noneConfigured ??
              tr("No messaging platforms configured. Set one up under Channels to deliver reports.")}
          </p>
        )}
      </div>

      {!ownerMode && <div className="grid gap-2">
        <Label htmlFor={`${idPrefix}-skills`}>{tr("Skills (optional)")}</Label>
        <NameCheckboxPicker
          id={`${idPrefix}-skills`}
          available={availableSkills}
          selected={form.skills}
          onChange={(skills) => update("skills", skills)}
          emptyLabel={tr("No skills installed for this profile.")}
        />
        <p className="text-xs text-muted-foreground">
          {tr("Selected skills are loaded before the prompt runs — the cron sets when, the skill sets how.")}
        </p>
      </div>}

      {!ownerMode && (
        <CronAdvancedFields
          idPrefix={`${idPrefix}-advanced`}
          form={form}
          onChange={onChange}
          modelOptions={modelOptions}
          availableToolsets={availableToolsets}
        />
      )}
    </>
  );
}

function getJobName(job: CronJob): string {
  return asText(job.name).trim();
}

function getJobTitle(job: CronJob): string {
  const name = getJobName(job);
  if (name) return name;

  const prompt = getJobPrompt(job);
  if (prompt) return truncateText(prompt, 60);

  const script = asText(job.script);
  if (script) return truncateText(script, 60);

  return job.id || "Задача расписания";
}

function getJobScheduleDisplay(
  job: CronJob,
  strings: ScheduleDescribeStrings,
): string {
  // Prefer a structured render so cron expressions like
  // ``30 14 * * 1,3,5`` surface as "Weekly on Mon, Wed, Fri at 14:30"
  // in the list instead of the raw five-field gibberish. Falls back
  // through the existing chain (``schedule_display`` from the backend,
  // then the structured ``display`` field, then the raw ``expr``) so
  // legacy job rows still render *something* meaningful.
  return describeSchedule(
    job.schedule,
    asText(job.schedule_display) || asText(job.schedule?.display),
    strings,
  );
}

function getJobState(job: CronJob): string {
  return asText(job.state) || (job.enabled === false ? "disabled" : "scheduled");
}

function getRepeatDisplay(job: CronJob): string {
  const repeat = job.repeat;
  if (!repeat || repeat.times == null) return "без ограничения";
  const completed = repeat.completed ?? 0;
  return completed > 0 ? `${completed}/${repeat.times}` : `${repeat.times} запусков`;
}

function getJobMode(job: CronJob): string {
  if (job.no_agent) return "no_agent";
  if (job.script) return "script+agent";
  return "agent";
}

function getModelDisplay(job: CronJob): string {
  const provider = asText(job.provider);
  const model = asText(job.model);
  if (provider && model) return `${provider}/${model}`;
  return model || provider;
}

function getJobProfile(job: CronJob): string {
  return asText(job.profile) || asText(job.profile_name) || "default";
}

function getJobKey(job: CronJob): string {
  return `${getJobProfile(job)}:${job.id}`;
}

function splitJobKey(key: string): { profile: string; id: string } {
  const idx = key.indexOf(":");
  if (idx === -1) return { profile: "default", id: key };
  return { profile: key.slice(0, idx) || "default", id: key.slice(idx + 1) };
}

function profileLabel(profile: string): string {
  return profile === "default" ? "Основной" : profile;
}

const STATUS_TONE: Record<string, "success" | "warning" | "destructive"> = {
  enabled: "success",
  scheduled: "success",
  paused: "warning",
  error: "destructive",
  completed: "success",
};

const STATUS_LABEL: Record<string, string> = {
  enabled: "Включено",
  scheduled: "Запланировано",
  paused: "Приостановлено",
  disabled: "Отключено",
  error: "Ошибка",
  completed: "Завершено",
};

export default function CronPage() {
  const clientMode = isProductUiMode();
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [triggeringJobKeys, setTriggeringJobKeys] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const triggerControllerRef = useRef<CronTriggerController | null>(null);

  useEffect(() => {
    const controller = createCronTriggerController((key, running) => {
      if (triggerControllerRef.current !== controller) return;
      setTriggeringJobKeys((current) => {
        const next = new Set(current);
        if (running) next.add(key);
        else next.delete(key);
        return next;
      });
    });
    triggerControllerRef.current = controller;

    return () => {
      triggerControllerRef.current = null;
    };
  }, []);
  const [profiles, setProfiles] = useState<ProfileInfo[]>([]);
  const [selectedProfile, setSelectedProfile] = useState("all");
  const [view, setView] = useState<"jobs" | "blueprints">("jobs");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { toast, showToast } = useToast();
  const { t, locale, tr } = useI18n();
  const { setEnd } = usePageHeader();

  // Translation surface for the human-readable schedule describer.
  // English ordinals are a special case ("1st", "2nd", "23rd"); every
  // other locale falls back to the plain numeric form, which avoids
  // shipping incorrect grammar (e.g. naive "1th"/"2th" suffixes that
  // don't exist in most languages).
  //
  // Built inline (not memoized) — the cron page renders a small job
  // list, this is single-digit microseconds, and a useMemo here would
  // just add boilerplate.
  const scheduleDescribeStrings: ScheduleDescribeStrings = {
    ...t.cron.scheduleDescribe,
    weekdaysShort: t.cron.scheduleModes.weekdaysShort,
    ordinal: locale === "en" ? englishOrdinal : (n: number) => String(n),
  };

  // New job modal state
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [createProfile, setCreateProfile] = useState("default");
  const [createForm, setCreateForm] = useState<CronJobEditorState>(
    emptyCronJobForm,
  );
  const [createRequestId, setCreateRequestId] = useState("");
  const openCreateModal = useCallback(() => {
    setCreateRequestId(
      globalThis.crypto?.randomUUID?.() ??
        `owner-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    );
    setCreateModalOpen(true);
  }, []);
  const closeCreateModal = useCallback(() => setCreateModalOpen(false), []);
  const createModalRef = useModalBehavior({
    open: createModalOpen,
    onClose: closeCreateModal,
  });
  const [deliveryTargets, setDeliveryTargets] = useState<CronDeliveryTarget[]>([
    { id: "local", name: "Local", home_target_set: true, home_env_var: null },
  ]);
  const [creating, setCreating] = useState(false);

  // Edit job modal state
  const [editJob, setEditJob] = useState<CronJob | null>(null);
  const [editForm, setEditForm] = useState<CronJobEditorState>(
    emptyCronJobForm,
  );
  const [saving, setSaving] = useState(false);
  const [resumeJob, setResumeJob] = useState<CronJob | null>(null);
  const [resumeConfirmation, setResumeConfirmation] = useState("");
  const [resuming, setResuming] = useState(false);
  const closeEditModal = useCallback(() => setEditJob(null), []);
  const editModalRef = useModalBehavior({
    open: editJob !== null,
    onClose: closeEditModal,
  });
  const closeResumeModal = useCallback(() => {
    if (!resuming) setResumeJob(null);
  }, [resuming]);
  const resumeModalRef = useModalBehavior({
    open: resumeJob !== null,
    onClose: closeResumeModal,
  });

  // Skills installed in the profile a job will run under, for the
  // attach-skill selector (parity with `hermes cron edit --add-skill`).
  // Keyed on the create-modal profile; the edit modal reuses the list —
  // a job's current skills are always shown even if not in it.
  const [availableSkills, setAvailableSkills] = useState<SkillInfo[]>([]);
  const [availableToolsets, setAvailableToolsets] = useState<ToolsetInfo[]>([]);
  const [modelOptions, setModelOptions] = useState<ModelOptionsResponse | null>(null);

  const resourceProfile = editJob ? getJobProfile(editJob) : createProfile;

  const openEditModal = useCallback((job: CronJob) => {
    setEditJob(job);
    setEditForm(editorFormFromJob(job));
  }, []);

  const selectedProfileRef = useRef(selectedProfile);
  const jobsRequestGenerationRef = useRef(0);
  const jobsActiveRef = useRef(false);

  const loadJobs = useCallback((profile: string = selectedProfile) => {
    if (!jobsActiveRef.current || selectedProfileRef.current !== profile) return;

    const generation = ++jobsRequestGenerationRef.current;
    setLoading(true);
    setError(null);

    const request = clientMode
      ? api.getOwnerCronJobs()
      : api.getCronJobs(profile);
    request
      .then((nextJobs) => {
        if (
          jobsRequestGenerationRef.current === generation &&
          selectedProfileRef.current === profile
        ) setJobs(nextJobs);
      })
      .catch((exception) => {
        if (
          jobsRequestGenerationRef.current === generation &&
          selectedProfileRef.current === profile
        ) {
          const message = ownerFacingError(
            exception,
            "Не удалось загрузить расписание.",
          );
          setError(message);
          showToast(message, "error");
        }
      })
      .finally(() => {
        if (
          jobsRequestGenerationRef.current === generation &&
          selectedProfileRef.current === profile
        ) setLoading(false);
      });
  }, [clientMode, selectedProfile, showToast]);

  useEffect(() => {
    if (clientMode) return;
    api
      .getProfiles()
      .then((res) => setProfiles(res.profiles))
      .catch(() => setProfiles([]));
  }, [clientMode]);

  useEffect(() => {
    const request = clientMode
      ? api.getOwnerCronDeliveryTargets()
      : api.getCronDeliveryTargets();
    request
      .then((res) => setDeliveryTargets(res.targets))
      .catch(() =>
        // Fall back to local-only so the modal still works if the endpoint fails.
        setDeliveryTargets([
          { id: "local", name: "Локально", home_target_set: true, home_env_var: null },
        ]),
      );
  }, [clientMode]);

  useEffect(() => {
    jobsActiveRef.current = true;
    selectedProfileRef.current = selectedProfile;
    loadJobs(selectedProfile);

    return () => {
      jobsActiveRef.current = false;
      jobsRequestGenerationRef.current += 1;
    };
  }, [loadJobs, selectedProfile]);

  // Load resources from the profile the create/edit form actually targets.
  // Pass "default" explicitly so the global dashboard profile switch cannot
  // redirect a default-profile cron form to some other profile.
  useEffect(() => {
    if (clientMode) return;
    let cancelled = false;
    Promise.all([
      api.getSkills(resourceProfile).catch(() => []),
      api.getToolsets(resourceProfile).catch(() => []),
      api.getModelOptions(resourceProfile).catch(() => null),
    ]).then(([skills, toolsets, options]) => {
      if (cancelled) return;
      setAvailableSkills([...skills].sort((a, b) => a.name.localeCompare(b.name)));
      setAvailableToolsets([...toolsets].sort((a, b) => a.name.localeCompare(b.name)));
      setModelOptions(options);
    });
    return () => {
      cancelled = true;
    };
  }, [clientMode, resourceProfile]);

  const handleCreate = async () => {
    const payload = buildCronJobPayloadFromEditor(createForm);
    if (
      !payload.schedule ||
      (!payload.no_agent && !cronJobHasExecutionContent(payload))
    ) {
      showToast(
        clientMode
          ? "Заполните действие Корры и расписание."
          : `${t.cron.prompt} & ${t.cron.schedule} required`,
        "error",
      );
      return;
    }
    if (clientMode && !payload.name?.trim()) {
      showToast("Укажите понятное название задачи.", "error");
      return;
    }
    if (payload.no_agent && !payload.script) {
      showToast(tr("no_agent jobs require a script"), "error");
      return;
    }
    setCreating(true);
    try {
      if (clientMode) {
        await api.createOwnerCronJob({
          request_id: createRequestId,
          name: payload.name?.trim() ?? "",
          prompt: payload.prompt?.trim() ?? "",
          schedule: payload.schedule ?? "",
          deliver: payload.deliver ?? "local",
        });
      } else {
        await api.createCronJob(payload, createProfile);
      }
      showToast(
        clientMode
          ? "Черновик добавлен и пока не запускается."
          : t.common.create + " ✓",
        "success",
      );
      setCreateForm(emptyCronJobForm());
      setCreateRequestId("");
      setCreateModalOpen(false);
      loadJobs(selectedProfile);
    } catch (e) {
      showToast(ownerFacingError(e, "Не удалось добавить задачу."), "error");
    } finally {
      setCreating(false);
    }
  };

  const handleEdit = async () => {
    if (!editJob) return;
    const payload = buildCronJobPayloadFromEditor(editForm);
    if (
      !payload.schedule ||
      (!payload.no_agent && !cronJobHasExecutionContent(payload))
    ) {
      showToast(
        clientMode
          ? "Заполните действие Корры и расписание."
          : `${t.cron.prompt} & ${t.cron.schedule} required`,
        "error",
      );
      return;
    }
    if (clientMode && !payload.name?.trim()) {
      showToast("Укажите понятное название задачи.", "error");
      return;
    }
    if (payload.no_agent && !payload.script) {
      showToast(tr("no_agent jobs require a script"), "error");
      return;
    }
    setSaving(true);
    try {
      if (clientMode) {
        if (!editJob.revision) throw new Error("Обновите страницу и повторите.");
        await api.updateOwnerCronJob(editJob.id, {
          expected_revision: editJob.revision,
          name: payload.name?.trim() ?? "",
          prompt: payload.prompt?.trim() ?? "",
          schedule: payload.schedule ?? "",
          deliver: payload.deliver ?? "local",
        });
      } else {
        await api.updateCronJob(
          editJob.id,
          payload,
          getJobProfile(editJob),
        );
      }
      showToast(clientMode ? "Изменения сохранены." : tr("Saved changes ✓"), "success");
      setEditJob(null);
      loadJobs(selectedProfile);
    } catch (e) {
      showToast(ownerFacingError(e, "Не удалось сохранить изменения."), "error");
    } finally {
      setSaving(false);
    }
  };

  const handlePauseResume = async (job: CronJob) => {
    try {
      const isPaused = getJobState(job) === "paused";
      if (clientMode && isPaused) {
        setResumeJob(job);
        setResumeConfirmation("");
        return;
      }
      const profile = getJobProfile(job);
      if (clientMode) {
        if (!job.revision) throw new Error("Обновите страницу и повторите.");
        await api.pauseOwnerCronJob(job.id, job.revision);
        showToast(
          `Новые запуски задачи «${truncateText(getJobTitle(job), 30)}» приостановлены. Уже начатая задача может завершиться.`,
          "success",
        );
      } else if (isPaused) {
        await api.resumeCronJob(job.id, profile);
        showToast(
          `${t.cron.resume}: "${truncateText(getJobTitle(job), 30)}"`,
          "success",
        );
      } else {
        await api.pauseCronJob(job.id, profile);
        showToast(
          `${t.cron.pause}: "${truncateText(getJobTitle(job), 30)}"`,
          "success",
        );
      }
      loadJobs(selectedProfile);
    } catch (e) {
      showToast(ownerFacingError(e, "Не удалось изменить состояние задачи."), "error");
      if (clientMode) loadJobs(selectedProfile);
    }
  };

  const handleConfirmedResume = async () => {
    if (!resumeJob?.revision) return;
    setResuming(true);
    try {
      await api.resumeOwnerCronJob(
        resumeJob.id,
        resumeJob.revision,
        resumeConfirmation,
      );
      showToast(
        `Задача «${truncateText(getJobTitle(resumeJob), 30)}» включена.`,
        "success",
      );
      setResumeJob(null);
      setResumeConfirmation("");
      loadJobs(selectedProfile);
    } catch (e) {
      showToast(ownerFacingError(e, "Не удалось включить задачу."), "error");
      loadJobs(selectedProfile);
    } finally {
      setResuming(false);
    }
  };

  const handleTrigger = async (job: CronJob) => {
    const jobKey = getJobKey(job);
    const label = `${t.cron.triggerNow}: "${truncateText(getJobTitle(job), 30)}"`;
    const viewProfile = selectedProfile;
    const controller = triggerControllerRef.current;

    if (!controller) return;

    try {
      // No pre-request toast: the controller's running state already gives
      // immediate in-progress feedback (disabled + spinning action), and a
      // success-styled toast before the HTTP response would claim a result
      // the request has not produced yet. Terminal feedback only.
      const result = await controller.run(
        jobKey,
        () => api.triggerCronJob(job.id, getJobProfile(job)),
      );

      if (
        triggerControllerRef.current !== controller ||
        selectedProfileRef.current !== viewProfile ||
        !result.started
      ) return;

      showToast(`${label} ✓`, "success");
      loadJobs(viewProfile);
    } catch (e) {
      if (
        triggerControllerRef.current === controller &&
        selectedProfileRef.current === viewProfile
      ) {
        showToast(ownerFacingError(e, "Не удалось запустить задачу."), "error");
      }
    }
  };

  const jobDelete = useConfirmDelete({
    onDelete: useCallback(
      async (key: string) => {
        const { profile, id } = splitJobKey(key);
        const job = jobs.find((j) => getJobKey(j) === key);
        try {
          if (clientMode) {
            if (!job?.revision) throw new Error("Обновите страницу и повторите.");
            await api.archiveOwnerCronJob(id, job.revision);
          } else {
            await api.deleteCronJob(id, profile);
          }
          showToast(
            clientMode
              ? `Задача «${job ? truncateText(getJobTitle(job), 30) : id}» убрана из расписания.`
              : `${t.common.delete}: "${job ? truncateText(getJobTitle(job), 30) : id}"`,
            "success",
          );
          loadJobs(selectedProfile);
        } catch (e) {
          showToast(ownerFacingError(e, "Не удалось убрать задачу из расписания."), "error");
          if (clientMode) loadJobs(selectedProfile);
          throw e;
        }
      },
      [clientMode, jobs, loadJobs, selectedProfile, showToast, t.common.delete],
    ),
  });

  // Put "Create" button in page header
  useLayoutEffect(() => {
    setEnd(
      <Button
        className={clientMode ? undefined : "uppercase"}
        size="sm"
        onClick={() => {
          setCreateProfile(selectedProfile === "all" ? "default" : selectedProfile);
          openCreateModal();
        }}
      >
        {clientMode ? "Добавить задачу" : t.common.create}
      </Button>,
    );
    return () => {
      setEnd(null);
    };
  }, [clientMode, openCreateModal, setEnd, t.common.create, loading, selectedProfile]);

  if (loading && jobs.length === 0 && !error) {
    return (
      <KorraLoader className="py-24" label="Загружаем расписание…" />
    );
  }

  const pendingJob = jobDelete.pendingId
    ? jobs.find((j) => getJobKey(j) === jobDelete.pendingId)
    : null;

  return (
    <div className="flex flex-col gap-6">
      <PluginSlot name="cron:top" />
      <Toast toast={toast} />

      {!clientMode && (
        <Segmented
          value={view}
          onChange={(v) => setView(v as "jobs" | "blueprints")}
          options={[
            { value: "jobs", label: "Задачи" },
            { value: "blueprints", label: "Шаблоны" },
          ]}
        />
      )}

      {clientMode && (
        <div className="space-y-1 text-sm text-muted-foreground">
          <p>
            Добавляйте и меняйте задачи прямо здесь. Новая задача сохраняется
            приостановленной: проверьте формулировку и время, затем включите её.
          </p>
          <p className="text-xs">Даты показаны по часовому поясу {ownerTimeZoneLabel()}.</p>
        </div>
      )}

      {error ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive" role="alert">
          <span>{error}</span>
          <Button
            type="button"
            size="xs"
            outlined
            onClick={() => loadJobs(selectedProfile)}
          >
            Повторить
          </Button>
        </div>
      ) : null}

      {view === "blueprints" && (
        <AutomationBlueprints
          profile={selectedProfile === "all" ? "default" : selectedProfile}
          onCreated={() => loadJobs(selectedProfile)}
        />
      )}


      <DeleteConfirmDialog
        open={jobDelete.isOpen}
        onCancel={jobDelete.cancel}
        onConfirm={jobDelete.confirm}
        title={clientMode ? "Убрать задачу из расписания?" : t.cron.confirmDeleteTitle}
        description={
          clientMode && pendingJob
            ? `«${truncateText(getJobTitle(pendingJob), 40)}» будет убрана из списка. Новые запуски уже приостановлены, история результатов сохранится.`
            : pendingJob
            ? `"${truncateText(getJobTitle(pendingJob), 40)}" — ${
                t.cron.confirmDeleteMessage
              }`
            : t.cron.confirmDeleteMessage
        }
        loading={jobDelete.isDeleting}
        confirmLabel={clientMode ? "Убрать из расписания" : undefined}
      />

      {/* Create job modal */}
      {createModalOpen && (
        <div
          ref={createModalRef}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-background/85 p-4"
          onClick={(e) => e.target === e.currentTarget && setCreateModalOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="create-cron-title"
        >
          <div className={cn(themedBody, "relative w-full max-w-3xl max-h-[90vh] border border-border bg-card shadow-2xl flex flex-col")}>
            <Button
              ghost
              size="icon"
              onClick={() => setCreateModalOpen(false)}
              className="absolute right-2 top-2 text-muted-foreground hover:text-foreground"
              aria-label={clientMode ? "Закрыть" : t.common.close}
            >
              <X />
            </Button>

            <header className="p-5 pb-3 border-b border-border">
              <h2
                id="create-cron-title"
                className="font-mondwest text-display text-base tracking-wider"
              >
                {clientMode ? "Новая задача" : t.cron.newJob}
              </h2>
            </header>

            <div className="min-h-0 overflow-y-auto p-5 grid gap-4">
              {!clientMode && <div className="grid gap-2">
                <Label htmlFor="cron-profile">{tr("Profile")}</Label>
                <Select
                  id="cron-profile"
                  value={createProfile}
                  onValueChange={(v) => setCreateProfile(v)}
                >
                  {profiles.map((profile) => (
                    <SelectOption key={profile.name} value={profile.name}>
                      {profileLabel(profile.name)}
                    </SelectOption>
                  ))}
                </Select>
              </div>}

              <CronJobFormFields
                idPrefix="cron"
                autoFocus
                ownerMode={clientMode}
                form={createForm}
                onChange={setCreateForm}
                resources={{
                  availableSkills,
                  availableToolsets,
                  modelOptions,
                  deliveryTargets,
                }}
              />

              <div className="flex justify-end">
                <Button
                  className="uppercase"
                  size="sm"
                  onClick={handleCreate}
                  disabled={creating}
                  prefix={creating ? <Spinner /> : undefined}
                >
                  {creating
                    ? t.common.creating
                    : clientMode
                      ? "Сохранить черновик"
                      : t.common.create}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Edit job modal */}
      {editJob && (
        <div
          ref={editModalRef}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-background/85 p-4"
          onClick={(e) => e.target === e.currentTarget && setEditJob(null)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="edit-cron-title"
        >
          <div className={cn(themedBody, "relative w-full max-w-3xl max-h-[90vh] border border-border bg-card shadow-2xl flex flex-col")}>
            <Button
              ghost
              size="icon"
              onClick={() => setEditJob(null)}
              className="absolute right-2 top-2 text-muted-foreground hover:text-foreground"
              aria-label={clientMode ? "Закрыть" : t.common.close}
            >
              <X />
            </Button>

            <header className="p-5 pb-3 border-b border-border">
              <h2
                id="edit-cron-title"
                className="font-mondwest text-display text-base tracking-wider"
              >
                {clientMode ? "Изменить задачу" : tr("Edit job")}
              </h2>
            </header>

            <div className="min-h-0 overflow-y-auto p-5 grid gap-4">
              <CronJobFormFields
                idPrefix="edit-cron"
                autoFocus
                ownerMode={clientMode}
                form={editForm}
                onChange={setEditForm}
                resources={{
                  availableSkills,
                  availableToolsets,
                  modelOptions,
                  deliveryTargets,
                }}
              />

              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground font-mono-ui truncate pr-4">
                  {editJob.id}
                </span>
                <Button
                  className="uppercase"
                  size="sm"
                  onClick={handleEdit}
                  disabled={saving}
                  prefix={saving ? <Spinner /> : undefined}
                >
                  {saving ? t.common.loading : clientMode ? "Сохранить" : tr("Save changes")}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {resumeJob && (
        <div
          ref={resumeModalRef}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-background/85 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="resume-cron-title"
          onClick={(event) => {
            if (event.target === event.currentTarget && !resuming) setResumeJob(null);
          }}
        >
          <div className={cn(themedBody, "w-full max-w-md border border-border bg-card p-5 shadow-2xl")}>
            <h2 id="resume-cron-title" className="font-mondwest text-display text-base tracking-wider">
              Включить задачу?
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">
              После включения Корра будет выполнять её автоматически. Для подтверждения
              введите название задачи точно:
            </p>
            <p className="mt-2 text-sm font-medium">{getJobTitle(resumeJob)}</p>
            <Label htmlFor="resume-cron-confirmation" className="mt-4 block">
              Название задачи
            </Label>
            <Input
              id="resume-cron-confirmation"
              autoFocus
              className="mt-1"
              value={resumeConfirmation}
              onChange={(event) => setResumeConfirmation(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && resumeConfirmation === getJobTitle(resumeJob)) {
                  void handleConfirmedResume();
                }
              }}
            />
            <div className="mt-5 flex justify-end gap-2">
              <Button outlined onClick={() => setResumeJob(null)} disabled={resuming}>
                Отмена
              </Button>
              <Button
                onClick={() => void handleConfirmedResume()}
                disabled={resuming || resumeConfirmation !== getJobTitle(resumeJob)}
                prefix={resuming ? <Spinner /> : <Play />}
              >
                Включить
              </Button>
            </div>
          </div>
        </div>
      )}

      {view === "jobs" && (
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <H2
            variant="sm"
            className="flex items-center gap-2 text-muted-foreground"
          >
            <Clock className="h-4 w-4" />
            {t.cron.scheduledJobs} ({jobs.length})
          </H2>

          {!clientMode && (
            <div className="grid gap-1 min-w-[220px]">
              <Label htmlFor="cron-profile-filter">Профиль</Label>
              <Select
                id="cron-profile-filter"
                value={selectedProfile}
                onValueChange={(v) => setSelectedProfile(v)}
              >
                <SelectOption value="all">Все профили</SelectOption>
                {profiles.map((profile) => (
                  <SelectOption key={profile.name} value={profile.name}>
                    {profileLabel(profile.name)}
                  </SelectOption>
                ))}
              </Select>
            </div>
          )}
        </div>

        {!error && jobs.length === 0 && (
          <Card>
            <CardContent className="flex flex-col items-center gap-3 py-8 text-center text-sm text-muted-foreground">
              <span>{t.cron.noJobs}</span>
              <Button
                className={clientMode ? undefined : "uppercase"}
                size="sm"
                onClick={() => {
                  setCreateProfile(
                    selectedProfile === "all" ? "default" : selectedProfile,
                  );
                  openCreateModal();
                }}
              >
                {clientMode ? "Добавить первую задачу" : t.common.create}
              </Button>
            </CardContent>
          </Card>
        )}

        {jobs.map((job) => {
          const state = getJobState(job);
          const promptText = getJobPrompt(job);
          const title = getJobTitle(job);
          const hasName = Boolean(getJobName(job));
          const deliver = asText(job.deliver);
          const profile = getJobProfile(job);
          const jobKey = getJobKey(job);
          const mode = getJobMode(job);
          const modelDisplay = getModelDisplay(job);
          const toolsets = Array.isArray(job.enabled_toolsets)
            ? job.enabled_toolsets.filter(Boolean)
            : [];

          return (
            <Card key={jobKey}>
              <CardContent className="flex items-start gap-4 py-4">
                <div className="flex-1 min-w-0">
                  <div className="mb-1 flex flex-wrap items-center gap-2">
                    <span className="font-medium text-sm truncate">
                      {title}
                    </span>
                    <Badge tone={STATUS_TONE[state] ?? "secondary"}>
                      {STATUS_LABEL[state] ?? "Статус уточняется"}
                    </Badge>
                    {!clientMode && (
                      <Badge tone="outline">{profileLabel(profile)}</Badge>
                    )}
                    {!clientMode && deliver && deliver !== "local" && (
                      <Badge tone="outline">{deliver}</Badge>
                    )}
                    {!clientMode && Array.isArray(job.skills) && job.skills.length > 0 && (
                      <Badge tone="outline" title={job.skills.join(", ")}>
                        {job.skills.length === 1
                          ? job.skills[0]
                          : `${job.skills.length} навыков`}
                      </Badge>
                    )}
                    {!clientMode && mode !== "agent" && (
                      <Badge tone="outline">{mode}</Badge>
                    )}
                    {!clientMode && modelDisplay && (
                      <Badge tone="outline" title={modelDisplay}>
                        модель
                      </Badge>
                    )}
                    {!clientMode && toolsets.length > 0 && (
                      <Badge tone="outline" title={toolsets.join(", ")}>
                        {toolsets.length} наборов инструментов
                      </Badge>
                    )}
                  </div>
                  {hasName && promptText && (
                    <p className="text-xs text-muted-foreground truncate mb-1">
                      {truncateText(promptText, 100)}
                    </p>
                  )}
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
                    <span className={clientMode ? "" : "font-mono-ui"}>
                      {getJobScheduleDisplay(job, scheduleDescribeStrings)}
                      {clientMode && SCHEDULE_TIME_ZONE !== OWNER_TIME_ZONE
                        ? ` (${SCHEDULE_TIME_ZONE})`
                        : ""}
                    </span>
                    <span>повторы: {getRepeatDisplay(job)}</span>
                    <span>
                      {t.cron.last}: {formatTime(job.last_run_at)}
                    </span>
                    <span>
                      {state === "paused" || state === "disabled"
                        ? "Следующий запуск: после включения"
                        : `${t.cron.next}: ${formatTime(job.next_run_at)}`}
                    </span>
                  </div>
                  {job.last_delivery_error && (
                    <p className="text-xs text-destructive mt-1">
                      Доставка: {ownerFacingError(
                        job.last_delivery_error,
                        "Результат пока не доставлен. Повторите позже.",
                      )}
                    </p>
                  )}
                  {!clientMode && job.last_fire_error?.detail && (
                    <p className="text-xs text-destructive mt-1">
                      {tr("missed scheduled fire ({time}):", { time: formatTime(job.last_fire_error.at ?? null) })}{" "}
                      {ownerFacingError(
                        job.last_fire_error.detail,
                        "Запланированный запуск не состоялся.",
                      )}
                    </p>
                  )}
                  {job.last_error && (
                    <p className="text-xs text-destructive mt-1">
                      {ownerFacingError(
                        job.last_error,
                        "Задача завершилась с ошибкой. Повторите позже.",
                      )}
                    </p>
                  )}
                </div>

                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    ghost
                    size="icon"
                    title={state === "paused" ? t.cron.resume : t.cron.pause}
                    aria-label={
                      state === "paused" ? t.cron.resume : t.cron.pause
                    }
                    onClick={() => handlePauseResume(job)}
                    className={
                      state === "paused" ? "text-success" : "text-warning"
                    }
                  >
                    {state === "paused" ? <Play /> : <Pause />}
                  </Button>

                  {!clientMode && (
                    <Button
                      ghost
                      size="icon"
                      disabled={triggeringJobKeys.has(jobKey)}
                      title={t.cron.triggerNow}
                      aria-label={t.cron.triggerNow}
                      onClick={() => handleTrigger(job)}
                    >
                      {triggeringJobKeys.has(jobKey) ? <Spinner /> : <Zap />}
                    </Button>
                  )}

                  <Button
                    ghost
                    size="icon"
                    title={clientMode && state !== "paused" ? "Сначала приостановите задачу" : "Изменить задачу"}
                    aria-label="Изменить задачу"
                    disabled={clientMode && state !== "paused"}
                    onClick={() => openEditModal(job)}
                  >
                    <Pencil />
                  </Button>

                  <Button
                    ghost
                    destructive
                    size="icon"
                    title={clientMode && state !== "paused" ? "Сначала приостановите задачу" : clientMode ? "Убрать из расписания" : t.common.delete}
                    aria-label={clientMode && state !== "paused" ? "Сначала приостановите задачу" : clientMode ? "Убрать из расписания" : t.common.delete}
                    disabled={clientMode && state !== "paused"}
                    onClick={() => jobDelete.requestDelete(jobKey)}
                  >
                    <Trash2 />
                  </Button>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
      )}

      <PluginSlot name="cron:bottom" />
    </div>
  );
}
