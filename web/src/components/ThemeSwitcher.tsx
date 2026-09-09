import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Palette, Check, Moon, Sun, Type } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { ListItem } from "@nous-research/ui/ui/components/list-item";
import { BottomSheet } from "@nous-research/ui/ui/components/bottom-sheet";
import { Typography } from "@nous-research/ui/ui/components/typography/index";
import { useBelowBreakpoint } from "@nous-research/ui/hooks/use-below-breakpoint";
import { THEME_DEFAULT_FONT_ID, useTheme } from "@/themes";
import type { FontChoice, ThemeListEntry } from "@/themes";
import { useI18n } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * Compact light/dark picker mounted next to the language switcher. Font
 * selection remains independent from the palette and lives in the same menu.
 *
 * When placed at the bottom of a container (e.g. the sidebar rail), pass
 * `dropUp` so the menu opens above the trigger instead of clipping below
 * the viewport. On viewports below the `sm` breakpoint, `dropUp` uses a
 * bottom sheet portaled to `document.body` so the picker is not clipped by
 * the sidebar (same idea as a responsive Drawer).
 */
export function ThemeSwitcher({ collapsed = false, dropUp = false }: ThemeSwitcherProps) {
  const { themeName, availableThemes, setTheme, fontId, fontChoices, setFont, saveState, saveError, retryTheme, preference, setEvening } = useTheme();
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const narrowViewport = useBelowBreakpoint(640);
  const useMobileSheet = Boolean(dropUp && narrowViewport);

  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, close]);

  useEffect(() => {
    if (!open || useMobileSheet) return;
    const onMouseDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (wrapperRef.current?.contains(target)) return;
      if (dropdownRef.current?.contains(target)) return;
      close();
    };
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, [open, close, useMobileSheet]);

  const current = availableThemes.find((th) => th.name === themeName);
  const label = current?.label ?? themeName;
  const sheetTitle = t.theme?.title ?? "Тема";

  return (
    <div ref={wrapperRef} className="relative">
      <Button
        ghost
        size={collapsed ? "icon" : undefined}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          collapsed
            ? "text-text-secondary hover:text-foreground hover:bg-transparent"
            : "px-2 py-1 normal-case tracking-normal font-normal text-xs text-text-secondary hover:text-foreground",
        )}
        title={`${t.theme?.switchTheme ?? "Сменить тему"}: ${label}`}
        aria-label={t.theme?.switchTheme ?? "Сменить тему"}
        aria-expanded={open}
        aria-haspopup="dialog"
      >
        <span className="inline-flex items-center gap-1.5">
          <Palette className="h-3.5 w-3.5" />

          {!collapsed && (
            <Typography
              className="hidden sm:inline text-display tracking-wide text-xs"
            >
              {label}
            </Typography>
          )}
        </span>
      </Button>

      {open && preference?.evening?.disabled && <button type="button" className="min-h-11 p-2 text-xs underline" disabled={saveState === "pending"} onClick={() => void setEvening("enable")}>Разрешить вечернее предложение</button>}
      {saveState !== "idle" && (
        <div className="max-w-64 text-xs p-2" data-theme-save-status>
          {saveState === "error" ? <><p role="alert">{saveError}</p><button className="min-h-11 underline" type="button" onClick={() => void retryTheme()}>Повторить сохранение</button></> :
            <p role="status">{saveState === "pending" ? "Сохраняем настройку…" : "Настройка сохранена"}</p>}
        </div>
      )}
      {useMobileSheet && (
        <BottomSheet
          backdropDismissLabel={t.common.close}
          onClose={close}
          open={open}
          title={sheetTitle}
        >
          <div aria-label={sheetTitle}>
            <ThemeSwitcherOptions
              availableThemes={availableThemes}
              close={close}
              setTheme={setTheme}
              themeName={themeName}
            />
            <FontSection
              fontChoices={fontChoices}
              fontId={fontId}
              setFont={setFont}
            />
          </div>
        </BottomSheet>
      )}

      {open && !useMobileSheet && (() => {
        const rect = wrapperRef.current?.getBoundingClientRect();
        const dropdown = (
          <div
            ref={dropdownRef}
            role="dialog"
            aria-label={sheetTitle}
            className={cn(
              "min-w-[240px] max-h-[70dvh] overflow-y-auto",
              "rounded-2xl bg-[var(--neo-surface)]",
              "shadow-[var(--neo-depth-3)]",
              dropUp ? "fixed z-[100]" : "absolute z-50 right-0 top-full mt-1",
            )}
            style={
              dropUp && rect
                ? { bottom: window.innerHeight - rect.top + 4, left: rect.left }
                : undefined
            }
          >
            <div className="px-3 py-2">
              <Typography
                className="text-display text-xs tracking-[0.12em] text-text-tertiary"
              >
                {sheetTitle}
              </Typography>
            </div>

            <ThemeSwitcherOptions
              availableThemes={availableThemes}
              close={close}
              setTheme={setTheme}
              themeName={themeName}
            />
            <FontSection
              fontChoices={fontChoices}
              fontId={fontId}
              setFont={setFont}
            />
          </div>
        );
        return dropUp ? createPortal(dropdown, document.body) : dropdown;
      })()}
    </div>
  );
}

function ThemeSwitcherOptions({
  availableThemes,
  close,
  setTheme,
  themeName,
}: ThemeSwitcherOptionsProps) {
  return (
    <div
      aria-label="Цветовая тема"
      className="grid grid-cols-2 gap-2 p-2"
      role="radiogroup"
    >
      {availableThemes.map((th) => {
        const isActive = th.name === themeName;
        const Icon = th.name === "dark" ? Moon : Sun;

        return (
          <button
            aria-checked={isActive}
            tabIndex={isActive ? 0 : -1}
            onKeyDown={(event) => {
              if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
              event.preventDefault();
              const choices = [...event.currentTarget.parentElement!.querySelectorAll<HTMLButtonElement>('[role="radio"]')];
              const index = choices.indexOf(event.currentTarget);
              const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? choices.length - 1 :
                (index + (event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 1) + choices.length) % choices.length;
              choices[nextIndex].focus();
              void setTheme(availableThemes[nextIndex].name);
            }}
            className={cn(
              "flex min-h-11 items-center justify-center gap-2 neo-button rounded-lg px-3 py-2",
              "text-sm font-medium transition-colors",
              isActive
                ? "bg-primary text-primary-foreground"
                : "bg-card text-card-foreground",
            )}
            key={th.name}
            onClick={() => {
              setTheme(th.name);
              close();
            }}
            role="radio"
            type="button"
          >
            <Icon className="size-4 shrink-0" aria-hidden />
            <span className="truncate">{th.label}</span>
          </button>
        );
      })}
    </div>
  );
}

const FONT_CATEGORY_LABEL_KEY: Record<FontChoice["category"], "fontSans" | "fontSerif" | "fontMono"> = {
  sans: "fontSans",
  serif: "fontSerif",
  mono: "fontMono",
};

/** Font-override section rendered below the theme list. Lets the user pick
 *  any catalog font independently of the active theme, or "Theme default"
 *  to clear the override. Each row previews itself in its own font. */
function FontSection({ fontChoices, fontId, setFont }: FontSectionProps) {
  const { t } = useI18n();
  const order: FontChoice["category"][] = ["sans", "serif", "mono"];
  return (
    <div aria-label={t.theme?.fontTitle ?? "Шрифт"} role="listbox">
      <div className="mt-1 px-3 pb-1 pt-2">
        <span className="inline-flex items-center gap-1.5">
          <Type className="h-3 w-3 text-text-tertiary" />
          <Typography
            className="text-display text-xs tracking-[0.12em] text-text-tertiary"
          >
            {t.theme?.fontTitle ?? "Шрифт"}
          </Typography>
        </span>
      </div>

      {/* Theme-default (clears the override). */}
      <ListItem
        active={fontId === THEME_DEFAULT_FONT_ID}
        aria-selected={fontId === THEME_DEFAULT_FONT_ID}
        className="gap-3"
        onClick={() => setFont(THEME_DEFAULT_FONT_ID)}
        role="option"
      >
        <span aria-hidden className="h-4 w-9 shrink-0" />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <Typography className="truncate text-xs tracking-normal">
            {t.theme?.fontDefault ?? "Шрифт темы"}
          </Typography>
          <Typography className="truncate text-xs tracking-normal text-text-tertiary">
            {t.theme?.fontDefaultHint ?? "Использовать шрифт активной темы"}
          </Typography>
        </div>
        <Check
          className={cn(
            "h-3 w-3 shrink-0 text-midground",
            fontId === THEME_DEFAULT_FONT_ID ? "opacity-100" : "opacity-0",
          )}
        />
      </ListItem>

      {order.map((cat) => {
        const fonts = fontChoices.filter((f) => f.category === cat);
        if (fonts.length === 0) return null;
        const catLabel =
          t.theme?.[FONT_CATEGORY_LABEL_KEY[cat]] ??
          ({ sans: "Без засечек", serif: "С засечками", mono: "Моноширинный" } as const)[cat];
        return (
          <div key={cat}>
            <div className="px-3 pb-0.5 pt-1.5">
              <Typography className="text-[0.65rem] uppercase tracking-[0.1em] text-text-tertiary">
                {catLabel}
              </Typography>
            </div>
            {fonts.map((f) => {
              const isActive = f.id === fontId;
              return (
                <ListItem
                  active={isActive}
                  aria-selected={isActive}
                  className="gap-3"
                  key={f.id}
                  onClick={() => setFont(f.id)}
                  role="option"
                >
                  <span aria-hidden className="h-4 w-9 shrink-0" />
                  <div className="flex min-w-0 flex-1 flex-col">
                    {/* Preview the font in its own stack. */}
                    <span
                      className="truncate text-sm"
                      style={{ fontFamily: f.stack }}
                    >
                      {f.label}
                    </span>
                  </div>
                  <Check
                    className={cn(
                      "h-3 w-3 shrink-0 text-midground",
                      isActive ? "opacity-100" : "opacity-0",
                    )}
                  />
                </ListItem>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}

interface ThemeSwitcherOptionsProps {
  availableThemes: ThemeListEntry[];
  close: () => void;
  setTheme: (name: string) => void;
  themeName: string;
}

interface FontSectionProps {
  fontChoices: FontChoice[];
  fontId: string;
  setFont: (id: string) => void;
}

interface ThemeSwitcherProps {
  collapsed?: boolean;
  dropUp?: boolean;
}
