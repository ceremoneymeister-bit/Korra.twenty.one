import { Check } from "lucide-react";
import { useTheme } from "@/themes";
import { contrast } from "@/themes/color";

const COLORS = [
  ["Синий", "#5275d9"], ["Фиолетовый", "#8657cf"], ["Розовый", "#d95791"],
  ["Красный", "#cf5252"], ["Оранжевый", "#d88736"], ["Жёлтый", "#dfc34a"],
  ["Зелёный", "#55985c"], ["Бирюзовый", "#369c94"], ["Голубой", "#489cbe"], ["Серый", "#777c89"],
];

/** The same palette in the sidebar and Appearance settings. */
export function ThemeColorPalette() {
  const { color, themeName, setTheme } = useTheme();
  return (
    <div className="theme-color-palette">
      <div className="theme-color-swatches" role="group" aria-label="Готовые цвета">
        {COLORS.map(([label, value]) => (
          <button
            key={value} type="button" title={label} aria-label={label}
            aria-pressed={themeName === "color" && color === value}
            onClick={() => void setTheme("color", value)}
          >
            <span style={{ background: value, color: contrast(value, "#000000") >= 4.5 ? "#000000" : "#ffffff" }}>
              {themeName === "color" && color === value && <Check size={18} aria-hidden />}
            </span>
          </button>
        ))}
      </div>
      <label className="theme-custom-color">
        <input type="color" aria-label="Любой цвет" value={color} onChange={event => void setTheme("color", event.target.value)} />
        <span>Любой цвет</span>
        <span className="theme-color-hex">{color.toUpperCase()}</span>
      </label>
      <p>Мягкий оттенок фона. Яркость акцента подстраивается для читаемости.</p>
    </div>
  );
}
