import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { BUILTIN_THEMES, defaultTheme, migrateThemeName } from "./presets";
import {
  FONT_CHOICES,
  THEME_DEFAULT_FONT_ID,
  getFontChoice,
  type FontChoice,
} from "./fonts";
import type {
  DashboardTheme,
  ThemeAssets,
  ThemeComponentStyles,
  ThemeDensity,
  ThemeLayer,
  ThemeLayout,
  ThemeLayoutVariant,
  ThemeListEntry,
  ThemePalette,
  ThemeSeriesColors,
  ThemeTypography,
} from "./types";
import {
  COLOR_OVERRIDE_CSS_VARS,
  colorOverrideVars,
} from "./semantic-colors";
import { NEUMORPHISM_CSS_VARS, neumorphismVars } from "./neumorphism";
import { api } from "@/lib/api";
import { cachePreference, readBootstrap, safeRead, safeWrite, validPreference, type ThemePreference } from "./preference";

/** LocalStorage key for the font override (independent of theme). Holds a
 *  font id from the catalog in `fonts.ts`, or the `THEME_DEFAULT_FONT_ID`
 *  sentinel / absent = "use the active theme's font". Pre-applied before
 *  the React tree mounts (see `main.tsx`) to avoid a font flash. */
const FONT_STORAGE_KEY = "hermes-dashboard-font";

const BUILTIN_THEME_ENTRIES: ThemeListEntry[] = Object.values(BUILTIN_THEMES).map(
  (theme) => ({
    name: theme.name,
    label: theme.label,
    description: theme.description,
  }),
);

/** Tracks fontUrls we've already injected so multiple theme switches don't
 *  pile up <link> tags. Keyed by URL. */
const INJECTED_FONT_URLS = new Set<string>();

// ---------------------------------------------------------------------------
// CSS variable builders
// ---------------------------------------------------------------------------

/** Turn a ThemeLayer into the two CSS expressions the DS consumes:
 *  `--<name>` (color-mix'd with alpha) and `--<name>-base` (opaque hex). */
function layerVars(
  name: "background" | "midground" | "foreground",
  layer: ThemeLayer,
): Record<string, string> {
  const pct = Math.round(layer.alpha * 100);
  return {
    [`--${name}`]: `color-mix(in srgb, ${layer.hex} ${pct}%, transparent)`,
    [`--${name}-base`]: layer.hex,
    [`--${name}-alpha`]: String(layer.alpha),
  };
}

function paletteVars(palette: ThemePalette): Record<string, string> {
  return {
    ...layerVars("background", palette.background),
    ...layerVars("midground", palette.midground),
    ...layerVars("foreground", palette.foreground),
  };
}

const DENSITY_MULTIPLIERS: Record<ThemeDensity, string> = {
  compact: "0.85",
  comfortable: "1",
  spacious: "1.2",
};

function typographyVars(typo: ThemeTypography): Record<string, string> {
  return {
    "--theme-font-sans": typo.fontSans,
    "--theme-font-mono": typo.fontMono,
    "--theme-font-display": typo.fontDisplay ?? typo.fontSans,
    "--theme-base-size": typo.baseSize,
    "--theme-line-height": typo.lineHeight,
    "--theme-letter-spacing": typo.letterSpacing,
  };
}

function layoutVars(layout: ThemeLayout): Record<string, string> {
  return {
    "--radius": layout.radius,
    "--theme-radius": layout.radius,
    "--theme-spacing-mul": DENSITY_MULTIPLIERS[layout.density] ?? "1",
    "--theme-density": layout.density,
  };
}

/** Map data-series accents to their CSS vars. Themes omit either field to
 *  inherit the `:root` default from `index.css`; when omitted we also
 *  proactively clear any leftover value from a previous theme so switches
 *  don't carry stale colors. */
const SERIES_KEY_TO_VAR: Record<keyof ThemeSeriesColors, string> = {
  inputTokenAccent: "--series-input-token",
  outputTokenAccent: "--series-output-token",
};

const ALL_SERIES_VARS = Object.values(SERIES_KEY_TO_VAR);

function seriesColorVars(
  series: ThemeSeriesColors | undefined,
): Record<string, string> {
  if (!series) return {};
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(series)) {
    if (!value) continue;
    const cssVar = SERIES_KEY_TO_VAR[key as keyof ThemeSeriesColors];
    if (cssVar) out[cssVar] = value;
  }
  return out;
}

// ---------------------------------------------------------------------------
// Asset + component-style + layout variant vars
// ---------------------------------------------------------------------------

/** Well-known named asset slots a theme may populate. Kept in sync with
 *  `_THEME_NAMED_ASSET_KEYS` in `korra_cli/web_server.py`. */
const NAMED_ASSET_KEYS = ["bg", "hero", "logo", "crest", "sidebar", "header"] as const;

/** Component buckets mirrored from the backend's `_THEME_COMPONENT_BUCKETS`.
 *  Each bucket emits `--component-<bucket>-<kebab-prop>` CSS vars. */
const COMPONENT_BUCKETS = [
  "card", "header", "footer", "sidebar", "tab",
  "progress", "badge", "backdrop", "page",
] as const;

/** Camel → kebab (`clipPath` → `clip-path`). */
function toKebab(s: string): string {
  return s.replace(/[A-Z]/g, (m) => `-${m.toLowerCase()}`);
}

/** Build `--theme-asset-*` CSS vars from the assets block. Values are wrapped
 *  in `url(...)` when they look like a bare path/URL; raw CSS expressions
 *  (`linear-gradient(...)`, pre-wrapped `url(...)`, `none`) pass through. */
function assetVars(assets: ThemeAssets | undefined): Record<string, string> {
  if (!assets) return {};
  const out: Record<string, string> = {};
  const wrap = (v: string): string => {
    const trimmed = v.trim();
    if (!trimmed) return "";
    // Already a CSS image/gradient/url/none — don't re-wrap.
    if (/^(url\(|linear-gradient|radial-gradient|conic-gradient|none$)/i.test(trimmed)) {
      return trimmed;
    }
    // Bare path / http(s) URL / data: URL → wrap in url().
    return `url("${trimmed.replace(/"/g, '\\"')}")`;
  };
  for (const key of NAMED_ASSET_KEYS) {
    const val = assets[key];
    if (typeof val === "string" && val.trim()) {
      out[`--theme-asset-${key}`] = wrap(val);
      out[`--theme-asset-${key}-raw`] = val;
    }
  }
  if (assets.custom) {
    for (const [key, val] of Object.entries(assets.custom)) {
      if (typeof val !== "string" || !val.trim()) continue;
      if (!/^[a-zA-Z0-9_-]+$/.test(key)) continue;
      out[`--theme-asset-custom-${key}`] = wrap(val);
      out[`--theme-asset-custom-${key}-raw`] = val;
    }
  }
  return out;
}

/** Build `--component-<bucket>-<prop>` CSS vars from the componentStyles
 *  block. Values pass through untouched so themes can use any CSS expression. */
function componentStyleVars(
  styles: ThemeComponentStyles | undefined,
): Record<string, string> {
  if (!styles) return {};
  const out: Record<string, string> = {};
  for (const bucket of COMPONENT_BUCKETS) {
    const props = (styles as Record<string, Record<string, string> | undefined>)[bucket];
    if (!props) continue;
    for (const [prop, value] of Object.entries(props)) {
      if (typeof value !== "string" || !value.trim()) continue;
      // Same guardrail as backend — camelCase or kebab-case alnum only.
      if (!/^[a-zA-Z0-9_-]+$/.test(prop)) continue;
      out[`--component-${bucket}-${toKebab(prop)}`] = value;
    }
  }
  return out;
}

// Tracks keys we set on the previous theme so we can clear them when the
// next theme has fewer assets / component vars. Without this, switching
// from a richly-decorated theme to a plain one would leave stale vars.
let _PREV_DYNAMIC_VAR_KEYS: Set<string> = new Set();

/** ID for the injected <style> tag that carries a theme's customCSS.
 *  A single tag is reused + replaced on every theme switch. */
const CUSTOM_CSS_STYLE_ID = "hermes-theme-custom-css";

function applyCustomCSS(css: string | undefined) {
  if (typeof document === "undefined") return;
  let el = document.getElementById(CUSTOM_CSS_STYLE_ID) as HTMLStyleElement | null;
  if (!css || !css.trim()) {
    if (el) el.remove();
    return;
  }
  if (!el) {
    el = document.createElement("style");
    el.id = CUSTOM_CSS_STYLE_ID;
    el.setAttribute("data-hermes-theme-css", "true");
    document.head.appendChild(el);
  }
  el.textContent = css;
}

function applyLayoutVariant(variant: ThemeLayoutVariant | undefined) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  const final: ThemeLayoutVariant = variant ?? "standard";
  root.dataset.layoutVariant = final;
  root.style.setProperty("--theme-layout-variant", final);
}

// ---------------------------------------------------------------------------
// Font stylesheet injection
// ---------------------------------------------------------------------------

function injectFontStylesheet(url: string | undefined) {
  if (!url || typeof document === "undefined") return;
  if (INJECTED_FONT_URLS.has(url)) return;
  // Also skip if the page already has this href (e.g. SSR'd or persisted).
  const existing = document.querySelector<HTMLLinkElement>(
    `link[rel="stylesheet"][href="${CSS.escape(url)}"]`,
  );
  if (existing) {
    INJECTED_FONT_URLS.add(url);
    return;
  }
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = url;
  link.setAttribute("data-hermes-theme-font", "true");
  document.head.appendChild(link);
  INJECTED_FONT_URLS.add(url);
}

// ---------------------------------------------------------------------------
// Font override (independent of theme)
// ---------------------------------------------------------------------------

/** The active font-override id, mirrored at module scope so `applyTheme`
 *  can re-assert it after every theme switch (theme application rewrites
 *  `--theme-font-sans`, so the override has to win again afterwards). */
let _ACTIVE_FONT_OVERRIDE: string = THEME_DEFAULT_FONT_ID;

/** Apply (or clear) the font override on `:root`. When a catalog font is
 *  active we override `--theme-font-sans` and `--theme-font-display` and
 *  inject its webfont; the theme keeps ownership of `--theme-font-mono`
 *  (code/terminal) so picking a body font doesn't mangle code blocks.
 *  Passing the theme-default sentinel removes the override so the theme's
 *  own font shows through. */
function applyFontOverride(fontId: string | undefined) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  const choice: FontChoice | undefined = getFontChoice(fontId);
  if (!choice) {
    // Clear → fall back to whatever the active theme set (applyTheme already
    // wrote the theme's --theme-font-sans/-display before this runs).
    root.style.removeProperty("--theme-font-override-sans");
    return;
  }
  injectFontStylesheet(choice.fontUrl);
  // Set both the override marker var (used by the picker for diagnostics)
  // and the live consumed vars. We re-set the consumed vars directly so the
  // change is immediate and survives the next applyTheme via _ACTIVE_FONT_OVERRIDE.
  root.style.setProperty("--theme-font-override-sans", choice.stack);
  root.style.setProperty("--theme-font-sans", choice.stack);
  root.style.setProperty("--theme-font-display", choice.stack);
}

// ---------------------------------------------------------------------------
// Apply a full theme to :root
// ---------------------------------------------------------------------------

let transitionFrame = 0;
function applyTheme(theme: DashboardTheme) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  // All palette tokens change in one layout effect. Suppress existing hover /
  // surface transitions for this frame so intermediate colors never smear.
  let suppression = document.getElementById("korra-theme-no-transition");
  if (!suppression) {
    suppression = document.createElement("style");
    suppression.id = "korra-theme-no-transition";
    suppression.textContent = "*,*::before,*::after{transition:none!important}";
    document.head.append(suppression);
  }
  root.style.colorScheme = theme.name === "dark" ? "dark" : "light";
  cancelAnimationFrame(transitionFrame);
  transitionFrame = requestAnimationFrame(() => {
    void root.offsetHeight;
    transitionFrame = requestAnimationFrame(() => suppression?.remove());
  });

  // Clear any overrides from a previous theme before applying the new set.
  for (const cssVar of COLOR_OVERRIDE_CSS_VARS) {
    root.style.removeProperty(cssVar);
  }
  for (const cssVar of NEUMORPHISM_CSS_VARS) {
    root.style.removeProperty(cssVar);
  }
  // Same clear-then-set for series colors so switches never carry stale
  // chart accents from the previous palette.
  for (const cssVar of ALL_SERIES_VARS) {
    root.style.removeProperty(cssVar);
  }
  // Clear dynamic (asset/component) vars from the previous theme so the
  // new one starts clean — otherwise stale notched clip-paths, hero URLs,
  // etc. would bleed across theme switches.
  for (const prevKey of _PREV_DYNAMIC_VAR_KEYS) {
    root.style.removeProperty(prevKey);
  }

  const assetMap = assetVars(theme.assets);
  const componentMap = componentStyleVars(theme.componentStyles);
  _PREV_DYNAMIC_VAR_KEYS = new Set([
    ...Object.keys(assetMap),
    ...Object.keys(componentMap),
  ]);

  const vars = {
    ...paletteVars(theme.palette),
    ...typographyVars(theme.typography),
    ...layoutVars(theme.layout),
    ...colorOverrideVars(theme.colorOverrides),
    ...neumorphismVars(theme.neumorphism),
    ...seriesColorVars(theme.seriesColors),
    ...assetMap,
    ...componentMap,
  };
  for (const [k, v] of Object.entries(vars)) {
    root.style.setProperty(k, v);
  }

  injectFontStylesheet(theme.typography.fontUrl);
  applyCustomCSS(theme.customCSS);
  applyLayoutVariant(theme.layoutVariant);

  // Terminal colors — read by ChatPage via useTheme(); also available as CSS vars.
  root.style.setProperty(
    "--theme-terminal-background",
    theme.terminalBackground ?? "#000000",
  );
  root.style.setProperty(
    "--theme-terminal-foreground",
    theme.terminalForeground ?? "#f0e6d2",
  );

  // Re-assert the font override last: theme application just rewrote
  // --theme-font-sans/-display, so an active override has to win again.
  applyFontOverride(_ACTIVE_FONT_OVERRIDE);
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreference] = useState(readBootstrap);
  const preferenceRef = useRef(preference);
  const generation = useRef(0);
  const saveQueue = useRef<Promise<unknown>>(Promise.resolve());
  const desiredTheme = useRef(preference?.theme ?? "light");
  const mounted = useRef(true);
  const [themeName, setThemeName] = useState<string>(desiredTheme.current);
  const [saveState, setSaveState] = useState<"idle" | "pending" | "saved" | "error">("idle");
  const [saveError, setSaveError] = useState("");
  const [fontId, setFontId] = useState(() => {
    const stored = safeRead(FONT_STORAGE_KEY);
    const valid = stored && getFontChoice(stored) ? stored : THEME_DEFAULT_FONT_ID;
    _ACTIVE_FONT_OVERRIDE = valid;
    return valid;
  });
  const resolveTheme = useCallback((name: string): DashboardTheme =>
    BUILTIN_THEMES[migrateThemeName(name)] ?? defaultTheme, []);

  useLayoutEffect(() => {
    _ACTIVE_FONT_OVERRIDE = fontId;
    applyTheme(resolveTheme(themeName));
  }, [themeName, resolveTheme, fontId]);

  const acceptPreference = useCallback((next: ThemePreference) => {
    preferenceRef.current = next;
    cachePreference(next);
    if (mounted.current) setPreference(next);
  }, []);

  useEffect(() => {
    mounted.current = true;
    let cancelled = false;
    if (preferenceRef.current) cachePreference(preferenceRef.current);
    const initialGeneration = generation.current;
    api.getThemes().then((resp) => {
      // An older GET may arrive after a manual choice or its ACK. Neither the
      // visible theme nor its CAS revision may regress in that case.
      if (cancelled || generation.current !== initialGeneration) return;
      const next = validPreference(resp.preference);
      if (!next) return;
      acceptPreference(next);
      desiredTheme.current = next.theme;
      setThemeName(next.theme);
    }).catch(() => { /* Bootstrap remains authoritative; no healing write. */ });
    return () => { cancelled = true; mounted.current = false; };
  }, [acceptPreference]);

  useEffect(() => {
    let cancelled = false;
    api.getFontPref().then((resp) => {
      if (cancelled) return;
      const next = resp?.font && getFontChoice(resp.font) ? resp.font : THEME_DEFAULT_FONT_ID;
      setFontId(next);
      safeWrite(FONT_STORAGE_KEY, next);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const saveTheme = useCallback((name: string, refresh = false): Promise<boolean> => {
    const next = migrateThemeName(name);
    const thisGeneration = ++generation.current;
    desiredTheme.current = next;
    setThemeName(next);
    setSaveState("pending");
    setSaveError("");
    // Serialize writes: each explicit choice uses the previous durable ACK's
    // revision. The final user choice wins even with delayed responses.
    const saving = saveQueue.current.then(async () => {
      try {
        if (refresh || !preferenceRef.current) {
          const latest = validPreference((await api.getThemes()).preference);
          if (!latest) throw new Error("unknown preference");
          acceptPreference(latest);
        }
        const response = await api.setTheme(next, preferenceRef.current!.revision);
        const ack = validPreference(response.preference);
        if (!response.ok || !ack || ack.theme !== next) throw new Error("missing durable ACK");
        acceptPreference(ack);
        if (mounted.current && generation.current === thisGeneration) setSaveState("saved");
        return true;
      } catch {
        if (mounted.current && generation.current === thisGeneration) {
          setSaveState("error");
          setSaveError("Тема показана на этом экране, но не сохранена. Проверьте соединение и повторите.");
        }
        return false;
      }
    });
    saveQueue.current = saving;
    return saving;
  }, [acceptPreference]);
  const setTheme = useCallback((name: string) => saveTheme(name), [saveTheme]);
  const retryTheme = useCallback(() => saveTheme(desiredTheme.current, true), [saveTheme]);
  const setFont = useCallback((id: string) => {
    const next = getFontChoice(id) ? id : THEME_DEFAULT_FONT_ID;
    setFontId(next);
    safeWrite(FONT_STORAGE_KEY, next);
    api.setFontPref(next).catch(() => {});
  }, []);

  const value = useMemo<ThemeContextValue>(() => ({
    theme: resolveTheme(themeName), themeName, availableThemes: BUILTIN_THEME_ENTRIES,
    setTheme, preference, saveState, saveError, retryTheme,
    fontId, fontChoices: FONT_CHOICES, setFont,
  }), [themeName, setTheme, resolveTheme, preference, saveState, saveError, retryTheme, fontId, setFont]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
export function useTheme(): ThemeContextValue { return useContext(ThemeContext); }
const ThemeContext = createContext<ThemeContextValue>({
  theme: defaultTheme, themeName: "light", availableThemes: BUILTIN_THEME_ENTRIES,
  setTheme: async () => false, preference: null, saveState: "idle", saveError: "",
  retryTheme: async () => false,
  fontId: THEME_DEFAULT_FONT_ID, fontChoices: FONT_CHOICES, setFont: () => {},
});
interface ThemeContextValue {
  availableThemes: ThemeListEntry[];
  setTheme: (name: string) => Promise<boolean>;
  preference: ThemePreference | null;
  saveState: "idle" | "pending" | "saved" | "error";
  saveError: string;
  retryTheme: () => Promise<boolean>;
  theme: DashboardTheme;
  themeName: string;
  fontId: string;
  fontChoices: FontChoice[];
  setFont: (id: string) => void;
}
