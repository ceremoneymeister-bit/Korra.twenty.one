import type { DashboardTheme } from "./types";

export type ColorScheme = "light" | "dark";
export const DEFAULT_COLOR = "#5275d9";
export const THEME_COLORS = [
  ["Лавандовый", "#ded5f0"], ["Голубой", "#92b7ec"], ["Мятный", "#91c7b1"],
  ["Песочный", "#dcc599"], ["Розовый", "#dba7b8"], ["Сиреневый", "#b4a4d9"],
];
export function validColor(value: unknown): value is string {
  return typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value);
}
const rgb = (hex: string) => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255);
const hex = (channels: number[]) => "#" + channels.map(c => Math.floor(Math.max(0, Math.min(1, c)) * 255 + .500000001).toString(16).padStart(2, "0")).join("");
export function luminance(color: string): number {
  return rgb(color).reduce((sum, c, i) => sum + (c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4) * [.2126, .7152, .0722][i], 0);
}
export function contrast(a: string, b: string): number {
  const x = luminance(a), y = luminance(b);
  return (Math.max(x, y) + .05) / (Math.min(x, y) + .05);
}
/** Only old preferences infer a scheme from the seed. New choices keep it explicit. */
export function colorScheme(color: string, scheme?: ColorScheme): ColorScheme {
  return scheme ?? (luminance(validColor(color) ? color : DEFAULT_COLOR) < .35 ? "dark" : "light");
}
export function themeColorScheme(theme: DashboardTheme): ColorScheme {
  return luminance(theme.palette.background.hex) < .35 ? "dark" : "light";
}
function chromaHue(color: string): [number, number] {
  const [r, g, b] = rgb(color).map(c => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4);
  const l = Math.cbrt(.4122214708*r + .5363325363*g + .0514459929*b);
  const m = Math.cbrt(.2119034982*r + .6806995451*g + .1073969566*b);
  const s = Math.cbrt(.0883024619*r + .2817188376*g + .6299787005*b);
  const a = 1.9779984951*l - 2.428592205*m + .4505937099*s;
  const bb = .0259040371*l + .7827717662*m - .808675766*s;
  return [Math.hypot(a, bb), Math.atan2(bb, a)];
}
/** OKLCH roles, with chroma reduced into sRGB. Keep math in sync with dashboard_theme.py. */
function tone(lightness: number, chroma: number, hue: number): string {
  let channels: number[] = [];
  for (let step = 0; step <= 80; step++) {
    const a = chroma * Math.cos(hue), b = chroma * Math.sin(hue);
    const l = (lightness + .3963377774*a + .2158037573*b) ** 3;
    const m = (lightness - .1055613458*a - .0638541728*b) ** 3;
    const s = (lightness - .0894841775*a - 1.291485548*b) ** 3;
    channels = [4.0767416621*l - 3.3077115913*m + .2309699292*s,
      -1.2684380046*l + 2.6097574011*m - .3413193965*s,
      -.0041960863*l - .7034186147*m + 1.707614701*s];
    if (channels.every(c => c >= 0 && c <= 1)) break;
    chroma *= .94;
  }
  return hex(channels.map(c => c <= .0031308 ? 12.92*c : 1.055*c ** (1/2.4) - .055));
}
export function colorTokens(value: string, scheme?: ColorScheme) {
  const color = validColor(value) ? value.toLowerCase() : DEFAULT_COLOR;
  const dark = colorScheme(color, scheme) === "dark";
  const [c, h] = chromaHue(color), tint = Math.min(c, .042);
  const accentChroma = c < .003 ? 0 : Math.min(.17, Math.max(.07, c * 1.6));
  const role = (light: number, night: number, amount: number) => tone(dark ? night : light, tint * amount, h);
  const background = role(.92, .19, .55), surface = role(.955, .265, .45);
  const accent = tone(dark ? .79 : .44, accentChroma, h);
  const destructive = dark ? "#ffb4b9" : "#9f2433";
  const on = (bg: string) => contrast(bg, "#000000") >= contrast(bg, "#ffffff") ? "#000000" : "#ffffff";
  return {
    dark, background, surface,
    rail: role(.89, .215, .72), field: role(.91, .225, .48),
    elevated: role(.978, .31, .25), selected: role(.855, .355, .9),
    shadow: role(.78, .16, .45), highlight: role(.99, .32, .2),
    textPrimary: tone(dark ? .95 : .255, .012, h),
    textSecondary: tone(dark ? .77 : .445, .018, h),
    accent, accentLine: accent, accentForeground: on(accent),
    destructive, destructiveForeground: on(destructive),
    success: dark ? "#ace4bc" : "#20583f", warning: dark ? "#efd69b" : "#6e4909",
    gradient: `linear-gradient(135deg, ${background}, ${role(.93, .205, .55)})`,
  };
}

export interface HSV { h: number; s: number; v: number }
export function colorToHSV(color: string, previousHue = 0): HSV {
  const [r, g, b] = rgb(color), max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  const h = d ? ((max === r ? (g-b)/d : max === g ? 2+(b-r)/d : 4+(r-g)/d)*60+360)%360 : previousHue;
  return { h, s: max ? d/max : 0, v: max };
}
export function hsvToColor({ h, s, v }: HSV): string {
  const channel = (n: number) => { const k = (n + h/60)%6; return v - v*s*Math.max(0, Math.min(k, 4-k, 1)); };
  return hex([channel(5), channel(3), channel(1)]);
}
