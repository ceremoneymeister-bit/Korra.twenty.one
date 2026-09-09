/**
 * OrdersPage — заказы расчётчика.
 *
 * До этого экрана состояние конвейера видел только агент: стадии, ставки,
 * нормы и «звёздочки» лежали в реестре, а человек узнавал их, спрашивая
 * агента словами. Экран показывает то же самое напрямую и отвечает на два
 * вопроса, ради которых на него заходят: где заказ стоит и что от меня
 * требуется.
 *
 * Все изменения проходят через команды `metal-calc-admin`: панель передаёт
 * точную ожидаемую ревизию, а движок держит порядок гейтов и журнал решений.
 * QA технолога и коммерсанта подписывает человек в этой карточке; остальные
 * действия по-прежнему открываются у профильного расчётчика.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "@nanostores/react";
import { Link, useNavigate, useSearchParams } from "react-router";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Circle,
  Clock3,
  FileText,
  RefreshCw,
  ShieldCheck,
  Star,
  XCircle,
} from "lucide-react";

import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { ProductButton } from "@/components/ProductButton";
import { fetchJSON } from "@/lib/api";
import { $folderUpload } from "@/store/calc-folder-upload";
import { cn } from "@/lib/utils";
import { productUiMode } from "@/lib/dashboard-flags";
import {
  STAGE_AGENTS,
  STAGE_ORDER,
  STAGE_TITLES,
  QA_ADJUST_OWNERS,
  QA_VERDICTS,
  actionDraft,
  contractorQuoteValidationError,
  customerPriceRows,
  customerPriceStatusLabel,
  currentHumanQaGate,
  formatMoment,
  formatMoney,
  isCustomerPriceFinal,
  manualReviewValidationError,
  nextAction,
  orderStatusLabel,
  qaVerdictLabel,
  qaVerdictValidationError,
  stageStatusLabel,
  unresolvedBlockers,
  workflowStageStatusLabel,
  type OrderCard,
  type OrdersResponse,
  type ContractorQuoteBasis,
  type ContractorQuoteDraft,
  type ContractorQuoteLine,
  type ManualReviewDraft,
  type QaAdjustOwner,
  type QaVerdict,
  type StageBody,
  type StageName,
} from "@/lib/calc-orders";

/* ------------------------------------------------------------------ */
/*  Мелкие части экрана                                                */
/* ------------------------------------------------------------------ */

function WorkflowStageStrip({ order }: { order: OrderCard }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {(order.workflow_stages ?? []).map((stage, index) => {
        const Icon =
          stage.status === "complete"
            ? CheckCircle2
            : stage.status === "blocked"
              ? XCircle
              : stage.status === "current"
                ? Clock3
                : Circle;
        return (
          <div key={stage.name} className="flex items-center gap-1.5">
            {index > 0 && (
              <ArrowRight
                aria-hidden
                className="size-3.5 shrink-0 text-text-secondary"
              />
            )}
            <span
              title={`${stage.title} — ${workflowStageStatusLabel(stage.status)}`}
              className={cn(
                "flex items-center gap-1.5 rounded-lg border px-2.5 py-1",
                "font-sans text-sm leading-snug whitespace-nowrap",
                stage.status === "complete" && "border-success/50 text-success",
                stage.status === "current" &&
                  "border-midground ring-1 ring-midground",
                stage.status === "blocked" && "border-warning/70 text-warning",
                stage.status === "pending" && "border-border text-text-secondary",
              )}
            >
              <Icon aria-hidden className="size-3.5 shrink-0" />
              {stage.title}
              <span className="sr-only">
                {workflowStageStatusLabel(stage.status)}
              </span>
            </span>
          </div>
        );
      })}
    </div>
  );
}

/** Полоса конвейера: четыре стадии в их порядке, с состоянием каждой. */
function StageStrip({
  order,
  compact = false,
}: {
  order: OrderCard;
  compact?: boolean;
}) {
  if (order.kind === "draft") return null;
  if (order.kind === "workflow") return <WorkflowStageStrip order={order} />;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {STAGE_ORDER.map((name, index) => {
        const stage = order.stages[name];
        const approved = stage?.status === "approved";
        const proposed = stage?.status === "proposed";
        const current = order.current_stage === name;
        return (
          <div key={name} className="flex items-center gap-1.5">
            {index > 0 && (
              <ArrowRight
                aria-hidden
                // Не opacity: значок несёт смысл «дальше», а треть
                // непрозрачности топит его в фоне на обеих темах.
                className="size-3.5 shrink-0 text-text-secondary"
              />
            )}
            <span
              // Состояние проговариваем словом, а не только цветом: цвет —
              // не единственный канал, и на печати/в контрасте он исчезает.
              title={`${STAGE_TITLES[name]} — ${stageStatusLabel(stage?.status)}`}
              className={cn(
                // Канон продукта: без капса и без display-шрифта. Стадия —
                // название работы, а не системный ярлык, и читать её надо с
                // одного взгляда, а не разбирать по буквам.
                "flex items-center gap-1 rounded-lg border px-2.5 py-1",
                "font-sans text-sm leading-snug whitespace-nowrap",
                approved && "border-success/50 text-success",
                proposed && "border-warning/60 text-warning",
                !approved && !proposed && "border-border text-text-secondary",
                current && "ring-1 ring-midground",
              )}
            >
              {STAGE_TITLES[name]}
              {stage?.provisional && (
                <Star aria-hidden className="size-2.5 fill-current" />
              )}
            </span>
            {!compact && (
              <span className="sr-only">{stageStatusLabel(stage?.status)}</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Строка «код · примечание · количество · ставка · сумма» стадии заготовки. */
function BlankLines({ result }: { result: Record<string, unknown> }) {
  const lines = Array.isArray(result.lines)
    ? (result.lines as Record<string, unknown>[])
    : [];
  if (!lines.length) return null;
  return (
    <table className="block w-full overflow-x-auto text-sm">
      <thead className="text-left text-text-secondary">
        <tr>
          <th className="py-1.5 pr-3 font-normal">Позиция</th>
          <th className="py-1.5 pr-3 font-normal">Кол-во</th>
          <th className="py-1.5 pr-3 font-normal">Ставка</th>
          <th className="py-1.5 text-right font-normal">Сумма</th>
        </tr>
      </thead>
      <tbody>
        {lines.map((line, index) => {
          const provisional = line.provisional === true;
          const source = line.rate_source as Record<string, unknown> | undefined;
          return (
            <tr key={index} className="border-t border-border/40 align-top">
              <td className="py-2 pr-3">
                <span>{String(line.code ?? "—")}</span>
                {provisional && (
                  <Star
                    aria-label="цена требует подтверждения снабжением"
                    className="ml-1 inline size-3.5 fill-warning text-warning"
                  />
                )}
                {line.note ? (
                  <div className="text-text-secondary">{String(line.note)}</div>
                ) : null}
                {source?.ref ? (
                  <div className="text-text-secondary">
                    источник: {String(source.ref)}
                    {source.as_of ? ` · ${String(source.as_of)}` : ""}
                  </div>
                ) : null}
              </td>
              <td className="py-2 pr-3 whitespace-nowrap">
                {String(line.quantity ?? "—")} {String(line.unit ?? "")}
              </td>
              <td className="py-2 pr-3 whitespace-nowrap">
                {formatMoney(line.rate_rub as number)}
              </td>
              <td className="py-2 text-right whitespace-nowrap">
                {formatMoney(line.subtotal_rub as number)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** Маршрут изготовления: операции по порядку, как их выдал агент 2. */
function RouteSteps({ result }: { result: Record<string, unknown> }) {
  const steps = Array.isArray(result.steps)
    ? (result.steps as Record<string, unknown>[])
    : [];
  if (!steps.length) return null;
  return (
    <ol className="space-y-2 text-sm">
      {steps.map((step, index) => (
        <li key={index} className="flex gap-2">
          <span className="w-5 shrink-0 text-right text-text-secondary">
            {String(step.seq ?? index + 1)}.
          </span>
          <span>
            <span>{String(step.op_code ?? "—")}</span>
            {step.note ? (
              <span className="text-text-secondary"> — {String(step.note)}</span>
            ) : null}
          </span>
        </li>
      ))}
    </ol>
  );
}

/**
 * Нормы времени: операция, штучное время, стоимость и ОБЯЗАТЕЛЬНО источник.
 *
 * Источник нормы здесь не украшение: формула без сверки со справочником —
 * это систематическая ошибка во всех ценах, поэтому его видно на экране, а
 * не только в данных.
 */
function TimeItems({ result }: { result: Record<string, unknown> }) {
  const items = Array.isArray(result.items)
    ? (result.items as Record<string, unknown>[])
    : [];
  if (!items.length) return null;
  return (
    <table className="block w-full overflow-x-auto text-sm">
      <thead className="text-left text-text-secondary">
        <tr>
          <th className="py-1.5 pr-3 font-normal">Операция</th>
          <th className="py-1.5 pr-3 font-normal">t шт., мин</th>
          <th className="py-1.5 pr-3 font-normal">Ставка/час</th>
          <th className="py-1.5 text-right font-normal">Стоимость</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item, index) => {
          const norm = item.norm_source as Record<string, unknown> | undefined;
          return (
            <tr key={index} className="border-t border-border/40 align-top">
              <td className="py-2 pr-3">
                <span>{String(item.op_code ?? "—")}</span>
                {norm?.ref ? (
                  <div className="text-text-secondary">
                    норма: {String(norm.ref)}
                  </div>
                ) : (
                  <div className="text-warning">норма без источника</div>
                )}
              </td>
              <td className="py-2 pr-3 whitespace-nowrap">
                {String(item.t_piece_min ?? "—")}
              </td>
              <td className="py-2 pr-3 whitespace-nowrap">
                {formatMoney(item.rate_rub_per_hour as number)}
              </td>
              <td className="py-2 text-right whitespace-nowrap">
                {formatMoney(item.cost_rub as number)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** Итог КП: из чего сложилась цена и что в ней НДС. */
function QuoteTotals({ result }: { result: Record<string, unknown> }) {
  const cost = (result.cost ?? {}) as Record<string, number>;
  const price = (result.price ?? {}) as Record<string, number | boolean | string>;
  const rows: [string, string][] = [
    ["Заготовка", formatMoney(cost.blank_rub)],
    ["Обработка", formatMoney(cost.machining_rub)],
    ["Доп. расходы", formatMoney(cost.extras_rub)],
    ["Себестоимость", formatMoney(cost.total_rub)],
    ["Без НДС", formatMoney(price.net_total_rub as number)],
    [
      `НДС ${price.vat_rate_pct ?? "—"}%`,
      formatMoney(price.vat_amount_rub as number),
    ],
  ];
  return (
    <div className="space-y-1.5 text-sm">
      {rows.map(([label, value]) => (
        <div key={label} className="flex justify-between gap-4">
          <span className="text-text-secondary">{label}</span>
          <span className="whitespace-nowrap">{value}</span>
        </div>
      ))}
      <div className="flex justify-between gap-4 border-t border-border pt-2 text-base font-semibold">
        <span>Итого</span>
        <span className="whitespace-nowrap">
          {formatMoney(price.total_rub as number)}
          {result.provisional === true && (
            <Star
              aria-label="итог содержит цену со звёздочкой"
              className="ml-1 inline size-3.5 fill-warning text-warning"
            />
          )}
        </span>
      </div>
    </div>
  );
}

function StageBodyView({ name, stage }: { name: StageName; stage: StageBody }) {
  const result = (stage.result ?? {}) as Record<string, unknown>;
  return (
    <section className="space-y-3 border-t border-border pt-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-lg font-semibold">{STAGE_TITLES[name]}</h3>
        <span className="text-sm text-text-secondary">
          {stageStatusLabel(stage.status)}
          {stage.approved_by ? ` · ${stage.approved_by}` : ""}
          {stage.approved_at ? ` · ${formatMoment(stage.approved_at)}` : ""}
        </span>
      </header>
      {name === "blank" && <BlankLines result={result} />}
      {name === "route" && <RouteSteps result={result} />}
      {name === "time" && <TimeItems result={result} />}
      {name === "quote" && <QuoteTotals result={result} />}
      {typeof result.total_rub === "number" && name === "blank" && (
        <div className="flex justify-between border-t border-border pt-2 text-sm">
          <span className="text-text-secondary">Итого по заготовке</span>
          <span className="font-semibold">{formatMoney(result.total_rub)}</span>
        </div>
      )}
    </section>
  );
}

function CustomerPrice({ order }: { order: OrderCard }) {
  const rows = customerPriceRows(order.price);
  if (!rows.length) return null;
  const final = isCustomerPriceFinal(order);
  const finality = final ? "Окончательная" : "Предварительная";
  return (
    <section className="space-y-3 rounded-xl border border-border p-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-lg font-semibold">Цена заказчику</h3>
        <span
          className={cn(
            "rounded-lg border px-2.5 py-1 text-sm",
            final
              ? "border-success/50 text-success"
              : "border-warning/60 text-warning",
          )}
        >
          {finality}
        </span>
      </header>
      <div className="space-y-2 text-sm">
        {rows.map((row) => (
          <div
            key={row.key}
            className={cn(
              "flex items-baseline justify-between gap-4",
              row.emphasis && "border-t border-border pt-2 text-base font-semibold",
            )}
          >
            <span className={row.emphasis ? undefined : "text-text-secondary"}>
              {row.label}
            </span>
            <span className="whitespace-nowrap tabular-nums">{row.value}</span>
          </div>
        ))}
      </div>
      {order.price?.valid_until && (
        <p className="text-sm text-text-secondary">
          Действует до {order.price.valid_until}
        </p>
      )}
      {order.price?.status_note && (
        <p className="text-sm leading-relaxed text-warning">
          {order.price.status_note}
        </p>
      )}
    </section>
  );
}

function InternalCostView({ order }: { order: OrderCard }) {
  const cost = order.internal_cost;
  if (!cost) return null;
  const rows: [string, number | undefined][] = [
    ["Заготовка", cost.blank_rub],
    ["Обработка", cost.machining_rub],
    ["Подряд", cost.contractor_rub],
    ["Доп. расходы", cost.extras_rub],
    ["Итого себестоимость", cost.total_rub],
  ];
  return (
    <section className="space-y-3 rounded-xl border border-dashed border-border p-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-lg font-semibold">Себестоимость</h3>
        <span className="text-sm text-text-secondary">Внутренние данные</span>
      </header>
      <div className="space-y-1.5 text-sm">
        {rows.map(([label, value], index) => (
          <div
            key={label}
            className={cn(
              "flex items-baseline justify-between gap-4",
              index === rows.length - 1 && "border-t border-border pt-2 font-semibold",
            )}
          >
            <span className="text-text-secondary">{label}</span>
            <span className="whitespace-nowrap tabular-nums">
              {formatMoney(value)}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

function BlockersView({ order }: { order: OrderCard }) {
  const blockers = unresolvedBlockers(order);
  if (!blockers.length) return null;
  return (
    <section
      aria-labelledby="workflow-blockers-title"
      className="space-y-3 rounded-xl border border-warning/60 p-4"
    >
      <h3
        id="workflow-blockers-title"
        className="flex items-center gap-2 text-lg font-semibold"
      >
        <AlertTriangle aria-hidden className="size-4 shrink-0 text-warning" />
        Что ещё нужно закрыть
      </h3>
      <ul className="space-y-3">
        {blockers.map((blocker, index) => (
          <li key={`${blocker.kind}-${blocker.route_seq ?? index}`} className="text-sm">
            <p className="font-medium">{blocker.title}</p>
            {blocker.detail && (
              <p className="mt-0.5 leading-relaxed text-text-secondary">
                {blocker.detail}
              </p>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function QaReceipts({ order }: { order: OrderCard }) {
  const receipts = Object.entries(order.qa_receipts ?? {});
  if (!receipts.length) return null;
  return (
    <section className="space-y-3 border-t border-border pt-4">
      <h3 className="flex items-center gap-2 text-lg font-semibold">
        <ShieldCheck aria-hidden className="size-4" />
        QA-квитанции · рев. {order.workflow?.calculation_revision ?? "—"}
      </h3>
      <div className="grid gap-3 sm:grid-cols-3">
        {receipts.map(([gate, receipt]) => {
          const Icon =
            receipt.status === "complete"
              ? CheckCircle2
              : receipt.verdict
                ? XCircle
                : Clock3;
          return (
            <article key={gate} className="space-y-2 rounded-xl border border-border p-3">
              <p className="text-sm font-medium">{receipt.title}</p>
              <p
                className={cn(
                  "flex items-center gap-1.5 text-sm",
                  receipt.status === "complete" ? "text-success" : "text-warning",
                )}
              >
                <Icon aria-hidden className="size-3.5 shrink-0" />
                {qaVerdictLabel(receipt)}
              </p>
              {receipt.checks_total != null && (
                <p className="text-sm text-text-secondary">
                  Проверки: {receipt.checks_passed ?? 0}/{receipt.checks_total}
                </p>
              )}
              {(receipt.by || receipt.computed_by) && (
                <p className="text-sm text-text-secondary">
                  {receipt.by || receipt.computed_by}
                  {receipt.at ? ` · ${formatMoment(receipt.at)}` : ""}
                </p>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function ManualReviewAction({
  order,
  onApplied,
}: {
  order: OrderCard;
  onApplied: () => void;
}) {
  const items = order.manual_review_required ?? [];
  const receipt = order.manual_review_receipt;
  const [reviewedBy, setReviewedBy] = useState(receipt?.reviewed_by ?? "");
  const [drafts, setDrafts] = useState<ManualReviewDraft[]>(() =>
    items.map((item) => ({
      item_index: item.item_index,
      item_digest: item.item_digest,
      evidence: "",
      reference: "",
      confirmed: false,
    })),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!items.length) return null;

  if (receipt?.valid === true) {
    return (
      <section className="space-y-2 rounded-xl border border-success/50 p-4">
        <h3 className="flex items-center gap-2 text-lg font-semibold">
          <CheckCircle2 aria-hidden className="size-4 text-success" />
          Ручная сверка закрыта
        </h3>
        <p className="text-sm text-text-secondary">
          {receipt.reviewed_by || "QA"}
          {receipt.recorded_at ? ` · ${formatMoment(receipt.recorded_at)}` : ""}
          {receipt.digest ? ` · квитанция ${receipt.digest.slice(0, 12)}` : ""}
        </p>
        <ul className="space-y-2 text-sm">
          {items.map((item, index) => {
            const review = receipt.reviews?.[index];
            return (
              <li key={`${index}-${item.item_digest}`} className="rounded-lg border border-border p-3">
                <p className="font-medium">
                  Шаг {item.seq ?? "—"} · {item.process_code || "Операция"}
                </p>
                <p className="text-text-secondary">{item.reason}</p>
                <p className="mt-1 text-text-secondary">
                  {review?.evidence} · {review?.reference}
                </p>
              </li>
            );
          })}
        </ul>
      </section>
    );
  }

  const update = (
    index: number,
    field: "evidence" | "reference",
    value: string,
  ) => {
    setDrafts((current) =>
      current.map((draft, draftIndex) =>
        draftIndex === index
          ? { ...draft, [field]: value, confirmed: false }
          : draft,
      ),
    );
    setError(null);
  };

  const confirmItem = (index: number) => {
    const draft = drafts[index];
    if (!draft.evidence.trim() || !draft.reference.trim()) {
      setError(`Пункт ${index + 1}: заполните результат и основание.`);
      return;
    }
    setDrafts((current) =>
      current.map((item, itemIndex) =>
        itemIndex === index ? { ...item, confirmed: true } : item,
      ),
    );
    setError(null);
  };

  const submit = async () => {
    const validationError = manualReviewValidationError(items, drafts, reviewedBy);
    if (validationError) {
      setError(validationError);
      return;
    }
    if (!order.book_digest) {
      setError("Контрольная сумма книги недоступна — обновите карточку.");
      return;
    }
    if (!window.confirm(`Зафиксировать сверку ${items.length} пунктов?`)) return;
    setBusy(true);
    setError(null);
    try {
      await fetchJSON(
        `/api/calc/orders/${encodeURIComponent(order.order_id)}/manual-review`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            expected_revision: order.revision,
            expected_book_digest: order.book_digest,
            reviewed_by: reviewedBy.trim(),
            reviews: drafts.map((review) => ({
              item_index: review.item_index,
              item_digest: review.item_digest,
              evidence: review.evidence.trim(),
              reference: review.reference.trim(),
            })),
          }),
        },
      );
      onApplied();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось закрыть сверку");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="space-y-3 rounded-xl border border-warning/60 p-4">
      <div className="space-y-1">
        <h3 className="text-lg font-semibold">Ручная коммерческая сверка</h3>
        <p className="text-sm leading-relaxed text-text-secondary">
          Каждый пункт требует отдельного результата и документа-основания.
          Квитанция будет привязана к этой книге и ревизии расчёта.
        </p>
      </div>
      <label className="flex max-w-lg flex-col gap-1 text-sm">
        <span className="text-text-secondary">Кто выполнил сверку</span>
        <input
          value={reviewedBy}
          maxLength={512}
          disabled={busy}
          onChange={(event) => {
            setReviewedBy(event.target.value);
            setError(null);
          }}
          className="h-10 rounded-lg border border-border bg-background px-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-primary/40 sm:text-sm"
        />
      </label>
      <div className="space-y-3">
        {items.map((item, index) => {
          const draft = drafts[index];
          return (
            <article key={`${index}-${item.item_digest}`} className="space-y-3 rounded-lg border border-border p-3">
              <div>
                <p className="font-medium">
                  Шаг {item.seq ?? "—"} · {item.process_code || "Операция"}
                </p>
                <p className="text-sm text-text-secondary">{item.reason}</p>
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-text-secondary">Результат проверки</span>
                  <input
                    value={draft?.evidence ?? ""}
                    maxLength={512}
                    disabled={busy}
                    onChange={(event) => update(index, "evidence", event.target.value)}
                    className="h-10 rounded-lg border border-border bg-background px-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-primary/40 sm:text-sm"
                  />
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-text-secondary">Документ / ссылка</span>
                  <input
                    value={draft?.reference ?? ""}
                    maxLength={512}
                    disabled={busy}
                    onChange={(event) => update(index, "reference", event.target.value)}
                    className="h-10 rounded-lg border border-border bg-background px-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-primary/40 sm:text-sm"
                  />
                </label>
              </div>
              <ProductButton
                outlined={!draft?.confirmed}
                disabled={busy}
                onClick={() => confirmItem(index)}
              >
                {draft?.confirmed ? "Пункт подтверждён" : "Подтвердить этот пункт"}
              </ProductButton>
            </article>
          );
        })}
      </div>
      {error && <p className="text-sm text-warning" role="alert">{error}</p>}
      <ProductButton disabled={busy} onClick={() => void submit()}>
        Зафиксировать QA-квитанцию
      </ProductButton>
    </section>
  );
}

function HumanQaAction({
  order,
  onApplied,
}: {
  order: OrderCard;
  onApplied: () => void;
}) {
  const gate = currentHumanQaGate(order);
  const [reason, setReason] = useState("");
  const [adjustOwner, setAdjustOwner] = useState<QaAdjustOwner | "">("");
  const [showAdjustOwner, setShowAdjustOwner] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!gate) return null;

  const gateTitle = order.qa_receipts?.[gate]?.title ?? gate;
  const submit = async (verdict: QaVerdict) => {
    const owner = verdict === "ADJUST" ? adjustOwner || null : null;
    const validationError = qaVerdictValidationError(verdict, reason, owner);
    if (validationError) {
      setError(validationError);
      return;
    }
    const action = QA_VERDICTS.find((item) => item.value === verdict)?.label ?? verdict;
    if (!window.confirm(`${action}: ${gateTitle.toLowerCase()}?`)) return;

    setBusy(true);
    setError(null);
    try {
      await fetchJSON(`/api/calc/orders/${encodeURIComponent(order.order_id)}/qa-verdict`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_revision: order.revision,
          gate,
          verdict,
          reasons: reason.trim() ? [reason.trim()] : [],
          ...(verdict === "ADJUST" ? { adjust_owner: adjustOwner } : {}),
        }),
      });
      setReason("");
      setAdjustOwner("");
      setShowAdjustOwner(false);
      onApplied();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось записать решение QA");
    } finally {
      setBusy(false);
    }
  };

  const returnRoute = async () => {
    if (!reason.trim()) {
      setError("Укажите причину возврата маршрута технологу.");
      return;
    }
    if (!window.confirm("Вернуть текущий маршрут технологу на пересмотр?")) return;
    setBusy(true);
    setError(null);
    try {
      await fetchJSON(
        `/api/calc/orders/${encodeURIComponent(order.order_id)}/route-return`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            expected_revision: order.revision,
            reason: reason.trim(),
          }),
        },
      );
      setReason("");
      onApplied();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось вернуть маршрут");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      aria-labelledby="human-qa-title"
      className="space-y-3 rounded-xl border border-midground/60 p-4"
    >
      <div className="space-y-1">
        <h3 id="human-qa-title" className="text-lg font-semibold">
          Решение человека: {gateTitle.toLowerCase()}
        </h3>
        <p className="text-sm leading-relaxed text-text-secondary">
          Решение будет записано для ревизии {order.revision}. После подтверждения
          карточка обновится из реестра.
        </p>
      </div>
      <label className="flex max-w-2xl flex-col gap-1 text-sm">
        <span className="text-text-secondary">
          Причина — обязательна для корректировки, блокировки и отсутствия данных
        </span>
        <textarea
          value={reason}
          maxLength={500}
          rows={3}
          disabled={busy}
          onChange={(event) => {
            setReason(event.target.value);
            setError(null);
          }}
          className="min-h-24 w-full resize-y rounded-lg border border-border bg-background px-3 py-2 text-base outline-none focus-visible:ring-2 focus-visible:ring-primary/40 sm:text-sm"
        />
      </label>
      {showAdjustOwner && (
        <div className="flex max-w-2xl flex-wrap items-end gap-2 rounded-lg border border-border bg-surface/40 p-3">
          <label className="flex min-w-64 flex-1 flex-col gap-1 text-sm">
            <span className="text-text-secondary">Кому вернуть на корректировку</span>
            <select
              value={adjustOwner}
              disabled={busy}
              onChange={(event) => {
                setAdjustOwner(event.target.value as QaAdjustOwner | "");
                setError(null);
              }}
              className="h-10 rounded-lg border border-border bg-background px-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-primary/40 sm:text-sm"
            >
              <option value="">Выберите исполнителя</option>
              {QA_ADJUST_OWNERS.map((owner) => (
                <option key={owner.value} value={owner.value}>
                  {owner.label}
                </option>
              ))}
            </select>
          </label>
          <ProductButton disabled={busy} onClick={() => void submit("ADJUST")}>
            Записать корректировку
          </ProductButton>
        </div>
      )}
      {error && (
        <p className="text-sm text-warning" role="alert">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2" aria-busy={busy}>
        {QA_VERDICTS.map((item) => (
          <ProductButton
            key={item.value}
            outlined={item.value !== "PASS"}
            destructive={item.value === "BLOCK"}
            disabled={busy}
            onClick={() => {
              if (item.value === "ADJUST") {
                setShowAdjustOwner(true);
                setError(null);
                return;
              }
              setShowAdjustOwner(false);
              void submit(item.value);
            }}
          >
            {item.label}
          </ProductButton>
        ))}
        {gate === "technological" && (
          <ProductButton outlined disabled={busy} onClick={() => void returnRoute()}>
            Вернуть маршрут технологу
          </ProductButton>
        )}
      </div>
    </section>
  );
}

function ContractorQuoteEditor({
  order,
  line,
  onApplied,
}: {
  order: OrderCard;
  line: ContractorQuoteLine;
  onApplied: () => void;
}) {
  const quote = line.quote;
  const [editing, setEditing] = useState(line.status !== "current");
  const [draft, setDraft] = useState<ContractorQuoteDraft>({
    amount_rub: quote?.amount_rub != null ? String(quote.amount_rub) : "",
    basis: quote?.basis ?? "order_total",
    vat_included: quote?.vat_included ?? true,
    quoted_at: quote?.quoted_at ?? new Date().toISOString().slice(0, 10),
    valid_until: quote?.valid_until ?? new Date().toISOString().slice(0, 10),
    source: quote?.source ?? "",
    reference: quote?.reference ?? "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const title = line.actual_process_name || line.process_code || `Шаг ${line.seq}`;
  const statusLabel =
    line.status === "current"
      ? "Учтено в текущей ревизии"
      : line.status === "stale"
        ? "КП устарело — требуется обновление"
        : "КП не записано";

  const update = <K extends keyof ContractorQuoteDraft>(
    key: K,
    value: ContractorQuoteDraft[K],
  ) => {
    setDraft((current) => ({ ...current, [key]: value }));
    setError(null);
  };

  const submit = async () => {
    const validationError = contractorQuoteValidationError(draft);
    if (validationError) {
      setError(validationError);
      return;
    }
    const basisLabel = draft.basis === "per_piece" ? "за штуку" : "за весь заказ";
    const vatLabel = draft.vat_included ? "с НДС" : "без НДС";
    if (
      !window.confirm(
        `Записать КП для шага ${line.seq ?? "—"} «${title}»: ${draft.amount_rub} ₽ ${basisLabel}, ${vatLabel}, действует до ${draft.valid_until}?`,
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await fetchJSON(
        `/api/calc/orders/${encodeURIComponent(order.order_id)}/contractor-quote`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            expected_revision: order.revision,
            route_seq: line.seq,
            amount_rub: draft.amount_rub.trim(),
            basis: draft.basis,
            vat_included: draft.vat_included,
            quoted_at: draft.quoted_at,
            valid_until: draft.valid_until,
            source: draft.source.trim(),
            reference: draft.reference.trim(),
          }),
        },
      );
      setEditing(false);
      onApplied();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось записать КП подрядчика");
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className="space-y-3 rounded-xl border border-border p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-medium">
            Шаг {line.seq ?? "—"} · {title}
          </p>
          <p
            className={cn(
              "mt-0.5 text-sm",
              line.status === "current" ? "text-success" : "text-warning",
            )}
          >
            {statusLabel}
          </p>
          {quote && (
            <p className="mt-1 text-sm text-text-secondary">
              {formatMoney(quote.amount_rub)} ·{" "}
              {quote.basis === "per_piece" ? "за штуку" : "за заказ"} ·{" "}
              {quote.vat_included ? "с НДС" : "без НДС"} · {quote.source} ·{" "}
              {quote.reference} · действует до {quote.valid_until}
            </p>
          )}
        </div>
        {!editing && (
          <ProductButton outlined size="sm" onClick={() => setEditing(true)}>
            Обновить КП
          </ProductButton>
        )}
      </div>

      {editing && (
        <div className="grid gap-3 md:grid-cols-2">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-text-secondary">Сумма, ₽</span>
            <input
              value={draft.amount_rub}
              inputMode="decimal"
              disabled={busy}
              onChange={(event) => update("amount_rub", event.target.value)}
              className="h-10 rounded-lg border border-border bg-background px-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-primary/40 sm:text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-text-secondary">Базис суммы</span>
            <select
              value={draft.basis}
              disabled={busy}
              onChange={(event) =>
                update("basis", event.target.value as ContractorQuoteBasis)
              }
              className="h-10 rounded-lg border border-border bg-background px-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-primary/40 sm:text-sm"
            >
              <option value="order_total">За весь заказ</option>
              <option value="per_piece">За одну штуку</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-text-secondary">Дата КП</span>
            <input
              type="date"
              value={draft.quoted_at}
              disabled={busy}
              onChange={(event) => update("quoted_at", event.target.value)}
              className="h-10 rounded-lg border border-border bg-background px-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-primary/40 sm:text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-text-secondary">Действует до</span>
            <input
              type="date"
              value={draft.valid_until}
              disabled={busy}
              onChange={(event) => update("valid_until", event.target.value)}
              className="h-10 rounded-lg border border-border bg-background px-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-primary/40 sm:text-sm"
            />
          </label>
          <label className="flex items-center gap-2 self-end py-2 text-sm">
            <input
              type="checkbox"
              checked={draft.vat_included}
              disabled={busy}
              onChange={(event) => update("vat_included", event.target.checked)}
              className="size-4 rounded border-border"
            />
            Сумма включает НДС
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-text-secondary">Источник / подрядчик</span>
            <input
              value={draft.source}
              maxLength={512}
              disabled={busy}
              onChange={(event) => update("source", event.target.value)}
              className="h-10 rounded-lg border border-border bg-background px-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-primary/40 sm:text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-text-secondary">Номер или ссылка на КП</span>
            <input
              value={draft.reference}
              maxLength={512}
              disabled={busy}
              onChange={(event) => update("reference", event.target.value)}
              className="h-10 rounded-lg border border-border bg-background px-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-primary/40 sm:text-sm"
            />
          </label>
          {error && (
            <p className="text-sm text-warning md:col-span-2" role="alert">
              {error}
            </p>
          )}
          <div className="flex flex-wrap gap-2 md:col-span-2" aria-busy={busy}>
            <ProductButton disabled={busy} onClick={() => void submit()}>
              {quote ? "Записать обновлённое КП" : "Записать КП"}
            </ProductButton>
            {line.status === "current" && (
              <ProductButton outlined disabled={busy} onClick={() => setEditing(false)}>
                Отмена
              </ProductButton>
            )}
          </div>
        </div>
      )}
    </article>
  );
}

function ContractorQuotesView({
  order,
  onApplied,
}: {
  order: OrderCard;
  onApplied: () => void;
}) {
  const lines = order.contractor_quotes ?? [];
  if (!lines.length) return null;
  return (
    <section aria-labelledby="contractor-quotes-title" className="space-y-3">
      <div className="space-y-1">
        <h3 id="contractor-quotes-title" className="text-lg font-semibold">
          КП подрядчиков
        </h3>
        <p className="text-sm leading-relaxed text-text-secondary">
          Отдельная внутренняя себестоимость для каждого outsource-шага до наценки
          заказчику. Источник и номер КП обязательны; запись привязывается к текущей
          ревизии и может потребовать пересборки книги.
        </p>
      </div>
      <div className="space-y-3">
        {lines.map((line) => (
          <ContractorQuoteEditor
            key={`${line.seq}-${line.process_code}`}
            order={order}
            line={line}
            onApplied={onApplied}
          />
        ))}
      </div>
    </section>
  );
}

function WorkflowRoute({ order }: { order: OrderCard }) {
  const steps = order.detail?.route_steps ?? [];
  if (!steps.length) return null;
  return (
    <section className="space-y-3 border-t border-border pt-4">
      <h3 className="text-lg font-semibold">Замороженный маршрут</h3>
      <ol className="space-y-2 text-sm">
        {steps.map((step, index) => (
          <li key={`${step.seq ?? index}-${step.process_code}`} className="flex gap-3">
            <span className="w-6 shrink-0 text-right text-text-secondary">
              {step.seq ?? index + 1}.
            </span>
            <span className="min-w-0">
              <span className="font-medium">
                {step.actual_process_name || step.process_code || "Операция"}
              </span>
              {step.actual_process_name && step.process_code && (
                <span className="text-text-secondary"> · {step.process_code}</span>
              )}
              <span className="block text-text-secondary">
                {[step.execution_mode, step.cost_owner, step.note]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function SourceFiles({ order }: { order: OrderCard }) {
  const files = order.detail?.source_files ?? [];
  if (order.kind === "draft") {
    const formats = { PDF: 0, Excel: 0, другие: 0 };
    for (const file of files) {
      const extension = (file.relative_path || file.name || "").split(".").at(-1)?.toLowerCase();
      if (extension === "pdf") formats.PDF++;
      else if (["xls", "xlsx", "xlsm", "xlsb"].includes(extension ?? "")) formats.Excel++;
      else formats.другие++;
    }
    return <section aria-label="Документы заказа" className="space-y-4">
      <div className="space-y-2 text-sm text-text-secondary">
        <p>Файлов: {order.file_count ?? files.length}{files.length > 0 && ` · ${Object.entries(formats).filter(([, count]) => count > 0).map(([format, count]) => `${format}: ${count}`).join(" · ")}`}</p>
        <p>Приёмщик получит этот комплект и сохранённые результаты подготовки. Состав и готовность к расчёту сотрудник подтверждает отдельно.</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {productUiMode() === "calc" && (
          <Link to={`/orders/${encodeURIComponent(order.order_id)}/intake`} className="inline-flex min-h-11 items-center rounded-lg border border-primary bg-primary px-4 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-primary">
            Приёмка
          </Link>
        )}
        <Link to={`/files?order=${encodeURIComponent(order.order_id)}`} className="inline-flex min-h-11 items-center rounded-lg border border-border px-4 text-sm font-semibold hover:bg-muted/40 focus-visible:outline focus-visible:outline-primary">Открыть документы</Link>
      </div>
    </section>;
  }
  if (order.folder_name) return <section className="space-y-3 border-t border-border pt-4">
    <h3 className="text-lg font-semibold">Исходные документы</h3>
    <p className="text-sm text-text-secondary">Папка «{order.folder_name}» · файлов: {order.file_count ?? files.length}</p>
    <Link to={`/files?order=${encodeURIComponent(order.order_id)}`} className="inline-flex min-h-11 items-center rounded-lg border border-border px-4 text-sm font-semibold hover:bg-muted/40">Открыть все файлы заказа</Link>
  </section>;
  if (!files.length) return null;
  return (
    <section className="space-y-3 border-t border-border pt-4">
      <h3 className="flex items-center gap-2 text-lg font-semibold">
        <FileText aria-hidden className="size-4" />
        Исходные файлы
      </h3>
      <ul className="grid gap-2 sm:grid-cols-2">
        {files.map((file, index) => (
          <li
            key={file.source_file_id ?? index}
            className="min-w-0 rounded-xl border border-border p-3 text-sm"
          >
            <p className="truncate font-medium" title={file.name}>
              {file.name || file.source_file_id || "Файл"}
            </p>
            <p className="text-text-secondary">
              {[file.format?.toUpperCase(), file.bytes != null ? `${file.bytes} Б` : null]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  CalcOrdersPage (default export)                                    */
/* ------------------------------------------------------------------ */

export default function CalcOrdersPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const upload = useStore($folderUpload);
  const completedOrderId = upload?.status === "complete" ? upload.orderId : undefined;
  const seenCompletion = useRef(completedOrderId);
  const [orders, setOrders] = useState<OrderCard[]>([]);
  const listVersion = useRef(0);
  const [fallbackId, setFallbackId] = useState<string | null>(null);
  const selectedId = params.get("order") ?? fallbackId;
  const [detail, setDetail] = useState<OrderCard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailVersion, setDetailVersion] = useState(0);

  const load = useCallback(async () => {
    const version = ++listVersion.current;
    setError(null);
    try {
      const payload = await fetchJSON<OrdersResponse>("/api/calc/orders");
      if (version !== listVersion.current) return;
      setOrders(payload.orders ?? []);
      setFallbackId((current) => current ?? payload.orders?.[0]?.order_id ?? null);
    } catch (cause) {
      if (version === listVersion.current) setError(
        cause instanceof Error ? cause.message : "Не удалось прочитать реестр заказов",
      );
    } finally {
      if (version === listVersion.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (completedOrderId && completedOrderId !== seenCompletion.current) {
      seenCompletion.current = completedOrderId;
      void load();
    }
  }, [load, completedOrderId]);

  useEffect(() => {
    if (!selectedId) {
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const card = await fetchJSON<OrderCard>(
          `/api/calc/orders/${encodeURIComponent(selectedId)}`,
        );
        if (!cancelled) { setDetail(card); setDetailError(null); }
      } catch (cause) {
        if (!cancelled) {
          setDetailError(
            cause instanceof Error
              ? cause.message
              : "Не удалось прочитать карточку заказа",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [detailVersion, selectedId]);

  const selectOrder = useCallback((orderId: string) => {
    setDetail(null);
    setDetailError(null);
    setFallbackId(orderId);
    setParams({ order: orderId }, { replace: true });
    // selectedId does not change on a repeated click, so use the explicit
    // reload generation as well; otherwise the card stays in loading forever.
    setDetailVersion((value) => value + 1);
  }, [setParams]);

  const refreshAfterQa = useCallback(() => {
    setDetail(null);
    setDetailError(null);
    setDetailVersion((value) => value + 1);
    void load();
  }, [load]);

  const selected = detail?.order_id === selectedId ? detail : null;
  const action = useMemo(() => (selected ? nextAction(selected) : null), [selected]);
  const actionProfile =
    action?.profile ?? (action?.stage ? STAGE_AGENTS[action.stage] : null);

  // Не-QA действия по-прежнему открываются у профильного расчётчика.
  const openWithAgent = useCallback(() => {
    if (!selected || !action) return;
    const profile =
      action.profile ?? (action.stage ? STAGE_AGENTS[action.stage] : null);
    if (!profile) return;
    navigate(
      `/agents?agent=${encodeURIComponent(profile)}&draft=${encodeURIComponent(
        actionDraft(selected),
      )}`,
    );
  }, [action, navigate, selected]);

  if (loading) {
    return (
      <div
        className="flex h-full items-center justify-center"
        role="status"
        aria-label="Загрузка заказов"
      >
        <Spinner />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center">
        <div className="max-w-md space-y-3">
          <AlertTriangle aria-hidden className="mx-auto size-5 text-warning" />
          <p className="text-base">{error}</p>
          <ProductButton outlined onClick={() => void load()}>
            Повторить
          </ProductButton>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 lg:flex-row">
      {/* Список заказов */}
      <aside className="flex max-h-72 w-full shrink-0 flex-col gap-2 overflow-y-auto pr-1 lg:max-h-none lg:w-72">
        <div className="flex items-center justify-between">
          <span className="text-sm text-text-secondary">
            Заказов: {orders.length}
          </span>
          <ProductButton
            ghost
            size="icon"
            onClick={() => void load()}
            aria-label="Обновить список заказов"
          >
            <RefreshCw />
          </ProductButton>
        </div>
        {orders.length === 0 && (
          <p className="text-sm leading-relaxed text-text-secondary">
            Загрузите папку во вкладке «Файлы». Здесь появится черновик заказа.
          </p>
        )}
        {orders.map((order) => (
          <button
            key={order.order_id}
            type="button"
            onClick={() => selectOrder(order.order_id)}
            aria-current={order.order_id === selectedId}
            className={cn(
              // Выбор показываем рамкой, а не гашением невыбранных карточек.
              // Общая opacity гасит вместе с оформлением и статус стадии: на
              // светлой теме зелёный «утверждена» падал с 5,2 до 2,9 к единице,
              // то есть переставал читаться ровно тот текст, ради которого на
              // список и смотрят.
              "space-y-2 rounded-xl border p-3 text-left transition-colors cursor-pointer",
              order.order_id === selectedId
                ? "border-midground ring-1 ring-midground"
                : "border-border hover:border-midground/50",
            )}
          >
            {/* Первой строкой — заказчик: человек ищет заказ по нему, а не по
                коду реестра. Код остаётся, но как техническая деталь. */}
            <div className={cn("text-base leading-snug font-medium", order.kind === "draft" && "[overflow-wrap:anywhere]")}>
              {order.folder_name || order.customer || `Заказ ${order.order_id}`}
            </div>
            {order.kind === "draft" ? <p className="text-sm text-text-secondary">Документы загружены · файлов: {order.file_count ?? 0}</p> : <div className="flex items-baseline justify-between gap-2 text-sm text-text-secondary">
              <span>{order.customer ? order.order_id : ""}</span>
              <span>ред. {order.revision}</span>
            </div>}
            {order.kind === "workflow" && (
              <p className="text-sm font-medium leading-snug">
                {order.status_title || order.status}
              </p>
            )}
            <StageStrip order={order} compact />
            {(order.stale_stages?.length ?? 0) > 0 && (
              <div className="flex items-center gap-1.5 text-sm text-warning">
                <AlertTriangle aria-hidden className="size-3.5 shrink-0" />
                данные изменились — нужен пересчёт
              </div>
            )}
            {(order.blockers?.length ?? 0) > 0 && (
              <div className="flex items-center gap-1.5 text-sm text-warning">
                <AlertTriangle aria-hidden className="size-3.5 shrink-0" />
                Нужно закрыть: {order.blockers?.length}
              </div>
            )}
            <div className="flex items-baseline justify-between gap-2 text-sm text-text-secondary">
              <span>{formatMoment(order.updated_at)}</span>
              {order.price?.total_rub != null && (
                <span className="flex flex-col items-end">
                  <span className="tabular-nums">{formatMoney(order.price.total_rub)}</span>
                  <span
                    className={cn(
                      "text-xs",
                      isCustomerPriceFinal(order) ? "text-success" : "text-warning",
                    )}
                  >
                    {customerPriceStatusLabel(order)}
                  </span>
                </span>
              )}
            </div>
          </button>
        ))}
      </aside>

      {/* Карточка заказа */}
      <div className="min-h-0 min-w-0 flex-1 overflow-y-auto">
        {!selectedId && (
          <p className="pt-8 text-center text-base text-text-secondary">
            Выберите заказ слева.
          </p>
        )}
        {selectedId && !selected && !detailError && (
          <div
            className="flex items-center justify-center gap-2 pt-8 text-text-secondary"
            role="status"
          >
            <Spinner /> Загрузка карточки…
          </div>
        )}
        {detailError && (
          <div className="mx-auto max-w-md space-y-3 pt-8 text-center" role="alert">
            <AlertTriangle aria-hidden className="mx-auto size-5 text-warning" />
            <p>{detailError}</p>
            <ProductButton
              outlined
              onClick={() => {
                setDetailError(null);
                setDetailVersion((value) => value + 1);
              }}
            >
              Повторить
            </ProductButton>
          </div>
        )}
        {selected && (
          <div className="space-y-5 pb-8">
            <header className="space-y-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className={cn("text-2xl font-semibold", selected.kind === "draft" && "min-w-0 [overflow-wrap:anywhere]")}>
                  {selected.folder_name || selected.customer || `Заказ ${selected.order_id}`}
                </h2>
                {/* Не библиотечный Badge: он набирает статус капсом с
                    разрядкой в пятую эма, и «ч е р н о в и к» приходится
                    складывать по буквам. */}
                <span className="rounded-lg border border-border px-2.5 py-1 text-sm text-text-secondary">
                  {selected.kind === "draft" ? "Документы загружены" : selected.status_title || orderStatusLabel(selected.status)}
                </span>
              </div>
              {selected.customer && (
                <p className="text-sm text-text-secondary">
                  Заказ {selected.order_id} · ред. {selected.revision}
                  {selected.kind === "workflow" ? ` · ${selected.status}` : ""}
                </p>
              )}
              {selected.kind === "workflow" && (
                <p
                  className={cn(
                    "flex items-center gap-1.5 text-sm",
                    selected.stale_pack === false
                      ? "text-success"
                      : "text-warning",
                  )}
                >
                  {selected.stale_pack === false ? (
                    <CheckCircle2 aria-hidden className="size-3.5 shrink-0" />
                  ) : (
                    <AlertTriangle aria-hidden className="size-3.5 shrink-0" />
                  )}
                  {selected.stale_pack === true
                    ? "Пакет данных изменился после расчёта"
                    : selected.stale_pack === false
                      ? "Расчёт совпадает с действующим пакетом данных"
                      : "Сверка с действующим пакетом данных недоступна"}
                </p>
              )}
              <StageStrip order={selected} />
            </header>

            {/* Что требуется от человека — первым экраном, а не после таблиц. */}
            {selected.kind !== "draft" && action && action.kind !== "done" && (
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-midground/60 p-4">
                <span className="flex items-center gap-2 text-base">
                  {action.kind === "supply" && (
                    <Star
                      aria-hidden
                      className="size-4 shrink-0 fill-warning text-warning"
                    />
                  )}
                  {action.label}
                </span>
                {actionProfile && (
                  <ProductButton onClick={openWithAgent}>
                    Открыть у расчётчика
                  </ProductButton>
                )}
              </div>
            )}

            {selected.kind === "workflow" && (
              <ManualReviewAction
                key={`${selected.order_id}-${selected.book_digest ?? "no-book"}`}
                order={selected}
                onApplied={refreshAfterQa}
              />
            )}

            {selected.kind === "workflow" && (
              <HumanQaAction order={selected} onApplied={refreshAfterQa} />
            )}

            {selected.kind === "workflow" && (
              <ContractorQuotesView order={selected} onApplied={refreshAfterQa} />
            )}

            {selected.kind === "workflow" && <BlockersView order={selected} />}

            {selected.kind === "workflow" && (
              <div className="grid min-w-0 gap-4 xl:grid-cols-2">
                <CustomerPrice order={selected} />
                <InternalCostView order={selected} />
              </div>
            )}

            {(selected.stale_stages?.length ?? 0) > 0 && (
              <p className="flex max-w-[70ch] items-start gap-2 text-sm leading-relaxed text-warning">
                <AlertTriangle aria-hidden className="mt-1 size-3.5 shrink-0" />
                <span>
                  Данные предприятия изменились после расчёта. По прежним
                  ставкам посчитаны:{" "}
                  {(selected.stale_stages ?? [])
                    .map((name) => STAGE_TITLES[name])
                    .join(", ")}
                  . Утвердить или продолжить такой заказ движок откажется —
                  откройте его под пересчёт кнопкой выше, прежний расчёт
                  останется в истории.
                </span>
              </p>
            )}

            {selected.kind !== "workflow" && selected.provisional && (
              <p className="flex max-w-[70ch] items-start gap-2 text-sm leading-relaxed text-warning">
                <Star
                  aria-hidden
                  className="mt-1 size-3.5 shrink-0 fill-current"
                />
                <span>
                  В заказе есть цена со звёздочкой — металл закупается под заказ.
                  Пока снабжение не подтвердило цену, КП считается
                  предварительным.
                </span>
              </p>
            )}

            {selected.kind === "workflow" && <QaReceipts order={selected} />}
            {selected.kind === "workflow" && <WorkflowRoute order={selected} />}
            <SourceFiles order={selected} />

            {STAGE_ORDER.filter((name) => selected.detail?.stages?.[name]).map(
              (name) => (
                <StageBodyView
                  key={name}
                  name={name}
                  stage={selected.detail!.stages[name]}
                />
              ),
            )}

            {selected.detail?.events?.length ? (
              <section className="space-y-2 border-t border-border pt-4">
                <h3 className="text-lg font-semibold">События</h3>
                <ul className="space-y-1.5 text-sm">
                  {[...selected.detail.events].reverse().map((event, index) => (
                    <li key={index} className="flex gap-2">
                      <span className="w-24 shrink-0 text-text-secondary">
                        {formatMoment(event.at)}
                      </span>
                      <span className="min-w-0 break-words">
                        {event.stage
                          ? `${STAGE_TITLES[event.stage as StageName] ?? event.stage} — `
                          : ""}
                        {event.event || "событие"}
                        {event.by ? ` · ${event.by}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}
