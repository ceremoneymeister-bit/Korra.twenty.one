import { Link } from "react-router";
import { CircleAlert, LoaderCircle } from "lucide-react";
import type { StatusResponse } from "@/lib/api";
import { isProductUiMode } from "@/lib/dashboard-flags";
import { cn } from "@/lib/utils";
import { useI18n } from "@/i18n";

/** Product footer stays quiet until the connection or gateway needs attention. */
export function SidebarStatusStrip({ status, reachable, collapsed = false }: SidebarStatusStripProps) {
  const { t } = useI18n();

  if (isProductUiMode()) {
    const starting = reachable !== false && status?.gateway_state === "starting";
    const message = reachable === false
      ? "Нет связи с Коррой"
      : status === null || gatewayLine(status, t).tone === "text-success"
        ? null
        : starting
          ? "Корра запускается…"
          : status.gateway_state === "startup_failed"
            ? "Не удалось запустить Корру"
            : "Корра остановлена";
    if (!message) return null;
    const Icon = starting ? LoaderCircle : CircleAlert;
    return (
      <div
        role="status"
        className={cn("flex items-center gap-2 px-5 py-2 text-xs leading-snug text-text-secondary", collapsed && "lg:justify-center lg:px-0")}
        title={message}
      >
        <Icon aria-hidden className={cn("size-[15px] shrink-0", starting ? "animate-spin text-warning" : "text-destructive")} />
        <span className={cn(collapsed && "lg:sr-only")}>{message}</span>
      </div>
    );
  }

  // Обрыв связи с панелью важнее любого прошлого ответа: пока опрос не
  // доходит, про шлюз ничего не известно, и молчать об этом нельзя.
  if (reachable === false) {
    return (
      <div className="px-5 pb-2 pt-0.5">
        <p className="font-sans text-xs leading-snug tracking-[0.08em] text-text-secondary">
          <span className="text-text-tertiary">{t.app.gatewayStatusLabel}</span>{" "}
          <span className="font-medium text-destructive">Панель недоступна</span>
        </p>
      </div>
    );
  }

  if (status === null) {
    return (
      <div className="px-5 py-1.5" aria-hidden>
        <div className="h-2 w-[80%] max-w-full animate-pulse rounded-sm bg-midground/10" />
      </div>
    );
  }

  const gw = gatewayLine(status, t);
  const { activeSessionsLabel, gatewayStatusLabel } = t.app;

  return (
    <Link
      to="/sessions"
      title={t.app.statusOverview}
      className={cn(
        "block text-left",
        "px-5 pb-2 pt-0.5",
        "text-text-secondary",
        "transition-colors hover:text-midground",
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground/40",
        "focus-visible:ring-inset",
      )}
    >
      <div className="flex flex-col gap-1 font-sans text-xs leading-snug tracking-[0.08em]">
        <p className="break-words">
          <span className="text-text-tertiary">{gatewayStatusLabel}</span>{" "}
          <span className={cn("font-medium", gw.tone)}>{gw.label}</span>
        </p>

        <p className="break-words">
          <span className="text-text-tertiary">{activeSessionsLabel}</span>{" "}
          <span className="tabular-nums text-text-secondary">
            {status.active_sessions}
          </span>
        </p>
      </div>
    </Link>
  );
}

export function gatewayLine(
  status: StatusResponse,
  t: ReturnType<typeof useI18n>["t"],
): { label: string; tone: string } {
  const g = t.app.gatewayStrip;
  const byState: Record<string, { label: string; tone: string }> = {
    running: { label: g.running, tone: "text-success" },
    starting: { label: g.starting, tone: "text-warning" },
    startup_failed: { label: g.failed, tone: "text-destructive" },
    stopped: { label: g.stopped, tone: "text-muted-foreground" },
  };
  if (status.gateway_state && byState[status.gateway_state]) {
    return byState[status.gateway_state];
  }
  return status.gateway_running
    ? { label: g.running, tone: "text-success" }
    : { label: g.off, tone: "text-muted-foreground" };
}

interface SidebarStatusStripProps {
  status: StatusResponse | null;
  collapsed?: boolean;
  /** `false` — последний опрос `/api/status` не дошёл. */
  reachable: boolean | null;
}
