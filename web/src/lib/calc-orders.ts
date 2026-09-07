/**
 * Заказы расчётчика: типы реестра и чистые правила их чтения.
 *
 * Живёт отдельно от страницы по той же причине, что и `product-nav`: здесь
 * решения, которые должен проверять тест, а не глаз. Экран показывает деньги
 * и «звёздочки» — цифру, по которой человек отправляет КП заказчику.
 */

export type StageName = "blank" | "route" | "time" | "quote";

export const STAGE_ORDER: StageName[] = ["blank", "route", "time", "quote"];

export const STAGE_TITLES: Record<StageName, string> = {
  blank: "Заготовка",
  route: "Маршрут",
  time: "Нормы времени",
  quote: "КП",
};

/** Кто ведёт стадию — вкладка на экране «Расчётчики», куда уводит действие. */
export const STAGE_AGENTS: Record<StageName, string> = {
  blank: "raschet-blank",
  route: "raschet-route",
  time: "raschet-time",
  quote: "raschet-time",
};

export interface StageSummary {
  status: string;
  provisional: boolean;
  proposed_at?: string | null;
  approved_at?: string | null;
  approved_by?: string | null;
}

export interface QuotePrice {
  amount_kind?: "CUSTOMER_PRICE" | string;
  total_rub?: number;
  net_total_rub?: number;
  vat_amount_rub?: number;
  vat_rate_pct?: number;
  vat_included?: boolean;
  currency?: string;
  valid_until?: string;
  is_final?: boolean;
  status_note?: string;
}

export type WorkflowStageName =
  | "input"
  | "bom"
  | "route"
  | "costing"
  | "book"
  | "qa";

export interface WorkflowStage {
  name: WorkflowStageName;
  title: string;
  status: "complete" | "current" | "pending" | "blocked";
}

export interface WorkflowSummary {
  status?: string;
  schema_version?: number;
  calculation_revision?: number;
  route_return_count?: number;
  quantity?: number;
  kd_revision?: string;
  frozen_variant_id?: string;
}

export interface InternalCost {
  amount_kind?: "INTERNAL_COST" | string;
  blank_rub?: number;
  machining_rub?: number;
  contractor_rub?: number;
  extras_rub?: number;
  total_rub?: number;
  rates_include_vat?: boolean;
  net_equivalent_rub?: number;
}

export type ContractorQuoteBasis = "per_piece" | "order_total";

export interface ContractorQuote {
  contract_version?: number;
  route_seq: number;
  process_code: string;
  amount_rub: number;
  currency: "RUB" | string;
  basis: ContractorQuoteBasis;
  vat_included: boolean;
  quoted_at: string;
  valid_until: string;
  source: string;
  reference: string;
  panel_actor?: string;
  order_quantity?: number;
  order_amount_rub?: number;
  normalized_amount_rub?: number;
  normalized_basis?: "order_total";
  normalized_vat_included?: boolean;
  vat_rate_pct?: number;
  route_digest?: string;
  calculation_revision?: number;
  pack_revision?: string;
  pack_fingerprint?: string;
  recorded_at?: string;
  digest?: string;
}

export interface ContractorQuoteLine extends WorkflowItem {
  status: "missing" | "stale" | "current";
  stale_reasons: string[];
  quote?: ContractorQuote;
}

export interface ContractorQuoteDraft {
  amount_rub: string;
  basis: ContractorQuoteBasis;
  vat_included: boolean;
  quoted_at: string;
  valid_until: string;
  source: string;
  reference: string;
}

export interface WorkflowItem {
  seq?: number;
  process_code?: string;
  actual_process_name?: string;
  execution_mode?: string;
  cost_owner?: string;
  note?: string;
  reason?: string;
}

export interface ManualReviewItem extends WorkflowItem {
  item_index: number;
  item_digest: string;
}

export interface ManualReviewDraft {
  item_index: number;
  item_digest: string;
  evidence: string;
  reference: string;
  confirmed: boolean;
}

export interface ManualReviewReceipt {
  digest?: string;
  book_digest?: string;
  manual_items_digest?: string;
  calculation_revision?: number;
  pack_revision?: string;
  reviewed_by?: string;
  recorded_at?: string;
  reviews?: Array<{
    item_index?: number;
    item_digest?: string;
    evidence?: string;
    reference?: string;
  }>;
  valid: boolean;
}

export interface QaReceipt {
  title: string;
  verdict?: string | null;
  status: "complete" | "pending" | "attention";
  computed_by?: string;
  by?: string;
  actor_role?: string;
  at?: string;
  reasons?: unknown[];
  adjust_owner?: string;
  blocked?: boolean;
  checks_total?: number;
  checks_passed?: number;
}

export type QaGate = "mechanical" | "technological" | "commercial";
export type HumanQaGate = Exclude<QaGate, "mechanical">;
export type QaVerdict = "PASS" | "ADJUST" | "BLOCK" | "NO_EVIDENCE";
export type QaAdjustOwner = "supply" | "norm" | "front";

export const QA_VERDICTS: readonly { value: QaVerdict; label: string }[] = [
  { value: "PASS", label: "Проверка пройдена" },
  { value: "ADJUST", label: "Вернуть на корректировку" },
  { value: "BLOCK", label: "Заблокировать" },
  { value: "NO_EVIDENCE", label: "Недостаточно данных" },
];

export const QA_ADJUST_OWNERS: readonly {
  value: QaAdjustOwner;
  label: string;
}[] = [
  { value: "supply", label: "Снабжение" },
  { value: "norm", label: "Нормировщик" },
  { value: "front", label: "Фронт / исходные данные" },
];

export interface OrderBlocker {
  kind: string;
  title: string;
  detail?: string;
  route_seq?: number;
  gate?: QaGate;
}

export interface OrderNextAction {
  kind: string;
  label: string;
  profile?: string | null;
  stage?: StageName | null;
  qa_gate?: QaGate;
}

export interface OrderCard {
  kind?: "legacy" | "workflow" | "draft";
  folder_name?: string;
  file_count?: number;
  total_bytes?: number;
  order_id: string;
  revision: number;
  status: string;
  created_at: string;
  updated_at: string;
  customer: string | null;
  stages: Record<string, StageSummary>;
  current_stage: StageName | null;
  status_title?: string;
  registry_status?: string;
  workflow?: WorkflowSummary;
  workflow_stages?: WorkflowStage[];
  provisional: boolean;
  /** Стадии, посчитанные по прежней ревизии данных предприятия. */
  stale_stages?: StageName[];
  /** null — сверка с активным пакетом недоступна, false — совпадает. */
  stale_pack?: boolean | null;
  /** true — книга связана с текущей ревизией, маршрутом и частями costing. */
  book_binding_valid?: boolean | null;
  book_digest?: string | null;
  price: QuotePrice | null;
  internal_cost?: InternalCost | null;
  contractor_quotes?: ContractorQuoteLine[];
  route_unpriced?: WorkflowItem[];
  manual_review_required?: ManualReviewItem[];
  manual_review_receipt?: ManualReviewReceipt | null;
  qa_receipts?: Record<string, QaReceipt>;
  blockers?: OrderBlocker[];
  next_action?: OrderNextAction;
  warnings: unknown[];
  detail?: OrderDetail;
}

export interface OrderDetail {
  stages: Record<string, StageBody>;
  events: PipelineEvent[];
  source_files: SourceFile[];
  route_steps?: WorkflowItem[];
  provenance: Record<string, unknown>;
}

export interface SourceFile {
  relative_path?: string;
  download_url?: string;
  source_file_id?: string;
  name?: string;
  sha256?: string;
  format?: string;
  bytes?: number;
  received_at?: string;
}

export interface StageBody {
  status?: string;
  proposed_at?: string;
  approved_at?: string;
  approved_by?: string;
  result?: Record<string, unknown>;
}

export interface PipelineEvent {
  stage?: string;
  event?: string;
  at?: string;
  by?: string;
}

export interface OrdersResponse {
  orders: OrderCard[];
}

/** Подпись состояния стадии на языке расчётчика. */
export function stageStatusLabel(status: string | undefined): string {
  switch (status) {
    case "approved":
      return "утверждена";
    case "proposed":
      return "на проверке";
    case "pending":
    case undefined:
      return "не начата";
    default:
      return status;
  }
}

/**
 * Состояние заказа по-русски.
 *
 * Канон продукта: технические идентификаторы не используются как основной
 * текст. `draft` в углу карточки — слово из реестра, а не из работы, и
 * методолог читает его как английскую ошибку. Неизвестное состояние
 * возвращаем как есть: молча подставить чужое слово хуже, чем показать сырое.
 */
export function orderStatusLabel(status: string | undefined): string {
  switch (status) {
    case "draft":
      return "черновик";
    case "received":
      return "принят";
    case "analyzing":
      return "в разборе";
    case "needs_human":
      return "нужен человек";
    case "calculated":
      return "посчитан";
    case "quoted":
      return "КП отправлено";
    case "won":
      return "выигран";
    case "lost":
      return "проигран";
    case "cancelled":
      return "отменён";
    case undefined:
      return "—";
    default:
      return status;
  }
}

/**
 * Что заказ ждёт от человека прямо сейчас — одной строкой для карточки.
 *
 * Порядок веток — это приоритет, а не вкус. Непроверенная цена металла
 * бьёт всё остальное: пока снабжение её не подтвердило, утверждать стадию
 * нельзя (`service2` вернёт конфликт), и предлагать «утвердить» здесь
 * значило бы вести человека в отказ.
 */
export function nextAction(order: OrderCard): {
  kind: string;
  stage?: StageName | null;
  profile?: string | null;
  label: string;
} {
  if (order.kind === "draft") {
    return { kind: "documents", label: "Открыть документы", profile: null };
  }
  if (order.kind === "workflow") {
    return (
      order.next_action ?? {
        kind: "inspect",
        label: "Проверить состояние заказа",
        profile: null,
      }
    );
  }
  // Устаревшие данные бьют всё: утверждать и продолжать такой заказ движок
  // откажется, и любое другое предложение вело бы человека прямо в отказ.
  const stale = (order.stale_stages ?? []) as StageName[];
  if (stale.length > 0) {
    return {
      kind: "repack",
      stage: stale[0],
      label: `Пересчитать по новым данным: ${STAGE_TITLES[stale[0]]}`,
    };
  }

  const stage = order.current_stage;
  if (!stage) return { kind: "done", stage: null, label: "Конвейер пройден" };

  const summary = order.stages[stage];
  const blankProvisional = order.stages.blank?.provisional === true;
  if (blankProvisional) {
    return {
      kind: "supply",
      stage: "blank",
      label: "Подтвердить цену металла у снабжения",
    };
  }
  if (summary?.status === "proposed") {
    return {
      kind: "approve",
      stage,
      label: `Проверить и утвердить: ${STAGE_TITLES[stage]}`,
    };
  }
  return {
    kind: "run",
    stage,
    label: `Запустить стадию: ${STAGE_TITLES[stage]}`,
  };
}

/**
 * Готовый текст для чата агента.
 *
 * Панель НЕ пишет в реестр: ревизии, проверки конфликтов и событие в ленте
 * живут в typed-инструментах `metal_calc`. Кнопка доводит человека до
 * нужного агента с точной формулировкой, а решение по-прежнему проходит
 * через инструмент — один путь записи, одна история решений.
 */
export function actionDraft(order: OrderCard): string {
  if (order.kind === "draft") return "";
  const action = nextAction(order);
  if (order.kind === "workflow") {
    const status = order.workflow?.status ?? order.status;
    return `Заказ ${order.order_id}: покажи актуальное состояние workflow ${status} и выполни действие «${action.label}». Не меняй данные заказчика и не считай внутреннюю себестоимость клиентской ценой.`;
  }
  switch (action.kind) {
    case "repack":
      return `Заказ ${order.order_id}: данные предприятия изменились после расчёта. Открой стадию «${STAGE_TITLES[action.stage as StageName]}» под пересчёт инструментом pipeline_repack и посчитай её заново по действующим данным — следующие стадии откроются вместе с ней, прежний расчёт останется в истории.`;
    case "supply":
      return `Заказ ${order.order_id}: подтверди цену металла инструментом supply_confirm — цена стоит «со звёздочкой», без подтверждения снабжением стадию утверждать нельзя.`;
    case "approve":
      return `Заказ ${order.order_id}: покажи результат стадии «${STAGE_TITLES[action.stage as StageName]}» инструментом pipeline_status, и если всё верно — утверди её инструментом stage_approve.`;
    case "run":
      return `Заказ ${order.order_id}: прочитай состояние инструментами pipeline_status и order_get и выполни стадию «${STAGE_TITLES[action.stage as StageName]}».`;
    default:
      return `Заказ ${order.order_id}: покажи итоговое КП инструментом order_get.`;
  }
}

export interface PriceRow {
  key: "net" | "vat" | "gross";
  label: string;
  value: string;
  emphasis?: boolean;
}

/** Клиентская цена отдельными строками: нетто, НДС и сумма к оплате. */
export function customerPriceRows(price: QuotePrice | null): PriceRow[] {
  if (!price) return [];
  const rows: PriceRow[] = [];
  if (price.net_total_rub != null) {
    rows.push({ key: "net", label: "Без НДС", value: formatMoney(price.net_total_rub) });
  }
  if (price.vat_amount_rub != null) {
    rows.push({
      key: "vat",
      label: `НДС ${price.vat_rate_pct ?? "—"}%`,
      value: formatMoney(price.vat_amount_rub),
    });
  }
  if (price.total_rub != null) {
    rows.push({
      key: "gross",
      label: "Итого заказчику",
      value: formatMoney(price.total_rub),
      emphasis: true,
    });
  }
  return rows;
}

export function unresolvedBlockers(order: OrderCard): OrderBlocker[] {
  return order.blockers ?? [];
}

function mechanicalReceiptIsValid(receipt: QaReceipt | undefined): boolean {
  return Boolean(
    receipt?.verdict === "PASS" &&
      receipt.status === "complete" &&
      receipt.computed_by === "engine" &&
      typeof receipt.checks_total === "number" &&
      receipt.checks_total > 0 &&
      receipt.checks_passed === receipt.checks_total,
  );
}

function humanReceiptIsValid(receipt: QaReceipt | undefined): boolean {
  return Boolean(
    receipt?.verdict === "PASS" &&
      receipt.status === "complete" &&
      receipt.actor_role === "qa",
  );
}

/** Human controls appear only for the next ordered, non-mechanical QA gate. */
export function currentHumanQaGate(order: OrderCard): HumanQaGate | null {
  if (
    order.kind !== "workflow" ||
    order.stale_pack !== false ||
    order.book_binding_valid !== true
  ) {
    return null;
  }
  const gate = order.next_action?.qa_gate;
  if (
    order.next_action?.kind !== "qa_human" ||
    (gate !== "technological" && gate !== "commercial")
  ) {
    return null;
  }
  const receipts = order.qa_receipts ?? {};
  if (!mechanicalReceiptIsValid(receipts.mechanical)) return null;
  if (gate === "commercial") {
    if (!humanReceiptIsValid(receipts.technological)) return null;
  }
  return receipts[gate]?.status === "complete" ? null : gate;
}

export function qaVerdictValidationError(
  verdict: QaVerdict,
  reason: string,
  adjustOwner?: QaAdjustOwner | null,
): string | null {
  const trimmed = reason.trim();
  if (verdict !== "PASS" && !trimmed) {
    return "Укажите причину решения.";
  }
  if (trimmed.length > 500) return "Причина должна быть не длиннее 500 символов.";
  if (verdict === "ADJUST" && !adjustOwner) {
    return "Выберите исполнителя корректировки.";
  }
  if (verdict !== "ADJUST" && adjustOwner) {
    return "Исполнитель указывается только для корректировки.";
  }
  return null;
}

export function contractorQuoteValidationError(
  draft: ContractorQuoteDraft,
): string | null {
  const amount = Number(draft.amount_rub);
  if (
    !draft.amount_rub.trim() ||
    !Number.isFinite(amount) ||
    amount <= 0 ||
    amount > 1_000_000_000_000
  ) {
    return "Сумма должна быть числом больше нуля.";
  }
  if (draft.basis !== "per_piece" && draft.basis !== "order_total") {
    return "Выберите базис суммы.";
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(draft.quoted_at)) {
    return "Укажите дату КП.";
  }
  const quoteDate = new Date(`${draft.quoted_at}T00:00:00.000Z`);
  if (
    Number.isNaN(quoteDate.valueOf()) ||
    quoteDate.toISOString().slice(0, 10) !== draft.quoted_at
  ) {
    return "Укажите корректную дату КП.";
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(draft.valid_until)) {
    return "Укажите срок действия КП.";
  }
  const validUntil = new Date(`${draft.valid_until}T00:00:00.000Z`);
  if (
    Number.isNaN(validUntil.valueOf()) ||
    validUntil.toISOString().slice(0, 10) !== draft.valid_until ||
    draft.valid_until < draft.quoted_at
  ) {
    return "Срок действия КП не может быть раньше даты КП.";
  }
  if (!draft.source.trim() || draft.source.length > 512) {
    return "Укажите источник КП (до 512 символов).";
  }
  if (!draft.reference.trim() || draft.reference.length > 512) {
    return "Укажите номер или ссылку на КП (до 512 символов).";
  }
  return null;
}

export function manualReviewValidationError(
  items: ManualReviewItem[],
  drafts: ManualReviewDraft[],
  reviewedBy: string,
): string | null {
  if (!reviewedBy.trim() || reviewedBy.length > 512) {
    return "Укажите, кто выполнил сверку (до 512 символов).";
  }
  if (!items.length || items.length !== drafts.length) {
    return "Состав ручной сверки изменился — обновите карточку.";
  }
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    const draft = drafts[index];
    if (
      draft.item_index !== item.item_index ||
      draft.item_digest !== item.item_digest
    ) {
      return "Состав ручной сверки изменился — обновите карточку.";
    }
    if (!draft.evidence.trim() || draft.evidence.length > 512) {
      return `Пункт ${index + 1}: укажите результат проверки (до 512 символов).`;
    }
    if (!draft.reference.trim() || draft.reference.length > 512) {
      return `Пункт ${index + 1}: укажите документ или ссылку-основание.`;
    }
    if (!draft.confirmed) {
      return `Пункт ${index + 1}: подтвердите результат сверки.`;
    }
  }
  return null;
}

function priceIsValidOnUtcDate(value: unknown, now: Date): boolean {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00.000Z`);
  if (Number.isNaN(parsed.valueOf()) || parsed.toISOString().slice(0, 10) !== value) {
    return false;
  }
  return value >= now.toISOString().slice(0, 10);
}

/**
 * UI-side fail-closed guard for the customer-facing finality badge.
 * The backend remains authoritative, but a forged/incomplete card must never
 * turn green merely because `book.price.is_final` was set.
 */
export function isCustomerPriceFinal(order: OrderCard, now = new Date()): boolean {
  if (
    order.price?.is_final !== true ||
    !priceIsValidOnUtcDate(order.price.valid_until, now)
  ) {
    return false;
  }
  if (order.kind !== "workflow") return true;
  if (
    order.status !== "READY_FOR_LD" ||
    order.stale_pack !== false ||
    order.book_binding_valid !== true ||
    !Array.isArray(order.contractor_quotes) ||
    order.contractor_quotes.some((line) => line.status !== "current")
  ) {
    return false;
  }
  if (
    (order.manual_review_required?.length ?? 0) > 0 &&
    order.manual_review_receipt?.valid !== true
  ) {
    return false;
  }

  const receipts = order.qa_receipts ?? {};
  const mechanical = receipts.mechanical;
  const technological = receipts.technological;
  const commercial = receipts.commercial;

  return Boolean(
    mechanicalReceiptIsValid(mechanical) &&
      humanReceiptIsValid(technological) &&
      humanReceiptIsValid(commercial),
  );
}

export function customerPriceStatusLabel(
  order: OrderCard,
  now = new Date(),
): "Окончательная" | "Предварительная" | null {
  if (order.price?.total_rub == null) return null;
  return isCustomerPriceFinal(order, now) ? "Окончательная" : "Предварительная";
}

export function workflowStageStatusLabel(status: WorkflowStage["status"]): string {
  switch (status) {
    case "complete":
      return "завершён";
    case "current":
      return "текущий";
    case "blocked":
      return "остановлен";
    default:
      return "ожидает";
  }
}

export function qaVerdictLabel(receipt: QaReceipt): string {
  if (receipt.verdict === "PASS" && receipt.status === "complete") return "Пройдено";
  if (receipt.verdict === "PASS") return "Недействительно";
  if (!receipt.verdict) return "Ожидается";
  return receipt.verdict;
}

const MONEY = new Intl.NumberFormat("ru-RU", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** Деньги показываем всегда с копейками: округление на экране прячет расхождение. */
export function formatMoney(value: number | undefined | null): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${MONEY.format(value)} ₽`;
}

export function formatMoment(value: string | undefined | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
