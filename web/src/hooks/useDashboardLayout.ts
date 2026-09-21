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
 * - **Неудача записи прекращает очередь этого поколения.** Поколение кончается
 *   не в момент отказа, а когда разбирательство закончено и человек увидел,
 *   что на сервере. Нажатое вслепую — в том числе пока идёт перечитывание —
 *   силы не имеет: иначе следующее в очереди тихо перезапишет чужую запись
 *   новой ревизией.
 * - **Проигравший конфликт не откатывается молча.** Экран показывает
 *   победителя, называет причину словами и ждёт нового выбора человека —
 *   записывать снова может только он. Сравнивается при этом последний выбор
 *   человека, а не тот, с которого начался разбор.
 * - **Про чужую запись говорится только там, где она доказана.** Это ответ
 *   сервера с отказом по ревизии. Оборванная запись могла и дойти, и не
 *   дойти — тогда исход называется неизвестным, а не чьим-то.
 * - **Неактуальное чтение не меняет ничего.** Чтение, начатое раньше выбора
 *   человека, раньше исхода записи или раньше разбора отказа, к моменту
 *   ответа правды уже не знает: оно не трогает ни доску, ни состояние, ни
 *   сообщение. Пока очередь не затихла, чтение и не начинается.
 * - **Закрытый экран ничего не дописывает.** Ушедший запрос отменить нельзя,
 *   но то, что ещё стоит в очереди, за закрытым экраном не уходит: там уже
 *   может быть другой вход.
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
  const readRef = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      // Закрытый экран отменяет всё, что ещё не ушло: за ним может оказаться
      // уже другой человек, а запрос унесёт его же вход.
      readRef.current += 1;
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
    // Билет чтения. Выбор человека, исход записи и разбор отказа его
    // аннулируют: все они знают о сервере больше, чем чтение, начатое раньше.
    const read = ++readRef.current;
    // Пока запись в пути, сервер отвечает тем, что было до неё: показать этот
    // ответ — значит отыграть назад выбор, который ещё подтверждается. Ждём
    // очередь и проверяем билет заново: её исход может сделать чтение ненужным.
    await writeRef.current;
    if (read !== readRef.current || !mountedRef.current) return;
    try {
      const preference = await api.getDashboardLayout();
      if (read !== readRef.current) return;
      accept(preference);
      if (!mountedRef.current) return;
      setStatus("ready");
      setMessage("");
    } catch {
      if (read !== readRef.current || !mountedRef.current) return;
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
   * Сам по себе код отказа ещё не говорит, что случилось: 409 приходит и
   * когда запись перехватило другое окно, и когда сервер её не принял
   * (политика не дала сохранить — тогда в теле та же ревизия, что мы
   * отправили). Различает их сдвиг ревизии: запись ушла вперёд и это не наше
   * — значит писал кто-то другой; запись не двинулась — значит наше изменение
   * просто не сохранилось.
   *
   * Если сервера не добиться вовсе, исход остаётся неизвестным: запись могла
   * дойти и не подтвердиться. Об этом так и говорим, а не объявляем потерю.
   */
  const settle = useCallback(async (error: unknown, sent: number): Promise<void> => {
    const verdict = conflictWinner(error);
    let current = verdict;
    if (current === null) {
      try {
        current = await api.getDashboardLayout();
      } catch {
        readRef.current += 1;
        if (!mountedRef.current) return;
        setStatus("error");
        setMessage(
          "Не удалось проверить, сохранилось ли изменение: панель не отвечает. Нажмите «Повторить», когда связь вернётся.",
        );
        return;
      }
    }
    // Выяснённый исход новее любого чтения, начатого до него.
    readRef.current += 1;
    // Сверяемся с тем, что человек выбрал к этой минуте, а не к моменту
    // отказа: пока шло разбирательство, он мог нажать ещё раз, и это нажатие
    // очередь уже не понесёт — промолчать о нём значит потерять его.
    const intent = layoutRef.current;
    const arrived = accept(current);
    if (!mountedRef.current) return;
    if (sameLayout(arrived, intent)) {
      setStatus("ready");
      setMessage("");
      return;
    }
    setStatus("conflict");
    setMessage(
      verdict !== null && verdict.revision > sent
        ? "Дашборд изменился в другом окне — показано сохранённое там расположение. Повторите свой выбор, если он нужен."
        : current.revision === sent
          ? "Изменение не сохранилось: панель его не приняла. Показано сохранённое расположение — повторите выбор, если он нужен."
          : "Последний выбор не подтвердился: показано сохранённое на сервере расположение. Повторите выбор, если он нужен.",
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
      // Счётчик переводится не в момент отказа, а после разбирательства, так
      // что нажатое «вслепую» — и до известия о неудаче, и пока идёт
      // перечитывание — записываться не будет.
      const generation = generationRef.current;
      const mutation = ++mutationRef.current;
      // Выбор человека новее любого чтения, начатого до нажатия.
      readRef.current += 1;
      pendingRef.current += 1;
      setSaving(true);
      const write = writeRef.current.then(async () => {
        if (generation !== generationRef.current) return;
        // Экран закрыт — запись из очереди ещё не ушла и уйти не должна:
        // за тем же браузером уже может быть другой вход.
        if (!mountedRef.current) return;
        const revision = revisionRef.current;
        if (revision === null) return;
        try {
          const saved = await api.setDashboardLayout({
            revision,
            order: next.order,
            hidden: next.hidden,
            sizes: next.sizes,
          });
          // Подтверждённая запись новее любого чтения, начатого до ответа.
          readRef.current += 1;
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
          try {
            await settle(error, revision);
          } finally {
            generationRef.current += 1;
          }
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
