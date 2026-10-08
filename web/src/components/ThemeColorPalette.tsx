import { Check } from "lucide-react";
import { useEffect, useRef, useState, type CSSProperties, type PointerEvent, type KeyboardEvent } from "react";
import { useTheme } from "@/themes";
import { colorToHSV, hsvToColor, contrast, THEME_COLORS, validColor, type HSV } from "@/themes/color";

/** One in-page picker. Preview never writes; a completed gesture commits once. */
export function ThemeColorPalette() {
  const { color, scheme, themeName, setTheme, previewColor } = useTheme();
  const [draft, setDraft] = useState({ color, hsv: colorToHSV(color) });
  const hsv = draft.color === color ? draft.hsv : colorToHSV(color);
  const gesture = useRef({ color, hsv });
  const dirty = useRef(false);
  const committed = useRef(themeName === "color" ? color : null);
  const [hexDraft, setHexDraft] = useState<string | null>(null);
  const [hexError, setHexError] = useState(false);
  useEffect(() => () => previewColor(null), [previewColor]);

  function preview(next: HSV) {
    const hsv = { h: next.h, s: Math.max(0, Math.min(1, next.s)), v: Math.max(0, Math.min(1, next.v)) };
    const color = hsvToColor(hsv);
    dirty.current = true;
    gesture.current = { color, hsv };
    setDraft({ color, hsv });
    setHexDraft(null);
    setHexError(false);
    previewColor(color);
  }
  function commit() {
    if (!dirty.current) return;
    dirty.current = false;
    const value = gesture.current.color;
    if (committed.current === value) return;
    committed.current = value;
    void setTheme("color", value, scheme);
  }
  function point(event: PointerEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    preview({ ...hsv, s: (event.clientX - rect.left) / rect.width, v: 1 - (event.clientY - rect.top) / rect.height });
  }
  function arrows(event: KeyboardEvent<HTMLDivElement>) {
    if (event.target !== event.currentTarget) return;
    const delta = event.shiftKey ? .1 : .01;
    const movement: Record<string, Partial<HSV>> = {
      ArrowLeft: { s: hsv.s - delta }, ArrowRight: { s: hsv.s + delta },
      ArrowUp: { v: hsv.v + delta }, ArrowDown: { v: hsv.v - delta },
    };
    if (!movement[event.key]) return;
    event.preventDefault();
    preview({ ...hsv, ...movement[event.key] });
  }
  function choose(value: string) {
    dirty.current = false;
    committed.current = value;
    gesture.current = { color: value, hsv: colorToHSV(value) };
    setDraft(gesture.current);
    setHexDraft(null);
    setHexError(false);
    void setTheme("color", value, scheme);
  }
  function saveHex() {
    if (hexDraft === null) return;
    const raw = hexDraft.trim();
    const value = raw.startsWith("#") ? raw : `#${raw}`;
    if (!validColor(value)) { setHexError(true); return; }
    choose(value.toLowerCase());
  }
  const sliderKeys = (event: KeyboardEvent) => {
    if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"].includes(event.key)) commit();
  };
  return (
    <div className="theme-color-palette">
      <div className="theme-color-field" role="group" tabIndex={0}
        aria-label="Оттенок: влево и вправо — насыщенность, вверх и вниз — яркость"
        style={{ "--picker-hue": hsv.h } as CSSProperties}
        onPointerDown={event => { event.currentTarget.setPointerCapture(event.pointerId); point(event); }}
        onPointerMove={event => { if (event.currentTarget.hasPointerCapture(event.pointerId)) point(event); }}
        onPointerUp={event => { event.currentTarget.releasePointerCapture(event.pointerId); commit(); }}
        onPointerCancel={() => { dirty.current = false; previewColor(null); }}
        onKeyDown={arrows} onKeyUp={sliderKeys}
      >
        <span className="theme-color-cursor" style={{ left: `${hsv.s * 100}%`, top: `${(1-hsv.v) * 100}%` }} />
      </div>
      <input className="theme-hue" type="range" min={0} max={360} step={1} value={hsv.h}
        aria-label="Цветовой тон" aria-valuetext={`${Math.round(hsv.h)}°`}
        onChange={event => preview({ ...hsv, h: Number(event.target.value) })}
        onPointerUp={commit} onKeyUp={sliderKeys} onBlur={commit} />
      <div className="theme-accessible-sliders">
        <input type="range" aria-label="Насыщенность" min={0} max={100} value={Math.round(hsv.s * 100)}
          onChange={event => preview({ ...hsv, s: Number(event.target.value) / 100 })} onKeyUp={sliderKeys} onBlur={commit} />
        <input type="range" aria-label="Яркость цвета" min={0} max={100} value={Math.round(hsv.v * 100)}
          onChange={event => preview({ ...hsv, v: Number(event.target.value) / 100 })} onKeyUp={sliderKeys} onBlur={commit} />
      </div>
      <div className="theme-color-swatches" role="group" aria-label="Готовые цвета">
        {THEME_COLORS.map(([label, value]) => (
          <button key={value} type="button" title={label} aria-label={label}
            aria-pressed={themeName === "color" && color === value} onClick={() => choose(value)}>
            <span style={{ background: value, color: contrast(value, "#000000") >= 4.5 ? "#000000" : "#ffffff" }}>
              {themeName === "color" && color === value && <Check size={16} aria-hidden />}
            </span>
          </button>
        ))}
      </div>
      <div className="theme-color-details">
        <span className="theme-color-sample" style={{ background: color }} aria-hidden />
        <input className="theme-hex" type="text" aria-label="Код цвета HEX" spellCheck={false} maxLength={7}
          value={hexDraft ?? color.toUpperCase()} aria-invalid={hexError || undefined}
          onChange={event => { setHexDraft(event.target.value); setHexError(false); }} onBlur={saveHex}
          onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); saveHex(); } }} />
        <button className="theme-color-reset" type="button" onClick={() => {
          dirty.current = false; committed.current = null; setHexDraft(null); setHexError(false); void setTheme(scheme);
        }}>Стандартная</button>
      </div>
      {hexError && <p className="theme-color-error" role="alert">Введите цвет в формате #AABBCC</p>}
    </div>
  );
}
