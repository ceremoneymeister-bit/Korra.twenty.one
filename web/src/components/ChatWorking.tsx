/**
 * "Agent is working" indicator.
 *
 * Replaces the empty assistant bubble. Before this, `useChatStream` created an
 * assistant message with empty content the moment you pressed send, and the
 * transcript rendered it as a bordered box with nothing in it — a small blank
 * square that sat there for the whole turn. The API server does not emit
 * `korra.tool.progress`, so there is genuinely nothing to show about *which*
 * tool is running; the honest maximum is "working, N seconds". We show that
 * instead of a blank box, and never claim more than we know.
 */

import { useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";

import { cn } from "@/lib/utils";

/** Что показываем и каким состоянием сферы. */
export type BusyKind = "working" | "reading" | "searching" | "listening";

const BUSY: Record<BusyKind, { label: string }> = {
  working: { label: "Агент работает" },
  reading: { label: "Читаю вложения" },
  searching: { label: "Ищу" },
  listening: { label: "Слушаю" },
};

export function ChatWorking({
  startedAt,
  state = "working",
}: {
  startedAt: number;
  state?: BusyKind;
}) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));

  return (
    <div
      className={cn(
        "inline-flex items-center gap-2.5 rounded-md pl-2 pr-3.5 py-1.5",
        "bg-card border border-border font-sans normal-case tracking-normal",
      )}
      role="status"
      aria-live="polite"
    >
      <LoaderCircle className="size-6 animate-spin text-primary" aria-hidden />
      <span className="text-sm text-muted-foreground">
        {(BUSY[state] ?? BUSY.working).label}
        {/* Only show the timer once the wait is long enough to be worth a
            number — a counter that starts at 0 s makes every answer feel slow. */}
        {seconds >= 3 && <span className="tabular-nums"> · {seconds} с</span>}
      </span>
    </div>
  );
}
