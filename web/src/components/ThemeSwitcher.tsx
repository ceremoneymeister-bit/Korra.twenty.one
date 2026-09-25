import { createPortal } from "react-dom";
import { Moon, Sun } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";
import { useTheme } from "@/themes";

/**
 * A direct light/dark control for the bottom of the sidebar.
 *
 * The expanded sidebar shows a compact 32 px pill with two icon-only
 * segments instead of a full-width block; the narrow rail shows the mode
 * pressing it will choose. With a mouse the targets stay small so the control
 * does not crowd the sidebar; on touch screens (`pointer-coarse`) they grow
 * back to finger size.
 */
export function ThemeSwitcher({ collapsed = false }: ThemeSwitcherProps) {
  const { themeName, setTheme, saveState, saveError, retryTheme } = useTheme();
  const isDark = themeName === "dark";
  const isSaving = saveState === "pending";

  const chooseTheme = (name: "light" | "dark") => {
    if (name !== themeName) void setTheme(name);
  };

  return (
    <div className="w-auto">
      {collapsed ? (
        <button
          aria-busy={isSaving || undefined}
          aria-label={isDark ? "Включить светлую тему" : "Включить тёмную тему"}
          data-theme-control
          className={cn(
            "grid size-[36px] place-items-center rounded-full pointer-coarse:size-[44px]",
            "text-[var(--neo-text-secondary)] transition-[box-shadow,color,opacity]",
            "hover:text-[var(--neo-text-primary)] hover:shadow-[var(--neo-inset-compact)]",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--neo-accent-line)]",
            isSaving && "opacity-65",
          )}
          onClick={() => chooseTheme(isDark ? "light" : "dark")}
          type="button"
        >
          {isDark ? <Sun aria-hidden className="size-[16px]" /> : <Moon aria-hidden className="size-[16px]" />}
        </button>
      ) : (
        <div
          aria-busy={isSaving || undefined}
          aria-label="Цветовая тема"
          className="grid h-[32px] w-[84px] grid-cols-2 gap-[2px] rounded-full bg-[var(--neo-surface)] p-[3px] shadow-[var(--neo-inset-compact)] pointer-coarse:h-[40px] pointer-coarse:w-[100px]"
          role="group"
        >
          <ThemeChoice
            active={!isDark}
            label="Светлая тема"
            onClick={() => chooseTheme("light")}
          >
            <Sun aria-hidden className="size-[15px]" />
          </ThemeChoice>
          <ThemeChoice
            active={isDark}
            label="Тёмная тема"
            onClick={() => chooseTheme("dark")}
          >
            <Moon aria-hidden className="size-[15px]" />
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
      data-theme-control
      className={cn(
        "grid h-full place-items-center rounded-full",
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
