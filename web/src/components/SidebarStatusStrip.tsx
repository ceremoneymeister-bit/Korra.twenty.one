import { Link } from "react-router";
import type { StatusResponse } from "@/lib/api";
import { isProductUiMode } from "@/lib/dashboard-flags";
import { cn } from "@/lib/utils";
import { useI18n } from "@/i18n";

/** Gateway + session summary for the System sidebar block (no separate strip chrome). */
export function SidebarStatusStrip({ status, reachable }: SidebarStatusStripProps) {
  const { t } = useI18n();

  // Обрыв связи с панелью важнее любого прошлого ответа: пока опрос не
  // доходит, про шлюз ничего не известно, и молчать об этом нельзя.
  if (reachable === false) {
    if (isProductUiMode()) {
      return (
        <div className="px-5 pb-2 pt-0.5">
          <p className="font-sans text-xs leading-snug tracking-[0.08em] text-text-secondary">
            <span className="font-medium text-destructive">Нет связи с Коррой</span>
          </p>
        </div>
      );
    }
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

  // «Статус шлюза» и «Активные сессии» — слова инженера. Владельцу продукта
  // важен один факт: на связи Корра или нет.
  if (isProductUiMode()) {
    const online = gw.tone === "text-success";
    return (
      <div className="px-5 pb-2 pt-0.5">
        <p className="font-sans text-xs leading-snug tracking-[0.08em] text-text-secondary">
          <span className={cn("font-medium", gw.tone)}>
            {online ? "Корра на связи" : "Корра сейчас недоступна"}
          </span>
        </p>
      </div>
    );
  }

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
  /** `false` — последний опрос `/api/status` не дошёл. */
  reachable: boolean | null;
}
