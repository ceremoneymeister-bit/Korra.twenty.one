import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "@/lib/api";
import {
  buildAgentTabs,
  MAIN_AGENT_TAB,
  sameAgentTabs,
  type AgentTabConfig,
} from "@/lib/agent-tabs";

/** Профили создаются и из CLI, и из другой вкладки браузера — опрос
 *  подхватывает их без перезагрузки страницы. */
const POLL_INTERVAL_MS = 30_000;

const ORDER_STORAGE_KEY = "korra.agentTabs.order";
const HIDDEN_STORAGE_KEY = "korra.agentTabs.hidden";

type MoveDirection = "left" | "right";

function readStoredProfiles(key: string): string[] {
  if (typeof window === "undefined") return [];
  try {
    const value: unknown = JSON.parse(window.localStorage.getItem(key) ?? "[]");
    if (!Array.isArray(value)) return [];
    return value.filter(
      (profile, index): profile is string =>
        typeof profile === "string" && value.indexOf(profile) === index,
    );
  } catch {
    return [];
  }
}

function storeProfiles(key: string, profiles: readonly string[]): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(profiles));
  } catch {
    // В приватном режиме хранилище может быть недоступно. Вкладки всё равно
    // работают до перезагрузки, поэтому ошибка localStorage не должна ронять UI.
  }
}

function sameProfiles(a: readonly string[], b: readonly string[]): boolean {
  return (
    a.length === b.length &&
    a.every((profile, index) => profile === b[index])
  );
}

/** Оставляет живые профили на сохранённых местах, новые добавляет в конец. */
function reconcileOrder(
  tabs: readonly AgentTabConfig[],
  storedOrder: readonly string[],
): string[] {
  const existing = new Set(tabs.map((tab) => tab.profile));
  const next = storedOrder.filter((profile) => existing.has(profile));
  for (const tab of tabs) {
    if (!next.includes(tab.profile)) next.push(tab.profile);
  }
  return next;
}

function reconcileHidden(
  tabs: readonly AgentTabConfig[],
  storedHidden: readonly string[],
): string[] {
  const existing = new Set(tabs.map((tab) => tab.profile));
  return storedHidden.filter(
    (profile) => profile !== MAIN_AGENT_TAB.profile && existing.has(profile),
  );
}

function putTabsInOrder(
  tabs: readonly AgentTabConfig[],
  order: readonly string[],
): AgentTabConfig[] {
  const byProfile = new Map(tabs.map((tab) => [tab.profile, tab]));
  const arranged = order.flatMap((profile) => {
    const tab = byProfile.get(profile);
    return tab ? [tab] : [];
  });
  for (const tab of tabs) {
    if (!order.includes(tab.profile)) arranged.push(tab);
  }
  return arranged;
}

export interface UseAgentTabsReturn {
  /** Видимые вкладки в пользовательском порядке. */
  tabs: AgentTabConfig[];
  /** Скрытые вкладки в том же общем порядке — для меню кнопки «+». */
  hiddenTabs: AgentTabConfig[];
  /** Перечитать профили сейчас — например, при возврате на экран агентов
   *  после создания профиля. */
  refresh: () => Promise<void>;
  /** Сохранить человекочитаемое имя и сразу обновить подпись вкладки. */
  updateDisplayName: (profile: string, displayName: string) => Promise<void>;
  hideTab: (profile: string) => void;
  showTab: (profile: string) => void;
  moveTab: (profile: string, direction: MoveDirection) => void;
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
  const [allTabs, setAllTabs] = useState<AgentTabConfig[]>(() =>
    buildAgentTabs([]),
  );
  const [order, setOrder] = useState<string[]>(() =>
    readStoredProfiles(ORDER_STORAGE_KEY),
  );
  const [hidden, setHidden] = useState<string[]>(() =>
    readStoredProfiles(HIDDEN_STORAGE_KEY),
  );
  const orderRef = useRef(order);
  const hiddenRef = useRef(hidden);
  const mountedRef = useRef(true);
  const inFlightRef = useRef<Promise<void> | null>(null);

  const saveOrder = useCallback((next: string[]) => {
    orderRef.current = next;
    setOrder(next);
    storeProfiles(ORDER_STORAGE_KEY, next);
  }, []);

  const saveHidden = useCallback((next: string[]) => {
    hiddenRef.current = next;
    setHidden(next);
    storeProfiles(HIDDEN_STORAGE_KEY, next);
  }, []);

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
        const nextOrder = reconcileOrder(next, orderRef.current);
        const nextHidden = reconcileHidden(next, hiddenRef.current);
        if (!sameProfiles(nextOrder, orderRef.current)) saveOrder(nextOrder);
        if (!sameProfiles(nextHidden, hiddenRef.current)) saveHidden(nextHidden);
        setAllTabs((previous) =>
          sameAgentTabs(previous, next) ? previous : next,
        );
      } catch {
        // Список не доехал — оставляем прошлый состав, главная вкладка есть
        // всегда. Следующий опрос попробует снова.
      } finally {
        inFlightRef.current = null;
      }
    })();
    inFlightRef.current = run;
    return run;
  }, [saveHidden, saveOrder]);

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

  const updateDisplayName = useCallback(
    async (profile: string, displayName: string): Promise<void> => {
      const cleaned = displayName.trim();
      await api.updateProfileDisplayName(profile || "default", cleaned);
      if (!mountedRef.current) return;
      setAllTabs((previous) =>
        previous.map((tab) =>
          tab.profile === profile
            ? {
                ...tab,
                // Главный профиль не превращаем в технический `default`:
                // его продуктовая подпись по-прежнему «Корра».
                label: profile ? cleaned || profile : MAIN_AGENT_TAB.label,
              }
            : tab,
        ),
      );
    },
    [],
  );

  const orderedTabs = useMemo(
    () => putTabsInOrder(allTabs, order),
    [allTabs, order],
  );
  const hiddenSet = useMemo(() => new Set(hidden), [hidden]);
  const tabs = useMemo(
    () => orderedTabs.filter((tab) => !hiddenSet.has(tab.profile)),
    [hiddenSet, orderedTabs],
  );
  const hiddenTabs = useMemo(
    () => orderedTabs.filter((tab) => hiddenSet.has(tab.profile)),
    [hiddenSet, orderedTabs],
  );

  const hideTab = useCallback(
    (profile: string) => {
      if (
        profile === MAIN_AGENT_TAB.profile ||
        !allTabs.some((tab) => tab.profile === profile) ||
        hiddenRef.current.includes(profile)
      ) {
        return;
      }
      saveHidden([...hiddenRef.current, profile]);
    },
    [allTabs, saveHidden],
  );

  const showTab = useCallback(
    (profile: string) => {
      if (!hiddenRef.current.includes(profile)) return;
      saveHidden(hiddenRef.current.filter((item) => item !== profile));
    },
    [saveHidden],
  );

  const moveTab = useCallback(
    (profile: string, direction: MoveDirection) => {
      const currentOrder = reconcileOrder(allTabs, orderRef.current);
      const visibleOrder = currentOrder.filter(
        (item) => !hiddenRef.current.includes(item),
      );
      const currentIndex = visibleOrder.indexOf(profile);
      const targetIndex = currentIndex + (direction === "left" ? -1 : 1);
      if (
        currentIndex < 0 ||
        targetIndex < 0 ||
        targetIndex >= visibleOrder.length
      ) {
        return;
      }

      // Меняем местами именно соседние ВИДИМЫЕ вкладки. Скрытая вкладка
      // между ними не должна поглощать клик, не меняя видимого порядка.
      const targetProfile = visibleOrder[targetIndex];
      const sourcePosition = currentOrder.indexOf(profile);
      const targetPosition = currentOrder.indexOf(targetProfile);
      const next = [...currentOrder];
      next[sourcePosition] = targetProfile;
      next[targetPosition] = profile;
      saveOrder(next);
    },
    [allTabs, saveOrder],
  );

  return {
    tabs,
    hiddenTabs,
    refresh,
    updateDisplayName,
    hideTab,
    showTab,
    moveTab,
  };
}
