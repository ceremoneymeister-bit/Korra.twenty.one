import { ThinkingOrb } from "thinking-orbs";

import { useTheme } from "@/themes";
import { cn } from "@/lib/utils";

/* Решение владельца 03.09.2026: пока содержимое страницы не пришло, вместо
 * пустых контейнеров крутится орбита. Анимация обязана идти всегда — раньше
 * «ставили, а она не работала». Библиотека уважает системное «уменьшить
 * движение» и в этом режиме рисует статичный кадр; на панели владельца это
 * не нужно, поэтому для одного этого запроса matchMedia отвечает «нет». */
declare global {
  interface Window {
    __korraMotionShim?: boolean;
  }
}

if (typeof window !== "undefined" && !window.__korraMotionShim) {
  const original = window.matchMedia.bind(window);
  window.matchMedia = (query: string): MediaQueryList => {
    if (query.includes("prefers-reduced-motion")) {
      const noop = () => undefined;
      return {
        matches: false,
        media: query,
        onchange: null,
        addEventListener: noop,
        removeEventListener: noop,
        addListener: noop,
        removeListener: noop,
        dispatchEvent: () => false,
      } as unknown as MediaQueryList;
    }
    return original(query);
  };
  window.__korraMotionShim = true;
}

interface KorraLoaderProps {
  /** Подпись для читалок и, при `showLabel`, под орбитой. */
  label?: string;
  showLabel?: boolean;
  /** 64 — экран/блок, 20 — строка. */
  size?: 64 | 20;
  className?: string;
}

export function KorraLoader({
  label = "Загрузка…",
  showLabel = true,
  size = 64,
  className,
}: KorraLoaderProps) {
  const { themeName } = useTheme();
  return (
    <div
      className={cn("flex flex-col items-center justify-center gap-4", className)}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      {/* У библиотеки два размера — 64 и 20; для экрана 64 мелковат, растим холст. */}
      <ThinkingOrb
        state="working"
        size={size}
        speed={1.4}
        theme={themeName === "dark" ? "dark" : "light"}
        aria-label={label}
        style={size === 64 ? { transform: "scale(1.75)", margin: "1.5rem" } : undefined}
      />
      {showLabel ? (
        <span className="text-sm text-[var(--neo-text-secondary)]">{label}</span>
      ) : (
        <span className="sr-only">{label}</span>
      )}
    </div>
  );
}
