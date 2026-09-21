import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { DashboardLayoutPreference } from "@/lib/api";
import {
  defaultLayout,
  normalizeLayout,
  sameLayout,
  type DashboardLayout,
  type WidgetCatalogShape,
} from "@/lib/dashboard-layout";

/**
 * Личная раскладка дашборда, хранимая на сервере.
 *
 * Правила, которые здесь удерживаются:
 *
 * - **Источник правды — сервер, не вкладка.** localStorage тут не заводится
 *   даже как кэш: он пережил бы смену человека за тем же браузером и тихо
 *   показал бы чужую доску.
 * - **Ревизия серверная и монотонная.** Каждая запись уходит с той ревизией,
 *   которую сервер подтвердил последней; записи выстраиваются в очередь,
 *   чтобы два быстрых нажатия не ушли с одной и той же. Ответ с меньшей
 *   ревизией — опоздавшее чтение, и он не отыгрывает назад уже подтверждённое.
 * - **Неудача записи прекращает очередь этого поколения.** Пока человек не
 *   увидел, что на сервере, остальные его нажатия не имеют силы: иначе
 *   следующее в очереди тихо перезапишет чужую запись новой ревизией.
 * - **Проигравший конфликт не откатывается молча.** Экран показывает
 *   победителя, называет причину словами и ждёт нового выбора человека —
 *   записывать снова может только он.
 * - **Пока сервер недоступен, доска работает.** Видно стандартный набор и
 *   явную отметку, что настройка не сохраняется; нажатие «Повторить»
 *   возвращает к живому состоянию.
 */
export type LayoutStatus = "loading" | "ready" | "error" | "conflict";

export interface UseDashboardLayoutReturn {
  layout: DashboardLayout;
  status: LayoutStatus;
  /** Идёт запись на сервер. */
  saving: boolean;
  /** Что именно случилось — человеку, а не в консоль. */
  message: string;
  /** Перечитать состояние с сервера. */
  reload: () => Promise<void>;
  /** Применить новую раскладку и сохранить её. */
  apply: (next: DashboardLayout) => void;
}

/**
 * Победившая запись из тела 409 — сервер прикладывает её к отказу.
 *
 * Отдельное чтение после конфликта успело бы разойтись с этим ответом, да и
 * лишний запрос делать незачем. Ответ другой формы (прокси, старая панель)
 * не принимаем — тогда остаётся обычное перечитывание.
 */
function conflictWinner(error: unknown): DashboardLayoutPreference | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const payload = error.payload as { preference?: unknown } | null | undefined;
  const preference = payload?.preference as DashboardLayoutPreference | undefined;
  if (!preference || typeof preference.revision !== "number") return null;
  return preference;
}

export function useDashboardLayout(
  catalog: WidgetCatalogShape,
): UseDashboardLayoutReturn {
  const [layout, setLayout] = useState<DashboardLayout>(() => defaultLayout(catalog));
  const [status, setStatus] = useState<LayoutStatus>("loading");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  const layoutRef = useRef(layout);
  const revisionRef = useRef<number | null>(null);
  const writeRef = useRef<Promise<void>>(Promise.resolve());
  const mutationRef = useRef(0);
  const generationRef = useRef(0);
  const pendingRef = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const accept = useCallback((preference: DashboardLayoutPreference) => {
    const known = revisionRef.current;
    if (known !== null && preference.revision < known) {
      // Ревизия сервера только растёт, значит этот ответ отстал от уже
      // подтверждённой записи. Принять его — показать человеку откат.
      return layoutRef.current;
    }
    revisionRef.current = preference.revision;
    const next = normalizeLayout(catalog, preference);
    layoutRef.current = next;
    if (mountedRef.current) setLayout((previous) => (sameLayout(previous, next) ? previous : next));
    return next;
  }, [catalog]);

  const reload = useCallback(async (): Promise<void> => {
    try {
      const preference = await api.getDashboardLayout();
      accept(preference);
      if (!mountedRef.current) return;
      setStatus("ready");
      setMessage("");
    } catch {
      if (!mountedRef.current) return;
      setStatus("error");
      setMessage(
        revisionRef.current === null
          ? "Настройка дашборда сейчас не сохраняется: панель не отвечает. Показан стандартный набор."
          : "Не удалось перечитать дашборд: панель не отвечает. Показано последнее известное расположение.",
      );
    }
  }, [accept]);

  useEffect(() => {
    // Первое чтение — внешний запрос, а не мгновенная правка состояния:
    // состояние меняется уже в его ответе.
    void (async () => {
      await reload();
    })();
  }, [reload]);

  /**
   * Разобраться, что на сервере, после неудачной записи.
   *
   * Сюда попадают два разных случая, и путать их нельзя: проигранный CAS —
   * это чужая запись, а любой другой отказ (5xx, обрыв) оставляет исход
   * записи неизвестным. Правду в обоих случаях знает сервер, поэтому
   * состояние берём у него и сверяем с последним выбором человека: если оно
   * не совпало, «сохранено» говорить нельзя.
   */
  const settle = useCallback(async (error: unknown): Promise<void> => {
    const intent = layoutRef.current;
    const lost = error instanceof ApiError && error.status === 409;
    let current = conflictWinner(error);
    if (current === null) {
      try {
        current = await api.getDashboardLayout();
      } catch {
        if (!mountedRef.current) return;
        setStatus("error");
        setMessage(
          "Изменение не сохранилось: панель не отвечает. Нажмите «Повторить», когда связь вернётся.",
        );
        return;
      }
    }
    const arrived = accept(current);
    if (!mountedRef.current) return;
    if (sameLayout(arrived, intent)) {
      setStatus("ready");
      setMessage("");
      return;
    }
    setStatus("conflict");
    setMessage(
      lost
        ? "Дашборд изменился в другом окне — показано сохранённое там расположение. Повторите свой выбор, если он нужен."
        : "Изменение не сохранилось: панель ответила ошибкой. Показано сохранённое расположение — повторите выбор, если он нужен.",
    );
  }, [accept]);

  const apply = useCallback(
    (next: DashboardLayout) => {
      // Показываем выбор сразу: ожидание сети на каждом нажатии превращает
      // настройку доски в анкету.
      layoutRef.current = next;
      setLayout(next);
      if (revisionRef.current === null) {
        // Состояния сервера ещё нет — записывать нечего и не с чем сверяться.
        setMessage(
          "Выбор действует в этом окне: панель не отвечает, и сохранить его пока нельзя.",
        );
        return;
      }
      // Поколение — это то, что человек знал о сервере, когда нажимал.
      // Первая же неудача переводит счётчик, и всё нажатое «вслепую» до
      // известия о ней записываться не будет.
      const generation = generationRef.current;
      const mutation = ++mutationRef.current;
      pendingRef.current += 1;
      setSaving(true);
      const write = writeRef.current.then(async () => {
        if (generation !== generationRef.current) return;
        const revision = revisionRef.current;
        if (revision === null) return;
        try {
          const saved = await api.setDashboardLayout({
            revision,
            order: next.order,
            hidden: next.hidden,
            sizes: next.sizes,
          });
          if (mutation !== mutationRef.current) {
            // Более старая запись из очереди не должна визуально откатывать
            // более новый выбор, который ещё ждёт своей очереди.
            revisionRef.current = saved.revision;
            return;
          }
          accept(saved);
          if (!mountedRef.current) return;
          setStatus("ready");
          setMessage("");
        } catch (error) {
          generationRef.current += 1;
          await settle(error);
        }
      });
      const settled = write.catch(() => {}).then(() => {
        pendingRef.current -= 1;
        if (mountedRef.current && pendingRef.current === 0) setSaving(false);
      });
      writeRef.current = settled;
    },
    [accept, settle],
  );

  return { layout, status, saving, message, reload, apply };
}
