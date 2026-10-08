/**
 * DashboardPage — личный дашборд владельца контура.
 *
 * Экран состоит из полосы «Требует внимания» над сеткой и модульной сетки
 * плиток под ней. Размер плитки задаёт только буква — S 1×1, M 2×1, L 2×2, —
 * поэтому две карточки одного размера выглядят одинаково независимо от того,
 * что внутри.
 *
 * Разделение ответственности:
 *   - `components/dashboard/widget-catalog` — какие карточки бывают;
 *   - `components/dashboard/widgets/*` — что показывает каждая из них;
 *   - `lib/dashboard-layout` — правила состава (чистые функции);
 *   - `hooks/useDashboardLayout` — серверное хранение и конфликты;
 *   - этот файл — шапка, сетка и режим настройки.
 *
 * Раскладка живёт на сервере и принадлежит человеку, а не вкладке: собранная
 * на ноутбуке доска открывается такой же на телефоне. localStorage здесь нет
 * намеренно — он пережил бы смену человека за тем же браузером.
 *
 * Данные карточек приходят одной сводкой `/api/dashboard/state`
 * (`lib/dashboard-state`); «Агенты» дополнительно живут потоком работ чатов.
 * Каждая карточка сама показывает ожидание, сбой источника и первый шаг для
 * пустого источника — ни нулей вместо неизвестного, ни примеров вместо фактов.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useStore } from "@nanostores/react";
import { useSearchParams } from "react-router";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Plus,
  RefreshCw,
  RotateCcw,
  SlidersHorizontal,
  Sun,
} from "lucide-react";
import { Card } from "@nous-research/ui/ui/components/card";
import { CrmConnectDialog } from "@/components/dashboard/CrmConnectDialog";
import { DashboardWidgetCard } from "@/components/dashboard/DashboardWidgetCard";
import { DashboardWidgetBoundary } from "@/components/dashboard/DashboardWidgetBoundary";
import { WidgetSizePicker } from "@/components/dashboard/WidgetSizePicker";
import {
  DASHBOARD_CATALOG,
  findWidget,
  unavailableWidgetIds,
} from "@/components/dashboard/widget-catalog";
import { ProductButton } from "@/components/ProductButton";
import { useDashboardLayout } from "@/hooks/useDashboardLayout";
import {
  hideWidget,
  isDefaultLayout,
  moveWidget,
  resetLayout,
  resizeWidget,
  restoreWidget,
  restoreWidgets,
  visibleTileIds,
  visibleWidgetIds,
  widgetSize,
  withoutWidgets,
  type DashboardLayout,
  type WidgetSize,
} from "@/lib/dashboard-layout";
import { openCrmDialog } from "@/lib/crm";
import { $dashboardState } from "@/lib/dashboard-state";
import { cn } from "@/lib/utils";

import "@/components/dashboard/dashboard-grid.css";
import "@/components/dashboard/dashboard-widgets.css";

const CATALOG_ID = "dashboard-widget-catalog";

/**
 * Дата сегодняшнего дня — единственное, что шапка знает о человеке.
 *
 * Имени здесь нет намеренно: панель открывают и владелец, и тот, кому он дал
 * доступ, а придуманное обращение читается как подделка. Дата настоящая и
 * остаётся верной, пока вкладка открыта, — в отличие от времени.
 */
function today(now: Date = new Date()): string {
  const text = now.toLocaleDateString("ru-RU", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Приветствие по местному времени человека у экрана. «Хороший день.»
 *  читалось как оборванная фраза (Дмитрий, 28.09). */
export function greeting(now: Date = new Date()): string {
  const hour = now.getHours();
  if (hour >= 5 && hour < 12) return "Доброе утро";
  if (hour >= 12 && hour < 18) return "Добрый день";
  if (hour >= 18 && hour < 23) return "Добрый вечер";
  return "Доброй ночи";
}

/** Текущий момент, который обновляется сам: вкладка может висеть открытой
 *  с утра до вечера, и приветствие не должно отстать от часов. */
function useNow(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const tick = () => setNow(new Date());
    const timer = window.setInterval(tick, 60_000);
    document.addEventListener("visibilitychange", tick);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
    };
  }, []);
  return now;
}

export default function DashboardPage() {
  const now = useNow();
  const { apply, layout: stored, message, reload, saving, status } =
    useDashboardLayout(DASHBOARD_CATALOG);
  // Общая сводка карточек. Подписка страницы держит её опрос, пока доска
  // открыта, и говорит, каким карточкам на этой установке есть место.
  const dashboardState = useStore($dashboardState);
  const unavailable = useMemo(() => unavailableWidgetIds(dashboardState), [dashboardState]);
  // Доска и каталог видят только доступные карточки; хранимая раскладка
  // помнит и остальные, поэтому любое изменение возвращает их на места.
  const layout = useMemo(() => withoutWidgets(stored, unavailable), [stored, unavailable]);
  const [setupOpen, setSetupOpen] = useState(false);
  // Ссылка из чата агента: «подключить CRM» открывает окно и не остаётся в адресе.
  const [query, setQuery] = useSearchParams();
  useEffect(() => {
    if (query.get("crm") !== "connect") return;
    openCrmDialog("connect");
    const next = new URLSearchParams(query);
    next.delete("crm");
    setQuery(next, { replace: true });
  }, [query, setQuery]);
  // Состав меняется без перезагрузки экрана, поэтому о результате действия
  // сообщаем голосом: иначе пользователь скринридера видит только то, что
  // фокус остался на кнопке.
  const [announcement, setAnnouncement] = useState("");

  const visibleIds = useMemo(() => visibleWidgetIds(layout), [layout]);
  const pinnedIds = useMemo(
    () => visibleIds.filter((id) => DASHBOARD_CATALOG.pinned.includes(id)),
    [visibleIds],
  );
  const tileIds = useMemo(
    () => visibleTileIds(DASHBOARD_CATALOG, layout),
    [layout],
  );
  const isDefault = isDefaultLayout(DASHBOARD_CATALOG, stored);

  const change = useCallback(
    (next: DashboardLayout, said: string) => {
      if (next === layout) return;
      apply(restoreWidgets(next, stored, unavailable));
      setAnnouncement(said);
    },
    [apply, layout, stored, unavailable],
  );

  const remove = (id: string, title: string) =>
    change(
      hideWidget(DASHBOARD_CATALOG, layout, id),
      `Карточка «${title}» убрана. Вернуть её можно в каталоге.`,
    );

  const restore = (id: string, title: string) =>
    change(restoreWidget(layout, id), `Карточка «${title}» вернулась на своё место.`);

  const resize = (id: string, title: string, size: WidgetSize) =>
    change(
      resizeWidget(DASHBOARD_CATALOG, layout, id, size),
      `Размер карточки «${title}» изменён.`,
    );

  const move = (id: string, title: string, direction: -1 | 1) =>
    change(
      moveWidget(DASHBOARD_CATALOG, layout, id, direction),
      `Карточка «${title}» перемещена ${direction === -1 ? "левее" : "правее"}.`,
    );

  const reset = () =>
    change(resetLayout(DASHBOARD_CATALOG), "Восстановлен стандартный набор карточек.");

  return (
    <div className="korra-dashboard mx-auto flex w-full max-w-6xl flex-col gap-6 pt-2">
      <CrmConnectDialog />
      <header className="flex flex-row items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-sm text-[var(--neo-text-secondary)]">
            <Sun className="size-4 shrink-0" aria-hidden />
            <span className="min-w-0 truncate">{today(now)}</span>
          </p>

          <h2 className="mt-1 text-2xl font-semibold text-[var(--neo-text-primary)]">
            {greeting(now)}
          </h2>

          {/* На телефоне карточки сами говорят, что в них: строка пояснения
              только отодвигала их на пол-экрана. */}
          <p className="mt-1 hidden max-w-[60ch] text-sm text-[var(--neo-text-secondary)] sm:block">
            Что ждёт вашего решения, чем заняты агенты и что уже готово.
          </p>
        </div>

        <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
          {/* Настройка доски — второстепенное действие: не ярче «Принять» и
              «Ответить» в карточках. На телефоне — значок с подписью для
              экранного диктора. */}
          <ProductButton
            outlined
            onClick={() => setSetupOpen(true)}
            aria-expanded={setupOpen}
            aria-controls={CATALOG_ID}
            aria-label="Настроить дашборд"
            prefix={<SlidersHorizontal className="size-4 shrink-0" aria-hidden />}
          >
            <span className="max-sm:sr-only">Настроить</span>
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

      {message ? (
        <div
          className="flex flex-wrap items-center gap-3 rounded-[var(--neo-radius-control)] px-4 py-3 text-sm shadow-[var(--neo-inset-compact)]"
          data-layout-status={status}
          role={status === "ready" ? "status" : "alert"}
        >
          <span className="min-w-0 flex-1 text-[var(--neo-text-primary)]">{message}</span>
          <ProductButton
            outlined
            size="sm"
            onClick={() => void reload()}
            prefix={<RefreshCw className="size-4 shrink-0" aria-hidden />}
          >
            Повторить
          </ProductButton>
        </div>
      ) : null}

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
                  Выберите, что показывать, каким размером и в каком порядке.
                  Набор хранится за вами и открывается таким же в другом
                  браузере.
                  {saving ? " Сохраняем…" : ""}
                </p>
              </div>

              <ProductButton
                outlined
                onClick={reset}
                disabled={isDefault}
                prefix={<RotateCcw className="size-4 shrink-0" aria-hidden />}
                className="max-w-full whitespace-normal"
              >
                Вернуть стандартный набор
              </ProductButton>
            </div>

            <ul className="flex flex-col gap-2">
              {layout.order.map((id) => {
                const widget = findWidget(id);
                if (!widget) return null;
                const shown = !layout.hidden.includes(id);
                const tilePosition = tileIds.indexOf(id);
                return (
                  <li
                    key={id}
                    data-catalog-widget={id}
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

                    <div className="flex w-full min-w-0 max-w-full shrink-0 flex-wrap items-center gap-3 sm:w-auto">
                      {widget.pinned ? (
                        <span className="text-sm text-[var(--neo-text-secondary)]">
                          Полоса над сеткой
                        </span>
                      ) : (
                        <>
                          <WidgetSizePicker
                            widgetId={id}
                            title={widget.title}
                            value={widgetSize(DASHBOARD_CATALOG, layout, id)}
                            onChange={(size) => resize(id, widget.title, size)}
                          />

                          <div className="flex items-center gap-1">
                            <ProductButton
                              ghost
                              size="icon"
                              disabled={!shown || tilePosition <= 0}
                              onClick={() => move(id, widget.title, -1)}
                              aria-label={`Переместить карточку «${widget.title}» левее`}
                            >
                              <ChevronLeft aria-hidden />
                            </ProductButton>
                            <ProductButton
                              ghost
                              size="icon"
                              disabled={
                                !shown ||
                                tilePosition < 0 ||
                                tilePosition >= tileIds.length - 1
                              }
                              onClick={() => move(id, widget.title, 1)}
                              aria-label={`Переместить карточку «${widget.title}» правее`}
                            >
                              <ChevronRight aria-hidden />
                            </ProductButton>
                          </div>
                        </>
                      )}

                      {/* Состояние словом, а не только цветом кнопки. */}
                      <span className="text-sm text-[var(--neo-text-secondary)]">
                        {shown ? "На дашборде" : "Убрана"}
                      </span>

                      <ProductButton
                        outlined={shown}
                        size="sm"
                        onClick={() =>
                          shown
                            ? remove(id, widget.title)
                            : restore(id, widget.title)
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

      {visibleIds.length > 0 ? (
        <div className="korra-dashboard__board">
          {pinnedIds.length > 0 ? (
            <ul aria-label="Полоса дашборда" className="korra-dashboard__pinned">
              {pinnedIds.map((id) => (
                <WidgetTile
                  key={id}
                  id={id}
                  layout={layout}
                  onRemove={setupOpen ? remove : undefined}
                  pinned
                />
              ))}
            </ul>
          ) : null}

          {tileIds.length > 0 ? (
            <ul aria-label="Карточки дашборда" className="korra-dashboard__grid">
              {tileIds.map((id) => (
                <WidgetTile
                  key={id}
                  id={id}
                  layout={layout}
                  onRemove={setupOpen ? remove : undefined}
                />
              ))}
            </ul>
          ) : null}
        </div>
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

interface WidgetTileProps {
  id: string;
  layout: DashboardLayout;
  onRemove?: (id: string, title: string) => void;
  pinned?: boolean;
}

function WidgetTile({ id, layout, onRemove, pinned }: WidgetTileProps) {
  const widget = findWidget(id);
  if (!widget) return null;
  const size = widgetSize(DASHBOARD_CATALOG, layout, id);
  return (
    <li
      className={cn(!pinned && "korra-dashboard__tile")}
      data-size={pinned ? undefined : size}
    >
      <DashboardWidgetCard
        widgetId={id}
        title={widget.title}
        purpose={widget.purpose}
        size={pinned ? undefined : size}
        action={widget.action}
        headerNote={widget.HeaderNote ? <widget.HeaderNote size={pinned ? undefined : size} /> : undefined}
        onRemove={onRemove ? () => onRemove(id, widget.title) : undefined}
      >
        <DashboardWidgetBoundary title={widget.title} widgetId={id}>
          <widget.Body size={pinned ? undefined : size} />
        </DashboardWidgetBoundary>
      </DashboardWidgetCard>
    </li>
  );
}
