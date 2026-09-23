/* eslint-disable react-refresh/only-export-components -- состояния карточек и хук их источника живут вместе */
/**
 * Общие состояния карточек на живых данных: ожидание, ошибка, первый шаг.
 *
 * Правило, ради которого они вынесены в одно место: ноль и выдумка одинаково
 * недопустимы. Пока сводки нет — карточка говорит «читаем», при сбое —
 * «не удалось» с повтором, у пустого источника — чего она ждёт и что сделать,
 * чтобы здесь появились данные.
 */

import { useEffect, useState, type ReactNode } from "react";
import { useStore } from "@nanostores/react";
import { ChevronRight, RefreshCw } from "lucide-react";
import { Link } from "react-router";

import { FittedText } from "@/components/dashboard/FittedText";
import { ProductButton } from "@/components/ProductButton";
import type { WidgetSize } from "@/lib/dashboard-layout";
import {
  $dashboardState,
  $dashboardStatus,
  refreshDashboardState,
  sectionReady,
  type DashboardState,
} from "@/lib/dashboard-state";
import { cn } from "@/lib/utils";

type SectionKey = Exclude<keyof DashboardState, "version" | "generated_at" | "timezone">;

export type SectionView<T> =
  | { phase: "loading" }
  | { phase: "error"; retry: () => void }
  | { phase: "ready"; section: T; stale: boolean; state: DashboardState; retry: () => void };

/**
 * Одна секция общей сводки дашборда.
 *
 * `stale` — ответ есть, но последний опрос не удался: карточка показывает
 * данные и честно говорит, что они не свежие.
 */
export function useDashboardSection<K extends SectionKey>(
  key: K,
): SectionView<Exclude<DashboardState[K], { status: "error" }>> {
  const state = useStore($dashboardState);
  const status = useStore($dashboardStatus);
  const retry = () => void refreshDashboardState();
  if (!state) {
    return status === "error" ? { phase: "error", retry } : { phase: "loading" };
  }
  const section = state[key] as DashboardState[K];
  if (!sectionReady(section as { status: string })) return { phase: "error", retry };
  // Ответ есть, но последний опрос не дошёл: показываем его как последнее
  // известное, а не как текущее.
  const stale = status === "error";
  return {
    phase: "ready",
    section: section as Exclude<DashboardState[K], { status: "error" }>,
    stale,
    state,
    retry,
  };
}

export function WidgetStack({
  busy,
  children,
  className,
}: {
  busy?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      aria-busy={busy || undefined}
      className={cn("flex min-h-0 flex-1 flex-col gap-2 overflow-hidden", className)}
    >
      {children}
    </div>
  );
}

export function NoteTitle({ children }: { children: ReactNode }) {
  return (
    <p className="korra-widget-line line-clamp-2 shrink-0 text-sm font-semibold text-[var(--neo-text-primary)]">
      {children}
    </p>
  );
}

export function RetryButton({ onClick, label = "Повторить" }: { onClick: () => void; label?: string }) {
  return (
    <ProductButton
      outlined
      size="sm"
      onClick={onClick}
      prefix={<RefreshCw className="size-4 shrink-0" aria-hidden />}
      className="w-fit"
    >
      {label}
    </ProductButton>
  );
}

export function WidgetLink({ children, to }: { children: ReactNode; to: string }) {
  return (
    <Link
      to={to}
      className="inline-flex min-h-[44px] w-fit shrink-0 items-center gap-2 rounded-lg px-3 text-sm font-semibold text-[var(--neo-text-primary)] transition-shadow hover:shadow-[var(--neo-inset-compact)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--neo-accent-line)]"
    >
      {children}
      <ChevronRight className="size-4 shrink-0" aria-hidden />
    </Link>
  );
}

export function LoadingNote({ text }: { text: string }) {
  return (
    <WidgetStack busy>
      <span className="text-sm text-[var(--neo-text-secondary)]">{text}</span>
      <span aria-hidden className="kdw-skeleton" />
    </WidgetStack>
  );
}

export interface ErrorNoteProps {
  title: string;
  detail?: string;
  size?: WidgetSize;
  onRetry: () => void;
}

/** Сбой источника: не ноль, а честное «не удалось» с повтором. */
export function ErrorNote({ detail, onRetry, size, title }: ErrorNoteProps) {
  return (
    <WidgetStack>
      <div role="alert" className="contents">
        <NoteTitle>{title}</NoteTitle>
        {size === "s" || !detail ? null : (
          <FittedText text={detail} className="text-[var(--neo-text-secondary)]" />
        )}
      </div>
      <RetryButton onClick={onRetry} />
    </WidgetStack>
  );
}

export interface FirstStepProps {
  title: string;
  text: string;
  size?: WidgetSize;
  action?: { label: string; to: string };
}

/** Пустой источник: чего карточка ждёт и что сделать, чтобы здесь появились данные. */
export function FirstStep({ action, size, text, title }: FirstStepProps) {
  return (
    <WidgetStack>
      <NoteTitle>{title}</NoteTitle>
      {size === "s" ? null : (
        <FittedText text={text} className="text-[var(--neo-text-secondary)]" />
      )}
      {action && size !== "s" ? <WidgetLink to={action.to}>{action.label}</WidgetLink> : null}
    </WidgetStack>
  );
}

/** Отметка «данные не свежие» у карточки, показывающей последний ответ. */
export function StaleMark({ stale }: { stale: boolean }) {
  if (!stale) return null;
  return (
    <p role="status" className="truncate text-xs text-[var(--neo-text-secondary)]" data-widget-stale>
      Показано последнее известное состояние
    </p>
  );
}

/**
 * Текущее время в секундах как подписка, а не как чтение часов в рендере:
 * «через 19 мин» должно меняться само, без нового ответа сервера. До первого
 * тика возвращается время ответа сервера — оно заведомо настоящее.
 */
export function useNowSeconds(fallback: number, intervalMs = 30_000): number {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    const tick = () => setNow(Date.now() / 1000);
    const first = window.setTimeout(tick, 0);
    const timer = window.setInterval(tick, intervalMs);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(timer);
    };
  }, [intervalMs]);
  return now ?? fallback;
}
