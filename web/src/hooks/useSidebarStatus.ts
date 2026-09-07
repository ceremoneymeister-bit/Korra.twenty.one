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
export function useSidebarStatus(): SidebarStatus {
  // One shared monitor lives with the shell, including non-chat routes.
  useStore($chatRuns);
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [reachable, setReachable] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      api
        .getPanelStatus()
        .then((next) => {
          if (cancelled) return;
          setStatus(next);
          setReachable(true);
        })
        .catch(() => {
          if (cancelled) return;
          setReachable(false);
        });
    };
    load();
    const id = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return { status, reachable };
}
