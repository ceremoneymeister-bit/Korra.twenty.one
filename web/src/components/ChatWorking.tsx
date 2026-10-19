import { themeColorScheme } from "@/themes/color";
/**
 * «Агент работает» — вместо пустого пузыря ответа.
 *
 * Пока ответ не начал приходить, показываем маленькую орбиту (та же
 * библиотека, что и у загрузчика страниц) и честную подпись: сервер не
 * сообщает, какой инструмент крутится, поэтому максимум правды — «работает,
 * N секунд». Решение владельца 03.09.2026: без зелёного спиннера, без
 * рамки, орбита как на загрузке.
 */
import { useEffect, useState } from "react";
import { ThinkingOrb } from "thinking-orbs";

import { useTheme } from "@/themes";

/** Что показываем и каким состоянием сферы. */
/** `awaiting` — ход стоит на вопросе владельцу: «работает» было бы неправдой. */
export type BusyKind = "working" | "reading" | "searching" | "listening" | "awaiting";

const BUSY: Record<BusyKind, { label: string; orb: "working" | "searching" | "listening" }> = {
  working: { label: "Агент работает", orb: "working" },
  reading: { label: "Читаю вложения", orb: "searching" },
  searching: { label: "Ищу", orb: "searching" },
  listening: { label: "Слушаю", orb: "listening" },
  awaiting: { label: "Ждёт вашего решения", orb: "listening" },
};

/** «45 с», «2 мин 5 с», «1 ч 3 мин» — минуты читать проще, чем «125 с». */
export function formatWaitSeconds(total: number): string {
  const seconds = Math.max(0, Math.floor(total));
  if (seconds < 60) return `${seconds} с`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  if (hours > 0) return minutes > 0 ? `${hours} ч ${minutes} мин` : `${hours} ч`;
  return rest > 0 ? `${minutes} мин ${rest} с` : `${minutes} мин`;
}

export function ChatWorking({
  startedAt,
  state = "working",
}: {
  startedAt: number;
  state?: BusyKind;
}) {
  const { theme } = useTheme();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));
  const busy = BUSY[state] ?? BUSY.working;
  return (
    <div
      className="inline-flex items-center gap-3 py-1 pl-1 font-sans normal-case tracking-normal"
      role="status"
      aria-live="polite"
    >
      <ThinkingOrb
        state={busy.orb}
        size={20}
        speed={1.3}
        theme={themeColorScheme(theme)}
        aria-label={busy.label}
        style={{ transform: "scale(1.4)", margin: "0.25rem" }}
      />
      <span className="text-sm text-[var(--neo-text-secondary)]">
        {busy.label}
        {/* Счётчик только когда ждать уже заметно — таймер с нуля делает
            каждый ответ медленным на вид. */}
        {/* Секунды тикают каждую секунду — диктору их не читаем, иначе
            aria-live объявлял бы статус заново раз в секунду. */}
        {seconds >= 3 && <span className="tabular-nums" aria-hidden="true"> · {formatWaitSeconds(seconds)}</span>}
      </span>
    </div>
  );
}
