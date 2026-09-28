import { forwardRef, type ComponentProps } from "react";
import { Activity } from "lucide-react";

import { agentMonogram, type AgentStatus } from "@/lib/agent-status";
import { cn } from "@/lib/utils";

import "./agents-mobile.css";

interface AgentFaceInnerProps {
  label: string;
  status?: AgentStatus;
  large?: boolean;
}

/** Лицо агента со знаками. Всё внутри скрыто от диктора: подпись у кнопки. */
function FaceInner({ label, status, large }: AgentFaceInnerProps) {
  const box = large ? 52 : 44;
  const ring = status ? (status.running > 0 ? "run" : status.queued > 0 ? "queued" : null) : null;
  return (
    <>
      <span className="k-av__face" aria-hidden>{agentMonogram(label)}</span>
      {ring && (
        <svg
          className={cn("k-av__ring", ring === "run" ? "k-av__ring--run" : "k-av__ring--queued")}
          viewBox={`0 0 ${box} ${box}`}
          aria-hidden
          data-agent-ring={ring}
        >
          <circle
            cx={box / 2}
            cy={box / 2}
            r={box / 2 - 1.4}
            pathLength={100}
            strokeDasharray={ring === "run" ? "66 34" : undefined}
          />
        </svg>
      )}
      {status && status.decisions > 0 ? (
        <span className="k-bdg k-bdg--dec" aria-hidden data-agent-sign="decision">{status.decisions}</span>
      ) : status?.error ? (
        <span className="k-bdg k-bdg--err" aria-hidden data-agent-sign="error">!</span>
      ) : status?.unread ? (
        <span className="k-bdg k-bdg--new" aria-hidden data-agent-sign="unread" />
      ) : null}
    </>
  );
}

export interface AgentFaceProps extends Omit<ComponentProps<"button">, "children"> {
  label: string;
  status?: AgentStatus;
  large?: boolean;
  active?: boolean;
}

/** Аватар-кнопка: кольцо — работает, пунктир — в очереди, точка — новый
 *  ответ, янтарное число — решения, «!» — ошибка. */
export const AgentFaceButton = forwardRef<HTMLButtonElement, AgentFaceProps>(function AgentFaceButton(
  { label, status, large, active, className, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      className={cn("k-av", large && "k-av--lg", active && "is-active", className)}
      {...props}
    >
      <FaceInner label={label} status={status} large={large} />
    </button>
  );
});

/** Тот же аватар без действия — в строках списков, где кнопка — вся строка. */
export function AgentFace({ label, status, large, active }: AgentFaceInnerProps & { active?: boolean }) {
  return (
    <span className={cn("k-av", large && "k-av--lg", active && "is-active")}>
      <FaceInner label={label} status={status} large={large} />
    </span>
  );
}

export interface ActivityRingProps extends Omit<ComponentProps<"button">, "children"> {
  count: number;
}

/** «Идут работы» — кольцо с числом вместо текста «В работе: 3». */
export const ActivityRing = forwardRef<HTMLButtonElement, ActivityRingProps>(function ActivityRing(
  { count, className, ...props },
  ref,
) {
  const label = count > 0 ? `Идут работы: ${count}. Показать список` : "Сейчас агенты ничего не выполняют";
  return (
    <button
      ref={ref}
      type="button"
      aria-label={label}
      title={label}
      className={cn("k-act", count === 0 && "is-idle", className)}
      {...props}
    >
      {count > 0 ? (
        <>
          <svg className="k-act__svg k-act__svg--spin" viewBox="0 0 36 36" aria-hidden>
            <circle className="k-act__track" cx="18" cy="18" r="15" />
            <circle className="k-act__arc" cx="18" cy="18" r="15" pathLength={100} strokeDasharray="32 68" />
          </svg>
          <span className="k-act__n" aria-hidden>{count}</span>
        </>
      ) : (
        <Activity size={18} aria-hidden className="k-icon" />
      )}
    </button>
  );
});
