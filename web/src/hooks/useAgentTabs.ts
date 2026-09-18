import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { AgentTabsPreference } from "@/lib/api";
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
const MIGRATION_STORAGE_KEY = "korra.agentTabs.server-layout.v1";

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

function markLegacyMigrated(): void {
  try {
    window.localStorage.setItem(MIGRATION_STORAGE_KEY, "1");
    window.localStorage.removeItem(ORDER_STORAGE_KEY);
    window.localStorage.removeItem(HIDDEN_STORAGE_KEY);
  } catch {
    // The durable server copy is authoritative even when local storage is
    // unavailable; the marker only prevents importing an old browser cache.
  }
}

function legacyAlreadyMigrated(): boolean {
  try {
    return window.localStorage.getItem(MIGRATION_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function sameProfiles(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((profile, index) => profile === b[index]);
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
  reorderTab: (profile: string, beforeProfile: string) => void;
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
  const allTabsRef = useRef(allTabs);
  const preferenceRef = useRef<AgentTabsPreference | null>(null);
  const layoutWriteRef = useRef<Promise<void>>(Promise.resolve());
  const layoutEpochRef = useRef(0);
  const layoutMutationRef = useRef(0);
  const mountedRef = useRef(true);
  const inFlightRef = useRef<Promise<void> | null>(null);

  const setCurrentOrder = useCallback((next: string[]) => {
    if (sameProfiles(orderRef.current, next)) return;
    orderRef.current = next;
    setOrder(next);
  }, []);

  const setCurrentHidden = useCallback((next: string[]) => {
    if (sameProfiles(hiddenRef.current, next)) return;
    hiddenRef.current = next;
    setHidden(next);
  }, []);

  const applyPreference = useCallback((preference: AgentTabsPreference, sourceTabs?: AgentTabConfig[]) => {
    const liveTabs = sourceTabs ?? allTabsRef.current;
    preferenceRef.current = preference;
    setCurrentOrder(reconcileOrder(liveTabs, preference.order));
    setCurrentHidden(reconcileHidden(liveTabs, preference.hidden));
  }, [setCurrentHidden, setCurrentOrder]);

  const persistLayout = useCallback((nextOrder: string[], nextHidden: string[]) => {
    if (!preferenceRef.current) return;
    setCurrentOrder(nextOrder);
    setCurrentHidden(nextHidden);
    const epoch = layoutEpochRef.current;
    const mutation = ++layoutMutationRef.current;
    const write = layoutWriteRef.current.then(async () => {
      if (epoch !== layoutEpochRef.current) return;
      const preference = preferenceRef.current;
      if (!preference) return;
      try {
        const saved = await api.setAgentTabs({
          revision: preference.revision,
          order: nextOrder,
          hidden: nextHidden,
        });
        preferenceRef.current = saved;
        // Earlier queued writes must not visually roll back a newer local
        // pointer move while that newer mutation is waiting its turn.
        if (mutation === layoutMutationRef.current) applyPreference(saved);
      } catch {
        // A stale browser never overwrites a newer layout. Cancel all writes
        // derived from the stale revision and accept the server winner.
        layoutEpochRef.current += 1;
        layoutMutationRef.current += 1;
        try {
          applyPreference(await api.getAgentTabs());
        } catch {
          // Keep the optimistic arrangement until focus/poll can reconcile it.
        }
      }
    });
    layoutWriteRef.current = write.catch(() => {});
  }, [applyPreference, setCurrentHidden, setCurrentOrder]);

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
        const [profilesResult, preferenceResult] = await Promise.allSettled([
          api.getProfiles(),
          api.getAgentTabs(),
        ]);
        if (!mountedRef.current) return;
        if (profilesResult.status === "rejected") throw profilesResult.reason;
        const next = buildAgentTabs(profilesResult.value?.profiles);
        allTabsRef.current = next;
        // Layout persistence is optional for rendering the actual agents.  A
        // transient preference-endpoint failure must not collapse every
        // profile to the main tab (or break Files → agent handoff).
        if (preferenceResult.status === "rejected") {
          setCurrentOrder(reconcileOrder(next, orderRef.current));
          setCurrentHidden(reconcileHidden(next, hiddenRef.current));
          setAllTabs((previous) =>
            sameAgentTabs(previous, next) ? previous : next,
          );
          return;
        }
        let preference = preferenceResult.value;
        if (!preference.initialized) {
          const importLegacy = !legacyAlreadyMigrated();
          const legacyOrder = importLegacy ? readStoredProfiles(ORDER_STORAGE_KEY) : [];
          const legacyHidden = importLegacy ? readStoredProfiles(HIDDEN_STORAGE_KEY) : [];
          try {
            preference = await api.setAgentTabs({
              revision: preference.revision,
              order: reconcileOrder(next, legacyOrder),
              hidden: reconcileHidden(next, legacyHidden),
            });
            markLegacyMigrated();
          } catch {
            // Another browser may have initialized it after our GET.
            preference = await api.getAgentTabs();
          }
        } else if (!legacyAlreadyMigrated()) {
          // A different device already established the server state: discard
          // this browser's obsolete cache instead of importing over it.
          markLegacyMigrated();
        }
        if (!mountedRef.current) return;
        applyPreference(preference, next);
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
  }, [applyPreference]);

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
      let target = profile;
      if (!target) {
        // Главная вкладка — профиль самой панели, он не всегда «default».
        try {
          target = (await api.getActiveProfile()).current || "default";
        } catch {
          target = "default";
        }
      }
      await api.updateProfileDisplayName(target, cleaned);
      if (!mountedRef.current) return;
      setAllTabs((previous) => {
        const next = previous.map((tab) =>
          tab.profile === profile
            ? {
                ...tab,
                // Главный профиль не превращаем в технический `default`:
                // его продуктовая подпись по-прежнему «Корра».
                label: profile ? cleaned || profile : MAIN_AGENT_TAB.label,
              }
            : tab,
        );
        allTabsRef.current = next;
        return next;
      });
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
      persistLayout(orderRef.current, [...hiddenRef.current, profile]);
    },
    [allTabs, persistLayout],
  );

  const showTab = useCallback(
    (profile: string) => {
      if (!hiddenRef.current.includes(profile)) return;
      persistLayout(
        orderRef.current,
        hiddenRef.current.filter((item) => item !== profile),
      );
    },
    [persistLayout],
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
      persistLayout(next, hiddenRef.current);
    },
    [allTabs, persistLayout],
  );

  const reorderTab = useCallback((profile: string, beforeProfile: string) => {
    if (profile === beforeProfile) return;
    const currentOrder = reconcileOrder(allTabs, orderRef.current);
    const visibleOrder = currentOrder.filter((item) => !hiddenRef.current.includes(item));
    const from = visibleOrder.indexOf(profile);
    const to = visibleOrder.indexOf(beforeProfile);
    if (from < 0 || to < 0) return;
    visibleOrder.splice(from, 1);
    visibleOrder.splice(to, 0, profile);
    const visible = new Set(visibleOrder);
    let index = 0;
    const next = currentOrder.map((item) => visible.has(item) ? visibleOrder[index++] : item);
    persistLayout(next, hiddenRef.current);
  }, [allTabs, persistLayout]);

  return {
    tabs,
    hiddenTabs,
    refresh,
    updateDisplayName,
    hideTab,
    showTab,
    moveTab,
    reorderTab,
  };
}
