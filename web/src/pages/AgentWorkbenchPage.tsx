import { AgentRunBadge, SessionRunActivity } from "@/components/chat/SessionRunActivity";
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
  Clock,
  Cpu,
  EyeOff,
  FileText,
  MessageSquarePlus,
  MoreVertical,
  Package,
  Pencil,
  Plus,
  Trash2,
  UserRoundPlus,
  X,
} from "lucide-react";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { useConfirmDelete } from "@nous-research/ui/hooks/use-confirm-delete";

import BubbleChatPage from "@/pages/BubbleChatPage";
import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";
import { api } from "@/lib/api";
import { soulNamedAs } from "@/lib/agent-wizard";
import { ownerFacingError } from "@/lib/owner-facing-error";
import { cn } from "@/lib/utils";
import { agentSettingsHref, MAIN_AGENT_TAB } from "@/lib/agent-tabs";
import { useAgentTabs } from "@/hooks/useAgentTabs";

/* ------------------------------------------------------------------ */
/*  AgentWorkbenchPage (default export)                                */
/* ------------------------------------------------------------------ */

/** Ширина портального меню — та же, что в разметке (`min-w-[250px]`). */
const TAB_MENU_WIDTH = 250;
/** Отступ от края экрана, чтобы меню не липло к рамке окна. */
const TAB_MENU_VIEWPORT_MARGIN = 12;

/**
 * Левый край меню под кнопкой, которая его открыла.
 *
 * Раньше меню прижималось правым краем к правому краю кнопки «⋮» и уезжало на
 * 250 px влево — визуально оно принадлежало СОСЕДНЕЙ вкладке, а не своей
 * (QA 03.09). Теперь меню начинается там же, где кнопка; у правого края экрана
 * оно переворачивается — правый край меню встаёт по правому краю кнопки.
 */
function tabMenuLeft(trigger: DOMRect): number {
  const viewportWidth =
    typeof window === "undefined" ? TAB_MENU_WIDTH : window.innerWidth;
  const maxLeft = viewportWidth - TAB_MENU_WIDTH - TAB_MENU_VIEWPORT_MARGIN;
  const preferred =
    trigger.left <= maxLeft ? trigger.left : trigger.right - TAB_MENU_WIDTH;
  return Math.max(
    TAB_MENU_VIEWPORT_MARGIN,
    Math.min(preferred, Math.max(TAB_MENU_VIEWPORT_MARGIN, maxLeft)),
  );
}

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
    | { kind: "tab"; profile: string; top: number; left: number }
    | { kind: "add"; top: number; left: number }
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
  const [resumeByProfile, setResumeByProfile] = useState<Record<string, string>>({});
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
    const closeFromViewport = (event: Event) => {
      // Длинное имя прокручивает сам input. Якорь меню при этом не
      // перемещается, и форму нельзя закрывать посреди ввода.
      if (event.type === "scroll" && event.target instanceof Node && menuRef.current?.contains(event.target)) return;
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
  const onAgentsRoute = (pathname.replace(/\/$/, "") || "/") === "/agents";
  useEffect(() => {
    if (onAgentsRoute) void refresh();
  }, [onAgentsRoute, refresh]);

  useEffect(() => {
    // Экран живёт смонтированным и на чужих маршрутах, а `?agent=` — ещё и
    // адрес редактора в «Настройках агентов» (`/profiles?agent=…&edit=role`).
    // Читаем и снимаем параметр только у себя, иначе диплинк соседнего
    // раздела терял бы имя агента, не успев открыться.
    if (!onAgentsRoute) return;
    const requested = searchParams.get("agent")?.trim();
    const draft = searchParams.get("draft")?.trim();
    if (!requested) return;
    // Снаружи главный агент известен под серверным именем `default` (карточка
    // в «Настройках агентов» шлёт `/agents?agent=default`), а вкладка у него —
    // пустой профиль панели.
    const agent =
      requested === "default" ? MAIN_AGENT_TAB.profile : requested;
    if (!tabs.some((tab) => tab.profile === agent)) {
      // Диплинк на профиль, которого в составе ещё нет (только что создан):
      // перечитываем список и оставляем параметры до его появления.
      void refresh();
      return;
    }
    setActiveId(agent);
    const resume = searchParams.get("resume");
    if (resume) setResumeByProfile(previous => ({ ...previous, [agent]: resume }));
    if (draft) setDraftByProfile((previous) => ({ ...previous, [agent]: draft }));
    // Параметры снимаем сразу: иначе возврат на экран назад-вперёд подставил
    // бы тот же текст поверх уже набранного.
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        next.delete("agent");
        next.delete("resume");
        next.delete("draft");
        return next;
      },
      { replace: true },
    );
  }, [onAgentsRoute, searchParams, setSearchParams, tabs, refresh]);

  const clearDraft = useCallback((profile: string) => {
    setDraftByProfile((previous) => {
      if (previous[profile] === undefined) return previous;
      const next = { ...previous };
      delete next[profile];
      return next;
    });
  }, []);

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
        left: tabMenuLeft(rect),
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
        left: tabMenuLeft(rect),
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

  /**
   * После переименования сказать, что роль агента имя не сменила.
   *
   * `PUT /display-name` меняет только подпись вкладки; в SOUL.md остаётся
   * «Ты — {старое имя}», и агент представляется по-старому (аудит 06.09,
   * F13). Роль здесь не переписываем — решение с Астрой 06.09: запись из
   * браузера поверх прочитанного могла бы затереть параллельную правку, а
   * `display_name` в движке намеренно только подпись. Читаем роль и, если она
   * начинается с нашего шаблона со старым именем, говорим человеку, где
   * поправить. Чужие инструкции и дефолт движка не трактуем — молчим.
   */
  const noteSoulName = useCallback(
    async (profile: string, oldName: string) => {
      try {
        const { content } = await api.getProfileSoul(profile);
        if (!soulNamedAs(content, oldName)) return;
        showToast(
          `Вкладка переименована. В роли агент по-прежнему зовётся «${oldName}» — при желании поправьте в «Роль и поведение».`,
          "success",
        );
      } catch {
        // Подсказка необязательна: переименование уже удалось.
      }
    },
    [showToast],
  );

  const submitDisplayName = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const profile = renamingProfile;
      const displayName = renameValue.trim();
      if (profile === null || !displayName || savingName) return;
      const previousLabel =
        tabs.find((tab) => tab.profile === profile)?.label ?? "";
      setSavingName(true);
      try {
        await updateDisplayName(profile, displayName);
        setOpenMenu(null);
        setRenamingProfile(null);
        // Главная вкладка — профиль самой панели: её подпись «Корра» не из
        // SOUL, роль главного агента не трогаем.
        if (profile && previousLabel && previousLabel !== displayName) {
          void noteSoulName(profile, previousLabel);
        }
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
    [
      renameValue,
      renamingProfile,
      savingName,
      showToast,
      noteSoulName,
      tabs,
      updateDisplayName,
    ],
  );

  // Удаление агента — из меню его вкладки, с подтверждением. Раньше путь
  // «создал → поговорил → удалил» из «Агентов» не замыкался: удалить можно
  // было только в карточке «Профилей» под «Настройками» (аудит 06.09, F12).
  const profileDelete = useConfirmDelete<string>({
    onDelete: useCallback(
      async (profile: string) => {
        try {
          await api.deleteProfile(profile);
        } catch (error) {
          showToast(ownerFacingError(error, "Не удалось удалить агента."), "error");
          throw error;
        }
        setOpenMenu(null);
        setRenamingProfile(null);
        // Вкладки читаются из /api/profiles — после удаления состав другой.
        await refresh();
        showToast("Агент удалён.", "success");
      },
      [refresh, showToast],
    ),
  });
  const deletingTab = profileDelete.pendingId
    ? tabs.find((tab) => tab.profile === profileDelete.pendingId) ?? null
    : null;

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
          className="neo-tabs-list flex min-h-14 items-center gap-2 p-2"
        >
          <div
            role="presentation"
            className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto px-1.5 py-1.5"
          >
            {tabs.map((tab) => {
              const active = tab.profile === activeId;
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
                    <AgentRunBadge profile={tab.profile} />
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
            className="neo-select-menu fixed z-50 w-[250px] p-1.5"
            style={{ top: openMenu.top, left: openMenu.left }}
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
                {/* Настройки *этого* агента, а не список всех: редактор роли
                    и модели в «Настройках агентов» открывается по адресу
                    `/profiles?agent=<профиль>&edit=role|model` (договорённость с
                    Астрой 05.09). Главная вкладка — профиль панели, `default`. */}
                <button
                  type="button"
                  role="menuitem"
                  className="neo-select-option flex w-full items-center gap-2 px-3 py-2 text-left font-sans text-sm normal-case tracking-normal"
                  onClick={() => {
                    setOpenMenu(null);
                    navigate(agentSettingsHref(menuTab.profile, "role"));
                  }}
                >
                  <FileText size={15} aria-hidden />
                  Роль и поведение
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className="neo-select-option flex w-full items-center gap-2 px-3 py-2 text-left font-sans text-sm normal-case tracking-normal"
                  onClick={() => {
                    setOpenMenu(null);
                    navigate(agentSettingsHref(menuTab.profile, "model"));
                  }}
                >
                  <Cpu size={15} aria-hidden />
                  Модель
                </button>
                {/* Обучение агента живёт в трёх разделах панели; из меню
                    вкладки они открываются сразу для этого агента (адрес
                    несёт `?profile=`), а не для запомненного в разделе. */}
                <button
                  type="button"
                  role="menuitem"
                  className="neo-select-option flex w-full items-center gap-2 px-3 py-2 text-left font-sans text-sm normal-case tracking-normal"
                  onClick={() => {
                    setOpenMenu(null);
                    navigate(agentSettingsHref(menuTab.profile, "skills"));
                  }}
                >
                  <Package size={15} aria-hidden />
                  Навыки
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className="neo-select-option flex w-full items-center gap-2 px-3 py-2 text-left font-sans text-sm normal-case tracking-normal"
                  onClick={() => {
                    setOpenMenu(null);
                    navigate(agentSettingsHref(menuTab.profile, "schedule"));
                  }}
                >
                  <Clock size={15} aria-hidden />
                  Расписание
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
                {menuTab.profile !== MAIN_AGENT_TAB.profile && (
                  <button
                    type="button"
                    role="menuitem"
                    className="neo-select-option flex w-full items-center gap-2 px-3 py-2 text-left font-sans text-sm normal-case tracking-normal text-destructive"
                    onClick={() => {
                      setOpenMenu(null);
                      setRenamingProfile(null);
                      profileDelete.requestDelete(menuTab.profile);
                    }}
                  >
                    <Trash2 size={15} aria-hidden />
                    Удалить агента…
                  </button>
                )}
              </>
            )}
          </div>,
          document.body,
        )}

      <DeleteConfirmDialog
        open={profileDelete.isOpen}
        onCancel={profileDelete.cancel}
        onConfirm={profileDelete.confirm}
        loading={profileDelete.isDeleting}
        title={`Удалить агента «${deletingTab?.label ?? profileDelete.pendingId ?? ""}»?`}
        description="Вместе с агентом удалятся его роль, настройки, навыки и вся история разговоров. Отменить это нельзя."
        confirmLabel="Удалить агента"
      />

      {openMenu?.kind === "add" &&
        createPortal(
          <div
            ref={menuRef}
            role="menu"
            aria-label="Добавить вкладку агента"
            className="neo-select-menu fixed z-50 w-[250px] p-1.5"
            style={{ top: openMenu.top, left: openMenu.left }}
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
                <Plus size={15} className="shrink-0" aria-hidden />
                <span className="min-w-0 truncate">{tab.label}</span>
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
      <SessionRunActivity tabs={mountedTabs} />

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
              active={onAgentsRoute && tab.profile === activeId}
              resumeSession={resumeByProfile[tab.profile]}
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
