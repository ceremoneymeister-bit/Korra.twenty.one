declare global {
  interface Window {
    /**
     * Injected by the server as `true`. The embedded TUI Chat surface
     * (`/chat`, `/api/ws`, `/api/pty`) is always enabled, so this is
     * effectively a constant; kept on `window` for any consumer that reads
     * it directly and for parity with the server's bootstrap script.
     */
    __HERMES_DASHBOARD_EMBEDDED_CHAT__?: boolean;
    /** Bubble-chat takeover for the browser dashboard. */
    __KORRA_DASHBOARD_CHAT__?: boolean;
    /** Product workspace selected by the server: fleet or calc. */
    __KORRA_UI_MODE__?: string;
    /** IANA timezone configured for this isolated contour. */
    __KORRA_OWNER_TIMEZONE__?: string;
    /** IANA timezone used to interpret persisted schedule expressions. */
    __KORRA_SCHEDULE_TIMEZONE__?: string;
  }
}

/**
 * Whether the dashboard's embedded TUI Chat surface is available.
 *
 * The embedded chat (`/chat` tab, `/api/ws` + `/api/pty` WebSockets) is now
 * an unconditional part of the dashboard — the desktop app and the in-browser
 * Chat tab both depend on it — so this always returns `true`. The function is
 * retained as a stable seam so call sites don't need to change if the surface
 * ever becomes conditional again.
 */
export function isDashboardEmbeddedChatEnabled(): boolean {
  return true;
}

export function isDashboardBubbleChatEnabled(): boolean {
  if (typeof window === "undefined") return false;
  return (
    window.__KORRA_DASHBOARD_CHAT__ === true ||
    productUiMode() !== null
  );
}

export type ProductUiMode = "fleet" | "calc";

export function productUiMode(): ProductUiMode | null {
  if (typeof window === "undefined") return null;
  const raw = (window.__KORRA_UI_MODE__ ?? "admin").toLowerCase();
  return raw === "fleet" || raw === "calc" ? raw : null;
}

export function isProductUiMode(): boolean {
  return productUiMode() !== null;
}

/** Kept for source-compatible owner-facing helpers; fleet is intentionally not client mode. */
export function isClientUiMode(): boolean {
  return false;
}

// Вкладки агентов больше не приходят из bootstrap HTML: их состав — реальные
// профили контура, см. lib/agent-tabs.ts и hooks/useAgentTabs.ts.

export function getOwnerTimeZone(): string {
  const configured =
    typeof window === "undefined"
      ? ""
      : (window.__KORRA_OWNER_TIMEZONE__ ?? "").trim();
  const fallback = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const candidate = configured || fallback;
  try {
    new Intl.DateTimeFormat("ru-RU", { timeZone: candidate }).format();
    return candidate;
  } catch {
    return fallback;
  }
}

export function getScheduleTimeZone(): string {
  if (typeof window === "undefined") return "UTC";
  return (window.__KORRA_SCHEDULE_TIMEZONE__ ?? "UTC").trim() || "UTC";
}
