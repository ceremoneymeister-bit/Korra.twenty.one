import { useCallback, useEffect, useRef, useState } from "react";
import { api, type SessionInfo } from "../lib/api";
import { ownerFacingError } from "../lib/owner-facing-error";

const DEFAULT_LIMIT = 50;
const DEFAULT_OFFSET = 0;

export interface UseSessionListReturn {
  sessions: SessionInfo[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

interface UseSessionListOptions {
  limit?: number;
  pollIntervalMs?: number;
  /** Профиль агента, чьи сессии показываем. Пусто — профиль самого процесса
   *  панели (прежнее поведение: api.getSessions подставит глобальный scope). */
  profile?: string;
}

function sortByLastActiveDesc(sessions: SessionInfo[]): SessionInfo[] {
  return [...sessions].sort((a, b) => b.last_active - a.last_active);
}

function errorToMessage(error: unknown): string {
  return ownerFacingError(error, "Не удалось загрузить список диалогов.");
}

export function useSessionList(
  options?: UseSessionListOptions
): UseSessionListReturn {
  const limit = options?.limit ?? DEFAULT_LIMIT;
  const pollIntervalMs = options?.pollIntervalMs ?? 0;
  const profile = options?.profile;
  const profileRef = useRef(profile);
  const previousPollIntervalRef = useRef(pollIntervalMs);
  const mountedRef = useRef(false);
  const requestRef = useRef(0);
  const [result, setResult] = useState<{ profile: string | undefined; sessions: SessionInfo[] }>(
    { profile, sessions: [] },
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    profileRef.current = profile;
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestRef.current += 1;
    };
  }, [profile]);

  const refresh = useCallback(async (): Promise<void> => {
    if (!mountedRef.current || profileRef.current !== profile) return;
    const request = ++requestRef.current;
    const current = () => mountedRef.current && requestRef.current === request;

    setLoading(true);
    setError(null);

    try {
      // Пустой/незаданный profile передаём как undefined, чтобы сработало
      // значение по умолчанию у api.getSessions (глобальный management scope).
      const response = await api.getSessions(
        limit,
        DEFAULT_OFFSET,
        profile === undefined ? undefined : profile || "default",
      );
      if (!current()) return;

      setResult({ profile, sessions: sortByLastActiveDesc(response.sessions) });
    } catch (err) {
      if (!current()) return;

      setError(errorToMessage(err));
    } finally {
      if (current()) {
        setLoading(false);
      }
    }
  }, [limit, profile]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const previous = previousPollIntervalRef.current;
    previousPollIntervalRef.current = pollIntervalMs;
    if (pollIntervalMs <= 0) return;
    // The ordinary load effect handles the initial render.  This extra read
    // is specifically the hidden (0) -> active (>0) transition.
    if (previous <= 0) void refresh();

    const intervalId = window.setInterval(() => {
      // Скрытая вкладка браузера не опрашивает сервер.
      if (typeof document !== "undefined" && document.hidden) return;
      void refresh();
    }, pollIntervalMs);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [pollIntervalMs, refresh]);

  return {
    // A different profile's rows must never appear even during its first
    // render before the new request's effect has run.
    sessions: result.profile === profile ? result.sessions : [],
    loading,
    error,
    refresh,
  };
}
