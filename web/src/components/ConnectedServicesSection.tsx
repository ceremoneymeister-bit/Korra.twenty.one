import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useSearchParams } from "react-router";
import { BookOpen, CalendarCheck, Plus, RefreshCw, ShieldCheck, ShieldOff } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@nous-research/ui/ui/components/card";
import { Switch } from "@nous-research/ui/ui/components/switch";

import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";
import { GoogleWorkspaceProfileCard } from "@/components/GoogleWorkspaceCard";
import { ProductButton } from "@/components/ProductButton";
import { api, type ConnectionsGoogleProfile, type ConnectionsResponse } from "@/lib/api";
import {
  borrowStatus,
  borrowStatusText,
  canToggle,
  capabilityNote,
  connectCandidates,
  defaultConnectTarget,
  googleSources,
  profileLabel,
  serviceLabels,
  sharingState,
  sourceIsUsable,
  toggledSharing,
} from "@/lib/connections";
import { ownerFacingError } from "@/lib/owner-facing-error";

/** Якорь раздела: сюда ведут «Подключить …» из виджетов и старый адрес /connections. */
const SERVICES_SECTION_ID = "section-services";

/**
 * «Подключённые сервисы» — верхний раздел «Ключей и доступов».
 *
 * Решение Дмитрия 23.09: подключение принадлежит платформе Korra, а не
 * агенту. Здесь видно, что подключено и кому доступно; «Доступно всем
 * агентам» открывает подключение всем агентам установки, в том числе
 * созданным позже. Кто вправе им пользоваться в конкретном разговоре,
 * решает сервер при каждом вызове: владелец или его фоновая работа, но не
 * посетители общих ботов.
 *
 * Раздел ничего не хранит сам: сводку отдаёт `GET /api/connections`, а все
 * изменения идут прежними маршрутами Google (`start/complete/cancel/revoke`,
 * `PUT sharing`), которые уже проверены периметром кабинета.
 */
export function ConnectedServicesSection({
  onError,
  onSuccess,
}: {
  onError: (message: string) => void;
  onSuccess: (message: string) => void;
}) {
  const [params] = useSearchParams();
  const { hash } = useLocation();
  const [data, setData] = useState<ConnectionsResponse | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const ticket = useRef(0);
  const scrolled = useRef(false);
  const sectionRef = useRef<HTMLElement | null>(null);

  const load = useCallback(async () => {
    const current = ++ticket.current;
    try {
      const next = await api.getConnections();
      if (current !== ticket.current) return;
      setData(next);
      setLoadState("ready");
    } catch (error) {
      if (current !== ticket.current) return;
      setLoadState((previous) => (previous === "ready" ? previous : "error"));
      onError(ownerFacingError(error, "Не удалось получить список подключений."));
    }
  }, [onError]);

  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  // Переход по ссылке «Подключить …» или со старого /connections: раздел
  // выше каталога ключей, но страница собирается не сразу — дожидаемся сводки.
  useEffect(() => {
    if (scrolled.current || hash !== `#${SERVICES_SECTION_ID}` || loadState === "loading") return;
    scrolled.current = true;
    const frame = window.requestAnimationFrame(() => sectionRef.current?.scrollIntoView?.({ block: "start" }));
    return () => window.cancelAnimationFrame(frame);
  }, [hash, loadState]);

  const requestedConnect = params.get("connect");
  const requestedProfile = params.get("profile");

  return (
    <section
      id={SERVICES_SECTION_ID}
      ref={sectionRef}
      aria-labelledby={`${SERVICES_SECTION_ID}-title`}
      className="flex scroll-mt-4 flex-col gap-3"
    >
      <div className="flex flex-col gap-1">
        <h2 id={`${SERVICES_SECTION_ID}-title`} className="text-base font-semibold">
          Подключённые сервисы
        </h2>
        <p className="text-sm text-muted-foreground">
          Сервис подключается к Korra один раз — агенты пользуются им по вашей просьбе, без настройки.
        </p>
        <p className="text-xs text-text-tertiary" data-owner-only-note>
          Подключения — ваши: в мессенджерах агент пользуется ими только в вашем личном чате, когда вы указаны
          владельцем бота. Посетители общих ботов и группы их не получают. Отключение действует сразу.
        </p>
      </div>

      {loadState === "loading" && !data ? (
        <p className="text-sm text-muted-foreground" aria-busy="true">Читаем подключения…</p>
      ) : null}

      {loadState === "error" && !data ? (
        <Card role="alert">
          <CardContent className="flex flex-wrap items-center gap-3 p-4">
            <p className="min-w-0 flex-1 text-sm">Список подключений не загрузился. Ничего не изменилось.</p>
            <ProductButton outlined size="sm" onClick={() => void load()} prefix={<RefreshCw aria-hidden />}>
              Повторить
            </ProductButton>
          </CardContent>
        </Card>
      ) : null}

      {data ? (
        <GoogleSection
          google={data.google}
          focusCalendar={requestedConnect === "calendar"}
          requestedProfile={requestedProfile}
          onChanged={load}
          onError={onError}
          onSuccess={onSuccess}
        />
      ) : null}
    </section>
  );
}

interface SectionProps {
  google: ConnectionsResponse["google"];
  focusCalendar: boolean;
  requestedProfile: string | null;
  onChanged: () => Promise<void> | void;
  onError: (message: string) => void;
  onSuccess: (message: string) => void;
}

function GoogleSection({ focusCalendar, google, onChanged, onError, onSuccess, requestedProfile }: SectionProps) {
  const rows = google.profiles;
  const sources = googleSources(rows);
  const byName = useMemo(() => new Map(rows.map((row) => [row.profile, row])), [rows]);
  const nameOf = useCallback(
    (profile: string) => {
      const row = byName.get(profile);
      return row ? profileLabel(row) : profile === "default" ? "Главный агент" : profile;
    },
    [byName],
  );

  const requestedRow = requestedProfile ? byName.get(requestedProfile) : undefined;
  // Пришли из «Календаря» к агенту со своим подключением: значит, его и
  // нужно переподключить — показываем именно его карточку.
  // Только если календаря в этом подключении действительно нет или доступ
  // истёк: с рабочим календарём переход из карточки просто открывает раздел.
  const calendarWorks = Boolean(
    requestedRow?.services.includes("calendar") &&
      (requestedRow.state === "connected" || requestedRow.legacy_compatible),
  );
  const reconnectTarget =
    focusCalendar && requestedRow?.access === "own" && !calendarWorks ? requestedRow.profile : null;
  const candidates = connectCandidates(rows);
  // Из карточки «Календарь» раскрываем подключение, только если календаря
  // ещё нет ни в одном подключении.
  const calendarConnected = sources.some(
    (row) => row.services.includes("calendar") && sourceIsUsable(row),
  );
  const [connectOpen, setConnectOpen] = useState(
    () =>
      (focusCalendar && !calendarConnected) ||
      sources.length === 0 ||
      candidates.some((row) => row.pending),
  );
  const [target, setTarget] = useState<string | null>(() => defaultConnectTarget(rows, requestedProfile));
  const effectiveTarget =
    target && candidates.some((row) => row.profile === target) ? target : defaultConnectTarget(rows, null);

  const connectedCount = rows.filter((row) => row.access !== "none" && row.state !== "not_connected").length;
  const badge = !google.app.configured
    ? "Не настроено на сервере"
    : sources.length === 0
      ? "Не подключено"
      : `Доступно агентам: ${connectedCount} из ${rows.length}`;

  return (
    <Card role="region" aria-labelledby="connections-google-title" data-service="google">
      <CardHeader className="bg-transparent">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            {sources.length ? (
              <ShieldCheck className="h-5 w-5 text-success" aria-hidden />
            ) : (
              <ShieldOff className="h-5 w-5 text-muted-foreground" aria-hidden />
            )}
            <CardTitle id="connections-google-title" className="text-base">
              Google
            </CardTitle>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge>{badge}</Badge>
            <Link
              to="/help/google"
              className="inline-flex min-h-[44px] items-center gap-1.5 rounded-lg px-3 text-sm font-medium text-primary hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            >
              <BookOpen className="size-4" aria-hidden />
              Инструкция
            </Link>
          </div>
        </div>
        <CardDescription>
          Календарь, почта, диск, таблицы и документы. Одно подключение можно открыть всем агентам — доступ не
          копируется, а отключение закрывает его всем.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 p-4">
        {!google.app.configured ? (
          <div className="rounded-md border border-warning/40 bg-warning/5 p-3 text-sm" role="status">
            Подключение Google на этом сервере ещё не настроено. Напишите в поддержку Korra — после настройки
            подключить можно будет здесь.
          </div>
        ) : null}

        {reconnectTarget ? (
          <section className="grid gap-2" aria-label="Переподключение Google">
            <p className="text-sm">
              Чтобы добавить календарь, отключите Google у агента «{nameOf(reconnectTarget)}» и подключите снова,
              отметив «Календарь». Если подключение открыто другим агентам, сначала закройте им доступ ниже.
            </p>
            <GoogleWorkspaceProfileCard
              profile={reconnectTarget}
              onChanged={onChanged}
              onError={onError}
              onSuccess={onSuccess}
            />
          </section>
        ) : null}

        {sources.map((source) => (
          <GrantBlock
            key={source.profile}
            source={source}
            sharedAllSource={google.shared_all_source ?? null}
            rows={rows}
            nameOf={nameOf}
            onChanged={onChanged}
            onError={onError}
            onSuccess={onSuccess}
          />
        ))}

        {google.app.configured && candidates.length > 0 ? (
          <section className="grid gap-3" aria-labelledby="connections-google-connect">
            {sources.length > 0 && !connectOpen ? (
              <ProductButton
                outlined
                size="sm"
                className="w-fit"
                onClick={() => setConnectOpen(true)}
                prefix={<Plus aria-hidden />}
                id="connections-google-connect"
              >
                Подключить другой аккаунт Google
              </ProductButton>
            ) : (
              <>
                <h3 id="connections-google-connect" className="text-sm font-semibold">
                  {sources.length ? "Подключить другой аккаунт Google" : "Подключить Google"}
                </h3>
                {candidates.length > 1 ? (
                  <label className="grid gap-1 text-sm sm:max-w-sm">
                    <span className="text-muted-foreground">Агент, которому принадлежит подключение</span>
                    <select
                      className="min-h-[44px] rounded-lg border border-border bg-background px-3 text-sm"
                      value={effectiveTarget ?? ""}
                      onChange={(event) => setTarget(event.target.value)}
                    >
                      {candidates.map((row) => (
                        <option key={row.profile} value={row.profile}>
                          {profileLabel(row)}
                          {row.pending ? " — ждёт завершения" : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null}
                {effectiveTarget ? (
                  <GoogleWorkspaceProfileCard
                    profile={effectiveTarget}
                    onChanged={onChanged}
                    onError={onError}
                    onSuccess={onSuccess}
                  />
                ) : null}
                <p className="text-xs text-text-tertiary">
                  После подключения его можно открыть всем агентам одним переключателем.
                </p>
              </>
            )}
          </section>
        ) : null}
      </CardContent>
    </Card>
  );
}

function GrantBlock({
  nameOf,
  onChanged,
  onError,
  onSuccess,
  rows,
  sharedAllSource,
  source,
}: {
  nameOf: (profile: string) => string;
  onChanged: () => Promise<void> | void;
  onError: (message: string) => void;
  onSuccess: (message: string) => void;
  rows: ConnectionsGoogleProfile[];
  sharedAllSource: string | null;
  source: ConnectionsGoogleProfile;
}) {
  const [busy, setBusy] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const usable = sourceIsUsable(source);
  const sharing = sharingState(rows, source);
  // «Всем агентам» — признак установки, а не список имён: он касается и
  // агентов, созданных позже. Явные списки остаются как есть.
  const toAll = sharedAllSource === source.profile;
  const allHeldElsewhere = Boolean(sharedAllSource) && !toAll;
  const services = source.services;
  const label = profileLabel(source);
  const headingId = `grant-${source.profile}`;

  const applySharing = async (next: string[] | null, success: string, allProfiles?: boolean) => {
    setBusy("sharing");
    try {
      await api.setGoogleWorkspaceSharing(next, source.profile, allProfiles);
      onSuccess(success);
    } catch (error) {
      onError(ownerFacingError(error, "Не удалось изменить общий доступ к Google."));
    } finally {
      setBusy("");
      await onChanged();
    }
  };

  const toggleAll = (on: boolean) =>
    void applySharing(
      null,
      on
        ? "Подключение Google открыто всем агентам, в том числе новым."
        : "Доступ «всем агентам» закрыт. Отдельно открытые агенты сохранили доступ.",
      on,
    );

  const toggleOne = (profile: string, on: boolean) =>
    void applySharing(
      toggledSharing(sharing.shared, profile, on),
      on ? `Агент «${nameOf(profile)}» получил доступ к Google.` : `Агент «${nameOf(profile)}» больше не пользуется Google.`,
    );

  const checkCalendar = async () => {
    setBusy("check");
    try {
      await api.checkGoogleWorkspaceService("calendar", source.profile);
      onSuccess("Календарь отвечает — агенты могут им пользоваться.");
    } catch (error) {
      onError(ownerFacingError(error, "Проверка календаря не прошла."));
    } finally {
      setBusy("");
    }
  };

  const disconnect = async () => {
    setBusy("revoke");
    try {
      if (sharing.shared.length || toAll) await api.setGoogleWorkspaceSharing([], source.profile, false);
      const result = await api.revokeGoogleWorkspace(source.profile);
      if (result.remote_revoked) {
        onSuccess("Google отключён. Агенты больше не имеют к нему доступа.");
      } else {
        onError(
          "Доступ в Korra удалён, но Google не подтвердил отзыв. Удалите доступ Ceremoneymeister в настройках Google Аккаунта.",
        );
      }
    } catch (error) {
      onError(ownerFacingError(error, "Не удалось отключить Google."));
    } finally {
      setBusy("");
      setConfirmOpen(false);
      await onChanged();
    }
  };

  const others = rows.filter((row) => row.profile !== source.profile);
  const stateText = source.state === "connected"
    ? "Подключено"
    : source.legacy_compatible
      ? "Работает с текущими правами"
      : "Нужно переподключить";

  return (
    <section
      className="grid gap-3 rounded-xl border border-border p-4"
      aria-labelledby={headingId}
      data-google-source={source.profile}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 id={headingId} className="text-sm font-semibold">
            Через агента «{label}»
          </h3>
          <p className="text-xs text-muted-foreground">{stateText}</p>
        </div>
        <ul className="flex flex-wrap gap-1.5" aria-label="Разрешённые сервисы">
          {serviceLabels(services).map((service) => (
            <li key={service} className="rounded-full border border-border px-2.5 py-1 text-xs">
              {service}
            </li>
          ))}
        </ul>
      </div>

      {!usable ? (
        <div className="rounded-md border border-warning/40 bg-warning/5 p-3 text-sm" role="status">
          Google больше не принимает это подключение. Отключите его и подключите заново — общий доступ станет
          доступен после этого.
        </div>
      ) : null}

      <div className="flex items-center justify-between gap-3 rounded-lg bg-muted/30 p-3">
        <div className="min-w-0">
          <p className="text-sm font-medium" id={`${headingId}-all`}>
            Доступно всем агентам
          </p>
          <p className="text-xs text-muted-foreground">
            {toAll
              ? "Открыто всем агентам установки, в том числе созданным позже. У кого своё подключение Google — пользуется своим."
              : allHeldElsewhere
                ? `Всем агентам уже открыт Google агента «${nameOf(sharedAllSource ?? "")}». Сначала выключите его.`
                : "Включите, чтобы подключением пользовался любой агент — и те, что появятся позже."}
          </p>
        </div>
        <Switch
          checked={toAll}
          disabled={!usable || Boolean(busy) || allHeldElsewhere}
          onCheckedChange={toggleAll}
          aria-labelledby={`${headingId}-all`}
        />
      </div>

      <ul className="grid gap-1" aria-label="Агенты и их доступ">
        <AgentRow row={source} status="Источник подключения" note={capabilityNote(source, services)} />
        {others.map((row) => {
          const status = borrowStatus(row, source);
          // При «всем агентам» отдельный переключатель ничего бы не менял:
          // доступ остался бы через общий признак.
          const toggle = canToggle(status) && !toAll;
          const on = status.kind === "shared";
          return (
            <AgentRow
              key={row.profile}
              row={row}
              status={on && row.via_all ? "Пользуется этим подключением — открыто всем агентам" : borrowStatusText(status, nameOf)}
              note={on ? capabilityNote(row, services) : null}
            >
              {toggle ? (
                <Switch
                  checked={on}
                  disabled={!usable || Boolean(busy)}
                  onCheckedChange={(next) => toggleOne(row.profile, next)}
                  aria-label={`Доступ к Google для агента «${profileLabel(row)}»`}
                />
              ) : null}
            </AgentRow>
          );
        })}
      </ul>

      <div className="flex flex-wrap gap-2">
        {services.includes("calendar") ? (
          <ProductButton
            outlined
            size="sm"
            disabled={Boolean(busy)}
            onClick={() => void checkCalendar()}
            prefix={<CalendarCheck aria-hidden />}
          >
            {busy === "check" ? "Проверяем…" : "Проверить календарь"}
          </ProductButton>
        ) : null}
        <ProductButton outlined destructive size="sm" disabled={Boolean(busy)} onClick={() => setConfirmOpen(true)}>
          Отключить Google
        </ProductButton>
        <Link
          to="/dashboard"
          className="inline-flex min-h-[44px] items-center px-2 text-sm text-muted-foreground hover:text-foreground"
        >
          На дашборд
        </Link>
      </div>

      <DeleteConfirmDialog
        open={confirmOpen}
        loading={busy === "revoke"}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => void disconnect()}
        title="Отключить Google?"
        confirmLabel="Отключить"
        description={
          toAll
            ? "Доступ потеряют все агенты установки. Встречи пропадут с дашборда."
            : sharing.shared.length
              ? `Доступ потеряют «${label}» и ещё ${sharing.shared.length}: ${sharing.shared.map(nameOf).join(", ")}. Встречи пропадут с дашборда.`
              : `Агент «${label}» потеряет доступ к Google, встречи пропадут с дашборда.`
        }
      />
    </section>
  );
}

function AgentRow({
  children,
  note,
  row,
  status,
}: {
  children?: ReactNode;
  note: string | null;
  row: ConnectionsGoogleProfile;
  status: string;
}) {
  return (
    <li
      className="flex min-h-[52px] items-center justify-between gap-3 rounded-lg px-2 py-1"
      data-connection-agent={row.profile}
    >
      <div className="min-w-0">
        <p className="truncate text-sm font-medium">{profileLabel(row)}</p>
        <p className="text-xs text-muted-foreground">{status}</p>
        {note ? <p className="text-xs text-text-tertiary">{note}</p> : null}
      </div>
      {children}
    </li>
  );
}
