import type { Ref } from "react";
import { useStore } from "@nanostores/react";
import { createPortal } from "react-dom";
import { Link } from "react-router";
import { X } from "lucide-react";
import { $chatRuns, $chatRunsReachable, $chatRunsUnavailableProfiles, $dismissedRunToasts, $unreadChatRuns, dismissRunToasts, hasUnreadResponse, isRunBusy, chatRunHref, waitingDecisionRuns, type ChatRun } from "@/lib/chat-runs";
import { agentChatHref, type AgentTabConfig } from "@/lib/agent-tabs";
import { cn } from "@/lib/utils";

/** Бейдж на вкладке агента: общий индикатор по всем его чатам. «В работе» и
 *  «Ответ готов» — разные состояния и показываются одновременно: готовый
 *  ответ одной сессии не прячется за работой другой. */
export function AgentRunBadge({ profile, label = profile || "Корра", onNavigate, onShowWaiting }: { profile: string; label?: string; onNavigate?: () => void; onShowWaiting?: () => void }) {
  const runs = useStore($chatRuns);
  const unread = useStore($unreadChatRuns);
  const reachable = useStore($chatRunsReachable);
  const unavailable = useStore($chatRunsUnavailableProfiles);
  const busy = runs.filter(run => run.profile === profile && isRunBusy(run));
  const waiting = waitingDecisionRuns(busy);
  const waitingTitle = `${label} ждёт вас · ${waiting.map(run => run.title || (run.decision_fallback ? run.decision_reason || "Исходный чат недоступен" : "Чат без названия")).join("; ")}`;
  const ready = unread.some(run => run.profile === profile);
  const stale = runs.some(run => run.profile === profile && run.status === "stale");
  const uncertain = unavailable.includes(profile) || (reachable === false && unavailable.length === 0 && busy.length > 0) || stale;
  return <>
    {waiting.length > 1
      ? <button type="button" data-waiting-link title={waitingTitle} aria-label={`${waitingTitle}. Открыть список ожидающих чатов`}
          className="inline-flex min-h-9 items-center justify-center rounded-full bg-[var(--neo-accent)] px-2 text-xs text-[var(--neo-accent-foreground)]"
          onClick={() => { onNavigate?.(); onShowWaiting?.(); }}>Ждёт вас · {waiting.length}</button>
      : waiting.length === 1
      ? <Link
          data-waiting-link
          to={chatRunHref(waiting[0])}
          onClick={onNavigate}
          title={waitingTitle}
          aria-label={`${waitingTitle}. ${!waiting[0].decision_fallback ? "Открыть чат" : "Открыть решения"}`}
          className="inline-flex min-h-9 items-center justify-center rounded-full bg-[var(--neo-accent)] px-2 text-xs text-[var(--neo-accent-foreground)]"
        >Ждёт вас</Link>
      : uncertain
        ? <span className="text-xs" title={stale ? "Статус задачи требует проверки" : "Не удалось обновить статус работы"}>?<span className="sr-only"> Статус требует проверки</span></span>
        : busy.length > 0 && <span className="inline-flex min-w-5 justify-center rounded-full bg-[var(--neo-accent)] px-1 text-xs text-[var(--neo-accent-foreground)]" title={busy[0].status === "queued" ? "В очереди" : `В работе: ${busy.length}`}>
          <span aria-hidden>{busy.length}</span><span className="sr-only">{busy[0].status === "queued" ? "В очереди" : "В работе"}</span>
        </span>}
    {ready && <span className="inline-flex" title="Есть непрочитанный ответ"><span aria-hidden className="size-2 rounded-full bg-[var(--neo-accent-line)]" /><span className="sr-only">Ответ готов</span></span>}

  </>;
}

/** Отметка «Новый ответ» у конкретного чата в списке слева. Связь — по
 *  profile + session, поэтому два разговора одного агента различаются. */
export function ChatUnreadMark({ profile, sessionId, className = "" }: { profile: string; sessionId: string | null | undefined; className?: string }) {
  const unread = useStore($unreadChatRuns);
  if (!hasUnreadResponse(unread, profile, sessionId)) return null;
  return <span data-unread-response className={`inline-flex shrink-0 items-center gap-1 rounded-full bg-[var(--neo-accent)] px-2 py-0.5 text-xs font-medium text-[var(--neo-accent-foreground)] ${className}`} aria-label="Новый ответ, не прочитан">
    <span aria-hidden className="size-1.5 rounded-full bg-[var(--neo-accent-foreground)]" />Новый ответ
  </span>;
}

export function SessionRunActivity({ tabs, panelRef }: { tabs: AgentTabConfig[]; panelRef?: Ref<HTMLDetailsElement> }) {
  const runs = useStore($chatRuns);
  const unread = useStore($unreadChatRuns);
  const dismissed = useStore($dismissedRunToasts);
  const reachable = useStore($chatRunsReachable);
  const unavailable = useStore($chatRunsUnavailableProfiles);
  const name = (profile: string) => tabs.find(tab => tab.profile === profile)?.label || profile || "Корра";
  const busy = runs.filter(run => isRunBusy(run) && run.status !== "waiting_decision");
  const waiting = waitingDecisionRuns(runs);
  const stale = runs.filter(run => run.status === "stale");
  const visible = [...waiting, ...busy, ...stale];
  const toast = unread.filter(run => !dismissed.includes(run.message_id));
  return <>
    {(visible.length > 0 || unavailable.length > 0) && <details ref={panelRef} className="relative shrink-0" onKeyDown={event => {
      if (event.key === "Escape") { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); }
    }} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.open = false; }}>
      <summary className="neo-tab cursor-pointer list-none rounded-full px-3 py-2 text-sm" aria-label={unavailable.length ? "Работа агентов: часть профилей недоступна" : `Работа агентов: ${visible.length}`}>
        {unavailable.length ? "Работа агентов · требует проверки" : `В работе: ${visible.length}`}
      </summary>
      <div className="absolute right-0 top-full z-40 mt-2 max-h-[50dvh] w-[min(340px,calc(100vw-32px))] overflow-y-auto rounded-2xl bg-[var(--neo-surface)] p-3 shadow-[var(--neo-depth-3)]" aria-label="Работающие чаты">
        <p className="mb-2 text-sm">{unavailable.length ? "Доступная часть активности агентов" : reachable === false ? "Не удалось обновить статус. Ниже — последнее известное состояние." : "Работа агентов"}</p>
        {unavailable.length > 0 && <p role="status" className="mb-2 text-sm">Недоступны профили: {unavailable.map(name).join(", ")}. Их активность и решения неизвестны.</p>}
        {visible.map(run => <Link key={run.message_id} to={chatRunHref(run)} className="mb-2 block rounded-xl p-2 text-sm focus-visible:outline" onClick={event => { const panel = event.currentTarget.closest("details"); if (panel) panel.open = false; }}>
          <span className="block font-medium">{name(run.profile)}{run.title ? ` · ${run.title}` : ""}</span>
          <span className="block break-words text-xs text-muted-foreground">{run.decision_fallback
            ? run.decision_reason || "Исходный чат недоступен. Ответьте в центре решений."
            : `Запрос: ${run.user_message.content}`}</span>
          {(run.channel || run.started_at) && <span className="block text-xs text-muted-foreground">{[run.channel, run.started_at ? new Date(run.started_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : null].filter(Boolean).join(" · ")}</span>}
          <span className="block text-xs">{unavailable.includes(run.profile) || (reachable === false && unavailable.length === 0) || run.status === "stale" ? "Статус требует проверки" : run.status === "queued" ? "В очереди — начнёт автоматически" : run.status === "waiting_decision" ? "Ожидает вашего решения" : "Выполняется"} · {run.decision_fallback ? "Открыть решения" : "Открыть чат"}</span>
        </Link>)}
      </div>
    </details>}
    {toast.length > 0 && createPortal(<RunToast runs={toast} name={name} />, document.body)}
  </>;
}

/** Уведомление показывает один ответ, остальные считает «+N». */
const RUN_TOAST_ITEMS = 1;

/**
 * Уведомление «агент ответил».
 *
 * Всегда одна полоса высотой в одну строку у правого края верхней
 * безопасной области — поверх пустого места шапки, а не поверх полосы
 * вкладок. Прежняя карточка на десктопе занимала 110 px и закрывала «Все
 * агенты» и «+» в правом конце полосы: при восьми профилях на 1280×800
 * переключить агента было нельзя, пока не скрыть уведомление (K21-295;
 * на телефоне то же случилось 21.09, скриншот владельца). Остальные
 * готовые ответы не исчезают — их считает «+N», а долговременный сигнал
 * остаётся у чата в списке (ChatUnreadMark).
 *
 * Автоперехода и перехвата фокуса нет.
 */
function RunToast({ runs, name }: { runs: ChatRun[]; name: (profile: string) => string }) {
  const shown = runs.slice(0, RUN_TOAST_ITEMS);
  const rest = runs.length - shown.length;
  return <div
    className={cn(
      "fixed z-50 rounded-2xl bg-[var(--neo-surface)] shadow-[var(--neo-depth-2)]",
      "right-[max(0.75rem,env(safe-area-inset-right,0px))] top-[max(0.5rem,env(safe-area-inset-top,0px))]",
      "flex max-w-[min(52vw,24rem)] items-center gap-1 p-1",
    )}
    role="status" aria-live="polite" data-run-toast>
    <ul className="m-0 min-w-0 list-none p-0">
      {shown.map(run => <li key={run.message_id} className="min-w-0">
        <Link className="flex min-h-[44px] items-center gap-2 rounded-lg px-2 text-sm hover:shadow-[var(--neo-inset-compact)]" to={agentChatHref(run.profile, run.session_id)} title={`${name(run.profile)}: ${run.user_message.content}`}>
          <span aria-hidden className="size-2 shrink-0 rounded-full bg-[var(--neo-accent-line)]" />
          {/* Без глагола: у агента нет рода, «Нюра ответил» резало глаз. */}
          <span className="min-w-0 flex-1 truncate">Новый ответ · {name(run.profile)}</span>
        </Link>
      </li>)}
    </ul>
    {rest > 0 && <p data-run-toast-rest className="m-0 shrink-0 px-1 text-xs text-muted-foreground">
      <span aria-hidden>+{rest}</span>
      <span className="sr-only">И ещё {rest} — отмечены в списке чатов</span>
    </p>}
    {/* Цель пальца 44 px. */}
    <button type="button" aria-label="Скрыть уведомление" title="Скрыть уведомление"
      className="flex size-[44px] shrink-0 items-center justify-center text-muted-foreground"
      onClick={() => dismissRunToasts()}>
      <X aria-hidden className="size-4" />
    </button>
  </div>;
}
