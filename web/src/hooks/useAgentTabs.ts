import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import {
  buildAgentTabs,
  sameAgentTabs,
  type AgentTabConfig,
} from "@/lib/agent-tabs";

/** Профили создаются и из CLI, и из другой вкладки браузера — опрос
 *  подхватывает их без перезагрузки страницы. */
const POLL_INTERVAL_MS = 30_000;

export interface UseAgentTabsReturn {
  tabs: AgentTabConfig[];
  /** Перечитать профили сейчас — например, при возврате на экран агентов
   *  после создания профиля. */
  refresh: () => Promise<void>;
}

/**
 * Живой состав вкладок агентов из `/api/profiles`.
 *
 * До первого ответа и при любой ошибке сети — только главная вкладка
 * «Корра», поэтому экран никогда не пуст и не падает. Состояние меняется
 * лишь когда состав действительно другой: чаты вкладок смонтированы
 * постоянно, и лишний новый массив перерисовал бы их без нужды.
 */
export function useAgentTabs(): UseAgentTabsReturn {
  const [tabs, setTabs] = useState<AgentTabConfig[]>(() => buildAgentTabs([]));
  const mountedRef = useRef(true);
  const inFlightRef = useRef<Promise<void> | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback((): Promise<void> => {
    // Один запрос за раз: фокус окна, таймер и смена маршрута часто
    // срабатывают вместе.
    if (inFlightRef.current) return inFlightRef.current;
    const run = (async () => {
      try {
        const response = await api.getProfiles();
        if (!mountedRef.current) return;
        const next = buildAgentTabs(response?.profiles);
        setTabs((previous) => (sameAgentTabs(previous, next) ? previous : next));
      } catch {
        // Список не доехал — оставляем прошлый состав, главная вкладка есть
        // всегда. Следующий опрос попробует снова.
      } finally {
        inFlightRef.current = null;
      }
    })();
    inFlightRef.current = run;
    return run;
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    const intervalId = window.setInterval(() => {
      if (document.visibilityState !== "hidden") void refresh();
    }, POLL_INTERVAL_MS);
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(intervalId);
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refresh]);

  return { tabs, refresh };
}
