import { useCallback, useEffect, useRef, useState } from "react";
import { api, type SessionInfo } from "../lib/api";
import { ownerFacingError } from "../lib/owner-facing-error";

const DEFAULT_LIMIT = 50;
// Сервер отдаёт не больше 100 строк за запрос; больше этого списка в боковой
// панели не держим — остальное находит поиск.
const PAGE_SIZE = 100;
const MAX_SHOWN = 300;

export interface UseSessionListReturn {
  sessions: SessionInfo[];
  /** Сколько разговоров всего на сервере (список отдаёт первую страницу). */
  total: number | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  /** Есть ли ещё разговоры, которые можно догрузить кнопкой «Показать ещё». */
  hasMore: boolean;
  loadMore: () => void;
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
  const step = options?.limit ?? DEFAULT_LIMIT;
  const [limit, setLimit] = useState(step);
  const pollIntervalMs = options?.pollIntervalMs ?? 0;
  const profile = options?.profile;
  const profileRef = useRef(profile);
  const previousPollIntervalRef = useRef(pollIntervalMs);
  const mountedRef = useRef(false);
  const requestRef = useRef(0);
  const [result, setResult] = useState<{ profile: string | undefined; sessions: SessionInfo[]; total: number | null }>(
    { profile, sessions: [], total: null },
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
      const pages = await Promise.all(
        Array.from({ length: Math.ceil(limit / PAGE_SIZE) }, (_, index) =>
          api.getSessions(
            Math.min(PAGE_SIZE, limit - index * PAGE_SIZE),
            index * PAGE_SIZE,
            profile === undefined ? undefined : profile || "default",
            "recent",
          ),
        ),
      );
      if (!current()) return;

      const seen = new Set<string>();
      const rows = pages.flatMap((page) => page.sessions).filter((row) => {
        if (seen.has(row.id)) return false;
        seen.add(row.id);
        return true;
      });
      const reported = pages[0]?.total;
      setResult({
        profile,
        sessions: sortByLastActiveDesc(rows),
        total: typeof reported === "number" ? reported : rows.length,
      });
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

  const loadMore = useCallback(() => {
    setLimit((value) => Math.min(MAX_SHOWN, value + step));
  }, [step]);
  const visibleSessions = result.profile === profile ? result.sessions : [];
  const visibleTotal = result.profile === profile ? result.total : null;

  return {
    hasMore: visibleTotal !== null && visibleSessions.length < visibleTotal && limit < MAX_SHOWN,
    loadMore,
    // A different profile's rows must never appear even during its first
    // render before the new request's effect has run.
    sessions: result.profile === profile ? result.sessions : [],
    total: result.profile === profile ? result.total : null,
    loading,
    error,
    refresh,
  };
}
