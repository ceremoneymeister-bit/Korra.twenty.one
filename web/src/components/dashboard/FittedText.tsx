import { useAvailableHeight } from "@/hooks/useAvailableHeight";
import { cn } from "@/lib/utils";

/**
 * Текст, укороченный по целым строкам под реально доставшуюся высоту.
 *
 * Плитка дашборда не растягивается под содержимое, поэтому длинное
 * объяснение обязано где-то кончиться. Кончиться оно должно многоточием на
 * границе строки: половина буквы под обрезом читается как поломка вёрстки, а
 * не как «дальше есть ещё».
 *
 * Сколько строк показать, решает измеренная высота, а не размер плитки:
 * медиазапрос о ней ничего не знает — та же плитка оказывается короче при
 * более длинном заголовке или другом масштабе страницы.
 */
export interface FittedTextProps {
  text: string;
  /** Оформление самой строки; высоту строки задаёт `korra-widget-line`. */
  className?: string;
  /** Сколько показать до первого измерения — и в jsdom, где размеров нет. */
  linesBeforeMeasure?: number;
}

/**
 * Высота строки в пикселях — та же, что у `.korra-widget-line` в
 * `dashboard-grid.css`. Она задана явно и в пикселях не из любви к числам:
 * собственный интерлиньяж темы (≈21.4px при кегле 15px) меняется вместе с
 * оформлением, а счёт строк обязан совпадать с тем, что рисует браузер.
 */
const LINE_HEIGHT = 22;

export function FittedText({ className, linesBeforeMeasure = 2, text }: FittedTextProps) {
  const [ref, height] = useAvailableHeight<HTMLDivElement>();
  const lines =
    height === null ? linesBeforeMeasure : Math.floor(height / LINE_HEIGHT);

  // Окно меряем отдельным узлом: его высоту целиком задаёт остаток от соседей
  // по колонке, поэтому число строк не зависит от того, сколько их сейчас.
  return (
    <div ref={ref} className="min-h-0 flex-1 overflow-hidden">
      {lines > 0 ? (
        <p
          data-widget-note
          className={cn(
            "korra-widget-line overflow-hidden text-sm [-webkit-box-orient:vertical] [display:-webkit-box]",
            className,
          )}
          style={{ WebkitLineClamp: lines }}
        >
          {text}
        </p>
      ) : null}
    </div>
  );
}
