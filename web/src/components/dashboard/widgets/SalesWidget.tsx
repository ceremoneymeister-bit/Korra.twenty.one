/* eslint-disable react-refresh/only-export-components -- карточка и запись каталога живут вместе */
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import { useStore } from "@nanostores/react";
import { MoreHorizontal, Sparkles } from "lucide-react";
import { Link } from "react-router";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ProductButton } from "@/components/ProductButton";
import { ErrorNote, LoadingNote, NoteTitle, WidgetStack, useNowSeconds } from "@/components/dashboard/widget-states";
import type { DashboardWidget, DashboardWidgetBodyProps } from "@/components/dashboard/widget-types";
import {
  crmApi,
  isCrmFailure,
  monthIn,
  monthOf,
  moneyFull,
  moneyParts,
  moneyText,
  currencySign,
  openCrmDialog,
  previousMonthTo,
  stuckChatLink,
  type CrmCandidate,
  type SalesDeal,
  type SalesReady,
  type SalesStage,
} from "@/lib/crm";
import {
  $dashboardState,
  $dashboardStatus,
  dashboardTimeZone,
  formatMoment,
  formatRelative,
  plural,
  refreshDashboardState,
} from "@/lib/dashboard-state";
import { cn } from "@/lib/utils";

const MAX_DOTS = 36;
const DEALS: [string, string, string] = ["сделка", "сделки", "сделок"];

function asOfSeconds(sales: SalesReady): number {
  const parsed = Date.parse(sales.as_of);
  return Number.isNaN(parsed) ? 0 : parsed / 1000;
}

function shortSource(sales: Pick<SalesReady, "source">): string {
  return sales.source === "bitrix24" ? "Б24" : "amo";
}

// ── Меню «⋯» ────────────────────────────────────────────────────────────

function SalesMenu({ sales }: { sales: SalesReady }) {
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const settings = sales.connection.settings;
  const checkedAt = sales.connection.last_check;

  useEffect(() => {
    if (!anchor) return;
    const close = (event: Event) => {
      if (event.target instanceof Node && (menu.current?.contains(event.target) || button.current?.contains(event.target))) return;
      setAnchor(null);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setAnchor(null);
        button.current?.focus();
      }
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [anchor]);

  const run = async (job: () => Promise<string | null>) => {
    setBusy(true);
    try {
      setNote(await job());
    } finally {
      setBusy(false);
    }
  };

  const toggleAccess = () =>
    run(async () => {
      const result = await crmApi.settings({ agents_access: !settings.agents_access });
      return isCrmFailure(result) ? result.error.message : settings.agents_access ? "Агенты больше не читают CRM." : "Агенты могут читать CRM.";
    });

  const recheck = () =>
    run(async () => {
      const result = await crmApi.recheck();
      return isCrmFailure(result) ? `${result.error.title}. ${result.error.message}` : "Подключение работает.";
    });

  const disconnect = async () => {
    setBusy(true);
    const result = await crmApi.disconnect();
    setBusy(false);
    setConfirm(false);
    if (isCrmFailure(result)) setNote(result.error.message);
    else setAnchor(null);
  };

  const open = () => {
    setNote(null);
    setAnchor(button.current?.getBoundingClientRect() ?? null);
  };
  const choose = (action: () => void) => () => {
    setAnchor(null);
    action();
  };

  return (
    <>
      <button
        ref={button}
        type="button"
        className="kdw-sales-more"
        aria-label="Меню карточки «Продажи»"
        aria-haspopup="menu"
        aria-expanded={anchor !== null}
        onClick={() => (anchor ? setAnchor(null) : open())}
      >
        <MoreHorizontal className="size-4" aria-hidden />
      </button>
      {anchor
        ? createPortal(
            <div
              ref={menu}
              role="menu"
              aria-label="Продажи"
              className="kdw-sales-menu"
              style={{ top: anchor.bottom + 6, right: Math.max(8, window.innerWidth - anchor.right) }}
              aria-busy={busy}
            >
              <button type="button" role="menuitem" onClick={choose(() => openCrmDialog("settings"))}>
                <span>Воронка и «застряла»</span>
                <small>
                  {sales.pipeline.name} · {settings.stuck_days} дн.
                </small>
              </button>
              <button type="button" role="menuitem" disabled={busy} onClick={() => void toggleAccess()}>
                <span>Доступ агентам</span>
                <small>{settings.agents_access ? "все" : "выключен"}</small>
              </button>
              <button type="button" role="menuitem" onClick={choose(() => openCrmDialog("replace"))}>
                <span>Заменить ключ</span>
              </button>
              <button type="button" role="menuitem" disabled={busy} onClick={() => void recheck()}>
                <span>Проверить подключение</span>
                <small>{checkedAt.at ? (checkedAt.ok ? "работает" : "была ошибка") : ""}</small>
              </button>
              <button type="button" role="menuitem" className="kdw-sales-menu-danger" onClick={() => setConfirm(true)}>
                <span>Отключить {sales.source_label}</span>
              </button>
              {note ? (
                <p role="status" className="kdw-sales-menu-note">
                  {note}
                </p>
              ) : null}
            </div>,
            document.body,
          )
        : null}
      <ConfirmDialog
        open={confirm}
        destructive
        title={`Отключить ${sales.source_label}?`}
        description="Ключ будет удалён с этой установки: карточка «Продажи» и агенты перестанут читать CRM. В самой CRM ничего не меняется. Подключить её снова можно в любой момент."
        cancelLabel="Отмена"
        confirmLabel="Отключить"
        loading={busy}
        onCancel={() => setConfirm(false)}
        onConfirm={() => void disconnect()}
      />
    </>
  );
}

// ── Шапка ───────────────────────────────────────────────────────────────

function SalesHeader({ size = "m" }: DashboardWidgetBodyProps) {
  const state = useStore($dashboardState);
  const status = useStore($dashboardStatus);
  const sales = state?.sales;
  const now = useNowSeconds(state?.generated_at ?? 0);
  if (!state || !sales || sales.status === "not_connected") return null;
  const label = "source_label" in sales ? sales.source_label : sales.connection?.source_label;
  const compact = size === "s";
  let freshness: { text: string; stale: boolean } | null = null;
  if (sales.status === "ok") {
    const at = asOfSeconds(sales);
    if (sales.stale || status === "error") {
      const moment = formatMoment(at, now, dashboardTimeZone(state));
      freshness = { text: `данные от ${moment.startsWith("сегодня в ") ? moment.slice("сегодня в ".length) : moment}`, stale: true };
    } else if (!compact) {
      freshness = { text: `обновлено ${formatRelative(at, now)}`, stale: false };
    }
  }
  return (
    <span className="kdw-sales-header">
      {label ? <span className="kdw-sales-src">{compact && sales.status === "ok" ? shortSource(sales) : label}</span> : null}
      {freshness ? (
        <span className={cn("kdw-sales-fresh", freshness.stale && "kdw-sales-fresh--stale")} data-testid="sales-updated">
          {freshness.stale ? null : <i aria-hidden />}
          {freshness.text}
        </span>
      ) : null}
      {sales.status === "ok" ? <SalesMenu sales={sales} /> : null}
    </span>
  );
}

// ── Не подключено ───────────────────────────────────────────────────────

function CandidateOffer({ candidate }: { candidate: CrmCandidate }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const adopt = async () => {
    setBusy(true);
    setError(null);
    const result = await crmApi.adopt({ profile: candidate.profile, type: candidate.type });
    setBusy(false);
    if (isCrmFailure(result)) setError(result.error.message);
  };
  return (
    <div className="kdw-sales-offer" data-testid="sales-offer">
      <b>
        У агента «{candidate.label}» уже подключён {candidate.source_label}
      </b>
      <span>{candidate.portal} · только чтение. Сделать подключение общим — для карточки и всех агентов?</span>
      {error ? (
        <span role="alert" className="kdw-sales-offer-error">
          {error}
        </span>
      ) : null}
      <div className="kdw-sales-offer-acts">
        <ProductButton outlined size="sm" disabled={busy} onClick={() => openCrmDialog("connect")}>
          Подключить другой
        </ProductButton>
        <ProductButton size="sm" disabled={busy} aria-busy={busy} onClick={() => void adopt()}>
          Использовать
        </ProductButton>
      </div>
    </div>
  );
}

function NotConnected({ candidates, size }: { candidates: CrmCandidate[]; size: "s" | "m" | "l" }) {
  const candidate = candidates[0];
  if (size === "s") {
    return (
      <WidgetStack>
        <NoteTitle>Подключите CRM</NoteTitle>
        <ProductButton size="sm" className="w-fit" onClick={() => openCrmDialog("connect")}>
          Подключить CRM
        </ProductButton>
      </WidgetStack>
    );
  }
  return (
    <div className="kdw-sales-empty">
      <div className="kdw-sales-ghost" aria-hidden>
        <i />
        <i />
        <i />
      </div>
      <div className="kdw-sales-empty-text">
        {candidate ? (
          <CandidateOffer candidate={candidate} />
        ) : (
          <>
            <b>Подключите Битрикс24 или amoCRM</b>
            <p>
              Здесь появятся деньги месяца, новые заявки, застрявшие сделки и просрочки менеджеров. Korra только читает
              CRM и ничего в ней не меняет.
            </p>
            <ProductButton size="sm" onClick={() => openCrmDialog("connect")}>
              Подключить CRM
            </ProductButton>
          </>
        )}
      </div>
    </div>
  );
}

// ── Данные ──────────────────────────────────────────────────────────────

function Hero({ sales, month, state }: { sales: SalesReady; month: number; state: NonNullable<ReturnType<typeof useStore<typeof $dashboardState>>> }) {
  void state;
  const { won } = sales;
  const money = moneyParts(won.amount);
  const sign = currencySign(sales.portal);
  const top = Math.max(...won.weeks.map((week) => week.amount), 1);
  const change = won.change_pct;
  const prev = previousMonthTo(month);
  return (
    <div className="kdw-sales-hero">
      <div className="kdw-sales-money">
        <span className="kdw-sales-k">
          Выиграно в {monthIn(month)} · {won.count} {plural(won.count, DEALS)}
        </span>
        <span className="kdw-sales-v" aria-label={moneyFull(won.amount, sales.portal)}>
          {money.value}{" "}
          <small>{[money.unit, sign].filter(Boolean).join(" ")}</small>
        </span>
        <span className="kdw-sales-chips">
          {change === null ? (
            <span>{won.prev_amount > 0 ? "" : `в прошлом месяце продаж не было`}</span>
          ) : (
            <>
              <span className={cn("kdw-sales-chip", change < 0 && "kdw-sales-chip--down")}>
                {change > 0 ? "↗ +" : change < 0 ? "↘ −" : ""}
                {Math.abs(change)} %
              </span>
              к {prev} на эту дату
            </>
          )}
          {won.limited ? <span className="kdw-sales-limited">сумма может быть выше: прочитана не вся история</span> : null}
        </span>
      </div>
      <div className="kdw-sales-bars" aria-hidden>
        {won.weeks.map((week, index) => (
          <i
            key={week.start}
            className={cn(index === won.weeks.length - 1 && "kdw-sales-bar--now")}
            style={{ height: `${Math.max(6, Math.round((week.amount / top) * 100))}%` }}
          />
        ))}
      </div>
    </div>
  );
}

function Signals({ sales, size }: { sales: SalesReady; size: "m" | "l" }) {
  const { new_leads: leads, stuck, overdue } = sales;
  const topSeries = Math.max(...leads.series, 1);
  return (
    <div className="kdw-sales-signals">
      <a className="kdw-sales-sig" href={sales.links.portal} target="_blank" rel="noopener noreferrer">
        <i className="kdw-sales-ico kdw-sales-ico--dark" aria-hidden>
          +
        </i>
        <span className="kdw-sales-sigt">
          <b>{leads.today}</b>
          <span>{size === "l" ? "новых заявок сегодня" : "заявок сегодня"}</span>
        </span>
        {size === "l" ? (
          <span className="kdw-sales-spark" aria-hidden>
            {leads.series.map((value, index) => (
              <i
                key={index}
                className={cn(index === leads.series.length - 1 && "kdw-sales-bar--now")}
                style={{ height: `${Math.max(12, Math.round((value / topSeries) * 100))}%` }}
              />
            ))}
          </span>
        ) : null}
      </a>
      <Link
        className="kdw-sales-sig"
        to={stuck.count > 0 ? stuckChatLink(sales) : "#"}
        aria-label={stuck.count > 0 ? `${stuck.count} застряли: разобрать с агентом` : "Застрявших сделок нет"}
        onClick={stuck.count > 0 ? undefined : (event) => event.preventDefault()}
      >
        <i className={cn("kdw-sales-ico", stuck.count > 0 ? "kdw-sales-ico--amber" : "kdw-sales-ico--dark")} aria-hidden>
          {stuck.count > 0 ? "⧗" : "✓"}
        </i>
        <span className="kdw-sales-sigt">
          <b>{stuck.count}</b>
          <span>
            {stuck.approx ? "около " : ""}
            застряли{size === "l" && stuck.count > 0 ? ` · ${moneyText(stuck.amount, sales.portal)}` : ""}
          </span>
        </span>
      </Link>
      <a className="kdw-sales-sig" href={sales.links.portal} target="_blank" rel="noopener noreferrer">
        <i className={cn("kdw-sales-ico", overdue.tasks > 0 ? "kdw-sales-ico--red" : "kdw-sales-ico--dark")} aria-hidden>
          {overdue.available ? (overdue.tasks > 0 ? "!" : "✓") : "–"}
        </i>
        <span className="kdw-sales-sigt">
          <b>{overdue.available ? overdue.tasks : "—"}</b>
          <span>{overdue.available ? (size === "l" ? "задач просрочено" : "просрочено") : "задачи недоступны"}</span>
        </span>
      </a>
    </div>
  );
}

function dotSize(amount: number, top: number): number {
  return Math.round(9 + 20 * Math.sqrt(Math.max(amount, 0) / Math.max(top, 1)));
}

function dealTitle(deal: SalesDeal, sales: SalesReady): string {
  const parts = [deal.title, moneyFull(deal.amount, sales.portal)];
  if (deal.days > 0) parts.push(`${deal.days} дн. без движения`);
  if (deal.late) parts.push("просрочена задача");
  return parts.join(" · ");
}

function Stage({ stage, top, sales }: { stage: SalesStage; top: number; sales: SalesReady }) {
  const shown = stage.deals.slice(0, MAX_DOTS);
  const hidden = stage.deals.length - shown.length;
  return (
    <div className={cn("kdw-sales-col", sales.river.busiest_stage === stage.name && "kdw-sales-col--hot")} data-stage={stage.id}>
      <div className="kdw-sales-col-h">
        <b>{stage.name}</b>
      </div>
      <div className="kdw-sales-dots">
        {shown.map((deal) => (
          <a
            key={deal.id}
            href={deal.url}
            target="_blank"
            rel="noopener noreferrer"
            title={dealTitle(deal, sales)}
            aria-label={dealTitle(deal, sales)}
            className={cn("kdw-sales-dot", deal.stuck && "kdw-sales-dot--stuck", deal.late && "kdw-sales-dot--late", deal.new && "kdw-sales-dot--new")}
            style={{ width: dotSize(deal.amount, top), height: dotSize(deal.amount, top) }}
          />
        ))}
        {hidden > 0 ? <span className="kdw-sales-more-dots">+{hidden}</span> : null}
      </div>
      <div className="kdw-sales-col-f">
        <span>{moneyText(stage.amount, sales.portal)}</span>
        {stage.stuck > 0 ? <em>{stage.stuck} стоят</em> : null}
      </div>
    </div>
  );
}

function River({ sales }: { sales: SalesReady }) {
  const { river } = sales;
  const top = Math.max(...river.stages.flatMap((stage) => stage.deals.map((deal) => deal.amount)), 1);
  return (
    <div className="kdw-sales-river-wrap">
      <div className="kdw-sales-river-h">
        <span>
          Воронка «{sales.pipeline.name}» ·{" "}
          <b>
            {river.deals_total} {plural(river.deals_total, DEALS)} на {moneyText(river.amount_total, sales.portal)}
          </b>
        </span>
        {river.truncated ? (
          <span>
            показаны первые {river.deals_loaded} из {river.deals_total}
          </span>
        ) : river.busiest_stage ? (
          <span className="kdw-sales-river-hot">больше всего стоит на «{river.busiest_stage}»</span>
        ) : null}
      </div>
      <div className="kdw-sales-river" data-testid="sales-river" tabIndex={0} role="group" aria-label="Сделки по этапам воронки">
        {river.stages.map((stage) => (
          <Stage key={stage.id} stage={stage} top={top} sales={sales} />
        ))}
      </div>
      <div className="kdw-sales-legend">
        <span>
          <i />в движении
        </span>
        <span>
          <i className="kdw-sales-dot--stuck" />
          стоит {sales.stuck_days}+ дней
        </span>
        {sales.overdue.available ? (
          <span>
            <i className="kdw-sales-dot--late" />
            просрочена задача
          </span>
        ) : null}
        <span>
          <i className="kdw-sales-dot--new" />
          новая сегодня
        </span>
      </div>
    </div>
  );
}

function StuckCards({ sales }: { sales: SalesReady }) {
  const top = sales.stuck.top;
  if (top.length === 0) return null;
  const longest = Math.max(...top.map((deal) => deal.days), 1);
  return (
    <div className="kdw-sales-stuck">
      {top.map((deal) => (
        <a key={deal.id} className="kdw-sales-deal" href={deal.url} target="_blank" rel="noopener noreferrer">
          <span className="kdw-sales-deal-top">
            <span className="kdw-sales-ring" style={{ "--p": Math.round((deal.days / longest) * 100) } as CSSProperties}>
              <span>
                {deal.days}
                <small>{plural(deal.days, ["день", "дня", "дней"])}</small>
              </span>
            </span>
          </span>
          <span className="kdw-sales-sum">{moneyFull(deal.amount, sales.portal)}</span>
          <span className="kdw-sales-nm">{deal.title}</span>
          <span className="kdw-sales-st">{[deal.stage, deal.manager].filter(Boolean).join(" · ")}</span>
        </a>
      ))}
    </div>
  );
}

function Team({ sales }: { sales: SalesReady }) {
  const people = sales.managers.slice(0, 5);
  if (people.length === 0) return null;
  return (
    <div className="kdw-sales-team">
      {people.map((person) => {
        const trouble = person.overdue;
        return (
          <span key={person.id} className="kdw-sales-mate">
            <span className={cn("kdw-sales-ava", person.leader && "kdw-sales-ava--lime")}>
              {person.initials}
              {trouble > 0 ? <i className="kdw-sales-badge">{trouble}</i> : null}
            </span>
            <span className="kdw-sales-who">
              <b>{person.name}</b>
              {person.leader
                ? `лидер месяца · ${moneyText(person.won_amount, sales.portal)}`
                : person.stuck > 0
                  ? `${person.stuck} стоят`
                  : "без застоя"}
            </span>
          </span>
        );
      })}
    </div>
  );
}

function AskAgent({ sales }: { sales: SalesReady }) {
  const count = sales.stuck.count;
  return (
    <div className="kdw-sales-foot">
      {count > 0 ? (
        <Link className="kdw-sales-btn" to={stuckChatLink(sales)}>
          <Sparkles className="size-4" aria-hidden />
          Разобрать {count} {plural(count, ["застрявшую", "застрявшие", "застрявших"])} с агентом
        </Link>
      ) : (
        <span className="kdw-sales-calm">Застрявших сделок нет</span>
      )}
      <a className="kdw-sales-link" href={sales.links.portal} target="_blank" rel="noopener noreferrer">
        Открыть {sales.source_label} ↗
      </a>
    </div>
  );
}

function Tile({ sales }: { sales: SalesReady }) {
  const { stuck, won, river } = sales;
  const stuckMode = stuck.count > 0;
  const money = moneyParts(stuckMode ? stuck.amount : won.amount);
  const share = river.amount_total > 0 ? Math.min(100, Math.round((stuck.amount / river.amount_total) * 100)) : 0;
  const unit = [money.unit, currencySign(sales.portal)].filter(Boolean).join(" ");
  return (
    <div className="kdw-sales kdw-sales--tile" style={{ "--p": stuckMode ? share : 0 } as CSSProperties}>
      <span className="kdw-sales-arc" aria-hidden />
      <span className="kdw-sales-big">{money.value}</span>
      <span className="kdw-sales-cap">
        {stuckMode ? (
          <>
            {unit} стоят без движения дольше {sales.stuck_days} {plural(sales.stuck_days, ["дня", "дней", "дней"])} · {stuck.count}{" "}
            {plural(stuck.count, DEALS)}
          </>
        ) : (
          <>{unit} выиграно в этом месяце · застрявших нет</>
        )}
      </span>
      {stuckMode ? <span className="kdw-sales-pct">{share} %</span> : null}
    </div>
  );
}

function Ready({ sales, size }: { sales: SalesReady; size: "s" | "m" | "l" }) {
  const state = useStore($dashboardState);
  if (size === "s") return <Tile sales={sales} />;
  if (!state) return null;
  const month = monthOf(sales.as_of, dashboardTimeZone(state));
  return (
    <div className={cn("kdw-sales", size === "l" ? "kdw-sales--l" : "kdw-sales--m")}>
      <Hero sales={sales} month={month} state={state} />
      <Signals sales={sales} size={size} />
      {size === "l" ? (
        <>
          <River sales={sales} />
          <div className="kdw-sales-extras">
            <StuckCards sales={sales} />
            <Team sales={sales} />
          </div>
          <AskAgent sales={sales} />
        </>
      ) : null}
    </div>
  );
}

function SalesBody({ size = "m" }: DashboardWidgetBodyProps) {
  const state = useStore($dashboardState);
  const status = useStore($dashboardStatus);
  const retry = () => void refreshDashboardState();
  if (!state) {
    return status === "error" ? (
      <ErrorNote size={size} title="Не удалось загрузить продажи" detail="Сводка не пришла. Повторите запрос." onRetry={retry} />
    ) : (
      <LoadingNote text="Читаем CRM…" />
    );
  }
  const sales = state.sales;
  if (!sales) {
    return <ErrorNote size={size} title="Продажи пока недоступны" detail="Обновите панель, чтобы получить раздел «Продажи»." onRetry={retry} />;
  }
  if (sales.status === "not_connected") return <NotConnected candidates={sales.candidates ?? []} size={size} />;
  if (sales.status === "loading") return <LoadingNote text="Читаем CRM…" />;
  if (sales.status === "error") {
    const replaceable = ["bad_key", "plan_closed", "bad_url", "forbidden"].includes(sales.error.code);
    return (
      <WidgetStack>
        <div role="alert" className="contents">
          <NoteTitle>{sales.error.title}</NoteTitle>
          {size === "s" ? null : <p className="kdw-sales-error-text">{sales.error.message}</p>}
        </div>
        <div className="kdw-sales-error-acts">
          {replaceable ? (
            <ProductButton size="sm" onClick={() => openCrmDialog("replace")}>
              Заменить ключ
            </ProductButton>
          ) : (
            <ProductButton outlined size="sm" onClick={retry}>
              Повторить
            </ProductButton>
          )}
        </div>
      </WidgetStack>
    );
  }
  return <Ready sales={sales} size={size} />;
}

export const SALES_WIDGET: DashboardWidget = {
  id: "sales",
  title: "Продажи",
  purpose: "Сколько выиграно за месяц, какие сделки застряли и кому нужна помощь — из Битрикс24 или amoCRM.",
  isAvailable: (state) => state?.sales !== undefined,
  Body: SalesBody,
  HeaderNote: SalesHeader,
};
