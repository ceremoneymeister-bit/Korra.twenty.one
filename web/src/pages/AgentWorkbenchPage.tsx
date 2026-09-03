/**
 * AgentWorkbenchPage — рабочее место команды агентов.
 *
 * Один экран, несколько агентов. Каждая вкладка — самостоятельный чат,
 * адресованный своему профилю Korra. Главное свойство экрана: переключение
 * вкладки НЕ прерывает соседний разговор: пользователь может вернуться позже,
 * когда ответ уже дописан до конца.
 *
 * Отсюда единственное архитектурное требование: все чаты смонтированы
 * одновременно и постоянно, неактивные спрятаны через `display: none`.
 * Размонтирование убило бы вместе с DOM и SSE-поток: `useChatStream` в
 * unmount-эффекте зовёт `abortController.abort()`, то есть вкладка,
 * переключённая на середине ответа, потеряла бы ответ целиком.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { createPortal } from "react-dom";
import { useLocation, useNavigate, useSearchParams } from "react-router";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  EyeOff,
  MessageSquarePlus,
  MoreVertical,
  Pencil,
  Plus,
  Settings,
  UserRoundPlus,
  X,
} from "lucide-react";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { useToast } from "@nous-research/ui/hooks/use-toast";

import BubbleChatPage from "@/pages/BubbleChatPage";
import { ownerFacingError } from "@/lib/owner-facing-error";
import { cn } from "@/lib/utils";
import { MAIN_AGENT_TAB } from "@/lib/agent-tabs";
import { useAgentTabs } from "@/hooks/useAgentTabs";

/* ------------------------------------------------------------------ */
/*  AgentWorkbenchPage (default export)                                */
/* ------------------------------------------------------------------ */

export default function AgentWorkbenchPage() {
  // Состав вкладок — реальные профили контура (см. lib/agent-tabs.ts):
  // главная «Корра» есть всегда, остальные приезжают из /api/profiles и
  // подхватываются без перезагрузки страницы.
  const {
    tabs,
    hiddenTabs,
    refresh,
    updateDisplayName,
    hideTab,
    showTab,
    moveTab,
  } = useAgentTabs();
  const [selectedId, setActiveId] = useState<string>(MAIN_AGENT_TAB.profile);
  const [newChatByProfile, setNewChatByProfile] = useState<
    Record<string, number>
  >({});
  const [openMenu, setOpenMenu] = useState<
    | { kind: "tab"; profile: string; top: number; right: number }
    | { kind: "add"; top: number; right: number }
    | null
  >(null);
  const [renamingProfile, setRenamingProfile] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [savingName, setSavingName] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const navigate = useNavigate();
  const { toast, showToast } = useToast();
  // Профиль удалили, пока его вкладка была выбрана, — показываем главную,
  // иначе экран остался бы без единой панели. Производное значение, а не
  // эффект: лишний каскад рендеров тут ни к чему.
  const activeId = tabs.some((tab) => tab.profile === selectedId)
    ? selectedId
    : MAIN_AGENT_TAB.profile;
  const [streamingByProfile, setStreamingByProfile] = useState<
    Record<string, boolean>
  >({});
  // Адресный черновик предназначен ОДНОМУ агенту. Держим его здесь и раздаём
  // адресно: сам чат `?draft=` не читает — несколько
  // смонтированных экземпляра приняли бы его каждый на свой счёт.
  const [draftByProfile, setDraftByProfile] = useState<Record<string, string>>(
    {},
  );
  const [searchParams, setSearchParams] = useSearchParams();
  const { pathname } = useLocation();

  useEffect(() => {
    if (!openMenu) return;
    const closeFromOutside = (event: PointerEvent) => {
      const target = event.target as Element;
      if (
        menuRef.current?.contains(target) ||
        target.closest("[data-agent-tab-menu-trigger]")
      ) {
        return;
      }
      setOpenMenu(null);
      setRenamingProfile(null);
    };
    const closeFromKeyboard = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpenMenu(null);
      setRenamingProfile(null);
    };
    const closeFromViewport = () => {
      setOpenMenu(null);
      setRenamingProfile(null);
    };
    document.addEventListener("pointerdown", closeFromOutside);
    document.addEventListener("keydown", closeFromKeyboard);
    window.addEventListener("resize", closeFromViewport);
    window.addEventListener("scroll", closeFromViewport, true);
    return () => {
      document.removeEventListener("pointerdown", closeFromOutside);
      document.removeEventListener("keydown", closeFromKeyboard);
      window.removeEventListener("resize", closeFromViewport);
      window.removeEventListener("scroll", closeFromViewport, true);
    };
  }, [openMenu]);

  // Экран смонтирован постоянно (см. App.tsx), поэтому возврат на него — это
  // не монтирование, а смена маршрута. Владелец создал профиль на соседнем
  // экране и вернулся сюда — вкладка должна быть уже здесь, а не через
  // полминуты опроса.
  useEffect(() => {
    if ((pathname.replace(/\/$/, "") || "/") === "/agents") void refresh();
  }, [pathname, refresh]);

  useEffect(() => {
    const agent = searchParams.get("agent")?.trim();
    const draft = searchParams.get("draft")?.trim();
    if (!agent) return;
    if (!tabs.some((tab) => tab.profile === agent)) {
      // Диплинк на профиль, которого в составе ещё нет (только что создан):
      // перечитываем список и оставляем параметры до его появления.
      void refresh();
      return;
    }
    setActiveId(agent);
    if (draft) setDraftByProfile((previous) => ({ ...previous, [agent]: draft }));
    // Параметры снимаем сразу: иначе возврат на экран назад-вперёд подставил
    // бы тот же текст поверх уже набранного.
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        next.delete("agent");
        next.delete("draft");
        return next;
      },
      { replace: true },
    );
  }, [searchParams, setSearchParams, tabs, refresh]);

  const clearDraft = useCallback((profile: string) => {
    setDraftByProfile((previous) => {
      if (previous[profile] === undefined) return previous;
      const next = { ...previous };
      delete next[profile];
      return next;
    });
  }, []);

  // Стабильная ссылка + выход без записи, когда значение не изменилось. Колбэк
  // зовётся из эффекта дочернего чата, и новый объект состояния на каждый
  // вызов означал бы бесконечный цикл рендеров.
  const handleStreamingChange = useCallback(
    (profile: string, streaming: boolean) => {
      setStreamingByProfile((previous) =>
        previous[profile] === streaming
          ? previous
          : { ...previous, [profile]: streaming },
      );
    },
    [],
  );

  const toggleTabMenu = useCallback(
    (event: MouseEvent<HTMLButtonElement>, profile: string) => {
      event.stopPropagation();
      if (openMenu?.kind === "tab" && openMenu.profile === profile) {
        setOpenMenu(null);
        setRenamingProfile(null);
        return;
      }
      const rect = event.currentTarget.getBoundingClientRect();
      setOpenMenu({
        kind: "tab",
        profile,
        top: rect.bottom + 8,
        right: Math.max(12, window.innerWidth - rect.right),
      });
      setRenamingProfile(null);
    },
    [openMenu],
  );

  const toggleAddMenu = useCallback(
    (event: MouseEvent<HTMLButtonElement>) => {
      event.stopPropagation();
      if (openMenu?.kind === "add") {
        setOpenMenu(null);
        return;
      }
      const rect = event.currentTarget.getBoundingClientRect();
      setOpenMenu({
        kind: "add",
        top: rect.bottom + 8,
        right: Math.max(12, window.innerWidth - rect.right),
      });
      setRenamingProfile(null);
    },
    [openMenu],
  );

  const startNewChat = useCallback((profile: string) => {
    setActiveId(profile);
    setNewChatByProfile((previous) => ({
      ...previous,
      [profile]: (previous[profile] ?? 0) + 1,
    }));
    setOpenMenu(null);
    setRenamingProfile(null);
  }, []);

  const submitDisplayName = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const profile = renamingProfile;
      const displayName = renameValue.trim();
      if (profile === null || !displayName || savingName) return;
      setSavingName(true);
      try {
        await updateDisplayName(profile, displayName);
        setOpenMenu(null);
        setRenamingProfile(null);
      } catch (error) {
        const isMissingEndpoint =
          error instanceof Error && /^404(?:\s|:)/.test(error.message);
        showToast(
          isMissingEndpoint
            ? "Переименование появится после обновления движка"
            : ownerFacingError(error, "Не удалось переименовать агента."),
          "error",
        );
      } finally {
        setSavingName(false);
      }
    },
    [renameValue, renamingProfile, savingName, showToast, updateDisplayName],
  );

  // role="tablist" обещает управление стрелками — выполняем обещание, иначе
  // роль врёт скринридеру. Фокус ведём за активной вкладкой: панели всё равно
  // смонтированы, переключение бесплатно.
  const onTabKeyDown = useCallback(
    (event: KeyboardEvent) => {
      if ((event.target as HTMLElement).getAttribute("role") !== "tab") return;
      const delta =
        event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      if (delta === 0 || tabs.length === 0) return;
      event.preventDefault();
      const currentIndex = tabs.findIndex((tab) => tab.profile === activeId);
      const index = currentIndex >= 0 ? currentIndex : 0;
      const next = tabs[(index + delta + tabs.length) % tabs.length];
      setActiveId(next.profile);
      // Фокус переносим на кнопку, к которой только что уехало выделение.
      window.requestAnimationFrame(() => {
        document.getElementById(`agent-tab-${next.profile}`)?.focus();
      });
    },
    [activeId, tabs],
  );

  const menuTab = openMenu
    ? openMenu.kind === "tab"
      ? tabs.find((tab) => tab.profile === openMenu.profile)
      : undefined
    : undefined;
  const menuTabIndex = menuTab
    ? tabs.findIndex((tab) => tab.profile === menuTab.profile)
    : -1;
  // Скрытые чаты тоже остаются смонтированы: «скрыть вкладку» — настройка
  // полосы, а не команда оборвать ответ или забыть открытый разговор.
  const mountedTabs = [...tabs, ...hiddenTabs];

  return (
    // Ту же полную высоту, что и у одиночного чата, даёт обёртка в App.tsx
    // (маршрут /agents получает `flex flex-1 flex-col` и `pb-0`).
    <div className="flex h-full min-h-0 flex-col">
      {/* Полоса вкладок тянется от края до края — отрицательные поля гасят
          горизонтальный padding обёртки, внутренние возвращают его тексту. */}
      <div className="shrink-0 -mx-3 px-3 py-2 sm:-mx-6 sm:px-6">
        <div
          role="tablist"
          aria-label="Агенты"
          onKeyDown={onTabKeyDown}
          className="neo-tabs-list flex min-h-14 items-center gap-2 overflow-hidden p-2.5"
        >
          <div
            role="presentation"
            className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto"
          >
            {tabs.map((tab) => {
              const active = tab.profile === activeId;
              const streaming = streamingByProfile[tab.profile] === true;
              return (
                <div
                  key={tab.profile}
                  role="presentation"
                  className="relative flex shrink-0 items-center"
                >
                  <button
                    id={`agent-tab-${tab.profile}`}
                    type="button"
                    role="tab"
                    aria-selected={active}
                    aria-controls={`agent-panel-${tab.profile}`}
                    tabIndex={active ? 0 : -1}
                    title={tab.description}
                    aria-label={
                      streaming ? `${tab.label} — агент отвечает` : undefined
                    }
                    data-active={active ? "true" : undefined}
                    onClick={() => setActiveId(tab.profile)}
                    className={cn(
                      "neo-tab flex min-h-9 items-center gap-2 px-3 py-2",
                      "font-sans text-[0.9375rem] leading-snug normal-case tracking-normal",
                      "cursor-pointer whitespace-nowrap",
                      active && "font-semibold",
                    )}
                  >
                    <span>{tab.label}</span>
                    {streaming && (
                      <span
                        aria-hidden
                        title="Агент отвечает"
                        className="size-1.5 shrink-0 animate-pulse rounded-full bg-[var(--neo-accent)]"
                      />
                    )}
                  </button>
                  <button
                    type="button"
                    className="neo-tab ml-0.5 flex size-8 items-center justify-center p-0"
                    aria-label={`Меню агента «${tab.label}»`}
                    aria-haspopup="menu"
                    aria-expanded={
                      openMenu?.kind === "tab" &&
                      openMenu.profile === tab.profile
                    }
                    data-agent-tab-menu-trigger
                    onClick={(event) => toggleTabMenu(event, tab.profile)}
                  >
                    <MoreVertical size={17} aria-hidden />
                  </button>
                </div>
              );
            })}
          </div>
          <button
            type="button"
            aria-label="Добавить вкладку агента"
            aria-haspopup="menu"
            aria-expanded={openMenu?.kind === "add"}
            data-agent-tab-menu-trigger
            onClick={toggleAddMenu}
            className={cn(
              "flex size-9 shrink-0 cursor-pointer items-center justify-center p-0",
              "rounded-[var(--neo-radius-round)] border-0 bg-[var(--neo-surface)]",
              "text-[var(--neo-text-secondary)] shadow-[var(--neo-depth-1)] outline-0",
              "hover:text-[var(--neo-text-primary)] active:shadow-[var(--neo-inset-compact)]",
              "focus:outline-0 focus-visible:outline-0",
            )}
          >
            <Plus size={18} aria-hidden />
          </button>
        </div>
      </div>

      {openMenu?.kind === "tab" && menuTab &&
        createPortal(
          <div
            ref={menuRef}
            role="menu"
            aria-label={`Действия агента «${menuTab.label}»`}
            className="neo-select-menu fixed z-50 min-w-[250px] p-1.5"
            style={{ top: openMenu.top, right: openMenu.right }}
          >
            {renamingProfile === menuTab.profile ? (
              <form onSubmit={submitDisplayName} className="flex items-center gap-1.5 p-1">
                <label htmlFor={`agent-display-name-${menuTab.profile}`} className="sr-only">
                  Новое имя агента «{menuTab.label}»
                </label>
                <input
                  id={`agent-display-name-${menuTab.profile}`}
                  autoFocus
                  required
                  value={renameValue}
                  onChange={(event) => setRenameValue(event.target.value)}
                  className="neo-field h-9 min-w-0 flex-1 px-3 font-sans text-sm normal-case tracking-normal"
                />
                <button
                  type="submit"
                  disabled={!renameValue.trim() || savingName}
                  className="neo-tab flex size-9 items-center justify-center p-0"
                  aria-label="Сохранить имя"
                  title="Сохранить"
                >
                  <Check size={16} aria-hidden />
                </button>
                <button
                  type="button"
                  className="neo-tab flex size-9 items-center justify-center p-0"
                  aria-label="Отменить переименование"
                  title="Отменить"
                  onClick={() => setRenamingProfile(null)}
                >
                  <X size={16} aria-hidden />
                </button>
              </form>
            ) : (
              <>
                <button
                  type="button"
                  role="menuitem"
                  className="neo-select-option flex w-full items-center gap-2 px-3 py-2 text-left font-sans text-sm normal-case tracking-normal"
                  onClick={() => {
                    setRenameValue(menuTab.label);
                    setRenamingProfile(menuTab.profile);
                  }}
                >
                  <Pencil size={15} aria-hidden />
                  Переименовать
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className="neo-select-option flex w-full items-center gap-2 px-3 py-2 text-left font-sans text-sm normal-case tracking-normal"
                  onClick={() => {
                    setOpenMenu(null);
                    navigate("/profiles");
                  }}
                >
                  <Settings size={15} aria-hidden />
                  Открыть настройки профиля
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className="neo-select-option flex w-full items-center gap-2 px-3 py-2 text-left font-sans text-sm normal-case tracking-normal"
                  onClick={() => startNewChat(menuTab.profile)}
                >
                  <MessageSquarePlus size={15} aria-hidden />
                  Новый чат
                </button>
                <button
                  type="button"
                  role="menuitem"
                  disabled={menuTabIndex <= 0}
                  className="neo-select-option flex w-full items-center gap-2 px-3 py-2 text-left font-sans text-sm normal-case tracking-normal disabled:cursor-not-allowed disabled:opacity-40"
                  onClick={() => {
                    moveTab(menuTab.profile, "left");
                    setOpenMenu(null);
                  }}
                >
                  <ArrowLeft size={15} aria-hidden />
                  Сдвинуть влево
                </button>
                <button
                  type="button"
                  role="menuitem"
                  disabled={menuTabIndex < 0 || menuTabIndex >= tabs.length - 1}
                  className="neo-select-option flex w-full items-center gap-2 px-3 py-2 text-left font-sans text-sm normal-case tracking-normal disabled:cursor-not-allowed disabled:opacity-40"
                  onClick={() => {
                    moveTab(menuTab.profile, "right");
                    setOpenMenu(null);
                  }}
                >
                  <ArrowRight size={15} aria-hidden />
                  Сдвинуть вправо
                </button>
                {menuTab.profile !== MAIN_AGENT_TAB.profile && (
                  <button
                    type="button"
                    role="menuitem"
                    className="neo-select-option flex w-full items-center gap-2 px-3 py-2 text-left font-sans text-sm normal-case tracking-normal"
                    onClick={() => {
                      hideTab(menuTab.profile);
                      setOpenMenu(null);
                      setRenamingProfile(null);
                    }}
                  >
                    <EyeOff size={15} aria-hidden />
                    Скрыть вкладку
                  </button>
                )}
              </>
            )}
          </div>,
          document.body,
        )}

      {openMenu?.kind === "add" &&
        createPortal(
          <div
            ref={menuRef}
            role="menu"
            aria-label="Добавить вкладку агента"
            className="neo-select-menu fixed z-50 min-w-[250px] p-1.5"
            style={{ top: openMenu.top, right: openMenu.right }}
          >
            {hiddenTabs.map((tab) => (
              <button
                key={tab.profile}
                type="button"
                role="menuitem"
                className="neo-select-option flex w-full items-center gap-2 px-3 py-2 text-left font-sans text-sm normal-case tracking-normal"
                onClick={() => {
                  showTab(tab.profile);
                  setActiveId(tab.profile);
                  setOpenMenu(null);
                }}
              >
                <Plus size={15} aria-hidden />
                {tab.label}
              </button>
            ))}
            <button
              type="button"
              role="menuitem"
              className="neo-select-option flex w-full items-center gap-2 px-3 py-2 text-left font-sans text-sm normal-case tracking-normal"
              onClick={() => {
                setOpenMenu(null);
                navigate("/profiles/new");
              }}
            >
              <UserRoundPlus size={15} aria-hidden />
              Создать нового агента
            </button>
          </div>,
          document.body,
        )}

      <Toast toast={toast} />

      <div className="flex min-h-0 flex-1 flex-col">
        {mountedTabs.map((tab) => (
          <div
            key={tab.profile}
            id={`agent-panel-${tab.profile}`}
            role="tabpanel"
            aria-labelledby={`agent-tab-${tab.profile}`}
            // display:none, а НЕ снятие с монтирования — на этом держится
            // весь экран (см. шапку файла).
            style={{ display: tab.profile === activeId ? undefined : "none" }}
            className="flex min-h-0 flex-1 flex-col"
          >
            <BubbleChatPage
              agentProfile={tab.profile}
              onStreamingChange={handleStreamingChange}
              draft={draftByProfile[tab.profile] ?? null}
              onDraftConsumed={() => clearDraft(tab.profile)}
              newChatRequest={newChatByProfile[tab.profile] ?? 0}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
