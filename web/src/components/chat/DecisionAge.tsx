import { useEffect, useState } from "react";
import type { EffectDecisionKind } from "@/lib/chat-types";
import { getOwnerTimeZone } from "@/lib/dashboard-flags";

// После полного рабочего дня текст, реквизиты или адресаты требуют проверки.
// Это подсказка, а не срок действия решения: кнопки и ledger не меняются.
const DAY_MS = 24 * 60 * 60 * 1000;

interface DecisionAgeProps {
  /** Время создания из ledger, Unix seconds; не updated_at и не время загрузки. */
  timestamp?: number;
  warningKind?: EffectDecisionKind;
}

export function DecisionAge({ timestamp, warningKind }: DecisionAgeProps) {
  const [now, setNow] = useState(() => Date.now());
  const created = new Date((timestamp ?? NaN) * 1000);
  const createdMs = created.getTime();
  useEffect(() => {
    if (!Number.isFinite(createdMs)) return;
    // Карточка может остаться открытой на ночь: обновляем день и предупреждение
    // без перезагрузки, на границе суток — сразу. После сна вкладки — по focus.
    const update = () => setNow(Date.now());
    const untilStale = createdMs + DAY_MS - now + 1;
    const timer = window.setTimeout(update, untilStale > 0 ? Math.min(60_000, untilStale) : 60_000);
    window.addEventListener("focus", update);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("focus", update);
    };
  }, [createdMs, now]);
  if (!Number.isFinite(createdMs)) return null;

  const timeZone = getOwnerTimeZone();
  const dateFormat = new Intl.DateTimeFormat("ru-RU", {
    timeZone, day: "2-digit", month: "2-digit", year: "numeric",
  });
  const day = dateFormat.format(created);
  const clock = new Intl.DateTimeFormat("ru-RU", {
    timeZone, hour: "2-digit", minute: "2-digit",
  }).format(created);
  const full = `${day} в ${clock}`;
  const label = day === dateFormat.format(now) ? `сегодня в ${clock}` : full;
  const old = now - createdMs > DAY_MS;
  const warning = !old ? null : warningKind === "outbound_message"
    ? `Текст создан ${full} и мог устареть. Проверьте его перед отправкой.`
    : warningKind === "payment"
      ? `Запрос создан ${full}. Данные могли устареть. Проверьте получателя, сумму и валюту перед оплатой.`
      : warningKind === "automation_recipients"
        ? `Запрос создан ${full}. Проверьте получателей автоматизации: за это время список мог измениться.`
        : null;

  return (
    <div className="mb-2 text-[11px] leading-snug">
      <p className="text-[var(--neo-text-secondary)]">
        Создано <time dateTime={created.toISOString()} title={`${full} (${timeZone})`}>{label}</time>
      </p>
      {warning && (
        <p role="note" className="mt-1 rounded-[var(--neo-radius-control)] border border-amber-600/30 bg-amber-500/10 px-2.5 py-2 text-[var(--neo-text-primary)]">
          {warning}
        </p>
      )}
    </div>
  );
}
