import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import { KorraLoader } from "@/components/KorraLoader";
import { createPortal } from "react-dom";
import {
  Brain,
  ChevronDown,
  Cpu,
  DollarSign,
  Eye,
  RefreshCw,
  Settings2,
  Star,
  Wrench,
  X,
  Zap,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  AuxiliaryModelsResponse,
  AuxiliaryTaskAssignment,
  MoaConfigResponse,
  MoaModelSlot,
  ModelsAnalyticsModelEntry,
  ModelsAnalyticsResponse,
} from "@/lib/api";
import { timeAgo, cn } from "@/lib/utils";
import {
  shouldCloseOuterModalOnEscape,
} from "@/lib/dashboard-modal-shell";
import { formatTokenCount } from "@/lib/format";
import { Button } from "@/components/ProductButton";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Card, CardContent, CardHeader, CardTitle } from "@nous-research/ui/ui/components/card";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Switch } from "@nous-research/ui/ui/components/switch";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useModalBehavior } from "@/hooks/useModalBehavior";
import { usePageHeader } from "@/contexts/usePageHeader";
import { ProfileScopeChip } from "@/components/ProfileScopeChip";
import { useI18n } from "@/i18n";
import { PluginSlot } from "@/plugins";
import { ModelPickerDialog } from "@/components/ModelPickerDialog";
import { ModelReloadConfirm } from "@/components/ModelReloadConfirm";
import { ownerFacingError } from "@/lib/owner-facing-error";
import { russianInterfaceText } from "@/lib/russian-interface-text";
import { providerDisplayName } from "@/lib/model-choices";

// `label` — ключ перевода в `ru.dashboard`, а не готовая подпись: сам период
// («7 дней») собирается из него через `tr` уже в разметке.
const PERIODS = [
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "90d", days: 90 },
] as const;

// Must match _AUX_TASK_SLOTS in korra_cli/web_server.py.
const AUX_TASKS: readonly { key: string; label: string; hint: string }[] = [
  { key: "vision", label: "Зрение", hint: "Анализ изображений" },
  { key: "compression", label: "Сжатие", hint: "Сжатие контекста" },
  { key: "skills_hub", label: "Каталог навыков", hint: "Поиск навыков" },
  { key: "approval", label: "Согласование", hint: "Умное автосогласование" },
  { key: "mcp", label: "MCP", hint: "Выбор MCP-инструментов" },
  { key: "title_generation", label: "Заголовки", hint: "Названия сессий" },
  { key: "review", label: "Ревью", hint: "Проверка изменений субагентом" },
  { key: "triage_specifier", label: "Уточнение задач", hint: "Детализация Kanban" },
  { key: "kanban_decomposer", label: "Декомпозиция", hint: "Разбиение задач" },
  { key: "profile_describer", label: "Описание профиля", hint: "Автоописание профилей" },
  { key: "curator", label: "Куратор", hint: "Проверка использования навыков" },
] as const;

/** Русская подпись вспомогательной задачи; незнакомый ключ показываем как есть. */
function auxTaskLabel(task: string): string {
  return AUX_TASKS.find((entry) => entry.key === task)?.label ?? task;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function formatCost(n: number): string {
  if (n >= 1) return `$${n.toFixed(2)}`;
  if (n >= 0.01) return `$${n.toFixed(3)}`;
  if (n > 0) return `$${n.toFixed(4)}`;
  return "$0";
}

/** Short model name: strip vendor prefix like "openrouter/" or "anthropic/". */
function shortModelName(model: string): string {
  const slashIdx = model.indexOf("/");
  if (slashIdx > 0) return model.slice(slashIdx + 1);
  return model;
}

/** Extract vendor prefix from a model string like "anthropic/claude-opus-4.7" → "anthropic". */
function modelVendor(model: string, fallback?: string): string {
  const slashIdx = model.indexOf("/");
  if (slashIdx > 0) return model.slice(0, slashIdx);
  return fallback || "";
}

function TokenBar({
  input,
  output,
  cacheRead,
  reasoning,
}: {
  input: number;
  output: number;
  cacheRead: number;
  reasoning: number;
}) {
  const total = input + output + cacheRead + reasoning;
  if (total === 0) return null;

  // Segments carry a CSS color value (hex or `var(--token)`) rather than
  // a Tailwind class so the input/output series can pick up the active
  // theme's `--series-*-token` vars — see `themes/types.ts`
  // `ThemeSeriesColors`. The /60–/70 fade on the bar is applied via
  // color-mix on the same value so themes don't need to ship two
  // separate hex literals.
  const segments: Array<{ color: string; label: string; value: number }> = [
    { value: cacheRead, color: "#60a5fa", label: "Кэш" }, // tailwind blue-400
    { value: reasoning, color: "#c084fc", label: "Рассуждение" }, // tailwind purple-400
    { value: input, color: "var(--series-input-token)", label: "Ввод" },
    { value: output, color: "var(--series-output-token)", label: "Вывод" },
  ].filter((s) => s.value > 0);

  return (
    <div className="space-y-1.5">
      {/* Stacked bar — segments fill proportionally to their share of total */}
      <div className="relative flex min-h-[0.75rem] w-full items-stretch overflow-hidden rounded-full">
        {segments.map((s, i) => (
          <div
            key={i}
            className="relative flex items-center transition-all duration-300"
            style={{
              backgroundColor: `color-mix(in srgb, ${s.color} 70%, transparent)`,
              width: `${(s.value / total) * 100}%`,
            }}
          >
            {/* Stepped fill pattern overlay */}
            <div
              className="absolute inset-0 opacity-30"
              style={{
                backgroundImage:
                  "repeating-linear-gradient(to right, transparent 0 0.4rem, currentColor 0.4rem calc(0.4rem + 1px))",
              }}
            />
          </div>
        ))}
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-text-secondary">
        {segments.map((s, i) => (
          <span key={i} className="flex items-center gap-1">
            <span
              className="inline-block h-1.5 w-1.5 rounded-full"
              style={{ backgroundColor: s.color }}
            />
            {s.label} {formatTokens(s.value)}
          </span>
        ))}
      </div>
    </div>
  );
}

function CapabilityBadges({
  capabilities,
}: {
  capabilities: ModelsAnalyticsModelEntry["capabilities"];
}) {
  const hasAny =
    capabilities.supports_tools ||
    capabilities.supports_vision ||
    capabilities.supports_reasoning ||
    capabilities.model_family;
  if (!hasAny) return null;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {capabilities.supports_tools && (
        <span className="inline-flex items-center gap-1 rounded-full bg-success/10 px-2 py-1 text-xs font-medium text-success">
          <Wrench className="h-2.5 w-2.5" /> Инструменты
        </span>
      )}
      {capabilities.supports_vision && (
        <span className="inline-flex items-center gap-1 rounded-full bg-blue-500/10 px-2 py-1 text-xs font-medium text-blue-600 dark:text-blue-400">
          <Eye className="h-2.5 w-2.5" /> Зрение
        </span>
      )}
      {capabilities.supports_reasoning && (
        <span className="inline-flex items-center gap-1 rounded-full bg-purple-500/10 px-2 py-1 text-xs font-medium text-purple-600 dark:text-purple-400">
          <Brain className="h-2.5 w-2.5" /> Рассуждение
        </span>
      )}
      {capabilities.model_family && (
        <span className="inline-flex items-center rounded-full bg-muted px-2 py-1 text-xs font-medium text-text-secondary">
          {capabilities.model_family}
        </span>
      )}
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────────── */
/*  Per-card "Use as" menu                                              */
/* ──────────────────────────────────────────────────────────────────── */

function UseAsMenu({
  provider,
  model,
  isMain,
  mainAuxTask,
  onAssigned,
}: {
  provider: string;
  model: string;
  /** True when this card's model+provider match config.yaml's main slot. */
  isMain: boolean;
  /** If this model is assigned to a specific aux task, that task's key. */
  mainAuxTask: string | null;
  onAssigned(): void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingConfirm, setPendingConfirm] = useState<{
    message: string;
    scope: "main" | "auxiliary";
    task: string;
  } | null>(null);

  const assign = async (
    scope: "main" | "auxiliary",
    task: string,
    confirmExpensiveModel = false,
  ) => {
    if (!provider || !model) {
      setError("Не указан провайдер или модель");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.setModelAssignment({
        confirm_expensive_model: confirmExpensiveModel,
        scope,
        provider,
        model,
        task,
      });
      if (result.confirm_required) {
        setPendingConfirm({
          scope,
          task,
          message: russianInterfaceText(
            result.confirm_message,
            "У этой модели необычно высокая стоимость.",
          ),
        });
        return;
      }
      onAssigned();
      setOpen(false);
    } catch (e) {
      setError(ownerFacingError(e, "Не удалось назначить модель."));
    } finally {
      setBusy(false);
    }
  };

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && !target.closest?.("[data-use-as-menu]")) setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);

  return (
    <div className={cn("relative", open && "z-20")} data-use-as-menu>
      <Button
        size="sm"
        outlined
        onClick={() => setOpen((v) => !v)}
        disabled={busy}
        className="min-h-9 px-3 text-xs"
        prefix={busy ? <Spinner /> : null}
      >
        Назначить <ChevronDown className="h-3 w-3" />
      </Button>
      {open && (
        <div className="neo-select-menu absolute right-0 top-full z-50 mt-2 min-w-[270px] overflow-hidden p-1.5 font-sans">
          <button
            type="button"
            onClick={() => assign("main", "")}
            disabled={busy}
            className="neo-select-option flex min-h-11 w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm disabled:opacity-40"
          >
            <span className="flex items-center gap-2">
              <Star className="h-3 w-3" />
              Основная модель
            </span>
            {isMain && (
              <span className="text-xs font-medium text-primary">
                выбрана
              </span>
            )}
          </button>

          <div className="px-3 pb-1 pt-3 text-xs font-semibold text-text-tertiary">
            Вспомогательные задачи
          </div>

          <button
            type="button"
            onClick={() => assign("auxiliary", "")}
            disabled={busy}
            className="neo-select-option flex min-h-11 w-full items-center justify-between px-3 py-2 text-left text-sm disabled:opacity-40"
          >
            <span>Все вспомогательные задачи</span>
          </button>

          {AUX_TASKS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => assign("auxiliary", t.key)}
              disabled={busy}
              className="neo-select-option flex min-h-10 w-full items-center justify-between px-3 py-2 text-left text-sm disabled:opacity-40"
            >
              <span>{t.label}</span>
              {mainAuxTask === t.key && (
                <span className="text-xs font-medium text-primary">
                  выбрана
                </span>
              )}
            </button>
          ))}

          {error && (
            <div className="px-3 py-2 text-sm text-destructive">
              {error}
            </div>
          )}
        </div>
      )}
      <ConfirmDialog
        open={!!pendingConfirm}
        title="Модель с высокой стоимостью"
        description={pendingConfirm?.message}
        destructive
        confirmLabel="Всё равно переключить"
        cancelLabel="Отмена"
        loading={busy}
        onCancel={() => setPendingConfirm(null)}
        onConfirm={() => {
          const pending = pendingConfirm;
          if (!pending) return;
          setPendingConfirm(null);
          void assign(pending.scope, pending.task, true);
        }}
      />
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────────── */
/*  ModelCard                                                           */
/* ──────────────────────────────────────────────────────────────────── */

function ModelCard({
  entry,
  rank,
  main,
  aux,
  onAssigned,
  showTokens,
}: {
  entry: ModelsAnalyticsModelEntry;
  rank: number;
  main: { provider: string; model: string } | null;
  aux: AuxiliaryTaskAssignment[];
  onAssigned(): void;
  showTokens: boolean;
}) {
  const { t, tr } = useI18n();
  const provider = entry.provider || modelVendor(entry.model);
  const totalTokens = entry.input_tokens + entry.output_tokens;
  const caps = entry.capabilities;

  const isMain =
    !!main &&
    main.provider === provider &&
    main.model === entry.model;

  // First aux task currently using this model (if any).
  const mainAuxTask =
    aux.find(
      (a) => a.provider === provider && a.model === entry.model,
    )?.task ?? null;

  return (
    <Card
      className={cn(
        "min-w-0 max-w-full overflow-hidden rounded-xl border border-border/60 font-sans",
        isMain && "border-primary/50",
      )}
    >
      <CardHeader className="gap-3 pb-3 font-sans">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <span className="text-xs text-text-tertiary">Модель {rank}</span>
            <CardTitle className="mt-1 truncate font-mono text-base normal-case tracking-normal" title={entry.model}>
                {shortModelName(entry.model)}
            </CardTitle>
          </div>
          <div className="shrink-0 text-right">
            <div className="font-mono text-base font-semibold">
              {showTokens ? formatTokens(totalTokens) : entry.sessions}
            </div>
            <div className="text-xs text-text-tertiary">
              {showTokens ? t.models.tokens : t.models.sessions}
            </div>
          </div>
        </div>

        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {provider && (
            <Badge tone="secondary" className="max-w-full rounded-full text-xs normal-case tracking-normal">
              <span className="truncate">{providerDisplayName(provider)}</span>
            </Badge>
          )}
          {isMain && (
            <span className="inline-flex items-center gap-1 rounded-full bg-primary/15 px-2 py-1 text-xs font-medium text-primary">
              <Star className="h-3 w-3" /> основная
            </span>
          )}
          {mainAuxTask && (
            <span className="inline-flex items-center rounded-full bg-purple-500/10 px-2 py-1 text-xs font-medium text-purple-600 dark:text-purple-400">
              задача · {auxTaskLabel(mainAuxTask)}
            </span>
          )}
        </div>

        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-secondary">
          {caps.context_window && caps.context_window > 0 && (
            <span>{formatTokenCount(caps.context_window)} контекст</span>
          )}
          {caps.max_output_tokens && caps.max_output_tokens > 0 && (
            <span>{formatTokenCount(caps.max_output_tokens)} вывод</span>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4 pt-2">
        {showTokens && (
          <>
            <TokenBar
              input={entry.input_tokens}
              output={entry.output_tokens}
              cacheRead={entry.cache_read_tokens}
              reasoning={entry.reasoning_tokens}
            />

            <div className="grid grid-cols-3 gap-2 text-xs">
              <div className="text-center">
                <div className="font-mono font-semibold">{entry.sessions}</div>
                <div className="text-xs text-text-tertiary">
                  {t.models.sessions}
                </div>
              </div>
              <div className="text-center">
                <div className="font-mono font-semibold">
                  {formatTokens(entry.avg_tokens_per_session)}
                </div>
                <div className="text-xs text-text-tertiary">
                  {t.models.avgPerSession}
                </div>
              </div>
              <div className="text-center">
                <div className="font-mono font-semibold">
                  {entry.api_calls > 0 ? formatTokens(entry.api_calls) : "—"}
                </div>
                <div className="text-xs text-text-tertiary">
                  {t.models.apiCalls}
                </div>
              </div>
            </div>
          </>
        )}

        <CapabilityBadges capabilities={entry.capabilities} />

        <div className="flex flex-wrap items-center justify-between gap-3 pt-1 text-xs text-text-secondary">
          <div className="flex items-center gap-3">
            {showTokens && entry.estimated_cost > 0 && (
              <span className="flex items-center gap-0.5">
                <DollarSign className="h-2.5 w-2.5" />
                {formatCost(entry.estimated_cost)}
              </span>
            )}
            {showTokens && entry.tool_calls > 0 && (
              <span className="flex items-center gap-0.5">
                <Zap className="h-2.5 w-2.5" />
                {entry.tool_calls} {t.models.toolCalls}
              </span>
            )}
          </div>
          {entry.last_used_at > 0 && (
            <span>{timeAgo(entry.last_used_at, tr)}</span>
          )}
        </div>

        <div className="flex justify-end border-t border-border/40 pt-3">
          <UseAsMenu
            provider={provider}
            model={entry.model}
            isMain={isMain}
            mainAuxTask={mainAuxTask}
            onAssigned={onAssigned}
          />
        </div>
      </CardContent>
    </Card>
  );
}

/* ──────────────────────────────────────────────────────────────────── */
/*  Model Settings panel (top of page)                                  */
/* ──────────────────────────────────────────────────────────────────── */

type PickerTarget =
  | { kind: "main" }
  | { kind: "aux"; task: string };

type MoaPickerTarget =
  | { kind: "reference"; index: number }
  | { kind: "aggregator" };

function AuxiliaryTasksModal({
  aux,
  refreshKey,
  onSaved,
  onClose,
}: {
  aux: AuxiliaryModelsResponse | null;
  refreshKey: number;
  onSaved(): void;
  onClose(): void;
}) {
  const [picker, setPicker] = useState<PickerTarget | null>(null);
  const [resetBusy, setResetBusy] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const modalRef = useModalBehavior({ open: true, onClose });

  const resetAllAux = async () => {
    setConfirmReset(false);
    setResetBusy(true);
    try {
      await api.setModelAssignment({
        scope: "auxiliary",
        task: "__reset__",
        provider: "",
        model: "",
      });
      onSaved();
    } finally {
      setResetBusy(false);
    }
  };

  return (
    <div
      ref={modalRef}
      className="neo-overlay fixed inset-0 z-[100] flex items-center justify-center p-3 sm:p-6"
      onClick={(e) => e.target === e.currentTarget && onClose()}
      role="dialog"
      aria-modal="true"
      aria-labelledby="aux-modal-title"
    >
      <div className="neo-dialog relative flex max-h-[90dvh] w-full max-w-2xl flex-col overflow-hidden font-sans">
        <Button
          ghost
          size="icon"
          onClick={onClose}
          className="absolute right-3 top-3 text-muted-foreground hover:text-foreground"
          aria-label="Закрыть"
        >
          <X />
        </Button>

        <header className="px-5 pb-4 pt-5 pr-16 sm:px-6 sm:pt-6">
          <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
            <h2
              id="aux-modal-title"
              className="text-2xl font-semibold leading-tight"
            >
              Служебные задачи
            </h2>
            <Button
              size="sm"
              outlined
              onClick={() => setConfirmReset(true)}
              disabled={resetBusy}
              prefix={resetBusy ? <Spinner /> : null}
            >
              Выбирать автоматически
            </Button>
          </div>
          <p className="mt-2 max-w-xl text-[15px] leading-relaxed text-text-secondary">
            Обычно Korra сама выбирает модель для зрения, сжатия контекста и других внутренних действий. Здесь можно задать исключения.
          </p>
        </header>

        <div className="flex-1 space-y-2 overflow-y-auto px-5 pb-5 sm:px-6 sm:pb-6">
          {AUX_TASKS.map((t) => {
            const cur = aux?.tasks.find((a) => a.task === t.key);
            const isAuto =
              !cur || cur.provider === "auto" || !cur.provider;
            return (
              <div
                key={t.key}
                className="flex flex-col gap-3 rounded-xl bg-[var(--neo-surface)] p-4 shadow-[var(--neo-inset-compact)] sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="text-sm font-semibold">{t.label}</span>
                    <span className="text-sm text-text-tertiary">
                      {t.hint}
                    </span>
                  </div>
                  <div className="mt-1 truncate font-mono text-xs text-text-secondary">
                    {isAuto
                      ? "Автоматически — используется основная модель"
                      : `${providerDisplayName(cur?.provider ?? "")} · ${cur?.model || "модель поставщика"}`}
                  </div>
                </div>
                <Button
                  size="sm"
                  outlined
                  onClick={() => setPicker({ kind: "aux", task: t.key })}
                  className="w-full sm:w-auto"
                >
                  Выбрать модель
                </Button>
              </div>
            );
          })}
        </div>

        {picker && picker.kind === "aux" && (
          <ModelPickerDialog
            key={`picker-${refreshKey}`}
            loader={api.getModelOptions}
            alwaysGlobal
            title={`Выбрать модель для задачи: ${auxTaskLabel(picker.task)}`}
            onApply={async ({ provider, model, confirmExpensiveModel }) => {
              const result = await api.setModelAssignment({
                confirm_expensive_model: confirmExpensiveModel,
                scope: "auxiliary",
                task: picker.task,
                provider,
                model,
              });
              if (!result.confirm_required) onSaved();
              return result;
            }}
            onClose={() => setPicker(null)}
          />
        )}
        <ConfirmDialog
          open={confirmReset}
          onCancel={() => setConfirmReset(false)}
          onConfirm={() => void resetAllAux()}
          title="Включить автоматический выбор?"
          description="Korra снова будет использовать основную модель для всех служебных задач. Ручные назначения удалятся."
          confirmLabel="Включить"
          cancelLabel="Отмена"
          loading={resetBusy}
        />
      </div>
    </div>
  );
}

function MoaModelsModal({
  config,
  refreshKey,
  onClose,
  onSaved,
}: {
  config: MoaConfigResponse;
  refreshKey: number;
  onClose(): void;
  onSaved(next: MoaConfigResponse): void;
}) {
  const [draft, setDraft] = useState<MoaConfigResponse>(config);
  const [selected, setSelected] = useState(config.default_preset || Object.keys(config.presets)[0] || "default");
  const [newName, setNewName] = useState("");
  const [picker, setPicker] = useState<MoaPickerTarget | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Nested ModelPickerDialog owns Escape while open — don't dismiss MoA too.
  const closeMoaUnlessPickerOpen = useCallback(() => {
    if (!shouldCloseOuterModalOnEscape(picker !== null)) return;
    onClose();
  }, [picker, onClose]);

  const modalRef = useModalBehavior({
    open: true,
    onClose: closeMoaUnlessPickerOpen,
  });

  const presetNames = Object.keys(draft.presets || {});
  const preset = draft.presets[selected] || draft.presets[presetNames[0]];
  const slotLabel = (slot: MoaModelSlot) =>
    `${slot.provider || "(провайдер не выбран)"} · ${slot.model || "(модель не выбрана)"}`;

  const updateSelectedPreset = (updater: (preset: MoaConfigResponse["presets"][string]) => MoaConfigResponse["presets"][string]) => {
    setDraft((prev) => ({
      ...prev,
      presets: {
        ...prev.presets,
        [selected]: updater(prev.presets[selected]),
      },
    }));
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const saved = await api.saveMoaModels(draft);
      onSaved(saved);
      onClose();
    } catch (e) {
      setError(ownerFacingError(e, "Не удалось сохранить настройки ансамбля моделей."));
    } finally {
      setBusy(false);
    }
  };

  const addPreset = () => {
    const name = newName.trim();
    if (!name || draft.presets[name]) return;
    const seed = preset || {
      reference_models: draft.reference_models,
      aggregator: draft.aggregator,
      reference_temperature: draft.reference_temperature,
      aggregator_temperature: draft.aggregator_temperature,
      reference_timeout: draft.reference_timeout,
      degraded_reference_policy: draft.degraded_reference_policy,
      max_tokens: draft.max_tokens,
      enabled: draft.enabled,
    };
    setDraft((prev) => ({
      ...prev,
      default_preset: prev.default_preset || name,
      presets: { ...prev.presets, [name]: { ...seed, reference_models: [...seed.reference_models] } },
    }));
    setSelected(name);
    setNewName("");
  };

  const deletePreset = () => {
    if (presetNames.length <= 1) return;
    const remaining = presetNames.filter((name) => name !== selected);
    const nextSelected = remaining[0];
    setDraft((prev) => {
      const next = { ...prev.presets };
      delete next[selected];
      return {
        ...prev,
        presets: next,
        default_preset: prev.default_preset === selected ? nextSelected : prev.default_preset,
        active_preset: prev.active_preset === selected ? "" : prev.active_preset,
      };
    });
    setSelected(nextSelected);
  };

  if (!preset) return null;

  // Portal to document.body: the main dashboard column is `relative z-2`,
  // which traps fixed descendants below the sidebar (same as ModelPickerDialog).
  return createPortal(
    <div
      ref={modalRef}
      className="neo-overlay fixed inset-0 z-[100] flex items-center justify-center p-3 sm:p-6"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) closeMoaUnlessPickerOpen();
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="moa-modal-title"
    >
      <div
        className="neo-dialog relative flex max-h-[90dvh] w-full max-w-2xl flex-col overflow-auto font-sans"
      >
        <header className="px-5 pb-3 pt-5 sm:px-6 sm:pt-6">
          <h2
            id="moa-modal-title"
            className="text-2xl font-semibold leading-tight"
          >
            Команда моделей
          </h2>
        </header>
        <div className="space-y-5 px-5 pb-5 sm:px-6 sm:pb-6">
          <p className="text-[15px] leading-relaxed text-text-secondary">
            Референсные модели предлагают варианты, а итоговая модель формирует
            ответ и вызывает инструменты.
          </p>

          <div className="flex flex-wrap items-center gap-2">
            <select
              className="min-h-11 rounded-xl bg-[var(--neo-surface)] px-3 py-2 text-sm shadow-[var(--neo-inset)] outline-none"
              value={selected}
              onChange={(event) => setSelected(event.target.value)}
            >
              {presetNames.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
            <Button size="sm" outlined onClick={() => setDraft((prev) => ({ ...prev, default_preset: selected }))}>По умолчанию</Button>
            <Button size="sm" ghost disabled={presetNames.length <= 1} onClick={deletePreset}>Удалить</Button>
            <input
              className="min-h-11 min-w-0 flex-1 rounded-xl bg-[var(--neo-surface)] px-3 py-2 text-sm shadow-[var(--neo-inset)] outline-none"
              placeholder="Название нового пресета"
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
            />
            <Button size="sm" outlined disabled={!newName.trim() || !!draft.presets[newName.trim()]} onClick={addPreset}>Добавить пресет</Button>
          </div>

          <div className="text-sm text-text-secondary">
            По умолчанию: <span className="font-mono">{draft.default_preset}</span>
          </div>

          <div className="space-y-2">
            <h3 className="text-base font-semibold">Референсные модели</h3>
            {preset.reference_models.map((slot, index) => (
              <div
                key={`${selected}-${slot.provider}-${slot.model}-${index}`}
                className={cn(
                  "flex flex-wrap items-center gap-2 rounded-xl bg-[var(--neo-surface)] px-3 py-3 shadow-[var(--neo-inset-compact)]",
                  slot.enabled === false && "opacity-60"
                )}
              >
                <Switch
                  checked={slot.enabled !== false}
                  onCheckedChange={(checked) =>
                    updateSelectedPreset((prev) => ({
                      ...prev,
                      reference_models: prev.reference_models.map((s, i) =>
                        i === index ? { ...s, enabled: checked === true } : s
                      ),
                    }))
                  }
                />
                <div className="min-w-0 flex-1 truncate font-mono text-xs text-text-secondary">{slotLabel(slot)}</div>
                <Button size="sm" outlined onClick={() => setPicker({ kind: "reference", index })}>Изменить</Button>
                <Button size="sm" ghost disabled={preset.reference_models.length <= 1} onClick={() => updateSelectedPreset((prev) => ({ ...prev, reference_models: prev.reference_models.filter((_, i) => i !== index) }))}>Убрать</Button>
              </div>
            ))}
            <Button size="sm" outlined onClick={() => updateSelectedPreset((prev) => ({ ...prev, reference_models: [...prev.reference_models, { ...prev.aggregator, enabled: true }] }))}>Добавить референсную модель</Button>
          </div>

          <div className="space-y-2">
            <h3 className="text-base font-semibold">Итоговая модель</h3>
            <div className="flex flex-wrap items-center gap-2 rounded-xl bg-[var(--neo-surface)] px-3 py-3 shadow-[var(--neo-inset-compact)]">
              <div className="min-w-0 flex-1 truncate font-mono text-xs text-text-secondary">{slotLabel(preset.aggregator)}</div>
              <Button size="sm" outlined onClick={() => setPicker({ kind: "aggregator" })}>Изменить</Button>
            </div>
          </div>

          {error && <div className="text-xs text-destructive">{error}</div>}
          <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:justify-end">
            <Button outlined onClick={onClose} disabled={busy}>Отмена</Button>
            <Button onClick={() => void save()} disabled={busy}>{busy ? "Сохранение…" : "Сохранить"}</Button>
          </div>
        </div>
      </div>
      {picker && (
        <ModelPickerDialog
          key={`moa-picker-${refreshKey}-${selected}-${picker.kind}-${picker.kind === "reference" ? picker.index : "agg"}`}
          loader={api.getModelOptions}
          alwaysGlobal
          title="Выбрать модель команды"
          onApply={async ({ provider, model }) => {
            if ((provider || "").toLowerCase() === "moa") {
              setError("Команда моделей не может рекурсивно использовать другую команду моделей.");
              return;
            }
            setError(null);
            updateSelectedPreset((prev) => {
              if (picker.kind === "aggregator") return { ...prev, aggregator: { provider, model } };
              return {
                ...prev,
                reference_models: prev.reference_models.map((slot, i) => i === picker.index ? { ...slot, provider, model } : slot),
              };
            });
          }}
          onClose={() => setPicker(null)}
        />
      )}
    </div>,
    document.body,
  );
}

function ModelSettingsPanel({
  aux,
  refreshKey,
  onSaved,
}: {
  aux: AuxiliaryModelsResponse | null;
  refreshKey: number;
  onSaved(): void;
}) {
  const [auxModalOpen, setAuxModalOpen] = useState(false);
  const [moaModalOpen, setMoaModalOpen] = useState(false);
  const [moa, setMoa] = useState<MoaConfigResponse | null>(null);
  const [picker, setPicker] = useState<PickerTarget | null>(null);
  const [pendingReloadModel, setPendingReloadModel] = useState<string | null>(
    null,
  );

  const mainProv = aux?.main.provider ?? "";
  const mainModel = aux?.main.model ?? "";

  useEffect(() => {
    api.getMoaModels().then(setMoa).catch(() => setMoa(null));
  }, [refreshKey]);

  const applyAssignment = async ({
    scope,
    task,
    provider,
    model,
    confirmExpensiveModel,
  }: {
    confirmExpensiveModel?: boolean;
    scope: "main" | "auxiliary";
    task: string;
    provider: string;
    model: string;
  }) => {
    const result = await api.setModelAssignment({
      confirm_expensive_model: confirmExpensiveModel,
      scope,
      task,
      provider,
      model,
    });
    if (!result.confirm_required) onSaved();
    return result;
  };

  // Count how many aux tasks have overrides
  const auxOverrideCount = aux?.tasks.filter(
    (a) => a.provider && a.provider !== "auto",
  ).length ?? 0;

  return (
    <Card className="min-w-0 max-w-full overflow-hidden rounded-xl border border-border/60 font-sans">
      <CardHeader className="min-w-0 gap-2 px-5 pb-4 pt-5 sm:px-6 sm:pt-6">
        <div className="flex items-center gap-3">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-primary/15 text-primary shadow-[var(--neo-inset-compact)]">
            <Settings2 className="h-5 w-5" />
          </span>
          <div>
            <CardTitle className="text-xl font-semibold normal-case tracking-normal">
              Модели для работы
            </CardTitle>
            <p className="mt-1 max-w-3xl text-[15px] leading-relaxed text-text-secondary">
              Основная модель отвечает в новых чатах. Для служебных задач Korra может выбирать модель автоматически.
            </p>
          </div>
        </div>
      </CardHeader>

      <CardContent className="min-w-0 px-5 pb-5 pt-0 sm:px-6 sm:pb-6">
        <div className="grid gap-3 lg:grid-cols-3">
          <div className="flex min-w-0 flex-col gap-4 rounded-xl bg-[var(--neo-surface)] p-4 shadow-[var(--neo-depth-1)] lg:col-span-1">
            <div className="flex min-w-0 items-start gap-3">
              <Star className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
              <div className="min-w-0 flex-1">
                <h3 className="text-base font-semibold">Основная модель</h3>
                <p className="mt-1 text-sm leading-relaxed text-text-secondary">
                  Отвечает в новых разговорах.
                </p>
                <p className="mt-3 truncate font-mono text-sm text-foreground" title={[mainProv, mainModel].filter(Boolean).join(" · ")}>
                  {mainProv ? providerDisplayName(mainProv) : "Не выбрана"}
                  {mainProv && mainModel && " · "}
                  {mainModel}
                </p>
              </div>
            </div>
            <Button size="sm" onClick={() => setPicker({ kind: "main" })} className="mt-auto w-full sm:w-fit">
              Выбрать модель
            </Button>
          </div>

          <div className="flex min-w-0 flex-col gap-4 rounded-xl bg-[var(--neo-surface)] p-4 shadow-[var(--neo-inset-compact)]">
            <div className="flex min-w-0 items-start gap-3">
              <Cpu className="mt-0.5 h-5 w-5 shrink-0 text-text-secondary" />
              <div className="min-w-0 flex-1">
                <h3 className="text-base font-semibold">Служебные задачи</h3>
                <p className="mt-1 text-sm leading-relaxed text-text-secondary">
                  Зрение, сжатие контекста и другие внутренние действия.
                </p>
                <p className="mt-3 text-sm text-foreground">
                  {auxOverrideCount > 0
                    ? `Назначено вручную: ${auxOverrideCount}`
                    : "Автоматический выбор"}
                </p>
              </div>
            </div>
            <Button size="sm" outlined onClick={() => setAuxModalOpen(true)} className="mt-auto w-full sm:w-fit">
              Настроить задачи
            </Button>
          </div>

          <div className="flex min-w-0 flex-col gap-4 rounded-xl bg-[var(--neo-surface)] p-4 shadow-[var(--neo-inset-compact)]">
            <div className="flex min-w-0 items-start gap-3">
              <Brain className="mt-0.5 h-5 w-5 shrink-0 text-text-secondary" />
              <div className="min-w-0 flex-1">
                <h3 className="text-base font-semibold">Команда моделей</h3>
                <p className="mt-1 text-sm leading-relaxed text-text-secondary">
                  Несколько моделей готовят один итоговый ответ.
                </p>
                <p className="mt-3 truncate text-sm text-foreground">
                  {moa ? `${moa.reference_models.length} референсных · ${shortModelName(moa.aggregator.model)}` : "Не настроена"}
                </p>
              </div>
            </div>
            <Button
              size="sm"
              outlined
              onClick={() => setMoaModalOpen(true)}
              disabled={!moa}
              className="mt-auto w-full sm:w-fit"
            >
              Настроить команду
            </Button>
          </div>
        </div>

        {picker && (
          <ModelPickerDialog
            key={`picker-${refreshKey}`}
            loader={api.getModelOptions}
            alwaysGlobal
            title="Выбрать основную модель"
            onApply={async ({ provider, model, confirmExpensiveModel }) => {
              const result = await applyAssignment({
                confirmExpensiveModel,
                scope: "main",
                task: "",
                provider,
                model,
              });
              if (!result.confirm_required) {
                setPendingReloadModel(model.split("/").slice(-1)[0]);
              }
              return result;
            }}
            onClose={() => setPicker(null)}
          />
        )}

        {auxModalOpen && (
          <AuxiliaryTasksModal
            aux={aux}
            refreshKey={refreshKey}
            onSaved={onSaved}
            onClose={() => setAuxModalOpen(false)}
          />
        )}

        <ModelReloadConfirm
          model={pendingReloadModel}
          onCancel={() => setPendingReloadModel(null)}
        />
        {moaModalOpen && moa && (
          <MoaModelsModal
            config={moa}
            refreshKey={refreshKey}
            onSaved={(next) => {
              setMoa(next);
              onSaved();
            }}
            onClose={() => setMoaModalOpen(false)}
          />
        )}
      </CardContent>
    </Card>
  );
}

/* ──────────────────────────────────────────────────────────────────── */
/*  Page                                                                */
/* ──────────────────────────────────────────────────────────────────── */

export default function ModelsPage() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<ModelsAnalyticsResponse | null>(null);
  const [aux, setAux] = useState<AuxiliaryModelsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saveKey, setSaveKey] = useState(0);
  // Gate the token/cost UI on `dashboard.show_token_analytics`.  See
  // korra_cli/config.py for the rationale: the numbers exclude auxiliary
  // calls and retries, so they're misleading next to provider billing.
  const [showTokens, setShowTokens] = useState(false);
  const { t, tr } = useI18n();
  const { setAfterTitle, setEnd } = usePageHeader();

  useEffect(() => {
    api
      .getConfig()
      .then((cfg) => {
        const dash = (cfg?.dashboard ?? {}) as { show_token_analytics?: unknown };
        setShowTokens(dash.show_token_analytics === true);
      })
      .catch(() => {
        // Default to hidden on any failure — safer than showing wrong numbers.
        setShowTokens(false);
      });
  }, []);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      api.getModelsAnalytics(days),
      api.getAuxiliaryModels().catch(() => null),
    ])
      .then(([models, auxData]) => {
        setData(models);
        setAux(auxData);
      })
      .catch((err) => setError(ownerFacingError(err, "Не удалось загрузить данные моделей.")))
      .finally(() => setLoading(false));
  }, [days]);

  const refreshAux = useCallback(() => {
    api
      .getAuxiliaryModels()
      .then(setAux)
      .catch(() => {});
  }, []);

  const onAssigned = useCallback(() => {
    // Reload aux state after any assignment change.
    refreshAux();
    setSaveKey((k) => k + 1);
  }, [refreshAux]);

  const usageStats = data
    ? showTokens
      ? [
          { label: t.models.modelsUsed, value: String(data.totals.distinct_models) },
          { label: t.analytics.totalSessions, value: String(data.totals.total_sessions) },
          { label: t.analytics.totalTokens, value: formatTokens(data.totals.total_input + data.totals.total_output) },
          { label: t.analytics.input, value: formatTokens(data.totals.total_input) },
          { label: t.analytics.output, value: formatTokens(data.totals.total_output) },
          { label: t.models.estimatedCost, value: formatCost(data.totals.total_estimated_cost) },
        ]
      : [
          { label: "Моделей использовано", value: String(data.totals.distinct_models) },
          { label: "Чатов", value: String(data.totals.total_sessions) },
        ]
    : [];

  useLayoutEffect(() => {
    // Period selector + refresh both live in afterTitle so the controls
    // sit immediately next to the page title instead of being pinned to
    // the far-right `end` slot. The active period is conveyed by the
    // filled (non-outlined) button — no redundant period badge.
    setAfterTitle(
      <div className="flex flex-wrap items-center gap-1.5">
        <ProfileScopeChip />
        {PERIODS.map((p) => (
          <Button
            key={p.label}
            type="button"
            size="sm"
            outlined={days !== p.days}
            onClick={() => setDays(p.days)}
          >
            {tr(p.label)}
          </Button>
        ))}
        <Button
          type="button"
          ghost
          size="icon"
          className="text-muted-foreground hover:text-foreground"
          onClick={load}
          disabled={loading}
          aria-label={t.common.refresh}
        >
          {loading ? <Spinner /> : <RefreshCw />}
        </Button>
      </div>,
    );
    setEnd(null);
    return () => {
      setAfterTitle(null);
      setEnd(null);
    };
  }, [days, loading, load, setAfterTitle, setEnd, t.common.refresh, tr]);

  useEffect(() => {
    load();
  }, [load]);

  // Model assignments can change outside this page (config editor, chat
  // /model --global, CLI), so refetch them when the page regains focus.
  useEffect(() => {
    let last = 0;
    const onFocus = () => {
      if (document.visibilityState !== "visible") return;
      if (Date.now() - last < 1000) return;
      last = Date.now();
      refreshAux();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [refreshAux]);

  return (
    <div className="mx-auto flex w-full min-w-0 max-w-7xl flex-col gap-8 font-sans">
      <PluginSlot name="models:top" />

      <ModelSettingsPanel
        aux={aux}
        refreshKey={saveKey}
        onSaved={onAssigned}
      />

      {data && (
        <section aria-labelledby="model-usage-title" className="space-y-4">
          <div>
            <h2 id="model-usage-title" className="text-xl font-semibold text-foreground">
              Использование за {days} дней
            </h2>
            <p className="mt-1 text-[15px] leading-relaxed text-text-secondary">
              Здесь видно, какие модели действительно участвовали в разговорах.
            </p>
          </div>

          <Card className="min-w-0 max-w-full overflow-hidden rounded-xl border border-border/60 font-sans">
            <CardContent className="min-w-0 p-5 sm:p-6">
              <div className="grid min-w-0 grid-cols-2 gap-3 lg:grid-cols-3">
                {usageStats.map((item) => (
                  <div key={item.label} className="rounded-xl bg-[var(--neo-surface)] p-4 shadow-[var(--neo-inset-compact)]">
                    <div className="font-mono text-2xl font-semibold text-foreground">{item.value}</div>
                    <div className="mt-1 text-sm text-text-secondary">{item.label}</div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </section>
      )}

      {loading && !data && (
        <KorraLoader className="py-24" />
      )}

      {error && (
        <Card>
          <CardContent className="py-6">
            <p className="text-sm text-destructive text-center">{error}</p>
          </CardContent>
        </Card>
      )}

      {data && (
        <>
          {data.models.length > 0 ? (
            <section aria-labelledby="models-in-work-title" className="space-y-4">
              <div>
                <h2 id="models-in-work-title" className="text-xl font-semibold text-foreground">
                  Модели в работе
                </h2>
                <p className="mt-1 text-[15px] leading-relaxed text-text-secondary">
                  Нажмите «Назначить», чтобы сделать модель основной или отдать ей отдельную задачу.
                </p>
              </div>
              <div className="grid min-w-0 gap-4 md:grid-cols-2 xl:grid-cols-3">
                {data.models.map((m, i) => (
                  <ModelCard
                    key={`${m.model}:${m.provider}`}
                    entry={m}
                    rank={i + 1}
                    main={aux?.main ?? null}
                    aux={aux?.tasks ?? []}
                    onAssigned={onAssigned}
                    showTokens={showTokens}
                  />
                ))}
              </div>
            </section>
          ) : (
            <Card className="rounded-xl border border-border/60 font-sans">
              <CardContent className="py-12">
                <div className="flex flex-col items-center text-muted-foreground">
                  <Cpu className="mb-3 h-8 w-8" />
                  <p className="text-base font-semibold">Модели ещё не использовались</p>
                  <p className="mt-1 text-center text-sm text-text-secondary">
                    Начните новый чат — выбранная модель появится здесь после первого ответа.
                  </p>
                </div>
              </CardContent>
            </Card>
          )}
        </>
      )}

      <PluginSlot name="models:bottom" />
    </div>
  );
}
