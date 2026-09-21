import {
  WIDGET_SIZES,
  WIDGET_SIZE_LABELS,
  type WidgetSize,
} from "@/lib/dashboard-layout";

/**
 * Выбор размера плитки — обычная radio-group.
 *
 * Именно `input[type=radio]`, а не три кнопки: стрелки, Home/End и чтение с
 * экрана работают как в любой форме, без собственной обработки клавиш.
 * Буква видна, полное название уходит в доступное имя — «S» вслух ничего не
 * значит.
 */
export interface WidgetSizePickerProps {
  widgetId: string;
  title: string;
  value: WidgetSize;
  onChange: (size: WidgetSize) => void;
}

export function WidgetSizePicker({
  onChange,
  title,
  value,
  widgetId,
}: WidgetSizePickerProps) {
  return (
    <fieldset className="korra-size-picker" data-size-picker={widgetId}>
      <legend className="sr-only">{`Размер карточки «${title}»`}</legend>
      {WIDGET_SIZES.map((size) => (
        <label className="korra-size-option" key={size}>
          <input
            aria-label={`${WIDGET_SIZE_LABELS[size].name} размер карточки «${title}»`}
            checked={value === size}
            name={`widget-size-${widgetId}`}
            onChange={() => onChange(size)}
            type="radio"
            value={size}
          />
          <span aria-hidden className="korra-size-face">
            {WIDGET_SIZE_LABELS[size].letter}
          </span>
        </label>
      ))}
    </fieldset>
  );
}
