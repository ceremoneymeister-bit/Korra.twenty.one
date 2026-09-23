import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { CalendarCheck, Plus, RefreshCw, ShieldCheck, ShieldOff } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@nous-research/ui/ui/components/card";
import { Switch } from "@nous-research/ui/ui/components/switch";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { useToast } from "@nous-research/ui/hooks/use-toast";

import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";
import { GoogleWorkspaceProfileCard } from "@/components/GoogleWorkspaceCard";
import { KorraLoader } from "@/components/KorraLoader";
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

/**
 * «Сервисы» — что подключено к Korra и каким агентам это доступно.
 *
 * Подключение становится инструментом агента сразу: календарь — встроенный
 * инструмент каждого агента, и он отвечает тем доступом, который здесь
 * открыт. Отключение здесь же закрывает доступ со следующего обращения.
 *
 * Экран ничего не хранит сам: сводку отдаёт `GET /api/connections`, а все
 * изменения идут прежними маршрутами Google (`start/complete/cancel/revoke`,
 * `PUT sharing`), которые уже проверены периметром кабинета.
 */
export default function ConnectionsPage() {
  const { toast, showToast } = useToast();
  const [params] = useSearchParams();
  const [data, setData] = useState<ConnectionsResponse | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const ticket = useRef(0);

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
      showToast(ownerFacingError(error, "Не удалось получить список подключений."), "error");
    }
  }, [showToast]);

  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  const notifyError = useCallback((message: string) => showToast(message, "error"), [showToast]);
  const notifySuccess = useCallback((message: string) => showToast(message, "success"), [showToast]);

  const requestedConnect = params.get("connect");
  const requestedProfile = params.get("profile");

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6">
      <Toast toast={toast} />
      <div className="flex flex-col gap-1">
        <p className="text-sm text-muted-foreground">
          Подключите сервис один раз — агенты пользуются им сами, без настройки. Здесь видно, кому что доступно.
        </p>
        <p className="text-xs text-text-tertiary">
          Отключение действует сразу: агент теряет доступ со следующего обращения.
        </p>
      </div>

      {loadState === "loading" && !data ? <KorraLoader className="py-16" /> : null}

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
          onError={notifyError}
          onSuccess={notifySuccess}
        />
      ) : null}
    </div>
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
  const reconnectTarget = focusCalendar && requestedRow?.access === "own" ? requestedRow.profile : null;
  const candidates = connectCandidates(rows);
  const [connectOpen, setConnectOpen] = useState(
    () => focusCalendar || sources.length === 0 || candidates.some((row) => row.pending),
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
    <Card role="region" aria-labelledby="connections-google-title">
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
          <Badge>{badge}</Badge>
        </div>
        <CardDescription>
          Календарь, почта, диск, таблицы и документы. Одно подключение можно открыть нескольким агентам — доступ не
          копируется, а отключение у источника закрывает его всем.
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
              отметив «Календарь». Если этим подключением пользуются другие агенты, сначала закройте им доступ ниже.
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
                  После подключения его можно открыть остальным агентам одним переключателем.
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
  source,
}: {
  nameOf: (profile: string) => string;
  onChanged: () => Promise<void> | void;
  onError: (message: string) => void;
  onSuccess: (message: string) => void;
  rows: ConnectionsGoogleProfile[];
  source: ConnectionsGoogleProfile;
}) {
  const [busy, setBusy] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const usable = sourceIsUsable(source);
  const sharing = sharingState(rows, source);
  const services = source.services;
  const label = profileLabel(source);
  const headingId = `grant-${source.profile}`;

  const applySharing = async (next: string[], success: string) => {
    setBusy("sharing");
    try {
      await api.setGoogleWorkspaceSharing(next, source.profile);
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
      on ? [...sharing.eligible].sort() : [],
      on ? "Подключение Google открыто всем агентам." : "Общий доступ к Google закрыт.",
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
      if (sharing.shared.length) await api.setGoogleWorkspaceSharing([], source.profile);
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
            {sharing.eligible.length === 0
              ? "Открыть некому: у остальных агентов своё подключение или их пока нет."
              : `Пользуются: ${sharing.shared.length} из ${sharing.eligible.length}. Новых агентов добавляйте здесь же.`}
          </p>
        </div>
        <Switch
          checked={sharing.all}
          disabled={!usable || Boolean(busy) || sharing.eligible.length === 0}
          onCheckedChange={toggleAll}
          aria-labelledby={`${headingId}-all`}
        />
      </div>

      <ul className="grid gap-1" aria-label="Агенты и их доступ">
        <AgentRow row={source} status="Источник подключения" note={capabilityNote(source, services)} />
        {others.map((row) => {
          const status = borrowStatus(row, source);
          const toggle = canToggle(status);
          const on = status.kind === "shared";
          return (
            <AgentRow
              key={row.profile}
              row={row}
              status={borrowStatusText(status, nameOf)}
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
          sharing.shared.length
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
