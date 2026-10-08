/**
 * Подключение CRM и данные карточки «Продажи» (K21-322).
 *
 * Ключ принимает только окно подключения и сразу отправляет серверу; обратно
 * он не приходит ни в каком ответе, поэтому в состоянии здесь его нет.
 */

import { atom } from "nanostores";

import { ApiError, fetchJSON } from "@/lib/api";
import { refreshDashboardState } from "@/lib/dashboard-state";

export type CrmType = "bitrix24" | "amocrm";

export interface CrmErrorInfo {
  code: string;
  title: string;
  message: string;
  retry: boolean;
  /** Ключ сохранён, несмотря на сбой (сеть, лимит запросов). */
  saved?: boolean;
}

export interface CrmPipeline {
  id: string;
  name: string;
}

export interface CrmSettings {
  pipeline_id: string;
  stuck_days: number;
  agents_access: boolean;
}

export interface CrmConnection {
  type: CrmType;
  source_label: string;
  portal: string;
  route: string;
  account: {
    user: string;
    deals: number | null;
    deals_capped: boolean;
    managers: number | null;
    tasks: boolean;
    pipelines: CrmPipeline[];
  };
  settings: CrmSettings;
  last_check: { ok: boolean; at: string | null; code: string | null };
  saved_at: string | null;
}

export interface CrmCandidate {
  profile: string;
  label: string;
  type: CrmType;
  source_label: string;
  portal: string;
}

export interface CrmFound {
  type: CrmType;
  source_label: string;
  portal: string;
  user: string;
  deals: number | null;
  deals_capped: boolean;
  managers: number | null;
  tasks: boolean;
  pipelines: CrmPipeline[];
  pipeline_id: string;
}

export interface SalesDeal {
  id: string;
  title: string;
  amount: number;
  days: number;
  stuck: boolean;
  late: boolean;
  new: boolean;
  url: string;
  manager: string;
  stage: string;
  /** Код валюты сделки; пустой — CRM её не назвала. */
  currency?: string;
  /** Дни без движения известны как нижняя граница («не менее N»). */
  days_min?: boolean;
}

export interface SalesStage {
  id: string;
  name: string;
  count: number;
  amount: number;
  stuck: number;
  deals: SalesDeal[];
}

export interface SalesManager {
  id: string;
  name: string;
  initials: string;
  overdue: number;
  stuck: number;
  won_amount: number;
  won_count: number;
  leader: boolean;
}

export interface SalesReady {
  status: "ok";
  connection: CrmConnection;
  source: CrmType;
  source_label: string;
  portal: string;
  pipeline: CrmPipeline;
  stuck_days: number;
  /** Валюта, в которой посчитаны все суммы; пустая — неизвестна. */
  currency: string;
  /** Валюты остальных сделок: их суммы в итоги не входят. */
  other_currencies: string[];
  won: {
    amount: number;
    count: number;
    prev_amount: number;
    prev_count: number;
    change_pct: number | null;
    weeks: { start: string; amount: number; count: number }[];
    limited: boolean;
  };
  new_leads: { today: number; series: number[]; unsorted: number | null; limited?: boolean };
  stuck: {
    count: number;
    amount: number;
    days: number;
    approx: boolean;
    /** Прочитана не вся воронка: число застрявших — нижняя граница. */
    limited?: boolean;
    /** Тройка действительно самых давних; при приближённых данных — нет. */
    top_exact?: boolean;
    top: SalesDeal[];
  };
  overdue: { available: boolean; tasks: number; managers: number; limited: boolean };
  river: {
    deals_total: number;
    /** Число открытых сделок точное; иначе это нижняя граница. */
    total_exact?: boolean;
    deals_loaded: number;
    amount_total: number;
    truncated: boolean;
    limit: number;
    busiest_stage: string | null;
    stages: SalesStage[];
  };
  managers: SalesManager[];
  links: { portal: string };
  /** ISO-время последнего удачного чтения CRM. */
  as_of: string;
  /** Свежие данные получить не удалось, показаны последние известные. */
  stale: boolean;
  error?: CrmErrorInfo;
}

export type DashboardSales =
  | SalesReady
  | { status: "not_connected"; candidates: CrmCandidate[] }
  | { status: "loading"; connection: CrmConnection }
  | { status: "error"; connection?: CrmConnection; error: CrmErrorInfo };

export interface CrmCheckOk {
  ok: true;
  found: CrmFound;
  connection?: CrmConnection;
}
export interface CrmSaveOk {
  ok: true;
  connection: CrmConnection;
  warning?: CrmErrorInfo;
}
export interface CrmFailure {
  ok: false;
  error: CrmErrorInfo;
}

export interface CrmKeyInput {
  type: CrmType;
  webhook_url?: string;
  domain?: string;
  token?: string;
}

const CRM_URL = "/api/dashboard/crm";

const NETWORK_FAILURE: CrmFailure = {
  ok: false,
  error: {
    code: "network",
    title: "Не удалось связаться с панелью",
    message: "Проверьте соединение и повторите чуть позже.",
    retry: true,
  },
};

/** Ответ сервера как есть: ошибка CRM приходит телом `{ok:false}`, а не исключением. */
async function request<T>(method: string, path: string, body?: unknown): Promise<T | CrmFailure> {
  try {
    return await fetchJSON<T>(`${CRM_URL}${path}`, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (cause) {
    const payload = cause instanceof ApiError ? (cause.payload as Partial<CrmFailure> | undefined) : undefined;
    if (payload && payload.ok === false && payload.error) return payload as CrmFailure;
    return NETWORK_FAILURE;
  }
}

/** Ошибка, которую сформировал сам браузер: до панели не дошли, о ключе ничего не известно. */
export function isPanelUnreachable(error: CrmErrorInfo): boolean {
  return error === NETWORK_FAILURE.error;
}

/** Сбои, после которых ключ сохраняют и проверяют позже: сервер принимает те же четыре кода. */
const DEFERRABLE_CODES = ["network", "rate_limited", "budget", "limit"];

export function canSaveForLater(error: CrmErrorInfo): boolean {
  return DEFERRABLE_CODES.includes(error.code) && !isPanelUnreachable(error);
}

function isFailure(value: unknown): value is CrmFailure {
  return Boolean(value && (value as { ok?: unknown }).ok === false);
}

/** Сводка читает карточку и меню; после любого изменения подключения её перечитывают. */
async function changed<T extends { ok: boolean }>(result: T | CrmFailure): Promise<T | CrmFailure> {
  if (!isFailure(result)) await refreshDashboardState();
  return result;
}

export const crmApi = {
  check: (input?: CrmKeyInput) => request<CrmCheckOk>("POST", "/check", input ?? {}),
  save: async (input: CrmKeyInput, settings: Partial<CrmSettings>) =>
    changed(await request<CrmSaveOk>("PUT", "", { ...input, settings })),
  settings: async (patch: Partial<CrmSettings>) => {
    const result = await request<CrmConnection>("PATCH", "", patch);
    if (!isFailure(result)) await refreshDashboardState();
    return result;
  },
  recheck: async () => changed(await request<CrmCheckOk>("POST", "/check", {})),
  adopt: async (candidate: Pick<CrmCandidate, "profile" | "type">) =>
    changed(await request<CrmSaveOk>("POST", "/adopt", candidate)),
  disconnect: async () => {
    const result = await request<{ state: string }>("DELETE", "");
    if (!isFailure(result)) await refreshDashboardState();
    return result;
  },
};

export { isFailure as isCrmFailure };

// ── Окно подключения ─────────────────────────────────────────────────────

export type CrmDialogMode = "connect" | "replace" | "settings";

/** Открытое окно подключения; `null` — окна нет. Одно на страницу. */
export const $crmDialog = atom<CrmDialogMode | null>(null);

export function openCrmDialog(mode: CrmDialogMode = "connect"): void {
  $crmDialog.set(mode);
}

export function closeCrmDialog(): void {
  $crmDialog.set(null);
}

// ── Правила, которые карточка показывает человеку ────────────────────────

const MONTHS_IN = [
  "январе", "феврале", "марте", "апреле", "мае", "июне",
  "июле", "августе", "сентябре", "октябре", "ноябре", "декабре",
];
const MONTHS_TO = [
  "январю", "февралю", "марту", "апрелю", "маю", "июню",
  "июлю", "августу", "сентябрю", "октябрю", "ноябрю", "декабрю",
];

/** Номер месяца в поясе установки по ISO-времени последнего чтения. */
export function monthOf(iso: string, timeZone: string): number {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return new Date().getMonth();
  const part = new Intl.DateTimeFormat("en-US", { month: "numeric", timeZone }).format(date);
  return Number(part) - 1;
}

export function monthIn(month: number): string {
  return MONTHS_IN[((month % 12) + 12) % 12];
}

/** «к сентябрю» — прошлый месяц для сравнения. */
export function previousMonthTo(month: number): string {
  return MONTHS_TO[(((month - 1) % 12) + 12) % 12];
}

export interface Money {
  value: string;
  unit: string;
}

const CURRENCY_SIGNS: Record<string, string> = {
  RUB: "₽", KZT: "₸", BYN: "Br", USD: "$", EUR: "€", UAH: "₴",
};

/** Знак по коду валюты из данных сделок; незнакомый код показываем как есть, пустой — без знака. */
export function currencySign(currency: string | undefined): string {
  const code = (currency ?? "").trim().toUpperCase();
  return CURRENCY_SIGNS[code] ?? code;
}

/** 2 840 000 → «2,8 млн», 126 500 → «126,5 тыс.», 840 → «840». */
export function moneyParts(amount: number): Money {
  const abs = Math.abs(amount);
  const fmt = (n: number, digits: number) =>
    new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits }).format(n);
  if (abs >= 1_000_000) return { value: fmt(amount / 1_000_000, abs >= 10_000_000 ? 0 : 1), unit: "млн" };
  if (abs >= 10_000) return { value: fmt(amount / 1_000, abs >= 100_000 ? 0 : 1), unit: "тыс." };
  return { value: fmt(amount, 0), unit: "" };
}

export function moneyText(amount: number, currency: string | undefined): string {
  const { value, unit } = moneyParts(amount);
  return [value, unit, currencySign(currency)].filter(Boolean).join(" ");
}

/** Полная сумма без сокращений: «840 000 ₽». */
export function moneyFull(amount: number, currency: string | undefined): string {
  return [new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(amount), currencySign(currency)]
    .filter(Boolean)
    .join(" ");
}

/** Текст поручения агенту; сделки он читает сам инструментом `crm_sales`. */
export function stuckDraft(sales: SalesReady): string {
  const sum = moneyText(sales.stuck.amount, sales.currency);
  const count = `${sales.stuck.limited ? "не менее " : ""}${sales.stuck.count}`;
  return (
    `Разбери застрявшие сделки в ${sales.source_label}: их ${count} на ${sum}, ` +
    `этап не менялся больше ${sales.stuck_days} дн. Возьми список инструментом crm_sales (action=stuck), ` +
    `начни с самых давних и по каждой предложи следующий шаг и что написать клиенту. В CRM ничего не меняй.`
  );
}

export function stuckChatLink(sales: SalesReady): string {
  return `/agents?agent=default&draft=${encodeURIComponent(stuckDraft(sales))}`;
}
