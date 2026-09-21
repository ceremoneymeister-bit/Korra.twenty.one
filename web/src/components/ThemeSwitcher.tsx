import { createPortal } from "react-dom";
import { Moon, Sun } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";
import { useTheme } from "@/themes";

/**
 * A direct light/dark control for the bottom of the sidebar.
 *
 * The expanded sidebar has two equally sized icon-only targets. The narrow
 * rail has room for one accessible 44 px toggle, so it shows the current
 * mode and switches to the other one when pressed.
 */
export function ThemeSwitcher({ collapsed = false }: ThemeSwitcherProps) {
  const { themeName, setTheme, saveState, saveError, retryTheme } = useTheme();
  const isDark = themeName === "dark";
  const isSaving = saveState === "pending";

  const chooseTheme = (name: "light" | "dark") => {
    if (name !== themeName) void setTheme(name);
  };

  return (
    <div className={cn("w-full", collapsed && "w-auto")}>
      {collapsed ? (
        <button
          aria-busy={isSaving || undefined}
          aria-label={isDark ? "Включить светлую тему" : "Включить тёмную тему"}
          className={cn(
            "grid size-[44px] place-items-center rounded-[var(--neo-radius-control)]",
            "text-[var(--neo-text-secondary)] transition-[box-shadow,color,opacity]",
            "hover:text-[var(--neo-text-primary)] hover:shadow-[var(--neo-inset-compact)]",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--neo-accent-line)]",
            isSaving && "opacity-65",
          )}
          onClick={() => chooseTheme(isDark ? "light" : "dark")}
          type="button"
        >
          {isDark ? <Moon aria-hidden className="size-[18px]" /> : <Sun aria-hidden className="size-[18px]" />}
        </button>
      ) : (
        <div
          aria-busy={isSaving || undefined}
          aria-label="Цветовая тема"
          className="grid min-h-[52px] w-full grid-cols-2 gap-1 rounded-[var(--neo-radius-control)] bg-[var(--neo-surface)] p-[4px] shadow-[var(--neo-inset-compact)]"
          role="group"
        >
          <ThemeChoice
            active={!isDark}
            label="Светлая тема"
            onClick={() => chooseTheme("light")}
          >
            <Sun aria-hidden className="size-[18px]" />
          </ThemeChoice>
          <ThemeChoice
            active={isDark}
            label="Тёмная тема"
            onClick={() => chooseTheme("dark")}
          >
            <Moon aria-hidden className="size-[18px]" />
          </ThemeChoice>
        </div>
      )}

      {saveState === "error" && typeof document !== "undefined" && createPortal(
        <div
          className="fixed bottom-4 left-4 right-4 z-[120] rounded-[var(--neo-radius-control)] bg-[var(--neo-surface)] p-3 text-sm text-[var(--neo-text-primary)] shadow-[var(--neo-depth-3)] sm:left-auto sm:max-w-sm"
          data-theme-save-status
          role="alert"
        >
          <p>{saveError || "Тема не сохранена. Проверьте соединение и повторите."}</p>
          <button
            // 44 px в абсолютной единице: шкала Tailwind у нас умножена на
            // плотность темы (`--spacing: var(--korra-space)` в index.css), и
            // `min-h-11` давал бы ~35 px — палец мимо единственной кнопки,
            // которой можно вернуть несохранённую тему.
            className="mt-2 min-h-[44px] rounded-lg px-3 font-medium text-[var(--neo-text-primary)] underline underline-offset-4 hover:shadow-[var(--neo-inset-compact)]"
            onClick={() => void retryTheme()}
            type="button"
          >
            Повторить сохранение
          </button>
        </div>,
        document.body,
      )}
    </div>
  );
}

function ThemeChoice({ active, children, label, onClick }: ThemeChoiceProps) {
  return (
    <button
      aria-label={label}
      aria-pressed={active}
      className={cn(
        "grid min-h-[44px] min-w-[44px] place-items-center rounded-[calc(var(--neo-radius-control)-0.25rem)]",
        "transition-[box-shadow,color,background-color]",
        "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--neo-accent-line)]",
        active
          ? "bg-[var(--neo-surface)] text-[var(--neo-text-primary)] shadow-[var(--neo-depth-1)]"
          : "text-[var(--neo-text-secondary)] hover:text-[var(--neo-text-primary)]",
      )}
      onClick={onClick}
      type="button"
    >
      {children}
    </button>
  );
}

interface ThemeChoiceProps {
  active: boolean;
  children: ReactNode;
  label: string;
  onClick: () => void;
}

interface ThemeSwitcherProps {
  collapsed?: boolean;
}
