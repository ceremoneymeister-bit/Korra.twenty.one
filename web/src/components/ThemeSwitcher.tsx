import { createPortal } from "react-dom";
import { Moon, Palette, Sun, X } from "lucide-react";
import { useState } from "react";
import { Popover } from "radix-ui";

import { useTheme } from "@/themes";
import { ThemeColorPalette } from "./ThemeColorPalette";
import "./theme-switcher.css";

const CHOICES = [
  { name: "light", label: "Светлая", Icon: Sun },
  { name: "dark", label: "Тёмная", Icon: Moon },
  { name: "color", label: "Цвет", Icon: Palette },
];

/** Collapsing the rail keeps an explicit menu, never a hidden three-way cycle. */
export function ThemeSwitcher({ collapsed = false, labeled = false }: ThemeSwitcherProps) {
  const { themeName, setTheme, saveState, saveError, retryTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const CurrentIcon = CHOICES.find(choice => choice.name === themeName)?.Icon ?? Sun;
  const choices = (inMenu = false) => (
    <div className={`theme-choices${labeled || inMenu ? " is-labeled" : ""}`} role="group" aria-label="Цветовая тема" aria-busy={saveState === "pending" || undefined}>
      {CHOICES.map(({ name, label, Icon }) => {
        const button = (
          <button
            type="button" data-theme-control aria-label={name === "color" ? "Цвет" : `${label} тема`}
            title={label} aria-pressed={themeName === name}
            onClick={() => {
              if (saveState === "error" && name === themeName) void retryTheme();
              else if (name !== themeName) void setTheme(name);
              if (name !== "color") setOpen(false);
            }}
          >
            <span><Icon size={15} aria-hidden />{(labeled || inMenu) && label}</span>
          </button>
        );
        return name === "color" && !inMenu
          ? <Popover.Trigger asChild key={name}>{button}</Popover.Trigger>
          : <span className="theme-choice-slot" key={name}>{button}</span>;
      })}
    </div>
  );
  return (
    <div className="theme-switcher">
      <Popover.Root open={open} onOpenChange={setOpen}>
        {collapsed ? (
          <Popover.Trigger asChild>
            <button type="button" data-theme-control className="theme-collapsed" aria-label="Выбрать тему" title="Выбрать тему">
              <CurrentIcon size={16} aria-hidden />
            </button>
          </Popover.Trigger>
        ) : choices()}
        <Popover.Portal>
          <Popover.Content className="theme-palette-popover" side="top" align="start" sideOffset={12} collisionPadding={12} aria-label="Цветовая тема">
            <div className="theme-palette-heading">
              <strong>{collapsed ? "Тема" : "Цвет интерфейса"}</strong>
              <Popover.Close className="theme-palette-close" aria-label="Закрыть палитру"><X size={18} aria-hidden /></Popover.Close>
            </div>
            {collapsed && choices(true)}
            <ThemeColorPalette />
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
      {saveState === "error" && !labeled && typeof document !== "undefined" && createPortal(
        <div
          className="fixed bottom-4 left-4 right-4 z-[120] rounded-[var(--neo-radius-control)] bg-[var(--neo-surface)] p-3 text-sm text-[var(--neo-text-primary)] shadow-[var(--neo-depth-3)] sm:left-auto sm:max-w-sm"
          data-theme-save-status role="alert"
        >
          <p>{saveError || "Тема не сохранена. Проверьте соединение и повторите."}</p>
          <button className="mt-2 min-h-[44px] rounded-lg px-3 font-medium text-[var(--neo-text-primary)] underline underline-offset-4 hover:shadow-[var(--neo-inset-compact)]" onClick={() => void retryTheme()} type="button">
            Повторить сохранение
          </button>
        </div>, document.body,
      )}
    </div>
  );
}
interface ThemeSwitcherProps {
  collapsed?: boolean;
  labeled?: boolean;
}
