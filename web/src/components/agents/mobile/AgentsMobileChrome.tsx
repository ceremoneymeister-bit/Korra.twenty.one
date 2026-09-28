/**
 * Мобильная шапка экрана агентов — вариант «D», принят Дмитрием 28.09.2026.
 *
 * Вверху две тонкие строки: разговор (☰, название и агент, «Чаты N») и под
 * ним полоса аватаров без прокрутки, «Все агенты N» и кольцо работ. Внизу
 * остаётся только поле ввода. Второй режим — «Список агентов»: главный экран
 * — список с последним разговором и состоянием, агент открывается на весь
 * экран с «‹». Режим выбирает человек в «Настройки → Вид».
 *
 * Только ниже `lg`: десктоп рисует прежнюю полосу вкладок и этих частей не
 * показывает. Хук отдаёт шапку, экран-список и слой шторок, а чаты агентов
 * расставляет сам экран — они остаются смонтированными в обоих режимах, на
 * этом держится весь экран (см. AgentWorkbenchPage).
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { useStore } from "@nanostores/react";
import { ChevronLeft, Menu, MessagesSquare, Plus, UsersRound } from "lucide-react";

import { ActivityRing, AgentFaceButton } from "@/components/agents/mobile/AgentFace";
import {
  ActivitySheet,
  AgentListHome,
  AgentMenuSheet,
  AllAgentsSheet,
  labelOf,
  ReplyNotice,
  type AgentsMobileActions,
  type LongPressHandlers,
} from "@/components/agents/mobile/AgentsMobileSheets";
import { $agentConversations } from "@/lib/agent-conversations";
import {
  activityCount,
  buildAgentStatuses,
  IDLE_STATUS,
  pickRail,
  railSlots,
  statusSpeech,
  strongestAttention,
  type AgentStatus,
} from "@/lib/agent-status";
import type { AgentTabConfig } from "@/lib/agent-tabs";
import { $agentsView, type AgentsMobileMode } from "@/lib/agents-view";
import {
  $chatRuns,
  $dismissedRunToasts,
  $failedChatRuns,
  $unreadChatRuns,
  $viewedChat,
  dismissRunToasts,
} from "@/lib/chat-runs";
import { openMobileNav } from "@/lib/mobile-nav";
import { cn } from "@/lib/utils";

export type { AgentsMobileActions } from "@/components/agents/mobile/AgentsMobileSheets";
export { formatListTime } from "@/components/agents/mobile/AgentsMobileSheets";

import "./agents-mobile.css";

/** Сколько живёт уведомление о новом ответе; точка на аватаре остаётся. */
const NOTICE_MS = 8_000;
/** Долгое нажатие на аватар или строку — меню агента. */
const LONG_PRESS_MS = 520;
/** Полосу потянули вниз на столько — открыть всех агентов. */
const PULL_OPEN_PX = 28;

export interface AgentsMobileChromeOptions {
  /** Ниже `lg`. Выше хук ничего не делает: ни таймеров, ни замеров. */
  enabled: boolean;
  mode: AgentsMobileMode;
  /** В режиме «Список»: главный экран-список или открытый агент. */
  listHome: boolean;
  /** Вкладки полосы в порядке пользователя (без скрытых). */
  tabs: AgentTabConfig[];
  /** Скрытые с полосы — они есть во «Все агенты» и в списке. */
  hiddenTabs: AgentTabConfig[];
  activeId: string;
  chatsOpen: boolean;
  onChatsHost: (element: HTMLElement | null) => void;
  /** Шторка чатов открыта — другие шторки закрываются, и наоборот. */
  onCloseChats: () => void;
  actions: AgentsMobileActions;
}

/** Части мобильной шапки. Их расставляет экран агентов вокруг своих чатов:
 *  чаты стоят на одном месте дерева и на телефоне, и на десктопе, поэтому
 *  поворот планшета через границу `lg` не обрывает идущий ответ. */
export interface AgentsMobileChrome {
  rootProps: {
    className: string;
    "data-agents-mobile": AgentsMobileMode;
    "data-typing"?: "true";
    onFocusCapture: (event: { target: EventTarget }) => void;
    onBlurCapture: (event: { target: EventTarget }) => void;
  };
  header: ReactNode;
  /** Экран-список режима «Список агентов» (или null). */
  home: ReactNode;
  /** Уведомление, шторки и место для шторки чатов — поверх чатов. */
  overlays: ReactNode;
}

type Sheet = null | { kind: "agents" } | { kind: "activity" } | { kind: "menu"; profile: string };

/** Долгое нажатие: вызывает `onLong` и гасит следующий за ним click. */
function useLongPress(onLong: (target: string) => void) {
  const timer = useRef<number | null>(null);
  const start = useRef<{ x: number; y: number } | null>(null);
  const suppress = useRef(false);
  const cancel = useCallback(() => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
  }, []);
  useEffect(() => cancel, [cancel]);
  const bind = (target: string): LongPressHandlers => ({
    onPointerDown: (event: ReactPointerEvent) => {
      if (event.pointerType === "mouse" && event.button !== 0) return;
      start.current = { x: event.clientX, y: event.clientY };
      cancel();
      timer.current = window.setTimeout(() => {
        timer.current = null;
        suppress.current = true;
        onLong(target);
      }, LONG_PRESS_MS);
    },
    onPointerMove: (event: ReactPointerEvent) => {
      if (!start.current) return;
      if (Math.abs(event.clientX - start.current.x) > 8 || Math.abs(event.clientY - start.current.y) > 8) cancel();
    },
    onPointerUp: cancel,
    onPointerCancel: cancel,
    onPointerLeave: cancel,
    onContextMenu: (event: { preventDefault: () => void }) => event.preventDefault(),
  });
  /** Проверить и сбросить: был ли click продолжением долгого нажатия. */
  const consumed = () => {
    const value = suppress.current;
    suppress.current = false;
    return value;
  };
  return { bind, consumed };
}

export function useAgentsMobileChrome({
  enabled,
  mode,
  listHome,
  tabs,
  hiddenTabs,
  activeId,
  chatsOpen,
  onChatsHost,
  onCloseChats,
  actions,
}: AgentsMobileChromeOptions): AgentsMobileChrome {
  const runs = useStore($chatRuns);
  const unread = useStore($unreadChatRuns);
  const failed = useStore($failedChatRuns);
  const dismissed = useStore($dismissedRunToasts);
  const viewed = useStore($viewedChat);
  const conversations = useStore($agentConversations);
  const view = useStore($agentsView);
  const allTabs = useMemo(() => [...tabs, ...hiddenTabs], [tabs, hiddenTabs]);
  const hidden = useMemo(() => new Set(hiddenTabs.map((tab) => tab.profile)), [hiddenTabs]);
  const statuses = useMemo(
    () => buildAgentStatuses(allTabs.map((tab) => tab.profile), runs, unread, failed),
    [allTabs, runs, unread, failed],
  );
  const statusOf = useCallback((profile: string): AgentStatus => statuses.get(profile) ?? IDLE_STATUS, [statuses]);
  const lastActive = useMemo(
    () => new Map(Object.entries(conversations).map(([profile, summary]) => [profile, summary.lastActive ?? 0])),
    [conversations],
  );
  const [sheet, setSheet] = useState<Sheet>(null);
  const [typing, setTyping] = useState(false);
  const [width, setWidth] = useState(() => (typeof window === "undefined" ? 390 : window.innerWidth));
  const headerRef = useRef<HTMLDivElement | null>(null);
  const blurTimer = useRef<number | null>(null);

  // Ширина полосы меняется с поворотом телефона и открытием меню.
  useEffect(() => {
    if (!enabled) return;
    const element = headerRef.current;
    const measure = () => setWidth(element?.getBoundingClientRect().width || window.innerWidth);
    measure();
    if (typeof ResizeObserver === "undefined" || !element) {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [enabled, listHome]);

  // Шторка чатов и шторки шапки не открыты одновременно.
  const openSheet = useCallback((next: Sheet) => {
    if (next) onCloseChats();
    setSheet(next);
  }, [onCloseChats]);
  const closeSheet = useCallback(() => setSheet(null), []);
  useEffect(() => { if (chatsOpen) setSheet(null); }, [chatsOpen]);
  // Сменился агент или экран — открытое меню другого агента уже ни к чему.
  useEffect(() => { setSheet(null); }, [listHome]);

  const toggleSheet = (kind: "agents" | "activity") =>
    openSheet(sheet?.kind === kind ? null : { kind });

  const activeLabel = labelOf(allTabs, activeId);
  const conversation = conversations[activeId];
  const title = conversation?.title || (conversation === undefined ? "Разговор" : "Новый чат");
  const chatCount = conversation?.chatCount ?? null;

  const rail = pickRail(
    tabs.some((tab) => tab.profile === activeId) ? tabs : [...tabs, ...allTabs.filter((tab) => tab.profile === activeId)],
    statuses,
    { active: activeId, pinned: view.pinned, lastActive, slots: railSlots(width) },
  );
  const works = activityCount(statuses.values());
  const others = strongestAttention(allTabs.filter((tab) => tab.profile !== activeId).map((tab) => statusOf(tab.profile)));

  // Новый ответ другого агента — под шапкой. Касание открывает ответ.
  const notices = unread.filter(
    (run) => !dismissed.includes(run.message_id)
      && !(viewed?.profile === run.profile && viewed.sessionId === run.session_id),
  );
  const notice = enabled ? notices[0] : undefined;
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => dismissRunToasts([notice.message_id]), NOTICE_MS);
    return () => window.clearTimeout(timer);
  }, [notice]);

  // Уведомление встаёт под полосой решений открытого агента, а не поверх
  // неё: «4 решения ждут вас» не должно пропадать за чужим ответом.
  const [noticeOffset, setNoticeOffset] = useState(0);
  const activeDecisions = statusOf(activeId).decisions;
  useLayoutEffect(() => {
    if (!notice) return;
    const panel = document.getElementById(`agent-panel-${activeId}`);
    // Полоса решений приходит своим запросом позже шапки и может раскрыться:
    // следим и за её появлением, и за высотой — только пока видно уведомление.
    let bar: HTMLElement | null = null;
    const resize = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => measure());
    const measure = () => {
      const next = panel?.querySelector<HTMLElement>("[data-decision-center]") ?? null;
      if (next !== bar) {
        if (bar) resize?.unobserve(bar);
        if (next) resize?.observe(next);
        bar = next;
      }
      setNoticeOffset(bar && bar.getClientRects().length > 0 ? bar.offsetTop + bar.offsetHeight : 0);
    };
    measure();
    const mutations = panel && typeof MutationObserver !== "undefined" ? new MutationObserver(measure) : null;
    mutations?.observe(panel!, { childList: true, subtree: true });
    return () => {
      mutations?.disconnect();
      resize?.disconnect();
    };
  }, [notice, activeId, activeDecisions]);

  const press = useLongPress((profile) => openSheet({ kind: "menu", profile }));

  // Полосу потянули вниз — все агенты. Отпускают палец часто уже ниже
  // полосы, поэтому конец жеста слушаем на окне.
  const onTrayPointerDown = (event: ReactPointerEvent) => {
    const from = event.clientY;
    const finish = (up: PointerEvent) => {
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
      if (up.type === "pointerup" && up.clientY - from > PULL_OPEN_PX) openSheet({ kind: "agents" });
    };
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
  };

  // Клавиатура открыта — полоса уходит, строка разговора остаётся.
  const onFocusCapture = (event: { target: EventTarget }) => {
    const target = event.target as HTMLElement;
    if (target.tagName !== "TEXTAREA" || !target.closest("[data-agent-panel]")) return;
    if (blurTimer.current !== null) window.clearTimeout(blurTimer.current);
    setTyping(true);
  };
  const onBlurCapture = (event: { target: EventTarget }) => {
    const target = event.target as HTMLElement;
    if (target.tagName !== "TEXTAREA") return;
    if (blurTimer.current !== null) window.clearTimeout(blurTimer.current);
    blurTimer.current = window.setTimeout(() => setTyping(false), 120);
  };
  useEffect(() => () => { if (blurTimer.current !== null) window.clearTimeout(blurTimer.current); }, []);

  const showChats = () => {
    setSheet(null);
    actions.openChats();
  };
  const chatsButton = (
    <button
      type="button"
      className="k-chats"
      aria-haspopup="dialog"
      aria-expanded={chatsOpen}
      aria-label={`Чаты агента «${activeLabel}»${chatCount !== null ? `: ${chatCount}` : ""}`}
      onClick={() => {
        if (chatsOpen) onCloseChats();
        else showChats();
      }}
      data-agent-chats
    >
      <MessagesSquare size={18} aria-hidden className="k-icon" />
      Чаты
      {chatCount !== null && <span className="k-chats__n" aria-hidden>{chatCount}</span>}
    </button>
  );
  const titleButton = (
    <button
      type="button"
      className="k-ttl"
      aria-label={`Разговор «${title}» с агентом «${activeLabel}». Показать чаты`}
      onClick={showChats}
    >
      <strong>{title}</strong>
      <span>{activeLabel}</span>
    </button>
  );

  const openFromAnywhere = (profile: string) => {
    setSheet(null);
    onCloseChats();
    actions.openAgent(profile);
  };

  const header = listHome ? (
    <div className="k-hd k-hd--plain" ref={headerRef} data-agents-header="list">
      <div className="k-tb k-tb--home">
        <button type="button" className="k-ib" aria-label="Меню Korra" onClick={openMobileNav}>
          <Menu size={20} aria-hidden className="k-icon" />
        </button>
        <h1 className="k-home-title">
          Агенты<span className="k-sheet__n">{allTabs.length}</span>
        </h1>
        <ActivityRing count={works} aria-haspopup="dialog" aria-expanded={sheet?.kind === "activity"} onClick={() => toggleSheet("activity")} />
        <button type="button" className="k-ib k-add" aria-label="Добавить агента" onClick={actions.addAgent}>
          <Plus size={20} aria-hidden className="k-icon" />
        </button>
      </div>
    </div>
  ) : mode === "list" ? (
    <div className="k-hd" ref={headerRef} data-agents-header="agent">
      <div className="k-tb k-tb--list">
        <button
          type="button"
          className="k-ib k-back"
          aria-label={`Все агенты${others.count ? `. Ждут вас: ${others.count}` : ""}`}
          onClick={actions.back}
        >
          <ChevronLeft size={22} aria-hidden className="k-icon" />
          {others.kind && (
            <span className={cn("k-bdg k-bdg--dot", others.kind === "error" && "is-error", others.kind === "unread" && "is-unread")} aria-hidden />
          )}
        </button>
        <AgentFaceButton
          label={activeLabel}
          status={statusOf(activeId)}
          aria-label={`Действия агента «${activeLabel}»${statusSpeech(statusOf(activeId))}`}
          aria-haspopup="dialog"
          aria-expanded={sheet?.kind === "menu"}
          onClick={() => openSheet(sheet?.kind === "menu" ? null : { kind: "menu", profile: activeId })}
        />
        {titleButton}
        {chatsButton}
      </div>
    </div>
  ) : (
    <div className="k-hd" ref={headerRef} data-agents-header="tabs">
      <div className="k-tb">
        <button type="button" className="k-ib" aria-label="Меню Korra" onClick={openMobileNav}>
          <Menu size={20} aria-hidden className="k-icon" />
        </button>
        {titleButton}
        {chatsButton}
      </div>
      <nav className="k-rail" aria-label="Агенты">
        <div className="k-tray" onPointerDown={onTrayPointerDown}>
          <div className="k-tray__avs">
            {rail.shown.map((tab) => {
              const active = tab.profile === activeId;
              const status = statusOf(tab.profile);
              return (
                <AgentFaceButton
                  key={tab.profile}
                  label={tab.label}
                  status={status}
                  active={active}
                  data-agent-rail-item={tab.profile}
                  aria-label={`${tab.label}${active ? ", открыт" : ""}${statusSpeech(status)}${active ? ". Действия агента" : ""}`}
                  aria-current={active ? "page" : undefined}
                  aria-haspopup={active ? "dialog" : undefined}
                  aria-expanded={active ? sheet?.kind === "menu" && sheet.profile === tab.profile : undefined}
                  {...press.bind(tab.profile)}
                  onClick={() => {
                    if (press.consumed()) return;
                    // Повторное касание открытого аватара — меню агента.
                    if (active) openSheet(sheet?.kind === "menu" ? null : { kind: "menu", profile: tab.profile });
                    else openFromAnywhere(tab.profile);
                  }}
                />
              );
            })}
          </div>
          <button
            type="button"
            className="k-all"
            aria-haspopup="dialog"
            aria-expanded={sheet?.kind === "agents"}
            aria-label={`Все агенты: ${allTabs.length}${rail.hiddenAttention ? ". Среди скрытых есть ждущие вас" : ""}`}
            onClick={() => toggleSheet("agents")}
            data-agent-all
          >
            <UsersRound size={18} aria-hidden className="k-icon" />
            <span aria-hidden>{allTabs.length}</span>
            {rail.hiddenAttention && (
              <span className={cn("k-bdg k-bdg--dot", rail.hiddenAttention === "error" && "is-error", rail.hiddenAttention === "unread" && "is-unread")} aria-hidden />
            )}
          </button>
        </div>
        <ActivityRing count={works} aria-haspopup="dialog" aria-expanded={sheet?.kind === "activity"} onClick={() => toggleSheet("activity")} />
      </nav>
    </div>
  );

  let sheetNode: ReactNode = null;
  if (sheet?.kind === "agents") {
    sheetNode = (
      <AllAgentsSheet
        tabs={allTabs}
        hidden={hidden}
        activeId={activeId}
        mode={mode}
        statusOf={statusOf}
        conversations={conversations}
        onClose={closeSheet}
        onOpen={openFromAnywhere}
        onMenu={(profile) => openSheet({ kind: "menu", profile })}
        onAdd={() => { closeSheet(); actions.addAgent(); }}
        onViewSettings={() => { closeSheet(); actions.openViewSettings(); }}
      />
    );
  } else if (sheet?.kind === "activity") {
    sheetNode = (
      <ActivitySheet
        runs={runs}
        failed={failed}
        tabs={allTabs}
        statusOf={statusOf}
        onClose={closeSheet}
        onOpenChat={(profile, sessionId) => { closeSheet(); actions.openChat(profile, sessionId); }}
        onDecide={(profile) => { closeSheet(); actions.openDecisions(profile); }}
      />
    );
  } else if (sheet?.kind === "menu") {
    const menuTab = allTabs.find((tab) => tab.profile === sheet.profile);
    if (menuTab) {
      sheetNode = (
        <AgentMenuSheet
          tab={menuTab}
          index={tabs.findIndex((tab) => tab.profile === menuTab.profile)}
          count={tabs.length}
          hidden={hidden.has(menuTab.profile)}
          mode={mode}
          pinned={view.pinned.includes(menuTab.profile)}
          status={statusOf(menuTab.profile)}
          lastActive={conversations[menuTab.profile]?.lastActive ?? null}
          onClose={closeSheet}
          actions={actions}
          onOpen={() => openFromAnywhere(menuTab.profile)}
          onNewChat={() => { closeSheet(); onCloseChats(); actions.newChat(menuTab.profile); }}
        />
      );
    }
  }

  const home = listHome ? (
    <AgentListHome
      tabs={allTabs}
      statusOf={statusOf}
      conversations={conversations}
      runs={runs}
      onOpen={(profile) => { if (!press.consumed()) actions.openAgent(profile); }}
      longPress={press.bind}
      onViewSettings={actions.openViewSettings}
    />
  ) : null;

  const overlays = (
    <>
      {!listHome && notice && !sheet && !chatsOpen && (
        <ReplyNotice
          run={notice}
          offset={noticeOffset}
          label={labelOf(allTabs, notice.profile)}
          status={statusOf(notice.profile)}
          onOpen={() => { dismissRunToasts([notice.message_id]); actions.openChat(notice.profile, notice.session_id); }}
          onDismiss={() => dismissRunToasts([notice.message_id])}
        />
      )}
      {sheetNode}
      <div ref={onChatsHost} className="contents" data-agent-chats-host />
    </>
  );

  return {
    rootProps: {
      className: "k-agents",
      "data-agents-mobile": mode,
      ...(typing ? { "data-typing": "true" as const } : {}),
      onFocusCapture,
      onBlurCapture,
    },
    header,
    home,
    overlays,
  };
}
