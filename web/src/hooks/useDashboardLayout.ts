import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
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
 * - **Ревизия серверная.** Каждая запись уходит с той ревизией, которую
 *   сервер подтвердил последней; записи выстраиваются в очередь, чтобы два
 *   быстрых нажатия не ушли с одной и той же.
 * - **Проигравший конфликт не откатывается молча.** Когда другое окно успело
 *   раньше, экран перечитывает победителя, показывает это словами и даёт
 *   повторить свой выбор поверх него.
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
  const pendingRef = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const accept = useCallback((preference: DashboardLayoutPreference) => {
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
      revisionRef.current = null;
      setStatus("error");
      setMessage(
        "Настройка дашборда сейчас не сохраняется: панель не отвечает. Показан стандартный набор.",
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
      const mutation = ++mutationRef.current;
      pendingRef.current += 1;
      setSaving(true);
      const write = writeRef.current.then(async () => {
        const revision = revisionRef.current;
        if (revision === null) return;
        try {
          const saved = await api.setDashboardLayout({
            revision,
            order: next.order,
            hidden: next.hidden,
            sizes: next.sizes,
          });
          // Более старая запись из очереди не должна визуально откатывать
          // более новый выбор, который ещё ждёт своей очереди.
          if (mutation === mutationRef.current) {
            accept(saved);
            if (mountedRef.current) {
              setStatus("ready");
              setMessage("");
            }
          } else {
            revisionRef.current = saved.revision;
          }
        } catch {
          // Либо другое окно успело раньше, либо панель недоступна. В обоих
          // случаях правду знает только сервер — перечитываем её.
          mutationRef.current += 1;
          try {
            const winner = await api.getDashboardLayout();
            const arrived = accept(winner);
            if (!mountedRef.current) return;
            if (sameLayout(arrived, next)) {
              setStatus("ready");
              setMessage("");
            } else {
              setStatus("conflict");
              setMessage(
                "Дашборд изменился в другом окне — показано сохранённое там расположение. Повторите свой выбор, если он нужен.",
              );
            }
          } catch {
            if (!mountedRef.current) return;
            setStatus("error");
            setMessage(
              "Изменение не сохранилось: панель не отвечает. Нажмите «Повторить», когда связь вернётся.",
            );
          }
        }
      });
      const settled = write.catch(() => {}).then(() => {
        pendingRef.current -= 1;
        if (mountedRef.current && pendingRef.current === 0) setSaving(false);
      });
      writeRef.current = settled;
    },
    [accept],
  );

  return { layout, status, saving, message, reload, apply };
}
