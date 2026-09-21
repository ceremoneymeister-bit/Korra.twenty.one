import { Checkbox } from "@nous-research/ui/ui/components/checkbox";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Input } from "@nous-research/ui/ui/components/input";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ProductButton";
import type { GatewayClient } from "@/lib/gatewayClient";
import { Check, RefreshCw, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { fuzzyRank } from "@/lib/fuzzy";
import { queryMatchesProviderOnly } from "@/lib/model-picker-filter";
import { modelSearchText } from "@/lib/model-search-text";
import { providerDisplayName } from "@/lib/model-choices";
import { useI18n } from "@/i18n";
import { ownerFacingError } from "@/lib/owner-facing-error";
import { russianInterfaceText } from "@/lib/russian-interface-text";

/**
 * Two-stage model picker modal.
 *
 * Mirrors ui-tui/src/components/modelPicker.tsx:
 *   Stage 1: pick provider (authenticated providers only)
 *   Stage 2: pick model within that provider
 *
 * Two invocation modes:
 *
 * 1. Chat-session mode (ChatSidebar) — pass `gw` + `sessionId`. The picker
 *    loads options via `model.options` JSON-RPC and applies the choice via
 *    `config.set`, so expensive-model confirmation can happen before switch.
 *
 * 2. Standalone mode (ModelsPage, Config settings) — pass a `loader` and
 *    `onApply`. The picker fetches options via the REST endpoint and calls
 *    `onApply(provider, model, persistGlobal)` instead of emitting a slash
 *    command.  This lets the Models page reuse the same UI without
 *    requiring an open chat PTY.
 */

interface ModelOptionProvider {
  name: string;
  slug: string;
  models?: string[];
  total_models?: number;
  is_current?: boolean;
  warning?: string;
}

interface ModelOptionsResponse {
  model?: string;
  provider?: string;
  providers?: ModelOptionProvider[];
}

interface ExpensiveModelConfirmResponse {
  confirm_message?: string;
  confirm_required?: boolean;
  warning?: string;
}

interface ConfigSetResponse extends ExpensiveModelConfirmResponse {
  value?: string;
}

interface PendingExpensiveConfirm {
  message: string;
  model: string;
  persistGlobal: boolean;
  provider: string;
}

interface Props {
  /** Chat-mode: when present, picker emits a slash command via onSubmit. */
  gw?: GatewayClient;
  sessionId?: string;
  onSubmit?(slashCommand: string): void;

  /** Standalone-mode: when present (and onSubmit absent), picker calls onApply. */
  loader?(options?: { refresh?: boolean }): Promise<ModelOptionsResponse>;
  onApply?(args: {
    confirmExpensiveModel?: boolean;
    provider: string;
    model: string;
    persistGlobal: boolean;
  }):
    | Promise<ExpensiveModelConfirmResponse | void>
    | ExpensiveModelConfirmResponse
    | void;

  onClose(): void;
  title?: string;
  /** If true, hides "Persist globally" checkbox — always saves to config.yaml. */
  alwaysGlobal?: boolean;
}

export function ModelPickerDialog(props: Props) {
  const { t, tr } = useI18n();
  const {
    gw,
    sessionId,
    onSubmit,
    loader,
    onApply,
    onClose,
    title,
    alwaysGlobal = false,
  } = props;
  const dialogTitle = title ?? tr("Switch Model");
  const standalone = !!loader && !!onApply;

  const [providers, setProviders] = useState<ModelOptionProvider[]>([]);
  const [currentModel, setCurrentModel] = useState("");
  const [currentProviderSlug, setCurrentProviderSlug] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedSlug, setSelectedSlug] = useState("");
  const [selectedModel, setSelectedModel] = useState("");
  const [query, setQuery] = useState("");
  const [persistGlobal, setPersistGlobal] = useState(alwaysGlobal);
  const [applying, setApplying] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [pendingConfirm, setPendingConfirm] =
    useState<PendingExpensiveConfirm | null>(null);
  const closedRef = useRef(false);

  const applyOptions = (r: ModelOptionsResponse) => {
    const next = r?.providers ?? [];
    setProviders(next);
    setCurrentModel(String(r?.model ?? ""));
    setCurrentProviderSlug(String(r?.provider ?? ""));
    setSelectedSlug((prev) => {
      if (prev && next.some((p) => p.slug === prev)) return prev;
      return (next.find((p) => p.is_current) ?? next[0])?.slug ?? "";
    });
    setSelectedModel("");
  };

  const requestOptions = (refresh = false) =>
    standalone
      ? (loader as (options?: { refresh?: boolean }) => Promise<ModelOptionsResponse>)({
          refresh,
        })
      : (gw as GatewayClient).request<ModelOptionsResponse>(
          "model.options",
          {
            ...(sessionId ? { session_id: sessionId } : {}),
            ...(refresh ? { refresh: true } : {}),
            // Dashboard picker mirrors the TUI: full provider universe with
            // setup warnings. The backend now defaults to the configured
            // subset (#56974), so opt into unconfigured rows explicitly.
            include_unconfigured: true,
          },
        );

  const refreshOptions = () => {
    setError(null);
    setRefreshing(true);

    requestOptions(true)
      .then((r) => {
        if (closedRef.current) return;
        applyOptions(r);
      })
      .catch((e) => {
        if (closedRef.current) return;
        setError(ownerFacingError(e, "Не удалось обновить список моделей."));
      })
      .finally(() => {
        if (closedRef.current) return;
        setRefreshing(false);
      });
  };

  // Load providers + models on open.
  useEffect(() => {
    closedRef.current = false;

    requestOptions()
      .then((r) => {
        if (closedRef.current) return;
        applyOptions(r);
      })
      .catch((e) => {
        if (closedRef.current) return;
        setError(ownerFacingError(e, "Не удалось загрузить список моделей."));
      })
      .finally(() => {
        if (closedRef.current) return;
        setLoading(false);
      });

    return () => {
      closedRef.current = true;
    };
    // Deliberately omit props from deps — stable for the dialog's lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Esc closes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const selectedProvider = useMemo(
    () => providers.find((p) => p.slug === selectedSlug) ?? null,
    [providers, selectedSlug],
  );

  const models = useMemo(
    () => selectedProvider?.models ?? [],
    [selectedProvider],
  );

  const trimmedQuery = query.trim();

  // Fuzzy-ranked providers: match on name + slug + the provider's model ids so
  // typing a model name surfaces its provider (preserves the prior behaviour
  // where a model match also revealed its provider).
  //
  // With no query, float providers that actually have models to the top
  // (stable within each group). A fresh install lists ~40 providers and only
  // a couple are configured — burying "OpenRouter · 37 models" under a wall
  // of "0 models" rows made the picker feel broken.
  const filteredProviders = useMemo(() => {
    const ranked = fuzzyRank(
      providers,
      trimmedQuery,
      (p) => `${p.name} ${p.slug} ${(p.models ?? []).join(" ")}`,
    ).map((r) => r.item);
    if (trimmedQuery) return ranked;
    const withModels = ranked.filter((p) => (p.models ?? []).length > 0);
    const withoutModels = ranked.filter((p) => (p.models ?? []).length === 0);
    return [...withModels, ...withoutModels];
  }, [providers, trimmedQuery]);

  // A query that matched the SELECTED provider by name/slug (not its models)
  // located that provider — it shouldn't also hide that provider's models
  // just because their ids don't share a substring with the provider name
  // (e.g. typing "aws" to find "AWS Build" then finding zero of its Claude
  // model ids contain "aws"). Fall back to an unfiltered model list in that
  // case; a query that also matches a model id keeps filtering normally.
  const queryMatchesSelectedProviderOnly = useMemo(
    () => queryMatchesProviderOnly(selectedProvider, models, trimmedQuery),
    [trimmedQuery, selectedProvider, models],
  );

  // Fuzzy-ranked models carrying the matched character positions so the model
  // list can highlight why each entry matched. modelSearchText adds aliases
  // for brand-less wire ids (e.g. Kimi Coding `k3` ↔ search "kimi").
  const filteredModels = useMemo(
    () =>
      fuzzyRank(
        models,
        queryMatchesSelectedProviderOnly ? "" : trimmedQuery,
        modelSearchText,
      ).map((r) => ({
        model: r.item,
        // Positions may land in alias suffixes — keep only in-id highlights.
        positions: r.positions.filter((i) => i >= 0 && i < r.item.length),
      })),
    [models, trimmedQuery, queryMatchesSelectedProviderOnly],
  );

  const canConfirm = !!selectedProvider && !!selectedModel && !applying;

  const applySelection = async (
    confirmExpensiveModel = false,
    forced?: PendingExpensiveConfirm,
  ) => {
    const providerSlug = forced?.provider ?? selectedProvider?.slug ?? "";
    const model = forced?.model ?? selectedModel;
    const shouldPersistGlobal = forced?.persistGlobal ?? persistGlobal;

    if (!providerSlug || !model || applying) return;

    if (standalone && onApply) {
      setApplying(true);
      try {
        const result = await onApply({
          confirmExpensiveModel,
          provider: providerSlug,
          model,
          persistGlobal: shouldPersistGlobal,
        });
        if (result?.confirm_required) {
          setPendingConfirm({
            provider: providerSlug,
            model,
            persistGlobal: shouldPersistGlobal,
            message: russianInterfaceText(
              result.confirm_message || result.warning,
              tr("This model has unusually high known pricing."),
            ),
          });
          return;
        }
        onClose();
      } catch (e) {
        setError(ownerFacingError(e, "Не удалось выбрать модель."));
      } finally {
        setApplying(false);
      }
    } else if (gw && sessionId) {
      setApplying(true);
      try {
        const global = shouldPersistGlobal ? " --global" : "";
        const result = await gw.request<ConfigSetResponse>("config.set", {
          confirm_expensive_model: confirmExpensiveModel,
          key: "model",
          session_id: sessionId,
          value: `${model} --provider ${providerSlug}${global}`,
        });
        if (result?.confirm_required) {
          setPendingConfirm({
            provider: providerSlug,
            model,
            persistGlobal: shouldPersistGlobal,
            message: russianInterfaceText(
              result.confirm_message || result.warning,
              tr("This model has unusually high known pricing."),
            ),
          });
          return;
        }
        onClose();
      } catch (e) {
        setError(ownerFacingError(e, "Не удалось выбрать модель."));
      } finally {
        setApplying(false);
      }
    } else if (onSubmit) {
      const global = shouldPersistGlobal ? " --global" : "";
      onSubmit(`/model ${model} --provider ${providerSlug}${global}`);
      onClose();
    }
  };

  const confirm = () => {
    if (!canConfirm) return;
    void applySelection();
  };

  // Portal to document.body: the main dashboard column in App.tsx is
  // `relative z-2`, which creates a stacking context that traps fixed
  // descendants below the app sidebar (z-50). Without the portal this
  // modal's z-[100] is scoped to z-2 and the sidebar covers its left
  // edge — visible especially in the Large theme variants where the
  // larger root font widens the dialog into the sidebar's column. See
  // Toast.tsx for the same pattern.
  return createPortal(
    <div
      className="neo-overlay fixed inset-0 z-[100] flex items-center justify-center p-3 sm:p-6"
      onClick={(e) => e.target === e.currentTarget && onClose()}
      role="dialog"
      aria-modal="true"
      aria-labelledby="model-picker-title"
    >
      <div className="neo-dialog relative flex max-h-[min(90dvh,52rem)] w-full max-w-4xl flex-col overflow-hidden font-sans">
        <Button
          ghost
          size="icon"
          onClick={onClose}
          className="absolute right-3 top-3 z-10 text-muted-foreground hover:text-foreground"
          aria-label={t.common.close}
        >
          <X />
        </Button>

        <header className="px-5 pb-4 pt-5 pr-16 sm:px-7 sm:pt-7">
          <h2
            id="model-picker-title"
            className="text-2xl font-semibold leading-tight text-foreground"
          >
            {dialogTitle}
          </h2>
          <div className="mt-3 flex flex-wrap items-center gap-2 text-sm text-text-secondary">
            <span>Сейчас выбрана</span>
            <span className="max-w-full truncate rounded-full bg-[var(--neo-surface)] px-3 py-1 font-mono text-xs text-foreground shadow-[var(--neo-inset-compact)]">
              {currentProviderSlug && `${providerDisplayName(currentProviderSlug)} · `}
              {currentModel || "модель не выбрана"}
            </span>
          </div>
        </header>

        <div className="px-5 pb-4 sm:px-7">
          <div className="relative">
            <Search className="absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              autoFocus
              aria-label="Найти поставщика или модель"
              placeholder="Найти поставщика или модель"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="h-11 rounded-xl pl-11 pr-4 font-sans text-sm"
            />
          </div>
        </div>

        <div className="mx-3 mb-3 grid min-h-0 flex-1 grid-cols-1 overflow-hidden rounded-2xl bg-[var(--neo-surface)] shadow-[var(--neo-inset-compact)] sm:mx-5 sm:mb-4 md:grid-cols-[minmax(15rem,0.8fr)_minmax(0,1.4fr)]">
          <ProviderColumn
            loading={loading}
            error={error}
            providers={filteredProviders}
            total={providers.length}
            selectedSlug={selectedSlug}
            query={trimmedQuery}
            onSelect={(slug) => {
              setSelectedSlug(slug);
              setSelectedModel("");
            }}
          />

          <ModelColumn
            provider={selectedProvider}
            models={filteredModels}
            allModels={models}
            selectedModel={selectedModel}
            currentModel={currentModel}
            currentProviderSlug={currentProviderSlug}
            onSelect={setSelectedModel}
            onConfirm={(m) => {
              setSelectedModel(m);
              void applySelection(false, {
                provider: selectedProvider?.slug ?? "",
                model: m,
                persistGlobal,
                message: "",
              });
            }}
          />
        </div>

        <footer className="flex flex-col gap-3 px-5 pb-5 pt-1 sm:px-7 sm:pb-6 lg:flex-row lg:items-center lg:justify-between">
          {alwaysGlobal ? (
            <span className="max-w-xl text-sm leading-relaxed text-text-secondary">
              Выбор будет действовать в новых чатах. Уже открытый разговор продолжит работать с прежней моделью.
            </span>
          ) : (
            <label
              className="flex min-h-11 cursor-pointer items-center gap-3 text-sm text-text-secondary"
              htmlFor="model-picker-persist-global"
            >
              <Checkbox
                checked={persistGlobal}
                id="model-picker-persist-global"
                onCheckedChange={(checked) =>
                  setPersistGlobal(checked === true)
                }
              />
              Использовать в новых чатах, а не только в этом
            </label>
          )}

          <div className="flex w-full flex-col-reverse gap-2 sm:flex-row lg:ml-auto lg:w-auto">
            <Button
              ghost
              outlined
              onClick={refreshOptions}
              disabled={applying || loading || refreshing}
              className="w-full sm:w-auto"
            >
              {refreshing ? <Spinner /> : <RefreshCw className="h-3.5 w-3.5" />}
              Обновить список
            </Button>
            <Button outlined onClick={onClose} disabled={applying} className="w-full sm:w-auto">
              {t.common.cancel}
            </Button>
            <Button onClick={confirm} disabled={!canConfirm} className="w-full sm:w-auto">
              {applying ? <Spinner /> : "Выбрать модель"}
            </Button>
          </div>
        </footer>
      </div>
      <ConfirmDialog
        open={!!pendingConfirm}
        title={tr("Expensive Model Warning")}
        description={pendingConfirm?.message}
        destructive
        confirmLabel={tr("Switch anyway")}
        cancelLabel={t.common.cancel}
        loading={applying}
        onCancel={() => setPendingConfirm(null)}
        onConfirm={() => {
          const pending = pendingConfirm;
          if (!pending) return;
          setPendingConfirm(null);
          void applySelection(true, pending);
        }}
      />
    </div>,
    document.body,
  );
}

/* ------------------------------------------------------------------ */
/*  Provider column                                                    */
/* ------------------------------------------------------------------ */

function ProviderColumn({
  loading,
  error,
  providers,
  total,
  selectedSlug,
  query,
  onSelect,
}: {
  loading: boolean;
  error: string | null;
  providers: ModelOptionProvider[];
  total: number;
  selectedSlug: string;
  query: string;
  onSelect(slug: string): void;
}) {
  const { tr } = useI18n();
  return (
    <section
      aria-label="Поставщики моделей"
      className="flex min-h-0 flex-col border-b border-border/50 md:border-b-0 md:border-r"
    >
      <div className="flex items-center justify-between px-4 pb-2 pt-4">
        <h3 className="text-sm font-semibold text-foreground">Поставщик</h3>
        {!loading && (
          <span className="text-xs text-text-tertiary">{providers.length} из {total}</span>
        )}
      </div>
      <div className="max-h-[28dvh] min-h-0 overflow-y-auto px-2 pb-2 md:max-h-none md:flex-1">
      {loading && (
        <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
          <Spinner /> Загружаем поставщиков…
        </div>
      )}

      {error && <div className="m-2 rounded-xl bg-destructive/10 p-3 text-sm text-destructive">{error}</div>}

      {!loading && !error && providers.length === 0 && (
        <div className="p-4 text-sm leading-relaxed text-muted-foreground">
          {query
            ? "Ничего не найдено. Попробуйте изменить запрос."
            : total === 0
              ? "Поставщики моделей пока не подключены."
              : "Ничего не найдено."}
        </div>
      )}

      {providers.map((p) => {
        const active = p.slug === selectedSlug;
        const modelCount = p.total_models ?? p.models?.length ?? 0;
        return (
          <button
            type="button"
            role="option"
            aria-selected={active}
            key={p.slug}
            onClick={() => onSelect(p.slug)}
            className={`mb-1 flex min-h-14 w-full items-center gap-3 rounded-xl px-3 py-2 text-left outline-none transition-colors ${
              active
                ? "bg-[var(--neo-surface)] text-foreground shadow-[var(--neo-depth-1)]"
                : "text-text-secondary hover:bg-background/35 hover:text-foreground"
            }`}
          >
            <span
              aria-hidden
              className={`flex size-7 shrink-0 items-center justify-center rounded-full ${
                active ? "bg-primary text-primary-foreground" : "bg-background/60 text-transparent"
              }`}
            >
              <Check className="size-4" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-2">
                <span className="truncate text-sm font-semibold">
                  {providerDisplayName(p.slug, p.name)}
                </span>
                {p.is_current && <CurrentTag />}
              </span>
              <span className="mt-0.5 block truncate text-xs text-text-tertiary">
                {modelCount > 0 ? tr("{count} models", { count: modelCount }) : "нужно подключить"}
              </span>
            </span>
          </button>
        );
      })}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Model column                                                       */
/* ------------------------------------------------------------------ */

function ModelColumn({
  provider,
  models,
  allModels,
  selectedModel,
  currentModel,
  currentProviderSlug,
  onSelect,
  onConfirm,
}: {
  provider: ModelOptionProvider | null;
  models: { model: string; positions: number[] }[];
  allModels: string[];
  selectedModel: string;
  currentModel: string;
  currentProviderSlug: string;
  onSelect(model: string): void;
  onConfirm(model: string): void;
}) {
  if (!provider) {
    return (
      <section className="min-h-40 overflow-y-auto" aria-label="Модели">
        <div className="p-5 text-sm text-muted-foreground">
          Сначала выберите поставщика.
        </div>
      </section>
    );
  }

  return (
    <section className="flex min-h-0 flex-col" aria-label="Модели">
      <div className="flex items-center justify-between gap-3 px-4 pb-2 pt-4">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-foreground">Модель</h3>
          <p className="truncate text-xs text-text-tertiary">
            {providerDisplayName(provider.slug, provider.name)}
          </p>
        </div>
        <span className="shrink-0 text-xs text-text-tertiary">{models.length} из {allModels.length}</span>
      </div>

      {provider.warning && (
        <div className="mx-3 mb-2 rounded-xl bg-warning/10 p-3 text-sm leading-relaxed text-text-secondary">
          {russianInterfaceText(
            provider.warning,
            "Провайдер требует настройки перед использованием.",
          )}
        </div>
      )}

      {models.length === 0 ? (
        <div className="p-5 text-sm leading-relaxed text-muted-foreground">
          {allModels.length
            ? "Среди моделей этого поставщика ничего не найдено."
            : "У этого поставщика пока нет доступных моделей."}
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          {models.map(({ model: m, positions }) => {
          const active = m === selectedModel;
          const isCurrent =
            m === currentModel && provider.slug === currentProviderSlug;

          return (
            <button
              type="button"
              role="option"
              aria-selected={active}
              key={m}
              onClick={() => onSelect(m)}
              onDoubleClick={() => onConfirm(m)}
              className={`mb-1 flex min-h-12 w-full items-center gap-3 rounded-xl px-3 py-2 text-left outline-none transition-colors ${
                active
                  ? "bg-[var(--neo-surface)] text-foreground shadow-[var(--neo-depth-1)]"
                  : "text-text-secondary hover:bg-background/35 hover:text-foreground"
              }`}
            >
              <Check
                className={`h-4 w-4 shrink-0 ${active ? "text-primary" : "text-transparent"}`}
              />
              <span className="flex-1 truncate font-mono text-sm">
                <HighlightedText text={m} positions={positions} />
              </span>
              {isCurrent && <CurrentTag />}
            </button>
          );
          })}
        </div>
      )}
    </section>
  );
}

function CurrentTag() {
  const { tr } = useI18n();
  return (
    <span className="shrink-0 rounded-full bg-primary/15 px-2 py-0.5 text-xs font-medium text-primary">
      {tr("current") === "current" ? "выбрана" : tr("current")}
    </span>
  );
}

/**
 * Render `text` with the characters at `positions` emphasised, so users can
 * see which characters their fuzzy query matched. Positions are indices into
 * `text`; out-of-range indices are ignored.
 */
function HighlightedText({
  text,
  positions,
}: {
  text: string;
  positions: number[];
}) {
  if (!positions.length) {
    return <>{text}</>;
  }

  const hit = new Set(positions);

  return (
    <>
      {Array.from(text).map((ch, i) =>
        hit.has(i) ? (
          <mark
            key={i}
            className="bg-transparent text-primary font-semibold underline underline-offset-2"
          >
            {ch}
          </mark>
        ) : (
          <span key={i}>{ch}</span>
        ),
      )}
    </>
  );
}
