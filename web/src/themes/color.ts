/** Color math shared in behavior with korra_cli.dashboard_theme (pre-paint). */
export const DEFAULT_COLOR = "#5275d9";
export function validColor(value: unknown): value is string {
  return typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value);
}
const rgb = (hex: string) => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255);
// Stabilize half-channel rounding across JS and the Python pre-paint path.
const hex = (channels: number[]) => "#" + channels.map(c => Math.floor(c * 255 + .500000001).toString(16).padStart(2, "0")).join("");
function mix(a: string, b: string, weight: number): string {
  const other = rgb(b);
  return hex(rgb(a).map((c, i) => c * (1 - weight) + other[i] * weight));
}
export function luminance(color: string): number {
  const linear = rgb(color).map(c => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4);
  return linear[0] * .2126 + linear[1] * .7152 + linear[2] * .0722;
}
export function contrast(a: string, b: string): number {
  const x = luminance(a), y = luminance(b);
  return (Math.max(x, y) + .05) / (Math.min(x, y) + .05);
}
function hsl(color: string): [number, number, number] {
  const [r, g, b] = rgb(color);
  const max = Math.max(r, g, b), min = Math.min(r, g, b), delta = max - min;
  const l = (max + min) / 2;
  if (!delta) return [0, 0, l];
  const h = max === r ? (g - b) / delta + (g < b ? 6 : 0) : max === g ? (b - r) / delta + 2 : (r - g) / delta + 4;
  return [h / 6, delta / (1 - Math.abs(2 * l - 1)), l];
}
function fromHsl(h: number, s: number, l: number): string {
  return hex([0, 8, 4].map(n => {
    const k = (n + h * 12) % 12;
    return l - s * Math.min(l, 1 - l) * Math.max(-1, Math.min(k - 3, 9 - k, 1));
  }));
}
export function colorTokens(value: string) {
  const [h, s, rawL] = hsl(validColor(value) ? value : DEFAULT_COLOR);
  let l = Math.max(.35, Math.min(.6, rawL));
  let accent = fromHsl(h, s, l);
  const gradientEnd = (lightness: number) => mix(fromHsl((h + .05) % 1, s, lightness), "#ffffff", .89);
  // Both the surface and the shifted hue at the far end of the gradient.
  while (Math.min(contrast(accent, mix(accent, "#ffffff", .88)), contrast(accent, gradientEnd(l))) < 3) {
    l -= .005;
    accent = fromHsl(h, s, l);
  }
  const end = gradientEnd(l);
  let lineL = l;
  let accentLine = accent;
  while (Math.min(contrast(accentLine, mix(accent, "#ffffff", .88)), contrast(accentLine, end)) < 4.5) {
    lineL -= .005;
    accentLine = fromHsl(h, s, lineL);
  }
  const background = mix(accent, "#ffffff", .94);
  const surface = mix(accent, "#ffffff", .88);
  return {
    background, surface, accent,
    shadow: mix(surface, accent, .22),
    highlight: mix(surface, "#ffffff", .8),
    textPrimary: "#1f1f1f", textSecondary: "#505050",
    accentLine,
    accentForeground: contrast(accent, "#000000") >= contrast(accent, "#ffffff") ? "#000000" : "#ffffff",
    gradient: `linear-gradient(135deg, ${background}, ${end})`,
  };
}
