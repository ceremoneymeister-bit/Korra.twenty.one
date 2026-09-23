/* eslint-disable react-refresh/only-export-components -- виджет и его каталожное описание намеренно живут вместе */
import { useStore } from "@nanostores/react";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";

import type {
  DashboardWidget,
  DashboardWidgetBodyProps,
} from "@/components/dashboard/widget-types";
import {
  ErrorNote,
  FirstStep,
  LoadingNote,
  StaleMark,
  useDashboardSection,
} from "@/components/dashboard/widget-states";
import { MetricBars } from "@/components/dashboard/visuals";
import {
  $dashboardPeriod,
  formatCompact,
  metricDelta,
  plural,
  setDashboardPeriod,
  type DashboardMetrics,
  type MetricDelta,
  type MetricKey,
  type MetricsPeriod,
} from "@/lib/dashboard-state";

/**
 * «Мои показатели» — крупное число, динамика к прошлому периоду и столбики
 * по дням. Источник — `sessions` всех агентов установки: диалоги с людьми
 * отдельно от фоновых запусков расписания и канбана.
 *
 * Карточка намеренно без перехода в шапке: отдельного экрана «мои
 * показатели» нет, а ссылка «в никуда» хуже её отсутствия.
 */

const PERIOD_LABEL: Record<MetricsPeriod, { short: string; phrase: string; previous: string }> = {
  week: { short: "Неделя", phrase: "за неделю", previous: "к прошлой неделе" },
  month: { short: "Месяц", phrase: "за 30 дней", previous: "к прошлым 30 дням" },
};

const METRIC_WORDS: Record<MetricKey, readonly [string, string, string]> = {
  dialogs: ["диалог", "диалога", "диалогов"],
  messages: ["сообщение", "сообщения", "сообщений"],
  background: ["фоновый запуск", "фоновых запуска", "фоновых запусков"],
  tokens: ["токен", "токена", "токенов"],
};

const SECONDARY: { key: MetricKey; label: string }[] = [
  { key: "messages", label: "Сообщения" },
  { key: "background", label: "Фоновые запуски" },
  { key: "tokens", label: "Токены" },
];

function MetricsBody({ size = "m" }: DashboardWidgetBodyProps) {
  const view = useDashboardSection("metrics");
  const period = useStore($dashboardPeriod);

  if (view.phase === "loading") return <LoadingNote text="Считаем показатели…" />;
  if (view.phase === "error") {
    return (
      <ErrorNote
        size={size}
        title="Не удалось посчитать показатели"
        detail="Сводка работы не пришла. Данные не потеряны — повторите запрос."
        onRetry={view.retry}
      />
    );
  }

  const metrics = view.section;
  const shown = metrics.period ?? period;
  if (metrics.status === "empty") {
    return (
      <div className="flex min-h-0 flex-1 flex-col gap-2">
        {size === "s" ? null : <PeriodSwitch value={shown} />}
        <FirstStep
          size={size}
          title={`Диалогов ${PERIOD_LABEL[shown].phrase} пока нет`}
          text="Напишите агенту в чате или в мессенджере — и здесь появится счёт ваших разговоров."
          action={{ label: "Написать агенту", to: "/agents" }}
        />
      </div>
    );
  }

  const total = metrics.totals.dialogs;
  const delta = metricDelta(total, metrics.previous.dialogs);
  const caption = `${plural(total, METRIC_WORDS.dialogs)} ${PERIOD_LABEL[shown].phrase}`;

  if (size === "s") {
    return (
      <div className="kdw-metric kdw-metric--s" data-metric="dialogs">
        <p className="kdw-metric-value">{formatCompact(total)}</p>
        <p className="kdw-metric-caption">{caption}</p>
        <DeltaChip delta={delta} period={shown} compact />
      </div>
    );
  }

  const chart = (
    <>
      <MetricBars
        className="kdw-metric-bars"
        values={metrics.series.dialogs}
        label={`Диалоги по дням ${PERIOD_LABEL[shown].phrase}: ${metrics.series.dialogs.join(", ")}`}
      />
      <ChartLabels metrics={metrics} />
    </>
  );

  if (size === "m") {
    // Плитка 2×1 низкая: число и динамика слева, столбики справа — как в
    // принятой v1, чтобы график не сжимался в полоску под текстом.
    return (
      <div className="kdw-metric kdw-metric--m" data-metric="dialogs">
        <div className="kdw-metric-summary">
          <p className="kdw-metric-value">{formatCompact(total)}</p>
          <p className="kdw-metric-caption">{caption}</p>
          <DeltaChip delta={delta} period={shown} compact />
        </div>
        <div className="kdw-metric-chart">
          <PeriodSwitch value={shown} />
          {chart}
        </div>
        <StaleMark stale={view.stale} />
      </div>
    );
  }

  return (
    <div className="kdw-metric kdw-metric--l" data-metric="dialogs">
      <div className="kdw-metric-head">
        <div className="min-w-0">
          <p className="kdw-metric-value">{formatCompact(total)}</p>
          <p className="kdw-metric-caption">{caption}</p>
        </div>
        <PeriodSwitch value={shown} />
      </div>
      <DeltaChip delta={delta} period={shown} />
      {chart}
      <dl className="kdw-metric-grid">
        {SECONDARY.map(({ key, label }) => (
          <div key={key}>
            <dt>{label}</dt>
            <dd>
              {formatCompact(metrics.totals[key])}
              <DeltaText delta={metricDelta(metrics.totals[key], metrics.previous[key])} />
            </dd>
          </div>
        ))}
      </dl>
      {metrics.unreadable.length ? (
        <p className="truncate text-xs text-[var(--neo-text-secondary)]" role="status">
          Без данных: {metrics.unreadable.join(", ")}
        </p>
      ) : null}
      <StaleMark stale={view.stale} />
    </div>
  );
}

function PeriodSwitch({ value }: { value: MetricsPeriod }) {
  return (
    <div className="kdw-period" role="group" aria-label="Период показателей">
      {(Object.keys(PERIOD_LABEL) as MetricsPeriod[]).map((period) => (
        <button
          key={period}
          type="button"
          aria-pressed={value === period}
          onClick={() => setDashboardPeriod(period)}
        >
          {PERIOD_LABEL[period].short}
        </button>
      ))}
    </div>
  );
}

function DeltaChip({
  compact,
  delta,
  period,
}: {
  compact?: boolean;
  delta: MetricDelta;
  period: MetricsPeriod;
}) {
  const Icon = delta.direction === "down" ? ArrowDownRight : delta.direction === "flat" ? Minus : ArrowUpRight;
  const text =
    delta.direction === "new"
      ? "впервые"
      : delta.direction === "flat"
        ? "без изменений"
        : `${delta.direction === "up" ? "+" : "−"}${delta.percent} %`;
  return (
    <p className="kdw-delta" data-direction={delta.direction}>
      <span className="kdw-delta-chip">
        <Icon className="size-3.5 shrink-0" aria-hidden />
        {text}
      </span>
      {compact ? null : <small>{PERIOD_LABEL[period].previous}</small>}
    </p>
  );
}

function DeltaText({ delta }: { delta: MetricDelta }) {
  if (delta.direction === "flat" || delta.percent === null) return null;
  return (
    <small data-direction={delta.direction}>
      {delta.direction === "up" ? " +" : " −"}
      {delta.percent} %
    </small>
  );
}

const DAY_FORMAT = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", timeZone: "UTC" });

function ChartLabels({ metrics }: { metrics: DashboardMetrics }) {
  const first = metrics.labels[0];
  const last = metrics.labels[metrics.labels.length - 1];
  if (!first || !last) return null;
  const format = (iso: string) => DAY_FORMAT.format(new Date(`${iso}T00:00:00Z`));
  return (
    <p className="kdw-chart-labels" aria-hidden>
      <span>{format(first)}</span>
      <span>сегодня</span>
    </p>
  );
}

export const METRICS_WIDGET: DashboardWidget = {
  id: "metrics",
  title: "Мои показатели",
  purpose: "Сколько разговоров с агентами было за неделю или месяц и как это меняется.",
  Body: MetricsBody,
};
