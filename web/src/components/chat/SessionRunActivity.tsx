import { useStore } from "@nanostores/react";
import { createPortal } from "react-dom";
import { Link } from "react-router";
import { $chatRuns, $chatRunsReachable, $dismissedRunToasts, $unreadChatRuns, dismissRunToasts, hasUnreadResponse, isRunBusy } from "@/lib/chat-runs";
import { agentChatHref, type AgentTabConfig } from "@/lib/agent-tabs";

/** Бейдж на вкладке агента: общий индикатор по всем его чатам. «В работе» и
 *  «Ответ готов» — разные состояния и показываются одновременно: готовый
 *  ответ одной сессии не прячется за работой другой. */
export function AgentRunBadge({ profile }: { profile: string }) {
  const runs = useStore($chatRuns);
  const unread = useStore($unreadChatRuns);
  const reachable = useStore($chatRunsReachable);
  const busy = runs.filter(run => run.profile === profile && isRunBusy(run));
  const ready = unread.some(run => run.profile === profile);
  const stale = runs.some(run => run.profile === profile && run.status === "stale");
  const uncertain = (reachable === false && busy.length > 0) || stale;
  return <>
    {uncertain
      ? <span className="text-xs" title={stale ? "Статус задачи требует проверки" : "Не удалось обновить статус работы"}>?<span className="sr-only"> Статус требует проверки</span></span>
      : busy.length > 0 && <span className="inline-flex min-w-5 justify-center rounded-full bg-[var(--neo-accent)] px-1 text-xs text-[#1f1f1f]" title={busy[0].status === "queued" ? "В очереди" : busy[0].status === "waiting_decision" ? "Ожидает решения" : `В работе: ${busy.length}`}>
      <span aria-hidden>{busy.length}</span><span className="sr-only">{busy[0].status === "queued" ? "В очереди" : busy[0].status === "waiting_decision" ? "Ожидает решения" : "В работе"}</span>
    </span>}
    {ready && <span className="inline-flex" title="Есть непрочитанный ответ"><span aria-hidden className="size-2 rounded-full bg-[var(--neo-accent-line)]" /><span className="sr-only">Ответ готов</span></span>}

  </>;
}

/** Отметка «Новый ответ» у конкретного чата в списке слева. Связь — по
 *  profile + session, поэтому два разговора одного агента различаются. */
export function ChatUnreadMark({ profile, sessionId, className = "" }: { profile: string; sessionId: string | null | undefined; className?: string }) {
  const unread = useStore($unreadChatRuns);
  if (!hasUnreadResponse(unread, profile, sessionId)) return null;
  return <span data-unread-response className={`inline-flex shrink-0 items-center gap-1 rounded-full bg-[var(--neo-accent)] px-2 py-0.5 text-xs font-medium text-[#1f1f1f] ${className}`} aria-label="Новый ответ, не прочитан">
    <span aria-hidden className="size-1.5 rounded-full bg-[#1f1f1f]" />Новый ответ
  </span>;
}

export function SessionRunActivity({ tabs }: { tabs: AgentTabConfig[] }) {
  const runs = useStore($chatRuns);
  const unread = useStore($unreadChatRuns);
  const dismissed = useStore($dismissedRunToasts);
  const reachable = useStore($chatRunsReachable);
  const name = (profile: string) => tabs.find(tab => tab.profile === profile)?.label || profile || "Корра";
  const busy = runs.filter(isRunBusy);
  const stale = runs.filter(run => run.status === "stale");
  const visible = [...busy, ...stale];
  const toast = unread.filter(run => !dismissed.includes(run.message_id));
  return <>
    {visible.length > 0 && <details className="relative shrink-0" onKeyDown={event => {
      if (event.key === "Escape") { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); }
    }} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.open = false; }}>
      <summary className="neo-tab cursor-pointer list-none rounded-full px-3 py-2 text-sm" aria-label={`Работа агентов: ${visible.length}`}>
        В работе: {visible.length}
      </summary>
      <div className="absolute right-0 top-full z-40 mt-2 max-h-[50dvh] w-[min(340px,calc(100vw-32px))] overflow-y-auto rounded-2xl bg-[var(--neo-surface)] p-3 shadow-[var(--neo-depth-3)]" aria-label="Работающие чаты">
        <p className="mb-2 text-sm">{reachable === false ? "Не удалось обновить статус. Ниже — последнее известное состояние." : "Работа агентов"}</p>
        {visible.map(run => <Link key={run.message_id} to={agentChatHref(run.profile, run.session_id)} className="mb-2 block rounded-xl p-2 text-sm focus-visible:outline" onClick={event => { const panel = event.currentTarget.closest("details"); if (panel) panel.open = false; }}>
          <span className="block font-medium">{name(run.profile)}{run.title ? ` · ${run.title}` : ""}</span>
          <span className="block break-words text-xs text-muted-foreground">Запрос: {run.user_message.content}</span>
          {(run.channel || run.started_at) && <span className="block text-xs text-muted-foreground">{[run.channel, run.started_at ? new Date(run.started_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : null].filter(Boolean).join(" · ")}</span>}
          <span className="block text-xs">{reachable === false || run.status === "stale" ? "Статус требует проверки" : run.status === "queued" ? "В очереди — начнёт автоматически" : run.status === "waiting_decision" ? "Ожидает вашего решения" : "Выполняется"} · Открыть чат</span>
        </Link>)}
      </div>
    </details>}
    {/* Краткое уведомление — в верхней безопасной области, не над composer /
        Send / Stop / вложениями, без перехвата фокуса и автоперехода. Основной
        долговременный сигнал — отметка у чата в списке (ChatUnreadMark). */}
    {toast.length > 0 && createPortal(<div
      className="fixed left-3 right-3 top-16 z-50 rounded-2xl bg-[var(--neo-surface)] p-3 shadow-[var(--neo-depth-2)] sm:left-auto sm:max-w-sm lg:top-3"
      role="status" aria-live="polite" data-run-toast>
      <ul className="m-0 list-none p-0">
        {toast.slice(0, 3).map(run => <li key={run.message_id}>
          <Link className="flex min-h-11 items-center gap-2 rounded-lg px-2 text-sm hover:shadow-[var(--neo-inset-compact)]" to={agentChatHref(run.profile, run.session_id)} title={`${name(run.profile)}: ${run.user_message.content}`}>
            <span aria-hidden className="size-2 shrink-0 rounded-full bg-[var(--neo-accent-line)]" />
            <span className="min-w-0 flex-1 truncate">{name(run.profile)} ответил</span>
            <span className="shrink-0 text-xs text-muted-foreground">Открыть чат</span>
          </Link>
        </li>)}
      </ul>
      {toast.length > 3 && <p className="m-0 px-2 pt-1 text-xs text-muted-foreground">И ещё {toast.length - 3} — отмечены в списке чатов</p>}
      <button type="button" className="mt-1 min-h-11 px-2 text-xs text-muted-foreground" onClick={dismissRunToasts}>Скрыть уведомление</button>
    </div>, document.body)}
  </>;
}
