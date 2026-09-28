import { readChatView, writeChatView } from "@/lib/chat-view-state";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { ArrowDown } from "lucide-react";

interface OlderMessages {
  hasOlder: boolean;
  loading: boolean;
  failed: boolean;
  load: () => void;
}

interface TranscriptViewportProps {
  children: ReactNode;
  storageKey?: string;
  /** Новое собственное сообщение возвращает к ответу на него. */
  followKey?: string;
  awaitingApproval?: boolean;
  /** id первого сообщения: его смена без прокрутки вниз — догруженное начало. */
  anchorKey?: string;
  /** Более ранние сообщения, которые догружаются к началу ленты. */
  older?: OlderMessages;
}

/** Ближе этого к началу ленты человек, листающий вверх, получает следующую
 *  страницу, не упираясь в край. */
const LOAD_OLDER_WITHIN_PX = 320;

/** Сколько страниц можно догрузить, возвращая человека к месту чтения. */
const MAX_RESTORE_PAGES = 10;

/**
 * Место чтения — сообщение и смещение внутри него. Пиксели от начала ленты
 * после перезагрузки указывали на другое сообщение: загружена уже только
 * последняя страница (0.21.15, ревью Astra R5). Прежние числовые значения
 * не восстанавливаются.
 */
interface ReadingPlace { id: string; offset: number }

function parsePlace(raw: string): ReadingPlace | null {
  try {
    const value = JSON.parse(raw) as Partial<ReadingPlace> | null;
    return value && typeof value.id === "string" && typeof value.offset === "number"
      ? { id: value.id, offset: value.offset }
      : null;
  } catch {
    return null;
  }
}

/** Номер строки истории из id сообщения (`<чат>-h<строка>`), если он есть. */
function historyRow(id: string | undefined): number | null {
  const match = id ? /-h(\d+)$/.exec(id) : null;
  return match ? Number(match[1]) : null;
}

/** Поток следует вниз, пока человек не начал читать предыдущий текст. */
export function TranscriptViewport({ children, followKey, awaitingApproval, storageKey, anchorKey, older }: TranscriptViewportProps) {
  const viewport = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const saved = storageKey ? parsePlace(readChatView(storageKey)) : null;
  const place = useRef<ReadingPlace | null>(saved);
  const restorePages = useRef(0);
  const following = useRef(!saved);
  const previousKey = useRef(followKey);
  const [away, setAway] = useState(Boolean(saved));
  const previousAnchor = useRef(anchorKey);
  const lastHeight = useRef(0);
  const lastTop = useRef(0);

  function messageTop(el: HTMLDivElement, item: Element): number {
    return item.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop;
  }

  function messages(): Element[] {
    return Array.from(content.current?.querySelectorAll("[data-chat-message]") ?? []);
  }

  /** Первое сообщение, чей низ ниже верха окна, и насколько окно в него ушло. */
  function currentPlace(el: HTMLDivElement): ReadingPlace | null {
    const top = el.getBoundingClientRect().top;
    const item = messages().find(candidate => candidate.getBoundingClientRect().bottom > top);
    const id = item?.getAttribute("data-chat-message");
    return item && id ? { id, offset: el.scrollTop - messageTop(el, item) } : null;
  }

  function stopRestoring(el: HTMLDivElement) {
    place.current = null;
    following.current = true;
    el.scrollTop = el.scrollHeight;
    if (storageKey) writeChatView(storageKey, "");
    setAway(false);
  }

  useLayoutEffect(() => {
    if (previousKey.current !== followKey) {
      previousKey.current = followKey;
      if (followKey && place.current === null) following.current = true;
    }
    const el = viewport.current;
    if (!el || !el.clientHeight) return;
    const prepended = previousAnchor.current !== undefined && previousAnchor.current !== anchorKey;
    previousAnchor.current = anchorKey;
    if (place.current !== null) {
      const reading = place.current;
      const target = messages().find(item => item.getAttribute("data-chat-message") === reading.id);
      const wanted = historyRow(reading.id);
      const first = historyRow(anchorKey);
      if (target) {
        el.scrollTop = messageTop(el, target) + reading.offset;
        place.current = null;
      } else if (wanted !== null && first !== null && wanted < first && older?.hasOlder && !older.failed
        && restorePages.current < MAX_RESTORE_PAGES) {
        // Место чтения раньше загруженной страницы: догружаем до него.
        if (!older.loading) {
          restorePages.current += 1;
          older.load();
        }
      } else if (!older?.loading) {
        // Сообщения больше нет или до него слишком далеко: последний ответ
        // лучше, чем чужое место.
        stopRestoring(el);
      }
    } else if (following.current) el.scrollTop = el.scrollHeight;
    // Более ранние сообщения встали над читаемым местом: сдвигаем прокрутку
    // на их высоту, чтобы текст перед глазами остался на месте.
    else if (prepended) el.scrollTop += el.scrollHeight - lastHeight.current;
    lastHeight.current = el.scrollHeight;
    lastTop.current = el.scrollTop;
  }, [children, followKey, anchorKey, older?.hasOlder, older?.loading, older?.failed]);

  useEffect(() => {
    if (!content.current || typeof ResizeObserver === "undefined") return;
    // Вложения и раскрытие хода меняют высоту без новых текстовых чанков.
    const observer = new ResizeObserver(() => {
      const el = viewport.current;
      if (el && el.clientHeight && following.current) el.scrollTop = el.scrollHeight;
      if (el) lastHeight.current = el.scrollHeight;
    });
    observer.observe(content.current);
    return () => observer.disconnect();
  }, []);

  function toLatest() {
    following.current = true;
    place.current = null;
    if (storageKey) writeChatView(storageKey, "");
    if (viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
    setAway(false);
  }

  return (
    <div className="relative min-h-0 flex-1">
      <div
        ref={viewport}
        className="h-full overflow-y-auto [overflow-anchor:none]"
        aria-label="Переписка"
        onWheel={() => { place.current = null; }}
        onTouchStart={() => { place.current = null; }}
        onScroll={event => {
          const el = event.currentTarget;
          if (!el.clientHeight || place.current !== null) return;
          const nearEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 64;
          if (storageKey) {
            const reading = nearEnd ? null : currentPlace(el);
            writeChatView(storageKey, reading ? JSON.stringify(reading) : "");
          }
          following.current = nearEnd;
          setAway(!nearEnd);
          // Только прокрутка вверх: прилипание к последнему ответу и сдвиг
          // после вставки идут вниз и страницы не просят.
          const up = el.scrollTop < lastTop.current;
          lastTop.current = el.scrollTop;
          if (up && el.scrollTop < LOAD_OLDER_WITHIN_PX && older?.hasOlder && !older.loading && !older.failed) older.load();
        }}
      >
        <div ref={content}>
          {older?.hasOlder && (
            <div className="flex justify-center px-4 pt-4">
              {older.loading ? (
                <p role="status" className="py-3 text-xs text-[var(--neo-text-secondary)]">Загружаем более ранние сообщения…</p>
              ) : (
                <button
                  type="button"
                  onClick={older.load}
                  className="min-h-[44px] rounded-full border-0 bg-[var(--neo-surface)] px-4 py-2 text-xs text-[var(--neo-text-primary)] shadow-[var(--neo-depth-2)] outline-none focus-visible:ring-2 focus-visible:ring-[var(--neo-accent-line)]"
                >
                  {older.failed ? "Не удалось загрузить более ранние сообщения — повторить" : "Показать более ранние сообщения"}
                </button>
              )}
            </div>
          )}
          {children}
        </div>
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
