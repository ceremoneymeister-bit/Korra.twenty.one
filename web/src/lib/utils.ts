import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Mondwest font only — use on layout shells; do not force normal-case here or `text-display` chrome (Segmented, badges) stops uppercasing. */
export const themedFont = "font-mondwest";

/** Mondwest body copy — sentence-case themed text (not uppercase chrome). */
export const themedBody = "font-mondwest normal-case";

/** Mondwest brand chrome — uppercase section headers and nav labels. */
export const themedChrome = "font-mondwest text-display";

/** Relative time from a Unix epoch timestamp (seconds). */
type Translate = (key: string, values?: Record<string, string | number>) => string;

function relativeTime(
  key: string,
  values: Record<string, string | number> = {},
  translate?: Translate,
): string {
  if (translate) return translate(key, values);
  return Object.entries(values).reduce(
    (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
    key,
  );
}

export function timeAgo(ts: number, translate?: Translate): string {
  const delta = Date.now() / 1000 - ts;
  if (delta < 60) return relativeTime("just now", {}, translate);
  if (delta < 3600) {
    return relativeTime("{minutes}m ago", { minutes: Math.floor(delta / 60) }, translate);
  }
  if (delta < 86400) {
    return relativeTime("{hours}h ago", { hours: Math.floor(delta / 3600) }, translate);
  }
  if (delta < 172800) return relativeTime("yesterday", {}, translate);
  return relativeTime("{days}d ago", { days: Math.floor(delta / 86400) }, translate);
}

/** Relative time from an ISO-8601 timestamp string. */
export function isoTimeAgo(iso: string, translate?: Translate): string {
  const delta = (Date.now() - new Date(iso).getTime()) / 1000;
  if (delta < 0 || Number.isNaN(delta)) return relativeTime("unknown", {}, translate);
  if (delta < 60) return relativeTime("just now", {}, translate);
  if (delta < 3600) {
    return relativeTime("{minutes}m ago", { minutes: Math.floor(delta / 60) }, translate);
  }
  if (delta < 86400) {
    return relativeTime("{hours}h ago", { hours: Math.floor(delta / 3600) }, translate);
  }
  return relativeTime("{days}d ago", { days: Math.floor(delta / 86400) }, translate);
}
