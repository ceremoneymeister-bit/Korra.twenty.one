import { useStore } from "@nanostores/react";
import { createPortal } from "react-dom";
import { Link } from "react-router";
import { $chatRuns, $chatRunsReachable, $unreadChatRuns, isRunBusy } from "@/lib/chat-runs";
import { agentChatHref, type AgentTabConfig } from "@/lib/agent-tabs";

export function AgentRunBadge({ profile }: { profile: string }) {
  const runs = useStore($chatRuns);
  const unread = useStore($unreadChatRuns);
  const reachable = useStore($chatRunsReachable);
  const busy = runs.filter(run => run.profile === profile && isRunBusy(run));
  const ready = unread.some(run => run.profile === profile);
  if (reachable === false && busy.length) return <span className="text-xs" title="Статус работы не подтверждён: нет связи">Нет связи</span>;
  if (busy.length) return <span className="rounded-full bg-[var(--neo-accent)] px-2 py-0.5 text-xs text-[#1f1f1f]" aria-label="Агент работает">{busy.length > 1 ? `В работе: ${busy.length}` : busy[0].status === "queued" ? "В очереди" : "В работе"}</span>;
  return ready ? <span className="text-xs" aria-label="Есть непрочитанный ответ">Ответ готов</span> : null;
}

export function SessionRunActivity({ tabs }: { tabs: AgentTabConfig[] }) {
  const runs = useStore($chatRuns);
  const unread = useStore($unreadChatRuns);
  const reachable = useStore($chatRunsReachable);
  const name = (profile: string) => tabs.find(tab => tab.profile === profile)?.label || profile || "Корра";
  const busy = runs.filter(isRunBusy);
  return <>
    {busy.length > 0 && <div className="flex shrink-0 flex-wrap items-center gap-3 py-2 text-sm" aria-label="Сейчас в работе">
      <span className="text-muted-foreground">{reachable === false ? "Проверяем связь с агентами" : "Сейчас в работе"}</span>
      {busy.map(run => <Link key={run.message_id} to={agentChatHref(run.profile, run.session_id)} className="neo-tab rounded-full px-3 py-2" title={run.user_message.content}>
        {name(run.profile)} · {run.user_message.content.slice(0, 35)}{run.status === "queued" ? " — в очереди, начнёт автоматически" : ""}
      </Link>)}
    </div>}
    {unread.length > 0 && createPortal(<div className="fixed bottom-5 right-5 z-50 max-w-sm rounded-2xl bg-[var(--neo-surface)] p-4 shadow-[var(--neo-depth-2)]" role="status">
      {unread.slice(0, 3).map(run => <Link className="block py-1 text-sm" key={run.message_id} to={agentChatHref(run.profile, run.session_id)}>{name(run.profile)} ответил · Открыть чат</Link>)}
      <button className="mt-2 text-xs text-muted-foreground" onClick={() => $unreadChatRuns.set([])}>Скрыть уведомления</button>
    </div>, document.body)}
  </>;
}
