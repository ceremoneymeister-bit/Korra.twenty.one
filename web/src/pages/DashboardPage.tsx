/**
 * DashboardPage — личный дашборд владельца контура.
 *
 * Первый срез: рабочая оболочка будущего экрана — шапка с назначением,
 * адаптивная сетка карточек и ограниченный режим настройки набора. Данных
 * здесь пока нет ни у одной карточки, и это намеренно: ноль вместо
 * неизвестного значения читается как «всё спокойно», а правдоподобная
 * цифра — как факт, и оба варианта дороже честного «источник ещё не
 * подключён». Каждая карточка говорит, что она покажет и откуда это
 * возьмётся, и уводит на экран, где сведения есть сегодня.
 *
 * Разделение ответственности:
 *   - `components/dashboard/widget-catalog` — какие карточки бывают;
 *   - `components/dashboard/widgets/*` — что показывает каждая из них;
 *   - `lib/dashboard-layout` — состав просмотра (чистые функции);
 *   - этот файл — шапка, сетка и режим настройки.
 *
 * Набор карточек живёт в памяти вкладки и сбрасывается при перезагрузке.
 * Страница говорит об этом прямо и не обещает синхронизацию между
 * устройствами: постоянное хранение — отдельный шаг с серверным контрактом,
 * а localStorage не должен стать источником правды явочным порядком.
 */

import { useState } from "react";
import { Check, Plus, RotateCcw } from "lucide-react";
import { Card } from "@nous-research/ui/ui/components/card";
import { DashboardWidgetCard } from "@/components/dashboard/DashboardWidgetCard";
import {
  DASHBOARD_WIDGETS,
  DASHBOARD_WIDGET_IDS,
} from "@/components/dashboard/widget-catalog";
import { ProductButton } from "@/components/ProductButton";
import {
  DEFAULT_HIDDEN_WIDGETS,
  hideWidget,
  isDefaultWidgetLayout,
  resetWidgets,
  restoreWidget,
  visibleWidgetIds,
} from "@/lib/dashboard-layout";
import { cn } from "@/lib/utils";

const CATALOG_ID = "dashboard-widget-catalog";

export default function DashboardPage() {
  const [hidden, setHidden] = useState<string[]>(() => [
    ...DEFAULT_HIDDEN_WIDGETS,
  ]);
  const [setupOpen, setSetupOpen] = useState(false);
  // Состав меняется без перезагрузки экрана, поэтому о результате действия
  // сообщаем голосом: иначе пользователь скринридера видит только то, что
  // фокус остался на кнопке.
  const [announcement, setAnnouncement] = useState("");

  const visibleIds = visibleWidgetIds(DASHBOARD_WIDGET_IDS, hidden);
  const visible = DASHBOARD_WIDGETS.filter((widget) =>
    visibleIds.includes(widget.id),
  );
  const isDefault = isDefaultWidgetLayout(hidden);

  const remove = (id: string, title: string) => {
    setHidden((prev) => hideWidget(DASHBOARD_WIDGET_IDS, prev, id));
    setAnnouncement(`Карточка «${title}» убрана. Вернуть её можно в каталоге.`);
  };

  const restore = (id: string, title: string) => {
    setHidden((prev) => restoreWidget(prev, id));
    setAnnouncement(`Карточка «${title}» вернулась на своё место.`);
  };

  const reset = () => {
    setHidden(resetWidgets());
    setAnnouncement("Восстановлен стандартный набор карточек.");
  };

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 pt-2">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 max-w-[70ch]">
          <h2 className="text-xl font-semibold text-[var(--neo-text-primary)]">
            Личный дашборд
          </h2>

          <p className="mt-2 text-sm leading-relaxed text-[var(--neo-text-secondary)]">
            Один экран о вашей работе: что требует решения, чем заняты агенты,
            что запланировано и что уже готово. Пока это каркас — карточки
            показывают, что появится и откуда будет взято, вместо придуманных
            цифр.
          </p>
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <ProductButton
            onClick={() => setSetupOpen(true)}
            aria-expanded={setupOpen}
            aria-controls={CATALOG_ID}
            prefix={<Plus className="size-4 shrink-0" aria-hidden />}
          >
            Добавить виджет
          </ProductButton>

          {setupOpen ? (
            <ProductButton
              outlined
              onClick={() => setSetupOpen(false)}
              prefix={<Check className="size-4 shrink-0" aria-hidden />}
            >
              Готово
            </ProductButton>
          ) : null}
        </div>
      </header>

      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>

      {setupOpen ? (
        <section id={CATALOG_ID} aria-labelledby="dashboard-catalog-title">
          <Card className="flex flex-col gap-4 p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0 max-w-[70ch]">
                <h3
                  id="dashboard-catalog-title"
                  className="text-base font-semibold text-[var(--neo-text-primary)]"
                >
                  Каталог карточек
                </h3>

                <p className="mt-1 text-sm leading-relaxed text-[var(--neo-text-secondary)]">
                  Выберите, что показывать на дашборде. Набор действует в этом
                  окне: после перезагрузки страницы вернётся стандартный —
                  сохранение появится вместе с хранением набора на сервере.
                </p>
              </div>

              <ProductButton
                outlined
                onClick={reset}
                disabled={isDefault}
                prefix={<RotateCcw className="size-4 shrink-0" aria-hidden />}
              >
                Вернуть стандартный набор
              </ProductButton>
            </div>

            <ul className="flex flex-col gap-2">
              {DASHBOARD_WIDGETS.map((widget) => {
                const shown = !hidden.includes(widget.id);
                return (
                  <li
                    key={widget.id}
                    data-catalog-widget={widget.id}
                    className="flex flex-wrap items-center justify-between gap-3 rounded-[var(--neo-radius-control)] px-3 py-2 shadow-[var(--neo-inset-compact)]"
                  >
                    <div className="min-w-0 max-w-[60ch]">
                      <p className="text-sm font-semibold text-[var(--neo-text-primary)]">
                        {widget.title}
                      </p>

                      <p className="mt-0.5 text-sm leading-relaxed text-[var(--neo-text-secondary)]">
                        {widget.purpose}
                      </p>
                    </div>

                    <div className="flex shrink-0 items-center gap-3">
                      {/* Состояние словом, а не только цветом кнопки. */}
                      <span className="text-sm text-[var(--neo-text-secondary)]">
                        {shown ? "На дашборде" : "Убрана"}
                      </span>

                      <ProductButton
                        outlined={shown}
                        size="sm"
                        onClick={() =>
                          shown
                            ? remove(widget.id, widget.title)
                            : restore(widget.id, widget.title)
                        }
                        aria-label={`${shown ? "Убрать" : "Вернуть"} карточку «${widget.title}»`}
                      >
                        {shown ? "Убрать" : "Вернуть"}
                      </ProductButton>
                    </div>
                  </li>
                );
              })}
            </ul>
          </Card>
        </section>
      ) : null}

      {visible.length > 0 ? (
        <ul
          aria-label="Карточки дашборда"
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3"
        >
          {visible.map((widget) => (
            <li
              key={widget.id}
              className={cn("min-w-0", widget.wide && "sm:col-span-2")}
            >
              <DashboardWidgetCard
                widgetId={widget.id}
                title={widget.title}
                purpose={widget.purpose}
                onRemove={
                  setupOpen
                    ? () => remove(widget.id, widget.title)
                    : undefined
                }
              >
                <widget.Body />
              </DashboardWidgetCard>
            </li>
          ))}
        </ul>
      ) : (
        <Card className="flex flex-col items-start gap-3 p-5">
          <p className="text-base font-semibold text-[var(--neo-text-primary)]">
            Все карточки убраны
          </p>

          <p className="max-w-[70ch] text-sm leading-relaxed text-[var(--neo-text-secondary)]">
            Дашборд остался пустым. Откройте каталог и верните нужные карточки
            или восстановите стандартный набор.
          </p>

          {/* Когда каталог уже открыт, вторая кнопка к нему не нужна. */}
          {setupOpen ? null : (
            <ProductButton
              onClick={() => setSetupOpen(true)}
              aria-controls={CATALOG_ID}
              prefix={<Plus className="size-4 shrink-0" aria-hidden />}
            >
              Открыть каталог карточек
            </ProductButton>
          )}
        </Card>
      )}
    </div>
  );
}
