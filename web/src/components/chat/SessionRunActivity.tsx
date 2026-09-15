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
  if (reachable === false && busy.length) return <span className="text-xs" title="Статус работы не подтверждён: нет связи">Нет связи</span>;
  return <>
    {busy.length > 0 && <span className="rounded-full bg-[var(--neo-accent)] px-2 py-0.5 text-xs text-[#1f1f1f]" aria-label="Агент работает">{busy.length > 1 ? `В работе: ${busy.length}` : busy[0].status === "queued" ? "В очереди" : "В работе"}</span>}
    {ready && <span className="inline-flex items-center gap-1 text-xs" aria-label="Есть непрочитанный ответ"><span aria-hidden className="size-2 rounded-full bg-[var(--neo-accent-line)]" />Ответ готов</span>}
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
  const toast = unread.filter(run => !dismissed.includes(run.message_id));
  return <>
    {busy.length > 0 && <div className="flex shrink-0 flex-wrap items-center gap-3 py-2 text-sm" aria-label="Сейчас в работе">
      <span className="text-muted-foreground">{reachable === false ? "Проверяем связь с агентами" : "Сейчас в работе"}</span>
      {busy.map(run => <Link key={run.message_id} to={agentChatHref(run.profile, run.session_id)} className="neo-tab rounded-full px-3 py-2" title={run.user_message.content}>
        {name(run.profile)} · {run.user_message.content.slice(0, 35)}{run.status === "queued" ? " — в очереди, начнёт автоматически" : ""}
      </Link>)}
    </div>}
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
