import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { sessionMessagesToChat, type HistoryMessage } from "@/hooks/useChatStream";

/** Сообщений базы за один запрос архива (сервер отдаёт не больше 100). */
export const CHAT_ARCHIVE_PAGE = 50;

/**
 * Часть разговора до живой ленты — только чтение (K21-203). Строки хранятся
 * как пришли и собираются в ленту целиком: ход агента, разрезанный границей
 * страницы, склеивается, когда подгружена его начальная часть. Живую ленту
 * хук не трогает. Хук живёт, пока архив открыт: закрытие сбрасывает чтение.
 */
export function useChatArchive(sessionId: string | null, profile: string) {
  const [rows, setRows] = useState<HistoryMessage[]>([]);
  const [hasMore, setHasMore] = useState(true);
  const [status, setStatus] = useState<"idle" | "loading" | "failed">("idle");
  const request = useRef<AbortController | null>(null);
  const loaded = useRef(0);

  const load = useCallback(async () => {
    if (!sessionId || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setStatus("loading");
    try {
      const resp = await api.getSessionArchive(sessionId, profile || "default", controller.signal, {
        limit: CHAT_ARCHIVE_PAGE, offset: loaded.current,
      });
      if (request.current !== controller) return;
      const page = resp.messages as HistoryMessage[];
      loaded.current += page.length;
      setRows(current => [...page, ...current]);
      setHasMore(resp.pagination?.has_more === true && page.length > 0);
      setStatus("idle");
    } catch {
      if (request.current === controller) setStatus("failed");
    } finally {
      if (request.current === controller) request.current = null;
    }
  }, [profile, sessionId]);

  useEffect(() => {
    void load();
    return () => { request.current?.abort(); request.current = null; };
  }, [load]);

  const messages = useMemo(
    () => (sessionId ? sessionMessagesToChat(sessionId, rows) : []),
    [rows, sessionId],
  );
  return {
    messages,
    older: { hasOlder: hasMore && rows.length > 0, loading: status === "loading", failed: status === "failed" },
    reachedStart: !hasMore && status === "idle",
    loading: status === "loading" && rows.length === 0,
    failed: status === "failed" && rows.length === 0,
    load,
  };
}
