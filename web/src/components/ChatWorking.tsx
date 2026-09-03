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
export type BusyKind = "working" | "reading" | "searching" | "listening";

const BUSY: Record<BusyKind, { label: string; orb: "working" | "searching" | "listening" }> = {
  working: { label: "Агент работает", orb: "working" },
  reading: { label: "Читаю вложения", orb: "searching" },
  searching: { label: "Ищу", orb: "searching" },
  listening: { label: "Слушаю", orb: "listening" },
};

export function ChatWorking({
  startedAt,
  state = "working",
}: {
  startedAt: number;
  state?: BusyKind;
}) {
  const { themeName } = useTheme();
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
        theme={themeName === "dark" ? "dark" : "light"}
        aria-label={busy.label}
        style={{ transform: "scale(1.4)", margin: "0.25rem" }}
      />
      <span className="text-sm text-[var(--neo-text-secondary)]">
        {busy.label}
        {/* Счётчик только когда ждать уже заметно — таймер с нуля делает
            каждый ответ медленным на вид. */}
        {seconds >= 3 && <span className="tabular-nums"> · {seconds} с</span>}
      </span>
    </div>
  );
}
