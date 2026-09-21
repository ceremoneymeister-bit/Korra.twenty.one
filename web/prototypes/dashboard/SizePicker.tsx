import { WIDGET_SIZES } from './model'
import type { WidgetId, WidgetSize } from './model'

export function SizePicker({
  id,
  title,
  value,
  onChange
}: {
  id: WidgetId
  title: string
  value: WidgetSize
  onChange: (size: WidgetSize) => void
}) {
  return (
    <fieldset className="dv-size-picker" aria-label={`Размер: ${title}`}>
      <legend className="dv-visually-hidden">Размер: {title}</legend>
      {WIDGET_SIZES.map(size => (
        <label key={size.id} className="dv-size-option">
          <input
            type="radio"
            name={`widget-size-${id}`}
            value={size.id}
            checked={value === size.id}
            aria-label={`${size.id.toUpperCase()} — ${size.label}`}
            onChange={() => onChange(size.id)}
          />
          <span className="dv-size-face">
            <i className={`dv-size-shape dv-size-shape--${size.id}`} aria-hidden="true" />
            <span>{size.id.toUpperCase()}</span>
            <small>{size.id === 's' ? '1×1' : size.id === 'm' ? '2×1' : '2×2'}</small>
          </span>
        </label>
      ))}
    </fieldset>
  )
}
