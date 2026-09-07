import { readChatView, writeChatView } from "@/lib/chat-view-state";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { ArrowDown } from "lucide-react";

interface TranscriptViewportProps {
  children: ReactNode;
  storageKey?: string;
  /** Новое собственное сообщение возвращает к ответу на него. */
  followKey?: string;
  awaitingApproval?: boolean;
}

/** Поток следует вниз, пока человек не начал читать предыдущий текст. */
export function TranscriptViewport({ children, followKey, awaitingApproval, storageKey }: TranscriptViewportProps) {
  const viewport = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const saved = storageKey ? readChatView(storageKey) : "";
  const restoringTop = useRef<number | null>(saved ? Number(saved) : null);
  const following = useRef(!saved);
  const previousKey = useRef(followKey);
  const [away, setAway] = useState(Boolean(saved));

  useLayoutEffect(() => {
    if (previousKey.current !== followKey) {
      previousKey.current = followKey;
      if (restoringTop.current === null) following.current = true;
    }
    const el = viewport.current;
    if (!el || !el.clientHeight) return;
    if (restoringTop.current !== null) {
      el.scrollTop = restoringTop.current;
      if (el.scrollHeight - el.clientHeight >= restoringTop.current) restoringTop.current = null;
    } else if (following.current) el.scrollTop = el.scrollHeight;
  }, [children, followKey]);

  useEffect(() => {
    if (!content.current || typeof ResizeObserver === "undefined") return;
    // Вложения и раскрытие хода меняют высоту без новых текстовых чанков.
    const observer = new ResizeObserver(() => {
      const el = viewport.current;
      if (el && el.clientHeight && following.current) el.scrollTop = el.scrollHeight;
    });
    observer.observe(content.current);
    return () => observer.disconnect();
  }, []);

  function toLatest() {
    following.current = true;
    restoringTop.current = null;
    if (storageKey) writeChatView(storageKey, "");
    if (viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
    setAway(false);
  }

  return (
    <div className="relative min-h-0 flex-1">
      <div
        ref={viewport}
        className="h-full overflow-y-auto"
        aria-label="Переписка"
        onWheel={() => { restoringTop.current = null; }}
        onTouchStart={() => { restoringTop.current = null; }}
        onScroll={event => {
          const el = event.currentTarget;
          if (!el.clientHeight || restoringTop.current !== null) return;
          const nearEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 64;
          if (storageKey) writeChatView(storageKey, nearEnd ? "" : String(el.scrollTop));
          following.current = nearEnd;
          setAway(!nearEnd);
        }}
      >
        <div ref={content}>{children}</div>
      </div>
      {away && (
        <button
          type="button"
          onClick={toLatest}
          className="absolute bottom-4 left-1/2 flex -translate-x-1/2 items-center gap-2 whitespace-nowrap rounded-full border-0 bg-[var(--neo-surface)] px-4 py-3 text-xs text-[var(--neo-text-primary)] shadow-[var(--neo-depth-2)] outline-none"
        >
          <ArrowDown size={15} aria-hidden />
          {awaitingApproval ? "Корра ждёт вашего решения" : "К последнему ответу"}
        </button>
      )}
    </div>
  );
}
