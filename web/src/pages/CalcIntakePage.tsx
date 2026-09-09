import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";

import { IntakePreparationPanel } from "@/components/IntakePreparationPanel";
import { ProductButton } from "@/components/ProductButton";
import { usePageHeader } from "@/contexts/usePageHeader";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import {
  createIntakeHandoff,
  findIntakeHandoff,
  getIntakeHandoff,
  type IntakeHandoff,
} from "@/lib/calc-intake-handoff";
import type { OrderCard } from "@/lib/calc-orders";
import { fetchJSON } from "@/lib/api";
import { ownerFacingError } from "@/lib/owner-facing-error";

const POLL_MS = 1_500;

function belongsToOrder(record: IntakeHandoff, orderId: string): IntakeHandoff {
  if (record.order_id !== orderId) {
    throw new Error("Ответ приёмки относится к другому заказу.");
  }
  return record;
}

export default function CalcIntakePage() {
  const { orderId = "" } = useParams();
  const { setTitle } = usePageHeader();
  const [order, setOrder] = useState<OrderCard | null>(null);
  const [handoff, setHandoff] = useState<IntakeHandoff | null>(null);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [reconcileRequired, setReconcileRequired] = useState(false);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const lifecycle = useRef(0);
  const startingRef = useRef(false);
  const orderName = order?.folder_name || handoff?.order_name || order?.order_id;

  useEffect(() => {
    setTitle(orderName ? `Заказы / ${orderName} / Приёмка` : "Приёмка заказа");
    return () => setTitle(null);
  }, [orderName, setTitle]);

  const poll = useCallback(async (initial: IntakeHandoff, version: number) => {
    let current = initial;
    while (current.initial_run_active) {
      current = belongsToOrder(await getIntakeHandoff(current.handoff_id), orderId);
      if (lifecycle.current !== version) return;
      setHandoff(current);
      if (current.initial_run_active) {
        await new Promise((resolve) => window.setTimeout(resolve, POLL_MS));
        if (lifecycle.current !== version) return;
      }
    }
  }, [orderId]);

  useEffect(() => {
    const version = ++lifecycle.current;
    setOrder(null);
    setHandoff(null);
    setLoading(true);
    startingRef.current = false;
    setStarting(false);
    setRefreshing(false);
    setReconcileRequired(false);
    setError(null);
    setActionError(null);
    void Promise.all([
      fetchJSON<OrderCard>(`/api/calc/orders/${encodeURIComponent(orderId)}`),
      findIntakeHandoff(orderId),
    ]).then(([nextOrder, lookup]) => {
      if (lifecycle.current !== version) return;
      if (nextOrder.order_id !== orderId) throw new Error("Открыт другой заказ.");
      const restored = lookup.handoff
        ? belongsToOrder(lookup.handoff, orderId)
        : null;
      setOrder(nextOrder);
      setHandoff(restored);
      setLoading(false);
      if (restored?.initial_run_active) {
        void poll(restored, version).catch((cause) => {
          if (lifecycle.current === version) {
            setActionError(ownerFacingError(cause, "Не удалось обновить состояние приёмки."));
          }
        });
      }
    }).catch((cause) => {
      if (lifecycle.current !== version) return;
      setError(ownerFacingError(cause, "Не удалось открыть приёмку заказа."));
      setLoading(false);
    });
    return () => {
      lifecycle.current += 1;
    };
  }, [orderId, poll, reload]);

  const refreshStatus = useCallback(async () => {
    if (!handoff || refreshing) return;
    const version = ++lifecycle.current;
    setRefreshing(true);
    setActionError(null);
    try {
      const current = belongsToOrder(await getIntakeHandoff(handoff.handoff_id), orderId);
      if (lifecycle.current !== version) return;
      setHandoff(current);
      await poll(current, version);
    } catch (cause) {
      if (lifecycle.current === version) {
        setActionError(ownerFacingError(cause, "Не удалось обновить состояние приёмки."));
      }
    } finally {
      if (lifecycle.current === version) setRefreshing(false);
    }
  }, [handoff, orderId, poll, refreshing]);

  const reconcileStart = useCallback(async (version: number) => {
    setRefreshing(true);
    try {
      const lookup = await findIntakeHandoff(orderId);
      if (lifecycle.current !== version) return;
      const restored = lookup.handoff
        ? belongsToOrder(lookup.handoff, orderId)
        : null;
      setHandoff(restored);
      setReconcileRequired(false);
      if (restored) setActionError(null);
      if (restored?.initial_run_active) {
        try {
          await poll(restored, version);
        } catch (cause) {
          if (lifecycle.current === version) {
            setActionError(ownerFacingError(cause, "Не удалось обновить состояние приёмки."));
          }
        }
      }
    } catch (cause) {
      if (lifecycle.current === version) {
        setReconcileRequired(true);
        setActionError(ownerFacingError(cause, "Не удалось сверить результат запуска."));
      }
    } finally {
      if (lifecycle.current === version) setRefreshing(false);
    }
  }, [orderId, poll]);

  const start = useCallback(async () => {
    if (startingRef.current) return;
    startingRef.current = true;
    const version = ++lifecycle.current;
    setStarting(true);
    setReconcileRequired(false);
    setActionError(null);
    try {
      const created = belongsToOrder(await createIntakeHandoff(orderId), orderId);
      if (lifecycle.current !== version) return;
      setHandoff(created);
      await poll(created, version);
    } catch (cause) {
      if (lifecycle.current === version) {
        setReconcileRequired(true);
        setActionError(ownerFacingError(cause, "Ответ запуска не получен. Проверяем сохранённое состояние."));
        await reconcileStart(version);
      }
    } finally {
      if (lifecycle.current === version) {
        startingRef.current = false;
        setStarting(false);
      }
    }
  }, [orderId, poll, reconcileStart]);

  if (loading) {
    return <div className="flex min-h-64 items-center justify-center" role="status" aria-label="Загрузка приёмки"><Spinner /></div>;
  }

  const back = `/orders?order=${encodeURIComponent(orderId)}`;
  if (error || !order) {
    return <div className="mx-auto max-w-xl space-y-4 py-10">
      <p role="alert">{error ?? "Заказ не найден."}</p>
      <ProductButton outlined onClick={() => setReload((value) => value + 1)}>Обновить состояние</ProductButton>
      <Link to={back} className="inline-flex min-h-11 items-center text-primary">Вернуться к заказам</Link>
    </div>;
  }

  const safeRetry = handoff?.status === "needs_attention" && !handoff.run_id
    && ["session_create_failed", "runtime_unavailable"].includes(handoff.error_code ?? "");
  const uncertain = ["dispatch_unknown", "dispatch_interrupted", "run_unavailable"]
    .includes(handoff?.error_code ?? "");
  return <section className="mx-auto w-full max-w-6xl space-y-5 pb-8">
    <nav aria-label="Навигация по заказу">
      <Link to={back} className="inline-flex min-h-11 items-center text-sm text-primary">← К заказу</Link>
    </nav>

    {!handoff && !reconcileRequired && <section className="space-y-3 rounded-xl border border-border bg-background p-4">
      <p>Приёмка для этого заказа ещё не начиналась.</p>
      <ProductButton disabled={starting} onClick={() => void start()}>
        {starting ? <><Spinner /> Начинаем…</> : "Начать приёмку"}
      </ProductButton>
    </section>}

    {handoff?.status === "stale" && !reconcileRequired && <section role="alert" className="space-y-3 rounded-xl border border-warning/60 bg-warning/10 p-4">
      <p>Комплект документов изменился. Передайте актуальную версию заказа в приёмку.</p>
      <ProductButton disabled={starting} onClick={() => void start()}>
        {starting ? <><Spinner /> Обновляем…</> : "Передать актуальный комплект"}
      </ProductButton>
    </section>}

    {handoff?.initial_run_active && <p role="status">Приёмщик получает комплект…</p>}
    {safeRetry && !reconcileRequired && <section className="space-y-3 rounded-xl border border-border bg-background p-4">
      <p>Приёмка не успела запуститься. Сохранённую связь можно безопасно использовать повторно.</p>
      <ProductButton disabled={starting} onClick={() => void start()}>
        {starting ? <><Spinner /> Повторяем…</> : "Повторить начало приёмки"}
      </ProductButton>
    </section>}
    {uncertain && <p role="status">Результат первоначального запуска пока неизвестен. Новый запуск не создаётся; проверьте сохранённое состояние.</p>}
    {reconcileRequired && <p role="status">Результат запуска пока неизвестен. Новый запуск недоступен до сверки сохранённой передачи.</p>}
    {actionError && <p role="alert" className="text-destructive">{actionError}</p>}
    {reconcileRequired && <ProductButton outlined disabled={refreshing} onClick={() => {
      const version = ++lifecycle.current;
      void reconcileStart(version);
    }}>
      {refreshing ? <><Spinner /> Обновляем…</> : "Обновить состояние"}
    </ProductButton>}
    {handoff && !reconcileRequired && (uncertain || Boolean(actionError)) && <ProductButton outlined disabled={refreshing} onClick={() => void refreshStatus()}>
      {refreshing ? <><Spinner /> Обновляем…</> : "Обновить состояние"}
    </ProductButton>}
    {handoff && <IntakePreparationPanel handoffId={handoff.handoff_id} layout="page" />}
  </section>;
}
