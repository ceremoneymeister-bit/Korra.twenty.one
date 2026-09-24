/* eslint-disable react-refresh/only-export-components -- виджет и его каталожное описание намеренно живут вместе */
import { useStore } from "@nanostores/react";
import { AlertTriangle } from "lucide-react";

import type {
  DashboardWidget,
  DashboardWidgetBodyProps,
} from "@/components/dashboard/widget-types";
import {
  ErrorNote,
  FirstStep,
  LoadingNote,
  useNowSeconds,
} from "@/components/dashboard/widget-states";
import { QuotaRing } from "@/components/dashboard/visuals";
import {
  $dashboardState,
  $dashboardStatus,
  dashboardTimeZone,
  formatDuration,
  formatMoment,
  formatRelative,
  refreshDashboardState,
  type DashboardQuota,
} from "@/lib/dashboard-state";
import { cn } from "@/lib/utils";

/**
 * «Квота Codex» — сколько израсходовано из окна подписки ChatGPT и когда оно
 * сбросится.
 *
 * Значение приходит само с ответами агентов: движок разбирает `rate_limits`,
 * который присылает Codex, и хранит последнее известное между перезапусками.
 * Своего запроса к Codex карточка не делает. Без подписки карточки на доске
 * нет вовсе; с подпиской, но до первого ответа — она говорит, чего ждёт.
 *
 * Пороги 80 % и 95 % совпадают с полосой «Требует внимания»: там квота
 * появляется, когда почти исчерпана, — раньше, чем агент перестанет отвечать.
 */

const LEVEL_TEXT = {
  normal: null,
  warn: "Израсходовано больше 80 % — стоит беречь запросы до сброса.",
  critical: "Квота почти исчерпана: агенты могут перестать отвечать до сброса.",
} as const;

function QuotaBody({ size = "m" }: DashboardWidgetBodyProps) {
  const state = useStore($dashboardState);
  const status = useStore($dashboardStatus);
  const now = useNowSeconds(state?.generated_at ?? 0);

  if (!state) {
    return status === "error" ? (
      <ErrorNote
        size={size}
        title="Не удалось узнать квоту"
        onRetry={() => void refreshDashboardState()}
      />
    ) : (
      <LoadingNote text="Проверяем квоту…" />
    );
  }
  const quota = state.quota;
  if (quota.status === "error") {
    return (
      <ErrorNote
        size={size}
        title="Не удалось прочитать квоту"
        detail="Последнее значение не прочиталось. Повторите запрос."
        onRetry={() => void refreshDashboardState()}
      />
    );
  }
  if (quota.status === "absent") {
    // Обычно такой карточки на доске нет (`isAvailable`); если она всё же
    // нарисована, старый процент или «ждём ответа» без подписки — выдумка.
    return (
      <FirstStep
        size={size}
        title="Подписка ChatGPT не подключена"
        text="Квота появится, когда агенты будут работать через подписку ChatGPT."
        action={{ label: "Ключи и доступы", to: "/env" }}
      />
    );
  }
  if (quota.status === "waiting") {
    return (
      <FirstStep
        size={size}
        title="Ждём первого ответа Codex"
        text="Процент и время сброса появятся после ближайшего ответа агента через подписку ChatGPT."
      />
    );
  }

  const timeZone = dashboardTimeZone(state);
  if (quota.status === "reset" || quota.used_percent === undefined) {
    const lastReset = Math.max(0, ...(quota.windows ?? []).map((window) => window.resets_at ?? 0));
    return (
      <FirstStep
        size={size}
        title="Окно квоты обновилось"
        text={`${lastReset ? `Лимит сбросился ${formatMoment(lastReset, now, timeZone)}. ` : ""}Новый процент придёт с ближайшим ответом агента.`}
      />
    );
  }

  return <QuotaReady quota={quota} size={size} now={now} timeZone={timeZone} />;
}

function QuotaReady({
  now,
  quota,
  size,
  timeZone,
}: {
  now: number;
  quota: DashboardQuota;
  size: "s" | "m" | "l";
  timeZone: string;
}) {
  const used = quota.used_percent ?? 0;
  const level = quota.level ?? "normal";
  const resetsAt = quota.resets_at ?? null;
  const left = resetsAt ? formatDuration(resetsAt - now) : null;
  const warning = LEVEL_TEXT[level];

  if (size === "s") {
    return (
      <div id="codex-quota" className="kdw-quota kdw-quota--s" data-quota-level={level}>
        <QuotaRing percent={used} level={level} size="s" />
        <p className="truncate text-xs text-[var(--neo-text-secondary)]">
          {left ? `сброс через ${left}` : quota.window_label}
        </p>
        <span className="sr-only">Израсходовано {Math.round(used)} %</span>
      </div>
    );
  }

  return (
    <div id="codex-quota" className={cn("kdw-quota", size === "l" && "kdw-quota--l")} data-quota-level={level}>
      <div className="kdw-quota-main">
        <QuotaRing percent={used} level={level} size={size === "l" ? "l" : "m"} />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-[var(--neo-text-primary)]">
            Израсходовано {Math.round(used)} %
            {quota.window_label ? ` · ${quota.window_label}` : ""}
          </p>
          {resetsAt ? (
            <p className="text-xs text-[var(--neo-text-secondary)]">
              Сброс {formatMoment(resetsAt, now, timeZone)}
              <span className="kdw-quota-left"> (через {left})</span>
            </p>
          ) : null}
          {warning ? (
            <p className="kdw-quota-warning" role={level === "critical" ? "alert" : "status"}>
              <AlertTriangle className="size-3.5 shrink-0" aria-hidden />
              <span>{warning}</span>
            </p>
          ) : null}
        </div>
      </div>
      {size === "l" ? (
        <ul className="kdw-quota-windows" aria-label="Окна подписки">
          {(quota.windows ?? []).map((window) => (
            <li key={window.key}>
              <span className="kdw-quota-window-label">
                {window.label}
                {window.expired ? " · сброшено" : ""}
              </span>
              <span className="kdw-quota-meter" aria-hidden>
                <i style={{ width: `${Math.max(0, Math.min(100, window.used_percent))}%` }} />
              </span>
              <span className="kdw-quota-window-value">{Math.round(window.used_percent)} %</span>
            </li>
          ))}
        </ul>
      ) : null}
      <p className="kdw-quota-foot truncate text-xs text-[var(--neo-text-secondary)]">
        {quota.plan_type ? `Тариф ${quota.plan_type.toUpperCase()} · ` : ""}
        {quota.captured_at ? `данные ${formatRelative(quota.captured_at, now)}` : ""}
        {quota.stale ? " — давно не было ответов через подписку" : ""}
      </p>
    </div>
  );
}

export const CODEX_QUOTA_WIDGET: DashboardWidget = {
  id: "codex-quota",
  title: "Квота Codex",
  purpose: "Сколько израсходовано из окна подписки ChatGPT и когда оно сбросится.",
  isAvailable: (state) => state?.quota?.available === true,
  Body: QuotaBody,
};
