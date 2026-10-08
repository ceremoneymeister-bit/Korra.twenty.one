import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useStore } from "@nanostores/react";
import { AlertTriangle, Check, Info, X } from "lucide-react";

import { ProductButton } from "@/components/ProductButton";
import {
  $crmDialog,
  closeCrmDialog,
  crmApi,
  isCrmFailure,
  type CrmConnection,
  type CrmDialogMode,
  type CrmErrorInfo,
  type CrmFound,
  type CrmKeyInput,
  type CrmSettings,
  type CrmType,
} from "@/lib/crm";
import { $dashboardState, plural } from "@/lib/dashboard-state";
import { cn } from "@/lib/utils";

const STUCK_CHOICES = [3, 5, 7, 10, 14, 21, 30];

type Step = "choose" | "key" | "found" | "failed" | "kept";

const CRM_NAMES: Record<CrmType, string> = { bitrix24: "Битрикс24", amocrm: "amoCRM" };

const GUIDE: Record<CrmType, { time: string; path: string[]; steps: ReactNode[] }> = {
  bitrix24: {
    time: "1 минута",
    path: ["Приложения", "Разработчикам", "Другое", "Входящий вебхук"],
    steps: [
      <>Права: <b>CRM</b>, <b>Задачи</b>, <b>Пользователи</b>.</>,
      <>Нажмите «Сохранить» и скопируйте адрес из поля «Вебхук для вызова rest api».</>,
    ],
  },
  amocrm: {
    time: "2 минуты",
    path: ["Настройки", "Интеграции", "Создать интеграцию", "Ключи и доступы"],
    steps: [
      <>Назовите интеграцию «Korra», доступ — «Сделки, контакты, задачи».</>,
      <>Нажмите «Сгенерировать долгосрочный токен» и скопируйте его.</>,
    ],
  },
};

function foundFromConnection(connection: CrmConnection): CrmFound {
  const { account } = connection;
  return {
    type: connection.type,
    source_label: connection.source_label,
    portal: connection.portal,
    user: account.user,
    deals: account.deals,
    deals_capped: account.deals_capped,
    managers: account.managers,
    tasks: account.tasks,
    pipelines: account.pipelines,
    pipeline_id: connection.settings.pipeline_id,
  };
}

function count(value: number | null, capped: boolean): string {
  if (value === null) return "—";
  return capped ? `${value}+` : String(value);
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="kdw-crm-field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function Toggle({ checked, label, onChange }: { checked: boolean; label: string; onChange: (value: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className="kdw-crm-switch"
      onClick={() => onChange(!checked)}
    />
  );
}

export function CrmSettingsForm({
  found,
  onChange,
  settings,
}: {
  found: CrmFound;
  onChange: (next: CrmSettings) => void;
  settings: CrmSettings;
}) {
  const pipelines = found.pipelines;
  return (
    <div className="kdw-crm-options">
      {pipelines.length > 0 ? (
        <div className="kdw-crm-opt">
          <span>
            Воронка в карточке<small>в карточке и графиках</small>
          </span>
          <select
            aria-label="Воронка в карточке"
            value={settings.pipeline_id}
            onChange={(event) => onChange({ ...settings, pipeline_id: event.target.value })}
          >
            {pipelines.map((pipeline) => (
              <option key={pipeline.id} value={pipeline.id}>
                {pipeline.name}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      <div className="kdw-crm-opt">
        <span>
          Считать застрявшей<small>этап не менялся столько дней</small>
        </span>
        <select
          aria-label="Считать застрявшей"
          value={settings.stuck_days}
          onChange={(event) => onChange({ ...settings, stuck_days: Number(event.target.value) })}
        >
          {(STUCK_CHOICES.includes(settings.stuck_days) ? STUCK_CHOICES : [...STUCK_CHOICES, settings.stuck_days].sort((a, b) => a - b)).map(
            (days) => (
              <option key={days} value={days}>
                {days} {plural(days, ["день", "дня", "дней"])}
              </option>
            ),
          )}
        </select>
      </div>
      <div className="kdw-crm-opt">
        <span>
          Агенты могут читать CRM<small>любой агент — по вашей просьбе; посетители общих ботов — нет</small>
        </span>
        <Toggle
          checked={settings.agents_access}
          label="Агенты могут читать CRM"
          onChange={(value) => onChange({ ...settings, agents_access: value })}
        />
      </div>
    </div>
  );
}

function Stepper({ step }: { step: Step }) {
  const at = step === "choose" ? 1 : step === "key" || step === "failed" ? 2 : 3;
  return (
    <div className="kdw-crm-stepper" aria-hidden>
      {[1, 2, 3].map((n) => (
        <i key={n} className={cn(n <= at && "kdw-crm-stepper--on")} />
      ))}
    </div>
  );
}

function ErrorPanel({ error }: { error: CrmErrorInfo }) {
  return (
    <div className="kdw-crm-err" role="alert">
      <i aria-hidden>
        <AlertTriangle className="size-4" />
      </i>
      <span>
        <b>{error.title}</b>
        <span>{error.message}</span>
      </span>
    </div>
  );
}

function DialogBody({ connection, mode }: { connection: CrmConnection | null; mode: CrmDialogMode }) {
  const [step, setStep] = useState<Step>(mode === "settings" ? "found" : mode === "replace" ? "key" : "choose");
  const [type, setType] = useState<CrmType>(connection?.type ?? "bitrix24");
  const [webhook, setWebhook] = useState("");
  const [domain, setDomain] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [found, setFound] = useState<CrmFound | null>(
    mode === "settings" && connection ? foundFromConnection(connection) : null,
  );
  const [settings, setSettings] = useState<CrmSettings>(
    connection?.settings ?? { pipeline_id: "", stuck_days: 7, agents_access: true },
  );
  const [error, setError] = useState<CrmErrorInfo | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const working = useRef(false);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialogRef.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeCrmDialog();
      }
    };
    document.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      previous?.focus?.();
    };
  }, []);

  const input = (): CrmKeyInput =>
    type === "bitrix24"
      ? { type, webhook_url: webhook.trim() }
      : { type, domain: domain.trim(), token: token.trim() };
  const ready = type === "bitrix24" ? webhook.trim() !== "" : domain.trim() !== "" && token.trim() !== "";

  const run = async (job: () => Promise<void>) => {
    if (working.current) return;
    working.current = true;
    setBusy(true);
    try {
      await job();
    } finally {
      working.current = false;
      setBusy(false);
    }
  };

  const check = () =>
    run(async () => {
      const result = await crmApi.check(input());
      if (isCrmFailure(result)) {
        setError(result.error);
        setStep("failed");
        return;
      }
      const same = connection && connection.type === type && connection.portal === result.found.portal;
      setFound(result.found);
      setSettings(
        same
          ? { ...connection.settings, pipeline_id: connection.settings.pipeline_id || result.found.pipeline_id }
          : { pipeline_id: result.found.pipeline_id, stuck_days: 7, agents_access: true },
      );
      setError(null);
      setStep("found");
    });

  const finish = () =>
    run(async () => {
      if (mode === "settings") {
        const result = await crmApi.settings(settings);
        if (isCrmFailure(result)) {
          setError(result.error);
          setStep("failed");
          return;
        }
        closeCrmDialog();
        return;
      }
      const result = await crmApi.save(input(), settings);
      if (isCrmFailure(result)) {
        setError(result.error);
        setStep("failed");
        return;
      }
      setWebhook("");
      setToken("");
      if (result.warning) {
        setError(result.warning);
        setStep("kept");
        return;
      }
      closeCrmDialog();
    });

  const title =
    step === "choose"
      ? "Какая у вас CRM?"
      : step === "key"
        ? type === "bitrix24"
          ? "Битрикс24: адрес вебхука"
          : "amoCRM: адрес и токен"
        : step === "found"
          ? mode === "settings"
            ? "Подключение CRM"
            : "Подключено"
          : step === "kept"
            ? "Ключ сохранён"
            : error?.title || "Не получилось";

  const guide = GUIDE[type];
  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="crm-dialog-title"
      className="neo-overlay fixed inset-0 z-[200] flex items-center justify-center p-4"
      onClick={(event) => {
        if (event.target === event.currentTarget) closeCrmDialog();
      }}
    >
      <div ref={dialogRef} className="neo-dialog kdw-crm-dialog relative w-full max-w-lg font-sans">
        <div className="kdw-crm-head">
          <h2 id="crm-dialog-title">{title}</h2>
          <button type="button" aria-label="Закрыть" className="kdw-crm-x" onClick={closeCrmDialog}>
            <X className="size-4" aria-hidden />
          </button>
        </div>
        {mode !== "settings" ? <Stepper step={step} /> : null}

        {step === "choose" ? (
          <>
            <div className="kdw-crm-choices" role="radiogroup" aria-label="CRM">
              {(["bitrix24", "amocrm"] as CrmType[]).map((kind) => (
                <button
                  key={kind}
                  type="button"
                  role="radio"
                  aria-checked={type === kind}
                  data-autofocus={kind === "bitrix24" ? "" : undefined}
                  className={cn("kdw-crm-choice", type === kind && "kdw-crm-choice--on")}
                  onClick={() => setType(kind)}
                >
                  <span className={cn("kdw-crm-logo", kind === "bitrix24" ? "kdw-crm-logo--b24" : "kdw-crm-logo--amo")}>
                    {kind === "bitrix24" ? "Б24" : "amo"}
                  </span>
                  <b>{CRM_NAMES[kind]}</b>
                  <span>
                    {kind === "bitrix24"
                      ? "Через входящий вебхук. Нужны права администратора портала."
                      : "Через долгосрочный токен своей интеграции."}
                  </span>
                </button>
              ))}
            </div>
            <p className="kdw-crm-ro">Korra будет только читать CRM</p>
            <div className="kdw-crm-acts">
              <ProductButton onClick={() => setStep("key")}>Дальше</ProductButton>
            </div>
          </>
        ) : null}

        {step === "key" ? (
          <>
            <div className="kdw-crm-how">
              <b>Где взять — {guide.time}</b>
              <div className="kdw-crm-path">
                {guide.path.map((part, index) => (
                  <span key={part}>
                    {index > 0 ? <em aria-hidden>›</em> : null}
                    {part}
                  </span>
                ))}
              </div>
              <ol>
                {guide.steps.map((text, index) => (
                  <li key={index}>{text}</li>
                ))}
              </ol>
            </div>
            {type === "bitrix24" ? (
              <Field label="Адрес вебхука">
                <input
                  data-autofocus
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="https://ваш-портал.bitrix24.ru/rest/12/…/"
                  value={webhook}
                  onChange={(event) => setWebhook(event.target.value)}
                />
              </Field>
            ) : (
              <>
                <Field label="Адрес аккаунта">
                  <input
                    data-autofocus
                    type="text"
                    autoComplete="off"
                    spellCheck={false}
                    placeholder="ваш-аккаунт.amocrm.ru"
                    value={domain}
                    onChange={(event) => setDomain(event.target.value)}
                  />
                </Field>
                <Field label="Долгосрочный токен">
                  <input
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    value={token}
                    onChange={(event) => setToken(event.target.value)}
                  />
                </Field>
              </>
            )}
            {type === "bitrix24" ? (
              <p className="kdw-crm-honest">
                <Info className="size-4 shrink-0" aria-hidden />
                <span>
                  Вебхук Битрикс24 нельзя сделать «только для чтения» — его права шире. Korra использует из них только
                  чтение сделок, лидов, задач и сотрудников и ничего не меняет.
                </span>
              </p>
            ) : null}
            <div className="kdw-crm-acts">
              {mode === "replace" ? null : (
                <ProductButton outlined onClick={() => setStep("choose")} disabled={busy}>
                  Назад
                </ProductButton>
              )}
              <ProductButton onClick={() => void check()} disabled={!ready || busy}>
                {busy ? "Проверяем…" : "Проверить"}
              </ProductButton>
            </div>
          </>
        ) : null}

        {step === "found" && found ? (
          <>
            <div className="kdw-crm-ok">
              <i aria-hidden>
                <Check className="size-4" />
              </i>
              <span>
                <b>{found.portal}</b>
                <span>
                  {found.user ? `Вошли как ${found.user} · ` : ""}только чтение
                </span>
              </span>
            </div>
            <div className="kdw-crm-found">
              <div>
                <b>{count(found.deals, found.deals_capped)}</b>
                <span>{plural(found.deals ?? 0, ["сделка в работе", "сделки в работе", "сделок в работе"])}</span>
              </div>
              <div>
                <b>{found.pipelines.length}</b>
                <span>{plural(found.pipelines.length, ["воронка", "воронки", "воронок"])}</span>
              </div>
              <div>
                <b>{count(found.managers, false)}</b>
                <span>{plural(found.managers ?? 0, ["менеджер", "менеджера", "менеджеров"])}</span>
              </div>
            </div>
            {found.tasks ? null : (
              <p className="kdw-crm-honest" role="status">
                <Info className="size-4 shrink-0" aria-hidden />
                <span>
                  У ключа нет прав на задачи: просроченные задачи считать не получится. Остальное работает; права
                  можно добавить и нажать «Проверить подключение» в меню карточки.
                </span>
              </p>
            )}
            <CrmSettingsForm found={found} settings={settings} onChange={setSettings} />
            <div className="kdw-crm-acts">
              <ProductButton data-autofocus onClick={() => void finish()} disabled={busy} className="kdw-crm-wide">
                {busy ? "Сохраняем…" : mode === "settings" ? "Сохранить" : "Готово — показать продажи"}
              </ProductButton>
            </div>
          </>
        ) : null}

        {step === "failed" && error ? (
          <>
            <ErrorPanel error={error} />
            <div className="kdw-crm-acts">
              {mode === "settings" ? (
                <ProductButton data-autofocus onClick={() => setStep("found")}>
                  Понятно
                </ProductButton>
              ) : (
                <>
                  <ProductButton outlined onClick={() => setStep("key")} disabled={busy}>
                    Изменить адрес
                  </ProductButton>
                  <ProductButton data-autofocus onClick={() => void check()} disabled={!ready || busy}>
                    {busy ? "Проверяем…" : "Проверить ещё раз"}
                  </ProductButton>
                </>
              )}
            </div>
          </>
        ) : null}

        {step === "kept" && error ? (
          <>
            <ErrorPanel error={error} />
            <div className="kdw-crm-acts">
              <ProductButton data-autofocus onClick={closeCrmDialog}>
                Понятно
              </ProductButton>
            </div>
          </>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}

/** Окно подключения CRM: одно на страницу, открывается через `openCrmDialog`. */
export function CrmConnectDialog() {
  const mode = useStore($crmDialog);
  const connection = useStore($dashboardState)?.sales;
  if (!mode) return null;
  const current = connection && "connection" in connection ? (connection.connection ?? null) : null;
  // Замена ключа и настройки имеют смысл только при подключённой CRM.
  const effective = current ? mode : "connect";
  return <DialogBody key={effective} mode={effective} connection={current} />;
}
