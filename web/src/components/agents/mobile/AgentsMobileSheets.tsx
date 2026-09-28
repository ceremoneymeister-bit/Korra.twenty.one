/**
 * Шторки и экран-список мобильной шапки агентов (вариант «D», 28.09).
 *
 * Здесь только отрисовка: что открыто и кто что нажал, решает
 * useAgentsMobileChrome. Шторки раскрываются вниз от шапки, шапка
 * остаётся видна.
 */

import { useId, useState, type FormEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronRight,
  Clock,
  Cpu,
  EyeOff,
  FileText,
  MessageSquare,
  MessageSquarePlus,
  MoreHorizontal,
  Package,
  Pencil,
  Pin,
  PinOff,
  Search,
  SlidersHorizontal,
  Trash2,
  UserRoundPlus,
  Volume2,
  X,
} from "lucide-react";

import { AgentSheet } from "@/components/agents/mobile/AgentSheet";
import { AgentFace } from "@/components/agents/mobile/AgentFace";
import {
  activityCount,
  attentionOf,
  decisionsWaitPhrase,
  groupByAttention,
  pluralRu,
  splitAgentName,
  statusSpeech,
  type AgentStatus,
} from "@/lib/agent-status";
import { filterAgentTabs, MAIN_AGENT_TAB, type AgentSettingsKind, type AgentTabConfig } from "@/lib/agent-tabs";
import { togglePinnedAgent, type AgentsMobileMode } from "@/lib/agents-view";
import { isRunBusy, type ChatRun } from "@/lib/chat-runs";
import { cn } from "@/lib/utils";

import "./agents-mobile.css";

export interface AgentsMobileActions {
  openAgent: (profile: string) => void;
  openChat: (profile: string, sessionId: string) => void;
  newChat: (profile: string) => void;
  openChats: () => void;
  openDecisions: (profile: string) => void;
  back: () => void;
  rename: (profile: string, name: string) => Promise<boolean>;
  remove: (profile: string) => void;
  hide: (profile: string) => void;
  move: (profile: string, direction: "left" | "right") => void;
  openSettings: (profile: string, kind: AgentSettingsKind) => void;
  addAgent: () => void;
  openViewSettings: () => void;
}

export interface LongPressHandlers {
  onPointerDown: (event: ReactPointerEvent) => void;
  onPointerMove: (event: ReactPointerEvent) => void;
  onPointerUp: () => void;
  onPointerCancel: () => void;
  onPointerLeave: () => void;
  onContextMenu: (event: { preventDefault: () => void }) => void;
}

export function labelOf(tabs: readonly AgentTabConfig[], profile: string): string {
  return tabs.find((tab) => tab.profile === profile)?.label || (profile ? profile : MAIN_AGENT_TAB.label);
}

/** «09:20», «вчера», «24.09» — как в списке мессенджера. */
export function formatListTime(seconds: number | null | undefined, now = Date.now()): string {
  if (!seconds) return "";
  const date = new Date(seconds * 1000);
  const today = new Date(now);
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  if (date.getTime() >= startOfToday) {
    return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  }
  if (date.getTime() >= startOfToday - 86_400_000) return "вчера";
  return date.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
}

export function AgentName({ label }: { label: string }) {
  const { prefix, main } = splitAgentName(label);
  return prefix ? <><span className="k-nm-pre">{prefix}</span> {main}</> : <>{main}</>;
}

/* ------------------------------------------------------------------ */

export function ReplyNotice({
  run,
  offset,
  label,
  status,
  onOpen,
  onDismiss,
}: {
  run: ChatRun;
  offset: number;
  label: string;
  status: AgentStatus;
  onOpen: () => void;
  onDismiss: () => void;
}) {
  // Без глагола: у агента нет рода, «Нюра ответил» резало глаз (п. 5).
  const text = `${label}${run.title ? ` · ${run.title}` : ""}`;
  return (
    <div className="k-notice" role="status" aria-live="polite" data-agent-notice style={offset ? { top: offset + 8 } : undefined}>
      <button type="button" className="k-notice__open" onClick={onOpen} aria-label={`Новый ответ · ${text}. Открыть`}>
        <AgentFace label={label} status={{ ...status, unread: true }} />
        <span className="k-notice__text" aria-hidden>
          <small>Новый ответ · открыть</small>
          <b>{text}</b>
        </span>
      </button>
      <button type="button" className="k-ib" aria-label="Скрыть уведомление" onClick={onDismiss}>
        <X size={18} aria-hidden className="k-icon" />
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ */

interface SummaryLike {
  lastActive: number | null;
  latestTitle: string | null;
}

function statusLine(status: AgentStatus, current: boolean, summary: SummaryLike | undefined): ReactNode {
  const tail = current ? " · открыт" : "";
  if (status.decisions > 0) {
    return <><span className="k-tag k-tag--dec">{status.decisions}</span><span className="k-st">{decisionsWaitPhrase(status.decisions).replace(/^\d+ /, "")}{tail}</span></>;
  }
  if (status.error) return <><span className="k-tag k-tag--err">!</span><span className="k-st">Работа не завершилась{tail}</span></>;
  if (status.unread) return <><span className="k-tag k-tag--new">Новый ответ</span><span className="k-st">{summary?.latestTitle ?? ""}</span></>;
  if (status.running > 0) return <span className="k-st"><span className="k-live">Работает</span>{tail}</span>;
  if (status.queued > 0) return <span className="k-st">В очереди, начнёт сам{tail}</span>;
  if (status.stale) return <span className="k-st">Статус требует проверки{tail}</span>;
  const when = formatListTime(summary?.lastActive);
  return <span className="k-st">{current ? `Открыт${when ? ` · ${when}` : ""}` : when || (summary ? "Разговоров пока нет" : "")}</span>;
}

interface AllAgentsSheetProps {
  tabs: AgentTabConfig[];
  hidden: Set<string>;
  activeId: string;
  mode: AgentsMobileMode;
  statusOf: (profile: string) => AgentStatus;
  conversations: Record<string, SummaryLike | undefined>;
  onClose: () => void;
  onOpen: (profile: string) => void;
  onMenu: (profile: string) => void;
  onAdd: () => void;
  onViewSettings: () => void;
}

export function AllAgentsSheet({ tabs, hidden, activeId, mode, statusOf, conversations, onClose, onOpen, onMenu, onAdd, onViewSettings }: AllAgentsSheetProps) {
  const titleId = useId();
  const [query, setQuery] = useState("");
  const found = filterAgentTabs(tabs, query);
  const statuses = new Map(found.map((tab) => [tab.profile, statusOf(tab.profile)]));
  const { waiting, rest } = groupByAttention(found, statuses);
  const row = (tab: AgentTabConfig) => {
    const current = tab.profile === activeId;
    const status = statusOf(tab.profile);
    return (
      <div key={tab.profile} className={cn("k-row", current && "is-current")}>
        <button
          type="button"
          className="k-row__main"
          aria-current={current ? "page" : undefined}
          data-agent-list-item={tab.profile}
          onClick={() => onOpen(tab.profile)}
        >
          <AgentFace label={tab.label} status={status} />
          <span className="k-row__x">
            <b><AgentName label={tab.label} /></b>
            <small>
              {statusLine(status, current, conversations[tab.profile])}
              {hidden.has(tab.profile) && <span className="k-st">· вкладка скрыта</span>}
            </small>
          </span>
        </button>
        <button type="button" className="k-more" aria-label={`Действия агента «${tab.label}»`} onClick={() => onMenu(tab.profile)}>
          <MoreHorizontal size={20} aria-hidden className="k-icon" />
        </button>
      </div>
    );
  };
  return (
    <AgentSheet
      titleId={titleId}
      title={<>Агенты <span className="k-sheet__n">{tabs.length}</span></>}
      onClose={onClose}
      headExtra={
        <button type="button" className="k-head-btn" onClick={onAdd}>
          <UserRoundPlus size={18} aria-hidden className="k-icon" />Добавить
        </button>
      }
      beforeBody={
        <label className="k-search">
          <Search size={18} aria-hidden className="k-icon" />
          <span className="sr-only">Найти агента</span>
          <input
            className="k-search__input"
            type="search"
            value={query}
            placeholder="Найти агента"
            autoComplete="off"
            enterKeyHint="go"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && found[0]) onOpen(found[0].profile);
            }}
          />
        </label>
      }
      afterBody={<ViewHint mode={mode} onClick={onViewSettings} />}
    >
      {found.length === 0 && <p className="k-empty" role="status">Нет агента с таким именем</p>}
      {waiting.length > 0 && <div className="k-sec">Ждут вас</div>}
      {waiting.map(row)}
      {waiting.length > 0 && rest.length > 0 && <div className="k-sec">Остальные</div>}
      {rest.map(row)}
    </AgentSheet>
  );
}

function ViewHint({ mode, onClick }: { mode: AgentsMobileMode; onClick: () => void }) {
  return (
    <button type="button" className="k-view-hint" onClick={onClick}>
      <SlidersHorizontal size={16} aria-hidden className="k-icon" />
      <span>Вид: <b>{mode === "tabs" ? "вкладки" : "список"}</b></span>
      <span className="k-view-hint__go">Изменить<ChevronRight size={16} aria-hidden className="k-icon" /></span>
    </button>
  );
}

/* ------------------------------------------------------------------ */

interface ActivitySheetProps {
  runs: ChatRun[];
  failed: ChatRun[];
  tabs: AgentTabConfig[];
  statusOf: (profile: string) => AgentStatus;
  onClose: () => void;
  onOpenChat: (profile: string, sessionId: string) => void;
  onDecide: (profile: string) => void;
}

function sinceText(run: ChatRun): string {
  const started = Number(run.started_at) || 0;
  if (!started) return "";
  const minutes = Math.max(0, Math.round((Date.now() / 1000 - started) / 60));
  if (minutes < 1) return "только что";
  if (minutes < 60) return `${minutes} мин`;
  return new Date(started * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

export function ActivitySheet({ runs, failed, tabs, statusOf, onClose, onOpenChat, onDecide }: ActivitySheetProps) {
  const titleId = useId();
  const name = (profile: string) => labelOf(tabs, profile);
  const waiting = [...new Set(runs.filter((run) => run.status === "waiting_decision").map((run) => run.profile))];
  const running = runs.filter((run) => run.status === "running");
  const queued = runs.filter((run) => run.status === "queued");
  const stale = runs.filter((run) => run.status === "stale");
  const count = activityCount(tabs.map((tab) => statusOf(tab.profile)));
  const work = (key: string, profile: string, question: ReactNode, state: ReactNode, button: ReactNode) => (
    <div key={key} className="k-work">
      <AgentFace label={name(profile)} status={statusOf(profile)} />
      <span className="k-work__x">
        <b>{name(profile)}</b>
        <span className="k-work__q">{question}</span>
        <span className="k-work__s">{state}</span>
      </span>
      {button}
    </div>
  );
  const open = (run: ChatRun) => (
    <button type="button" className="k-work__open" onClick={() => onOpenChat(run.profile, run.session_id)}>Открыть</button>
  );
  const empty = !waiting.length && !running.length && !queued.length && !stale.length && !failed.length;
  return (
    <AgentSheet titleId={titleId} title={<>Идут работы {count > 0 && <span className="k-sheet__n">{count}</span>}</>} onClose={onClose}>
      {empty && <p className="k-empty">Сейчас агенты ничего не выполняют. Когда работа начнётся, кольцо в полосе оживёт.</p>}
      {waiting.length > 0 && <div className="k-sec">Ждут вашего решения</div>}
      {waiting.map((profile) => {
        const n = statusOf(profile).decisions;
        const first = runs.find((run) => run.profile === profile && run.status === "waiting_decision");
        return work(
          `decision:${profile}`,
          profile,
          first?.title || "Отправка ждёт разрешения",
          <><span className="k-tag k-tag--dec">{n}</span>{pluralRu(n, "решение ждёт", "решения ждут", "решений ждут")} вашего ответа</>,
          <button type="button" className="k-work__open is-decision" onClick={() => onDecide(profile)}>Решить</button>,
        );
      })}
      {running.length > 0 && <div className="k-sec">Выполняются</div>}
      {running.map((run) => work(
        run.message_id,
        run.profile,
        run.user_message?.content ? `Запрос: «${run.user_message.content}»` : run.title || "Разговор",
        <><span className="k-live">Работает</span>{[sinceText(run), run.channel].filter(Boolean).map((part) => ` · ${part}`).join("")}</>,
        open(run),
      ))}
      {queued.length > 0 && <div className="k-sec">В очереди</div>}
      {queued.map((run) => work(run.message_id, run.profile, run.title || run.user_message?.content || "Разговор", "Начнёт сам, когда освободится место", open(run)))}
      {stale.length > 0 && <div className="k-sec">Требуют проверки</div>}
      {stale.map((run) => work(run.message_id, run.profile, run.title || run.user_message?.content || "Разговор", "Не удалось подтвердить, идёт ли работа", open(run)))}
      {failed.length > 0 && <div className="k-sec">Не завершились</div>}
      {failed.map((run) => work(
        run.message_id,
        run.profile,
        run.title || run.user_message?.content || "Разговор",
        <><span className="k-tag k-tag--err">!</span>{run.failure?.message ? run.failure.message.slice(0, 160) : "Работа не завершилась. Откройте чат, чтобы повторить."}</>,
        open(run),
      ))}
    </AgentSheet>
  );
}

/* ------------------------------------------------------------------ */

interface AgentMenuSheetProps {
  tab: AgentTabConfig;
  index: number;
  count: number;
  hidden: boolean;
  mode: AgentsMobileMode;
  pinned: boolean;
  status: AgentStatus;
  lastActive: number | null;
  onClose: () => void;
  onOpen: () => void;
  onNewChat: () => void;
  actions: AgentsMobileActions;
}

export function AgentMenuSheet({ tab, index, count, hidden, mode, pinned, status, lastActive, onClose, onOpen, onNewChat, actions }: AgentMenuSheetProps) {
  const titleId = useId();
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(tab.label);
  const [saving, setSaving] = useState(false);
  const main = tab.profile === MAIN_AGENT_TAB.profile;
  const settings = (kind: AgentSettingsKind) => () => { onClose(); actions.openSettings(tab.profile, kind); };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim() || saving) return;
    setSaving(true);
    const ok = await actions.rename(tab.profile, name.trim());
    setSaving(false);
    if (ok) onClose();
  };
  const subtitle = status.decisions > 0
    ? `Ждёт ${status.decisions} ${pluralRu(status.decisions, "решение", "решения", "решений")}`
    : status.error ? "Последняя работа не завершилась"
      : status.running > 0 ? "Работает"
        : lastActive ? `Последний разговор ${formatListTime(lastActive)}` : "Разговоров пока нет";
  const item = (icon: ReactNode, text: string, onClick: () => void, options: { danger?: boolean; disabled?: boolean } = {}) => (
    <button type="button" className={cn(options.danger && "is-danger")} disabled={options.disabled} onClick={onClick}>
      {icon}{text}
    </button>
  );
  const where = mode === "tabs" ? "в полосе" : "в списке";
  return (
    <AgentSheet
      titleId={titleId}
      title="Действия агента"
      onClose={onClose}
      beforeBody={
        <>
          <div className="k-agent-head">
            <AgentFace label={tab.label} status={status} large />
            <span style={{ flex: 1, minWidth: 0, lineHeight: 1.3 }}>
              <b>{tab.label}</b>
              <small>{subtitle}</small>
            </span>
          </div>
          <div className="k-quick">
            <button type="button" className="k-btn" onClick={onOpen}>
              <MessageSquare size={18} aria-hidden className="k-icon" />Открыть
            </button>
            <button type="button" className="k-btn" onClick={onNewChat}>
              <MessageSquarePlus size={18} aria-hidden className="k-icon" />Новый чат
            </button>
          </div>
        </>
      }
    >
      {renaming ? (
        <form className="k-rename" onSubmit={submit}>
          <label htmlFor={`${titleId}-name`} className="sr-only">Новое имя агента «{tab.label}»</label>
          <input id={`${titleId}-name`} autoFocus required value={name} onChange={(event) => setName(event.target.value)} />
          <button type="submit" className="k-ib" aria-label="Сохранить имя" disabled={!name.trim() || saving}>
            <Check size={18} aria-hidden className="k-icon" />
          </button>
          <button type="button" className="k-ib" aria-label="Отменить переименование" onClick={() => setRenaming(false)}>
            <X size={18} aria-hidden className="k-icon" />
          </button>
        </form>
      ) : (
        <div className="k-menu">
          {item(<Pencil size={18} aria-hidden className="k-icon" />, "Переименовать", () => { setName(tab.label); setRenaming(true); })}
          {item(<FileText size={18} aria-hidden className="k-icon" />, "Роль и поведение", settings("role"))}
          {item(<Cpu size={18} aria-hidden className="k-icon" />, "Модель", settings("model"))}
          {item(<Volume2 size={18} aria-hidden className="k-icon" />, "Голос", settings("voice"))}
          {item(<Package size={18} aria-hidden className="k-icon" />, "Навыки", settings("skills"))}
          {item(<Clock size={18} aria-hidden className="k-icon" />, "Расписание", settings("schedule"))}
          <hr />
          {mode === "tabs" && item(
            pinned ? <PinOff size={18} aria-hidden className="k-icon" /> : <Pin size={18} aria-hidden className="k-icon" />,
            pinned ? `Открепить ${where}` : `Закрепить ${where}`,
            () => { togglePinnedAgent(tab.profile); onClose(); },
          )}
          {item(<ArrowLeft size={18} aria-hidden className="k-icon" />, mode === "tabs" ? "Сдвинуть влево" : "Выше в списке", () => { actions.move(tab.profile, "left"); onClose(); }, { disabled: index <= 0 })}
          {item(<ArrowRight size={18} aria-hidden className="k-icon" />, mode === "tabs" ? "Сдвинуть вправо" : "Ниже в списке", () => { actions.move(tab.profile, "right"); onClose(); }, { disabled: index < 0 || index >= count - 1 })}
          {!main && !hidden && item(<EyeOff size={18} aria-hidden className="k-icon" />, "Скрыть вкладку", () => { actions.hide(tab.profile); onClose(); })}
          {!main && <hr />}
          {!main && item(<Trash2 size={18} aria-hidden className="k-icon" />, "Удалить агента…", () => { onClose(); actions.remove(tab.profile); }, { danger: true })}
        </div>
      )}
    </AgentSheet>
  );
}

/* ------------------------------------------------------------------ */

interface AgentListHomeProps {
  tabs: AgentTabConfig[];
  statusOf: (profile: string) => AgentStatus;
  conversations: Record<string, { latestTitle: string | null; lastActive: number | null } | undefined>;
  runs: ChatRun[];
  onOpen: (profile: string) => void;
  longPress: (profile: string) => LongPressHandlers;
  onViewSettings: () => void;
}

/** Главный экран режима «Список агентов»: кто ждёт вас — сверху. */
export function AgentListHome({ tabs, statusOf, conversations, runs, onOpen, longPress, onViewSettings }: AgentListHomeProps) {
  const [query, setQuery] = useState("");
  const found = filterAgentTabs(tabs, query);
  const statuses = new Map(found.map((tab) => [tab.profile, statusOf(tab.profile)]));
  const { waiting, rest } = groupByAttention(found, statuses);
  const line = (tab: AgentTabConfig, status: AgentStatus) => {
    const summary = conversations[tab.profile];
    const busy = runs.find((run) => run.profile === tab.profile && isRunBusy(run));
    switch (attentionOf(status)) {
      case "decision":
        return <><span className="k-tag k-tag--dec">{status.decisions}</span><span className="k-st">{decisionsWaitPhrase(status.decisions).replace(/^\d+ /, "")}</span></>;
      case "error":
        return <><span className="k-tag k-tag--err">!</span><span className="k-st">Работа не завершилась</span></>;
      case "unread":
        return <><span className="k-tag k-tag--new">Новый ответ</span><span className="k-st" style={{ color: "var(--neo-text-primary)", fontWeight: 550 }}>{summary?.latestTitle ?? ""}</span></>;
      default:
        if (status.running > 0) return <span className="k-st"><span className="k-live">Работает</span>{busy?.title ? ` · ${busy.title}` : ""}</span>;
        if (status.queued > 0) return <span className="k-st">В очереди, начнёт сам</span>;
        return <span className="k-st">{summary?.latestTitle ?? (summary ? "Разговоров пока нет" : "")}</span>;
    }
  };
  const row = (tab: AgentTabConfig) => {
    const status = statusOf(tab.profile);
    const when = formatListTime(Math.max(conversations[tab.profile]?.lastActive ?? 0, status.lastActivity));
    return (
      <button
        key={tab.profile}
        type="button"
        className="k-lrow"
        data-agent-list-item={tab.profile}
        aria-label={`${tab.label}${statusSpeech(status)}${when ? `. ${when}` : ""}`}
        {...longPress(tab.profile)}
        onClick={() => onOpen(tab.profile)}
      >
        <AgentFace label={tab.label} status={status} large />
        <span className="k-lrow__x" aria-hidden>
          <span className="k-lrow__l1">
            <span className="k-lrow__nm"><AgentName label={tab.label} /></span>
            <span className="k-lrow__tm">{when}</span>
          </span>
          <span className="k-lrow__l2">{line(tab, status)}</span>
        </span>
      </button>
    );
  };
  return (
    <div className="k-list" data-agent-list-home>
      <div className="k-list__scroll">
        <label className="k-search">
          <Search size={18} aria-hidden className="k-icon" />
          <span className="sr-only">Найти агента</span>
          <input className="k-search__input" type="search" value={query} placeholder="Найти агента" autoComplete="off" onChange={(event) => setQuery(event.target.value)} />
        </label>
        {found.length === 0 && <p className="k-empty" role="status" style={{ padding: "8px 20px" }}>Нет агента с таким именем</p>}
        {waiting.length > 0 && <div className="k-lsec">Ждут вас <span>· {waiting.length}</span></div>}
        {waiting.length > 0 && <div className="k-lrows">{waiting.map(row)}</div>}
        {waiting.length > 0 && rest.length > 0 && <div className="k-lsec">Остальные <span>· {rest.length}</span></div>}
        {rest.length > 0 && <div className="k-lrows">{rest.map(row)}</div>}
        <ViewHint mode="list" onClick={onViewSettings} />
      </div>
    </div>
  );
}
