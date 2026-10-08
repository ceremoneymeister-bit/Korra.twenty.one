/* eslint-disable react-refresh/only-export-components -- виджет и его каталожное описание намеренно живут вместе */
import { useStore } from "@nanostores/react";
import { useRef, useState } from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ProductButton";
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
import { fetchJSON } from "@/lib/api";
import {
  exhaustDay,
  exhaustMoment,
  formatGap,
  formatPace,
  percentLeft,
  pickForecast,
  QUOTA_STALE_AFTER_SECONDS,
  resetLine,
  resetShort,
  planName,
  spareText,
  windowName,
} from "@/lib/codex-quota";
import {
  $dashboardState,
  $dashboardStatus,
  dashboardTimeZone,
  formatMoment,
  formatRelative,
  refreshDashboardState,
  type DashboardQuota,
  type DashboardState,
  type QuotaLevel,
  type QuotaWindow,
} from "@/lib/dashboard-state";
import { cn } from "@/lib/utils";

/**
 * «Лимит Codex» — сколько осталось от лимита подписки ChatGPT и хватит ли его
 * до сброса.
 *
 * Свежие цифры движок сам спрашивает у Codex (`wham/usage`, как `/status`),
 * ответы агентов обновляют их между запросами. Линия залита на *оставшуюся*
 * долю: полный лимит — полная линия. Запасной сброс применяется кнопкой после
 * подтверждения; кнопка появляется, только когда Codex его примет.
 *
 * Без подписки карточки на доске нет вовсе; с подпиской, но до первого ответа
 * Codex, — она говорит, чего ждёт.
 */

const RESET_URL = "/api/dashboard/codex-limit/reset";

interface ResetResponse {
  ok: boolean;
  status: string;
  message: string;
  quota: DashboardQuota | null;
}

function blockedWindow(quota: DashboardQuota): QuotaWindow | null {
  const windows = quota.windows ?? [];
  const empty = windows.find((window) => window.remaining_percent <= 0);
  if (empty) return empty;
  if (!quota.limit_reached) return null;
  const live = windows.filter((window) => !window.renewed);
  return live.sort((a, b) => a.remaining_percent - b.remaining_percent)[0] ?? null;
}

function QuotaBody({ size = "m" }: DashboardWidgetBodyProps) {
  const state = useStore($dashboardState);
  const status = useStore($dashboardStatus);
  const now = useNowSeconds(state?.generated_at ?? 0);

  if (!state) {
    return status === "error" ? (
      <ErrorNote
        size={size}
        title="Не удалось узнать лимит"
        onRetry={() => void refreshDashboardState()}
      />
    ) : (
      <LoadingNote text="Проверяем лимит…" />
    );
  }
  const quota = state.quota;
  if (quota.status === "error") {
    return (
      <ErrorNote
        size={size}
        title="Не удалось загрузить лимит"
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
        text="Лимит появится, когда агенты будут работать через подписку ChatGPT."
        action={{ label: "Ключи и доступы", to: "/env" }}
      />
    );
  }
  if (quota.status === "waiting" || !quota.windows?.length) {
    return (
      <FirstStep
        size={size}
        title="Лимит пока не известен"
        text="Codex ещё не сообщил лимит. Он появится при ближайшем запросе или ответе агента через подписку ChatGPT."
      />
    );
  }

  return <QuotaReady quota={quota} size={size} now={now} state={state} />;
}

function QuotaLine({
  blocked,
  now,
  size,
  thin,
  timeZone,
  window,
}: {
  blocked: boolean;
  now: number;
  size: "s" | "m" | "l";
  thin: boolean;
  timeZone: string;
  window: QuotaWindow;
}) {
  const left = percentLeft(window);
  const name = windowName(window.window_minutes);
  return (
    <div className={cn("kdw-limit", thin && "kdw-limit--thin")} data-window={window.key}>
      <div className="kdw-limit-top">
        <span className="kdw-limit-name">{name}</span>
        <span className="kdw-limit-pct" data-level={window.level}>
          <strong>
            {left}
            <small> %</small>
          </strong>{" "}
          <span>осталось</span>
        </span>
      </div>
      <div
        className="kdw-limit-bar"
        data-level={window.level}
        role="meter"
        aria-label={`${name}: осталось`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={left}
        aria-valuetext={`осталось ${left} %`}
      >
        <i style={{ width: `${left}%` }} />
      </div>
      {window.resets_at ? (
        <p className="kdw-limit-sub">
          {size === "s"
            ? `сброс ${resetShort(window.resets_at, now, timeZone)}`
            : blocked
              ? `Агенты не ответят до сброса: ${resetLine(window.resets_at, now, timeZone)}`
              : `Сброс ${resetLine(window.resets_at, now, timeZone)}`}
        </p>
      ) : null}
    </div>
  );
}

function ForecastLine({
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
  const forecast = pickForecast(quota);
  if (!forecast) return null;
  const several = (quota.windows ?? []).length > 1;
  const prefix = several ? `${windowName(forecast.window.window_minutes)}. ` : "";
  if (forecast.kind === "calm") {
    return (
      <p className="kdw-limit-say" data-level="normal">
        <span className="kdw-limit-dot" aria-hidden />
        <span>{size === "s" ? "темп спокойный" : `${prefix}Темп спокойный — хватит до сброса.`}</span>
      </p>
    );
  }
  if (size === "s") {
    return (
      <p className="kdw-limit-say" data-level="warn">
        <span className="kdw-limit-dot" aria-hidden />
        <span>при таком темпе кончится {exhaustDay(forecast.exhaustsAt, now, timeZone)}</span>
      </p>
    );
  }
  return (
    <p className="kdw-limit-say" data-level="warn">
      <span className="kdw-limit-dot" aria-hidden />
      <span>
        <strong>
          {prefix}Тратится {formatPace(forecast.pace)} быстрее ровного темпа.
        </strong>{" "}
        Так лимит кончится {exhaustMoment(forecast.exhaustsAt, now, timeZone)} — за{" "}
        {formatGap(forecast.gap)} до сброса.
      </span>
    </p>
  );
}

function QuotaReady({
  now,
  quota,
  size,
  state,
}: {
  now: number;
  quota: DashboardQuota;
  size: "s" | "m" | "l";
  state: DashboardState;
}) {
  const timeZone = dashboardTimeZone(state);
  const windows = quota.windows ?? [];
  const blocked = blockedWindow(quota);
  const level: QuotaLevel = quota.level ?? "normal";
  const credits = quota.reset_credits;
  const spares = credits && credits.available > 0 ? credits.available : 0;
  const [resetMessage, setResetMessage] = useState<string | null>(null);

  return (
    <div
      id="codex-quota"
      className={cn("kdw-quota", `kdw-quota--${size}`)}
      data-quota-level={level}
    >
      {windows.map((window) => (
        <QuotaLine
          key={window.key}
          window={window}
          blocked={blocked?.key === window.key}
          now={now}
          size={size}
          thin={windows.length > 1}
          timeZone={timeZone}
        />
      ))}
      {blocked ? (
        <p className="kdw-limit-say" data-level="critical" role="alert">
          <span className="kdw-limit-dot" aria-hidden />
          <span>
            <strong>Лимит исчерпан.</strong>
            {size !== "s" && quota.can_reset ? " Его можно вернуть запасным сбросом." : ""}
          </span>
        </p>
      ) : (
        <ForecastLine quota={quota} size={size} now={now} timeZone={timeZone} />
      )}
      {resetMessage && size !== "s" ? (
        <p className="kdw-limit-message" role="status">
          {resetMessage}
        </p>
      ) : null}
      {size === "s" ? null : (
        <div className="kdw-limit-foot">
          {quota.can_reset && credits ? (
            <ResetAction spares={credits.available} onResult={setResetMessage} />
          ) : null}
          {spares > 0 ? <span className="kdw-limit-chip">{spareText(spares)}</span> : null}
          {quota.plan_type ? (
            <span className="kdw-limit-plan">Тариф {planName(quota.plan_type)}</span>
          ) : null}
        </div>
      )}
    </div>
  );
}

function ResetAction({
  onResult,
  spares,
}: {
  onResult: (message: string | null) => void;
  spares: number;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const running = useRef(false);

  const confirm = async () => {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    try {
      const result = await fetchJSON<ResetResponse>(RESET_URL, { method: "POST" });
      onResult(result.message);
      const current = $dashboardState.get();
      if (current && result.quota) $dashboardState.set({ ...current, quota: result.quota });
      void refreshDashboardState();
    } catch {
      onResult("Не удалось связаться с панелью. Проверьте лимит чуть позже.");
    } finally {
      running.current = false;
      setBusy(false);
      setOpen(false);
    }
  };

  return (
    <>
      <Button
        size="sm"
        disabled={busy}
        aria-busy={busy}
        onClick={() => {
          onResult(null);
          setOpen(true);
        }}
      >
        Сбросить лимит
      </Button>
      <ConfirmDialog
        open={open}
        title="Сбросить лимит Codex?"
        description={`Запасной сброс сразу вернёт полный лимит. В запасе останется ${Math.max(0, spares - 1)}. Отменить нельзя.`}
        cancelLabel="Отмена"
        confirmLabel="Сбросить"
        loading={busy}
        onCancel={() => {
          if (!running.current) setOpen(false);
        }}
        onConfirm={() => void confirm()}
      />
    </>
  );
}

function QuotaUpdated({ size = "m" }: DashboardWidgetBodyProps) {
  const state = useStore($dashboardState);
  const now = useNowSeconds(state?.generated_at ?? 0);
  const quota = state?.quota;
  if (!state || !quota || quota.status !== "ok" || !quota.captured_at) return null;

  const age = now - quota.captured_at;
  if (age > QUOTA_STALE_AFTER_SECONDS) {
    const timeZone = dashboardTimeZone(state);
    const moment = formatMoment(quota.captured_at, now, timeZone);
    const text = moment.startsWith("сегодня в ") ? moment.slice("сегодня в ".length) : moment;
    return (
      <span className="kdw-quota-updated kdw-quota-updated--stale" data-testid="quota-updated">
        данные от {text}
      </span>
    );
  }
  if (size === "s") return null;
  return (
    <span className="kdw-quota-updated" data-testid="quota-updated">
      <i aria-hidden />
      обновлено {formatRelative(quota.captured_at, now)}
    </span>
  );
}

export const CODEX_QUOTA_WIDGET: DashboardWidget = {
  id: "codex-quota",
  title: "Лимит Codex",
  purpose: "Сколько осталось от лимита подписки ChatGPT и хватит ли его до сброса.",
  isAvailable: (state) => state?.quota?.available === true,
  Body: QuotaBody,
  HeaderNote: QuotaUpdated,
};
