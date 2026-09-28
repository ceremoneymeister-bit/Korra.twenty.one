/**
 * Личный вид экрана агентов на телефоне: «Вкладки» или «Список агентов».
 *
 * «Вкладки» и «Список» — разные первые экраны. Чтобы телефон не показывал
 * мгновение чужой вид, выбор приезжает вместе со страницей
 * (`window.__KORRA_VIEW_PREF__`), а если его там нет — берётся из кэша этого
 * браузера. Правда — сервер: его ответ поправляет расхождение.
 *
 * Запись — как у раскладки дашборда: серверная ревизия, записи идут по
 * очереди, проигравшее устройство получает 409 с победителем, и экран
 * показывает победителя словами, а не откатывается молча.
 */

import { atom } from "nanostores";

import { api, ApiError, HERMES_BASE_PATH, type AgentsMobileMode, type DashboardViewPreference } from "@/lib/api";
import { readBootstrap as readThemeBootstrap } from "@/themes/preference";

export type { AgentsMobileMode } from "@/lib/api";

export type AgentsViewStatus = "idle" | "saving" | "saved" | "error" | "conflict";

export interface AgentsViewState {
  mode: AgentsMobileMode;
  /** Закреплённые в полосе, в профилях панели: `""` — главный агент. */
  pinned: string[];
  /** Последняя ревизия, подтверждённая сервером; null — ответа ещё не было. */
  revision: number | null;
  scope: string | null;
  status: AgentsViewStatus;
  message: string;
}

declare global {
  interface Window { __KORRA_VIEW_PREF__?: unknown }
}

const MODES: readonly AgentsMobileMode[] = ["tabs", "list"];
const PROFILE_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;

/** Главный агент на сервере зовётся `default`, в панели — пустым профилем. */
export function toWireProfile(profile: string): string {
  return profile || "default";
}

export function fromWireProfile(name: string): string {
  return name === "default" ? "" : name;
}

export function validViewPreference(value: unknown): DashboardViewPreference | null {
  if (!value || typeof value !== "object") return null;
  const pref = value as Partial<DashboardViewPreference>;
  if (pref.version !== 1) return null;
  if (typeof pref.revision !== "number" || !Number.isInteger(pref.revision) || pref.revision < 0) return null;
  if (!MODES.includes(pref.agents_mobile as AgentsMobileMode)) return null;
  if (!Array.isArray(pref.pinned) || !pref.pinned.every((name) => typeof name === "string" && PROFILE_RE.test(name))) return null;
  if (typeof pref.scope !== "string" || !/^[a-f0-9]{8,64}$/.test(pref.scope)) return null;
  return pref as DashboardViewPreference;
}

export function readViewBootstrap(): DashboardViewPreference | null {
  return typeof window === "undefined" ? null : validViewPreference(window.__KORRA_VIEW_PREF__);
}

/**
 * Ключ кэша: установка и адрес кабинета. Человека в ключе нет намеренно —
 * до ответа сервера он неизвестен; запись хранит его метку, и чужая метка
 * после ответа сервера просто перезаписывается.
 */
export function viewCacheKey(): string {
  const theme = readThemeBootstrap();
  return ["korra-agents-view-v1", theme?.installation_id ?? "", encodeURIComponent(HERMES_BASE_PATH)].join(":");
}

export function readCachedView(): DashboardViewPreference | null {
  try {
    return validViewPreference(JSON.parse(localStorage.getItem(viewCacheKey()) ?? "null"));
  } catch {
    return null;
  }
}

function cacheView(pref: DashboardViewPreference): void {
  try {
    localStorage.setItem(viewCacheKey(), JSON.stringify(pref));
  } catch {
    // Private browsing/quota: the server copy still decides.
  }
}

function fromPreference(pref: DashboardViewPreference, confirmed: boolean): AgentsViewState {
  return {
    mode: pref.agents_mobile,
    pinned: pref.pinned.map(fromWireProfile),
    revision: confirmed ? pref.revision : null,
    scope: pref.scope,
    status: "idle",
    message: "",
  };
}

export function initialAgentsView(): AgentsViewState {
  const boot = readViewBootstrap();
  // Страница пришла с выбором от сервера — это уже подтверждённая запись.
  if (boot) return fromPreference(boot, true);
  const cached = readCachedView();
  if (cached) return fromPreference(cached, false);
  return { mode: "tabs", pinned: [], revision: null, scope: null, status: "idle", message: "" };
}

export const $agentsView = atom<AgentsViewState>(initialAgentsView());

function accept(pref: DashboardViewPreference, status: AgentsViewStatus = "idle", message = ""): void {
  const current = $agentsView.get();
  if (current.revision !== null && pref.revision < current.revision) return;
  cacheView(pref);
  $agentsView.set({ ...fromPreference(pref, true), status, message });
}

let loading: Promise<void> | null = null;
let writes: Promise<void> = Promise.resolve();
let pendingWrites = 0;

/** Перечитать выбор с сервера. Пока идёт запись, чтение ждёт её исхода. */
export function loadAgentsView(): Promise<void> {
  if (loading) return loading;
  loading = (async () => {
    await writes;
    if (pendingWrites > 0) return;
    try {
      const pref = validViewPreference(await api.getDashboardView());
      if (pref && pendingWrites === 0) accept(pref, $agentsView.get().status === "saved" ? "saved" : "idle", "");
    } catch {
      // The bootstrap or cache stays in charge; a later visit retries.
    }
  })().finally(() => { loading = null; });
  return loading;
}

function conflictWinner(error: unknown): DashboardViewPreference | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const payload = error.payload as { preference?: unknown } | null | undefined;
  return validViewPreference(payload?.preference);
}

function persist(): void {
  pendingWrites += 1;
  writes = writes.then(async () => {
    try {
      let revision = $agentsView.get().revision;
      if (revision === null) {
        try {
          const pref = validViewPreference(await api.getDashboardView());
          if (!pref) throw new Error("invalid");
          const intent = $agentsView.get();
          revision = pref.revision;
          $agentsView.set({ ...intent, revision, scope: pref.scope });
        } catch {
          $agentsView.set({
            ...$agentsView.get(),
            status: "error",
            message: "Панель не отвечает: выбор действует на этом устройстве, но пока не сохранён.",
          });
          return;
        }
      }
      // Уходит последний выбор человека к этой минуте, а не тот, с которого
      // началась очередь: два быстрых нажатия — одна запись с итогом.
      const intent = $agentsView.get();
      try {
        const saved = await api.setDashboardView({
          revision,
          agents_mobile: intent.mode,
          pinned: intent.pinned.map(toWireProfile),
        });
        const pref = validViewPreference(saved);
        if (pref) accept(pref, "saved");
      } catch (error) {
        const winner = conflictWinner(error);
        if (winner) {
          const intentNow = $agentsView.get();
          const differs = winner.agents_mobile !== intentNow.mode
            || winner.pinned.join(",") !== intentNow.pinned.map(toWireProfile).join(",");
          $agentsView.set({ ...fromPreference(winner, true), status: differs ? "conflict" : "saved", message: "" });
          cacheView(winner);
          if (differs) {
            $agentsView.set({
              ...$agentsView.get(),
              message: "Вид изменили на другом устройстве — показан выбор оттуда. Выберите ещё раз, если нужен другой.",
            });
          }
          return;
        }
        $agentsView.set({
          ...$agentsView.get(),
          status: "error",
          message: "Не удалось сохранить: панель не отвечает. Выбор действует на этом устройстве.",
        });
      }
    } finally {
      pendingWrites -= 1;
    }
  });
}

/** Сменить способ переключения агентов. Экран меняется сразу, запись — следом. */
export function chooseAgentsMobileMode(mode: AgentsMobileMode): void {
  const current = $agentsView.get();
  if (current.mode === mode && current.status !== "error" && current.status !== "conflict") {
    $agentsView.set({ ...current, status: "saved", message: "" });
    return;
  }
  $agentsView.set({ ...current, mode, status: "saving", message: "" });
  persist();
}

/** Закрепить агента в полосе или открепить. */
export function togglePinnedAgent(profile: string): void {
  const current = $agentsView.get();
  const pinned = current.pinned.includes(profile)
    ? current.pinned.filter((item) => item !== profile)
    : [...current.pinned, profile];
  $agentsView.set({ ...current, pinned, status: "saving", message: "" });
  persist();
}

/** Только для тестов: вернуть хранилище в исходное состояние. */
export function resetAgentsViewForTests(): void {
  loading = null;
  writes = Promise.resolve();
  pendingWrites = 0;
  $agentsView.set(initialAgentsView());
}
