/**
 * AuthWidget — sidebar "Logged in as …" affordance for the dashboard
 * OAuth gate (Phase 7 of .hermes/plans/2026-05-21-dashboard-oauth-auth.md).
 *
 * Renders nothing in loopback / --insecure mode. In gated mode, fetches
 * /api/auth/me on mount and surfaces:
 *
 *   - the user_id (truncated to 14 chars + ellipsis) since the Nous Portal
 *     contract V1 doesn't emit email/display_name claims (Contract Anchor
 *     C4 in the plan; the API responds with empty strings for those
 *     fields, so we use user_id as the display value)
 *   - the provider's display_name (looked up from /api/auth/providers,
 *     defaults to the bare provider key)
 *   - a logout button that POSTs /auth/logout and full-page-navigates to
 *     /login (the dashboard becomes inaccessible again)
 *
 * Failure modes:
 *   - 401 from /api/auth/me means we're not gated (or the gate is on but
 *     we have no cookie — in that case the gate's middleware would have
 *     redirected us before App.tsx renders, so we won't see this). The
 *     widget renders nothing.
 *   - Network error: shows a minimal "auth status unavailable" message
 *     so the user knows the widget tried.
 */

import { useEffect, useState } from "react";
import { api, type AuthMeResponse } from "@/lib/api";
import { useCabinetSession } from "@/hooks/useCabinetSession";
import { cn } from "@/lib/utils";
import { LogOut } from "lucide-react";
import { useI18n } from "@/i18n";

const compactLogoutClassName = "group/logout inline-flex min-h-[44px] min-w-[44px] shrink-0 items-center justify-center rounded-full text-[var(--neo-text-secondary)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--neo-accent-line)]";

interface AuthWidgetProps {
  className?: string;
  collapsed?: boolean;
  compact?: boolean;
}

/** Truncate ``user_id`` to fit a small UI without revealing the full
 *  opaque identifier. 14 chars is enough to disambiguate users in a
 *  small org and short enough to fit a single sidebar row. */
function truncateUserId(id: string): string {
  if (id.length <= 14) return id;
  return `${id.slice(0, 14)}…`;
}

export function AuthWidget({ className, collapsed = false, compact = false }: AuthWidgetProps) {
  const { tr } = useI18n();
  const [me, setMe] = useState<AuthMeResponse | null>(null);
  const [hidden, setHidden] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { logout: cabinetLogout } = useCabinetSession();

  // Loopback / --insecure mode: the auth gate is off, so /api/auth/me is a
  // guaranteed 401. Don't fire the request at all — it only produces console
  // noise ("Failed to load resource: 401") on every dashboard load.
  const gated =
    typeof window !== "undefined" && !!window.__HERMES_AUTH_REQUIRED__;

  useEffect(() => {
    if (!gated || cabinetLogout) return;
    let cancelled = false;
    api
      .getAuthMe()
      .then((data) => {
        if (cancelled) return;
        setMe(data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // 401 from /api/auth/me means the gate isn't engaged in this
        // process (loopback mode) — render nothing. fetchJSON throws an
        // Error with the status code as a prefix; the global 401
        // handler only redirects on the structured envelope, so a plain
        // 401 from /api/auth/me with no envelope bubbles up here.
        const msg = err instanceof Error ? err.message : String(err);
        if (msg.startsWith("401:") || msg.startsWith("403:")) {
          setHidden(true);
          return;
        }
        setError(tr("auth status unavailable"));
      });
    return () => {
      cancelled = true;
    };
  }, [gated, cabinetLogout, tr]);

  useEffect(() => {
    if (!cabinetLogout && !gated) return;
    const restore = (event: PageTransitionEvent) => {
      if (event.persisted) window.location.reload();
    };
    window.addEventListener("pageshow", restore);
    return () => window.removeEventListener("pageshow", restore);
  }, [cabinetLogout, gated]);

  if (cabinetLogout) return <a href={cabinetLogout}
    aria-label="Выйти из кабинета" title="Выйти из кабинета"
    className={cn(
      compact
        ? compactLogoutClassName
        : "mx-3 my-2 inline-flex min-h-11 items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm hover:bg-current/10 focus-visible:ring-2",
      className,
    )}
    onClick={event => {
      event.preventDefault();
      // If the browser restores this document from its back/forward cache,
      // keep private content hidden until pageshow reloads through the gate.
      document.documentElement.style.visibility = "hidden";
      window.location.assign(cabinetLogout);
    }}>
    {compact ? (
      <CompactLogoutContent collapsed={collapsed} />
    ) : (
      <><LogOut size={17} aria-hidden /><span className={collapsed ? "lg:sr-only" : undefined}>Выйти из кабинета</span></>
    )}
  </a>;

  // Nothing to show in ungated mode — there is no logged-in identity.
  if (!gated) return null;

  if (hidden) return null;

  if (error && !me) {
    return (
      <div
        className={cn(
          "px-5 py-2 text-[0.65rem] tracking-[0.05em] text-muted-foreground/70",
          className,
        )}
      >
        {error}
      </div>
    );
  }

  if (!me) {
    // Loading. Reserve the row height so the sidebar doesn't flicker
    // when the data arrives.
    return (
      <div
        className={cn(
          "h-9 px-5 py-2 text-[0.65rem] text-muted-foreground/40",
          className,
        )}
        aria-busy="true"
      >
        …
      </div>
    );
  }

  const handleLogout = () => {
    setError(null);
    void api.logout().catch(() => setError("Не удалось выйти. Проверьте соединение и повторите."));
  };

  // Prefer display_name → email → truncated user_id. Contract V1 only
  // populates user_id; the fallthroughs are forward-compat for a future
  // Portal that adds a userinfo endpoint (OQ-C1 in the plan).
  const label = me.display_name || me.email || truncateUserId(me.user_id);

  return (
    <div
      className={cn(
        compact
          ? "relative shrink-0"
          : "flex shrink-0 items-center justify-between gap-2 border-t border-current/10 px-5 py-2 text-[0.65rem] tracking-[0.05em]",
        className,
      )}
      role="status"
      aria-label={tr("Logged in as {name}", { name: label })}
    >
      <div className={cn("flex min-w-0 flex-col", compact ? "sr-only" : collapsed && "lg:hidden")}>
        <span className="truncate font-mono text-foreground/90" title={me.user_id}>
          {label}
        </span>
        <span className="truncate text-muted-foreground/70">
          {tr("via")} {me.provider}
        </span>
      </div>
      <button
        type="button"
        onClick={handleLogout}
        className={cn(
          compact
            ? compactLogoutClassName
            : "inline-flex min-h-11 shrink-0 items-center gap-2 rounded px-3 py-2 text-muted-foreground/70 transition-colors hover:bg-current/10 hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-current/40",
        )}
        aria-label={tr("Log out")}
        title={tr("Log out")}
      >
        {compact ? <CompactLogoutContent collapsed={collapsed} /> : (
          <><LogOut className="h-3.5 w-3.5" /><span className={collapsed ? "lg:sr-only" : undefined}>Выйти</span></>
        )}
      </button>
      {error && <p role="alert" className={cn("text-xs text-destructive", compact && "absolute bottom-full right-0 mb-2 w-[180px] rounded-lg bg-[var(--neo-surface)] p-3 shadow-[var(--neo-depth-2)]")}>{error}</p>}
    </div>
  );
}

function CompactLogoutContent({ collapsed }: { collapsed: boolean }) {
  return (
    <span className={cn(
      "inline-flex h-[32px] min-w-[40px] items-center justify-center gap-[6px] rounded-full bg-[var(--neo-surface)] px-[6px] text-[13px] shadow-[var(--neo-depth-1)] pointer-coarse:h-[40px]",
      "transition-[box-shadow,color] group-hover/logout:text-[var(--neo-text-primary)] group-hover/logout:shadow-[var(--neo-inset-compact)] group-active/logout:shadow-[var(--neo-inset-compact)]",
      collapsed && "lg:px-0",
    )}>
      <LogOut className="size-[15px] shrink-0" aria-hidden />
      <span className={cn("sidebar-logout-label", collapsed && "lg:sr-only")}>Выйти</span>
    </span>
  );
}
