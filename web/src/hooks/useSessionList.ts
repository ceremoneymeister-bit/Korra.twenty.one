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
  const mountedRef = useRef(false);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback(async (): Promise<void> => {
    if (!mountedRef.current) return;

    setLoading(true);
    setError(null);

    try {
      // Пустой/незаданный profile передаём как undefined, чтобы сработало
      // значение по умолчанию у api.getSessions (глобальный management scope).
      const response = await api.getSessions(
        limit,
        DEFAULT_OFFSET,
        profile || undefined,
      );
      if (!mountedRef.current) return;

      setSessions(sortByLastActiveDesc(response.sessions));
    } catch (err) {
      if (!mountedRef.current) return;

      setError(errorToMessage(err));
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
    }
  }, [limit, profile]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (pollIntervalMs <= 0) return;

    const intervalId = window.setInterval(() => {
      void refresh();
    }, pollIntervalMs);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [pollIntervalMs, refresh]);

  return {
    sessions,
    loading,
    error,
    refresh,
  };
}
