import {
  formatDuration,
  formatMoment,
  plural,
  type DashboardQuota,
  type QuotaWindow,
} from "@/lib/dashboard-state";

/** Данные старше этого считаются несвежими: подпись в шапке меняет цвет. */
export const QUOTA_STALE_AFTER_SECONDS = 30 * 60;

const WEEKDAY = /^(пн|вт|ср|чт|пт|сб|вс), /;

/** «Неделя», «5 часов», «Сутки» — как называет окна Codex. */
export function windowName(minutes: number | null | undefined): string {
  if (!minutes || minutes <= 0) return "Окно";
  if (minutes % 10080 === 0) {
    const weeks = minutes / 10080;
    return weeks === 1 ? "Неделя" : `${weeks} ${plural(weeks, ["неделя", "недели", "недель"])}`;
  }
  if (minutes % 1440 === 0) {
    const days = minutes / 1440;
    return days === 1 ? "Сутки" : `${days} ${plural(days, ["день", "дня", "дней"])}`;
  }
  if (minutes % 60 === 0) {
    const hours = minutes / 60;
    return `${hours} ${plural(hours, ["час", "часа", "часов"])}`;
  }
  return `${minutes} мин`;
}

/** «Pro», «Plus» — как называет тариф сама подписка. */
export function planName(plan: string): string {
  return plan.charAt(0).toUpperCase() + plan.slice(1);
}

export function percentLeft(window: QuotaWindow): number {
  return Math.max(0, Math.min(100, Math.round(window.remaining_percent)));
}

/** «Сброс ср, 14 окт. в 11:00 · через 5 дн. 16 ч». */
export function resetLine(resetsAt: number, now: number, timeZone: string): string {
  const wait = resetsAt - now;
  return `${formatMoment(resetsAt, now, timeZone)}${wait > 0 ? ` · через ${formatDuration(wait)}` : ""}`;
}

/** «ср в 11:00» / «сегодня в 22:40» — для тесной плитки. */
export function resetShort(resetsAt: number, now: number, timeZone: string): string {
  const moment = formatMoment(resetsAt, now, timeZone);
  const weekday = WEEKDAY.exec(moment)?.[1];
  return weekday ? `${weekday} в ${moment.slice(-5)}` : moment;
}

function roundToHalfHour(seconds: number): number {
  return Math.round(seconds / 1800) * 1800;
}

/** «в вс, 11 окт. около 24:00», «сегодня около 18:30». */
export function exhaustMoment(exhaustsAt: number, now: number, timeZone: string): string {
  const moment = formatMoment(roundToHalfHour(exhaustsAt), now, timeZone);
  const approx = moment.replace(/ в (\d\d:\d\d)$/, " около $1");
  return WEEKDAY.test(approx) ? `в ${approx}` : approx;
}

/** «в вс» / «сегодня» / «завтра» — день, когда лимит кончится. */
export function exhaustDay(exhaustsAt: number, now: number, timeZone: string): string {
  const moment = formatMoment(exhaustsAt, now, timeZone);
  const weekday = WEEKDAY.exec(moment)?.[1];
  return weekday ? `в ${weekday}` : (moment.split(" ")[0] ?? moment);
}

/** «в 1,5 раза», «в 2 раза», «в 5 раз» — во сколько раз быстрее ровного темпа. */
export function formatPace(pace: number): string {
  const rounded = Math.max(1.1, Math.round(pace * 10) / 10);
  if (Number.isInteger(rounded)) return `в ${rounded} ${plural(rounded, ["раз", "раза", "раз"])}`;
  return `в ${rounded.toLocaleString("ru-RU")} раза`;
}

/** «2,5 дня», «3 дня», «5 ч 20 мин» — сколько не хватит до сброса. */
export function formatGap(seconds: number): string {
  if (seconds < 86_400) return formatDuration(seconds);
  const days = Math.round(seconds / 43_200) / 2;
  if (Number.isInteger(days)) return `${days} ${plural(days, ["день", "дня", "дней"])}`;
  return `${days.toLocaleString("ru-RU")} дня`;
}

/** «В запасе 2 сброса» / «В запасе 1 сброс» / «В запасе 5 сбросов». */
export function spareText(count: number): string {
  return `В запасе ${count} ${plural(count, ["сброс", "сброса", "сбросов"])}`;
}

export type QuotaForecastView =
  | { kind: "fast"; window: QuotaWindow; pace: number; exhaustsAt: number; gap: number }
  | { kind: "calm"; window: QuotaWindow };

/**
 * Единственная строка прогноза карточки.
 *
 * Берём окно, которое кончится раньше сброса (самое близкое); если таких нет,
 * но прогноз по какому-то окну есть — темп спокойный. Нет прогноза — нет строки.
 */
export function pickForecast(quota: DashboardQuota): QuotaForecastView | null {
  const withForecast = (quota.windows ?? []).filter(
    (window) => window.forecast !== null && window.resets_at !== null,
  );
  const fast = withForecast
    .filter((window) => window.forecast!.exhausts_before_reset)
    .sort((a, b) => a.forecast!.exhausts_at - b.forecast!.exhausts_at)[0];
  if (fast) {
    const forecast = fast.forecast!;
    return {
      kind: "fast",
      window: fast,
      pace: forecast.pace,
      exhaustsAt: forecast.exhausts_at,
      gap: (fast.resets_at ?? forecast.exhausts_at) - forecast.exhausts_at,
    };
  }
  const calm = withForecast[0];
  return calm ? { kind: "calm", window: calm } : null;
}
