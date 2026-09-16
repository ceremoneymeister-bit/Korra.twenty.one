import { useCallback, useEffect, useState } from "react";
import { api, type SessionInfo } from "@/lib/api";
import { ownerFacingError } from "@/lib/owner-facing-error";

interface SearchState { key: string; sessions: SessionInfo[]; error: string | null }

/** Search the whole history of this agent, including older, unloaded chats. */
export function useSessionSearch(profile: string, query: string, revision = "") {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<SearchState | null>(null);
  const q = query.trim();
  const scope = profile || "default";
  const key = JSON.stringify([scope, q, attempt, revision]);
  const refresh = useCallback(() => setAttempt(value => value + 1), []);
  useEffect(() => {
    if (!q) return;
    let current = true;
    const timer = window.setTimeout(() => {
      void api.searchSessions(q, { profile: scope }).then(response => {
        if (!current) return;
        const seen = new Set<string>();
        const sessions = response.results.flatMap(item => {
          const id = item.session_id || item.id;
          if (!id || seen.has(id)) return [];
          seen.add(id);
          return [{ ...item, id }];
        });
        setResult({ key, sessions, error: null });
      }).catch(cause => {
        if (current) setResult({ key, sessions: [], error: ownerFacingError(cause, "Не удалось выполнить поиск. Попробуйте ещё раз.") });
      });
    }, 250);
    return () => { current = false; window.clearTimeout(timer); };
  }, [key, q, scope]);
  const settled = Boolean(q && result?.key === key);
  return { sessions: settled ? result!.sessions : [], loading: Boolean(q && !settled), error: settled ? result!.error : null, refresh };
}
