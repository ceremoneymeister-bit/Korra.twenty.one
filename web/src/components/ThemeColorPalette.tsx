import { Check } from "lucide-react";
import { useTheme } from "@/themes";
import { contrast, THEME_COLORS } from "@/themes/color";

/** The same palette in the sidebar and Appearance settings. */
export function ThemeColorPalette() {
  const { color, themeName, setTheme } = useTheme();
  return (
    <div className="theme-color-palette">
      <div className="theme-color-swatches" role="group" aria-label="Готовые цвета">
        {THEME_COLORS.map(([label, value]) => (
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
      <p>Цвет всего интерфейса. Текст и кнопки подстраиваются для читаемости.</p>
    </div>
  );
}
