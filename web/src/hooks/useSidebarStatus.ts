import { useStore } from "@nanostores/react";
import { $chatRuns } from "@/lib/chat-runs";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { StatusResponse } from "@/lib/api";

const POLL_MS = 10_000;

export interface SidebarStatus {
  /** Последний удачно прочитанный статус; остаётся прежним при обрыве. */
  status: StatusResponse | null;
  /** Дошёл ли до панели последний опрос. `null` — ещё ни одного ответа. */
  reachable: boolean | null;
}

/**
 * Light-weight status poll for the app shell (sidebar). The Status page uses
 * its own faster interval; we keep this slower to avoid duplicate load.
 *
 * Провал опроса отдаётся наружу отдельным полем, а не молча проглатывается:
 * подвал показывает единственный постоянный индикатор состояния, и при обрыве
 * он обязан погаснуть, а не светить последним удачным ответом.
 */
export function useSidebarStatus(profile: string | null = null): SidebarStatus {
  // One shared monitor lives with the shell, including non-chat routes.
  useStore($chatRuns);
  const [result, setResult] = useState<{
    profile: string | null;
    status: StatusResponse | null;
    reachable: boolean | null;
  }>({ profile, status: null, reachable: null });

  useEffect(() => {
    let cancelled = false;
    let request = 0;
    const load = () => {
      const current = ++request;
      (profile === null ? api.getPanelStatus() : api.getProfileStatus(profile))
        .then((next) => {
          if (cancelled || current !== request) return;
          setResult({ profile, status: next, reachable: true });
        })
        .catch(() => {
          if (cancelled || current !== request) return;
          setResult((previous) => ({
            profile,
            status: previous.profile === profile ? previous.status : null,
            reachable: false,
          }));
        });
    };
    load();
    const id = setInterval(load, POLL_MS);
    const resume = () => {
      if (typeof document === "undefined" || !document.hidden) load();
    };
    window.addEventListener("focus", resume);
    window.addEventListener("online", resume);
    document.addEventListener("visibilitychange", resume);
    return () => {
      cancelled = true;
      request += 1;
      clearInterval(id);
      window.removeEventListener("focus", resume);
      window.removeEventListener("online", resume);
      document.removeEventListener("visibilitychange", resume);
    };
  }, [profile]);

  // A selected agent owns a different response.  Synchronously project an
  // empty state during the request instead of briefly showing the prior tab.
  return result.profile === profile
    ? { status: result.status, reachable: result.reachable }
    : { status: null, reachable: null };
}
