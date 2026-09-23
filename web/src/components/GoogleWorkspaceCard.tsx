import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ExternalLink, RefreshCw, ShieldCheck, ShieldOff } from "lucide-react";
import { api, type GoogleWorkspaceStatus } from "@/lib/api";
import { ownerFacingError } from "@/lib/owner-facing-error";
import { useProfileScope } from "@/contexts/useProfileScope";
import { Button } from "@nous-research/ui/ui/components/button";
import { Badge } from "@nous-research/ui/ui/components/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@nous-research/ui/ui/components/card";
import { Input } from "@nous-research/ui/ui/components/input";


const SERVICE_LABELS: Record<string, string> = {
  drive: "Google Drive",
  sheets: "Google Sheets",
  calendar: "Google Calendar",
  docs: "Google Docs",
  contacts: "Google Contacts",
  email: "Gmail",
};

const DEFAULT_SERVICES = ["drive", "sheets", "calendar"];


interface Props {
  onError: (message: string) => void;
  onSuccess: (message: string) => void;
  /** Подключение изменилось (начато, завершено, отменено или отозвано). */
  onChanged?: () => void;
}


export function GoogleWorkspaceCard(props: Props) {
  const { profile, currentProfile, profiles } = useProfileScope();
  const profileKey = profile || currentProfile || "default";
  const names = Object.fromEntries((profiles ?? []).map(item => [item.name, item.display_name?.trim() || item.name]));
  return <GoogleWorkspaceCardBody key={profileKey} {...props} profileKey={profileKey} names={names} />;
}

/**
 * Та же карточка для явно выбранного агента — экран «Сервисы» подключает
 * Google не тому агенту, что выбран в шапке, а тому, кого назвал владелец.
 */
export function GoogleWorkspaceProfileCard({ profile, ...props }: Props & { profile: string }) {
  const { profiles } = useProfileScope();
  const names = Object.fromEntries((profiles ?? []).map(item => [item.name, item.display_name?.trim() || item.name]));
  return <GoogleWorkspaceCardBody key={profile} {...props} profileKey={profile} names={names} />;
}

function GoogleWorkspaceCardBody({ onError, onSuccess, onChanged, profileKey, names }: Props & {
  profileKey: string; names: Record<string, string>;
}) {
  const alive = useRef(true);
  const loadRequest = useRef(0);
  const label = (name: string) => names[name] || (name === "default" ? "главного агента" : name);
  const [status, setStatus] = useState<GoogleWorkspaceStatus | null>(null);
  const [selected, setSelected] = useState<string[]>(DEFAULT_SERVICES);
  const [authUrl, setAuthUrl] = useState("");
  const [callbackUrl, setCallbackUrl] = useState("");
  const [busy, setBusy] = useState("");
  const [checks, setChecks] = useState<Record<string, "ok" | "error">>({});

  const load = useCallback(async () => {
    if (!alive.current) return;
    const ticket = ++loadRequest.current;
    try {
      const next = await api.getGoogleWorkspaceStatus(profileKey);
      if (!alive.current || ticket !== loadRequest.current) return;
      setStatus(next);
      if (next.pending.active && next.pending.services?.length) {
        setSelected(next.pending.services);
      } else if (next.connection.services?.length) {
        setSelected(next.connection.services.filter(service => service !== "all"));
      } else if (next.connection.usable_services?.length) {
        setSelected(next.connection.usable_services.filter(service => service !== "all"));
      } else {
        setSelected(DEFAULT_SERVICES);
      }
    } catch (error) {
      if (alive.current && ticket === loadRequest.current) onError(ownerFacingError(error, "Не удалось получить статус Google."));
    }
  }, [onError, profileKey]);

  useEffect(() => {
    alive.current = true;
    void load();
    return () => { alive.current = false; loadRequest.current += 1; };
  }, [load]);

  const available = status?.available_services ?? [];
  const connected = status?.connection.state === "connected";
  const needsReauth = status?.connection.state === "reauthorization_required";
  const legacyServices = status?.connection.usable_services ?? [];
  const legacyCompatible = Boolean(needsReauth && status?.connection.legacy_compatible);
  const sharedFrom = status?.connection.shared_from;
  const sharedWith = status?.connection.shared_with ?? [];
  const canStart = Boolean(status?.app.configured && selected.length && !busy && !needsReauth && !sharedFrom);
  const stateLabel = useMemo(() => {
    if (!status) return "Проверяем";
    if (!status.app.configured) return "Приложение не установлено";
    if (connected) return "Подключено";
    if (legacyCompatible) return "Работает с текущими правами";
    if (needsReauth) return "Нужно переподключить";
    if (status.pending.active) return "Ожидает подтверждения";
    return "Не подключено";
  }, [connected, legacyCompatible, needsReauth, status]);

  const start = async () => {
    setBusy("start");
    try {
      const result = await api.startGoogleWorkspace(selected, profileKey);
      if (!alive.current) return;
      setAuthUrl(result.authorization_url);
      window.open(result.authorization_url, "_blank", "noopener,noreferrer");
      await load();
      onChanged?.();
    } catch (error) {
      if (!alive.current) return;
      onError(ownerFacingError(error, "Не удалось начать подключение Google."));
    } finally {
      setBusy("");
    }
  };

  const complete = async () => {
    setBusy("complete");
    try {
      await api.completeGoogleWorkspace(callbackUrl, profileKey);
      if (!alive.current) return;
      setCallbackUrl("");
      setAuthUrl("");
      onSuccess("Google Workspace подключён к выбранному агенту.");
      await load();
      onChanged?.();
    } catch (error) {
      if (!alive.current) return;
      onError(ownerFacingError(error, "Не удалось завершить подключение Google."));
    } finally {
      setBusy("");
    }
  };

  const cancel = async () => {
    setBusy("cancel");
    try {
      await api.cancelGoogleWorkspace(profileKey);
      if (!alive.current) return;
      setAuthUrl("");
      setCallbackUrl("");
      await load();
      onChanged?.();
    } catch (error) {
      if (!alive.current) return;
      onError(ownerFacingError(error, "Не удалось отменить подключение Google."));
    } finally {
      setBusy("");
    }
  };

  const revoke = async () => {
    setBusy("revoke");
    try {
      const result = await api.revokeGoogleWorkspace(profileKey);
      if (!alive.current) return;
      setAuthUrl("");
      setCallbackUrl("");
      setChecks({});
      if (result.status === "detached") {
        onSuccess("Общий доступ к Google отключён для выбранного агента.");
      } else if (result.remote_revoked) {
        onSuccess("Google Workspace отключён от выбранного агента.");
      } else {
        onError(
          "Локальный доступ выбранного агента удалён, но Google не подтвердил отзыв. Удалите доступ Ceremoneymeister в настройках Google Аккаунта.",
        );
      }
      await load();
      onChanged?.();
    } catch (error) {
      if (!alive.current) return;
      onError(ownerFacingError(error, "Не удалось отключить Google."));
    } finally {
      setBusy("");
    }
  };

  const check = async (service: string) => {
    setBusy(`check:${service}`);
    try {
      await api.checkGoogleWorkspaceService(service, profileKey);
      if (!alive.current) return;
      setChecks(value => ({ ...value, [service]: "ok" }));
    } catch (error) {
      if (!alive.current) return;
      setChecks(value => ({ ...value, [service]: "error" }));
      onError(ownerFacingError(error, `Проверка ${SERVICE_LABELS[service] ?? service} не прошла.`));
    } finally {
      setBusy("");
    }
  };

  return (
    <Card role="region" aria-label="Подключение Google Workspace">
      <CardHeader className="bg-transparent">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            {connected || legacyCompatible ? <ShieldCheck className="h-5 w-5 text-success" /> : <ShieldOff className="h-5 w-5 text-muted-foreground" />}
            <CardTitle className="text-base">Google Workspace</CardTitle>
          </div>
          <Badge>{stateLabel}</Badge>
        </div>
        <CardDescription>
          Подключите Google-аккаунт и выберите, к каким сервисам агенту разрешён доступ. Одно подключение можно использовать для нескольких агентов.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 p-4">
        {!status ? <p className="text-sm text-muted-foreground">Загрузка…</p> : null}

        {status && !status.app.configured ? (
          <div className="rounded-md border border-warning/40 bg-warning/5 p-3 text-sm">
            Подключение Google ещё не настроено на сервере. Обратитесь в поддержку Korra.
          </div>
        ) : null}

        {sharedFrom ? (
          <div className="rounded-md border border-primary/30 bg-primary/5 p-3 text-sm">
            Источник подключения: <strong>{label(sharedFrom)}</strong>. Отключение здесь снимет доступ только у этого агента; остальные продолжат работу.
          </div>
        ) : null}

        {sharedWith.length ? (
          <div className="rounded-md border border-primary/30 bg-primary/5 p-3 text-sm">
            Это подключение также используют: <strong>{sharedWith.map(label).join(", ")}</strong>. Чтобы отключить Google целиком, сначала отключите общий доступ у этих агентов.
          </div>
        ) : null}

        {needsReauth ? (
          <div className="rounded-md border border-warning/40 bg-warning/5 p-3 text-sm">
            {legacyCompatible
              ? "Доступ к перечисленным сервисам сохранён — можно продолжать работу. Для дополнительных сервисов потребуется новое согласие Google."
              : "Не удалось подтвердить разрешения этого подключения. Отключите Google и подключите сервисы заново."}
            {legacyCompatible && sharedFrom ? " Расширять доступ нужно у агента, указанного как источник подключения." : ""}
          </div>
        ) : null}

        {!connected && !needsReauth && !sharedFrom ? (
          // Пока открыт поток согласия, выбор сервисов заморожен: он уже ушёл в
          // Google, и новый выбор без отмены молча заменил бы начатый поток.
          <fieldset className="grid gap-2" disabled={!status?.app.configured || Boolean(busy) || Boolean(status?.pending.active)}>
            <legend className="mb-1 text-sm font-medium">Какие сервисы разрешить этому агенту</legend>
            <div className="flex flex-wrap gap-x-5 gap-y-2">
              {available.map(service => (
                <label key={service} className="flex cursor-pointer items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={selected.includes(service)}
                    onChange={event => setSelected(value => event.target.checked ? [...value, service] : value.filter(item => item !== service))}
                  />
                  {SERVICE_LABELS[service] ?? service}
                </label>
              ))}
            </div>
          </fieldset>
        ) : null}

        {!connected && !needsReauth && !sharedFrom && status?.app.configured ? (
          <div className="flex flex-wrap gap-2">
            {/* Начать и завершить — взаимоисключающие шаги: при открытом потоке
                второй «Подключить Google» заменил бы state, и вставленный адрес
                перестал бы подходить (урок инцидента со Скрынник). */}
            {status.pending.active ? (
              <Button size="sm" outlined disabled={Boolean(busy)} onClick={() => void cancel()}>Отменить подключение</Button>
            ) : (
              <Button size="sm" disabled={!canStart} onClick={() => void start()}>
                {busy === "start" ? "Открываем…" : "Подключить Google"}
              </Button>
            )}
          </div>
        ) : null}

        {authUrl ? (
          <a className="inline-flex items-center gap-2 text-sm text-primary hover:underline" href={authUrl} target="_blank" rel="noreferrer">
            Открыть страницу Google ещё раз <ExternalLink className="h-4 w-4" />
          </a>
        ) : null}

        {status?.pending.active ? (
          <div className="grid gap-2">
            <p className="text-sm text-muted-foreground">
              После подтверждения браузер покажет ошибку localhost. Скопируйте полный адрес из адресной строки и вставьте сюда.
            </p>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                type="password"
                autoComplete="off"
                value={callbackUrl}
                onChange={event => setCallbackUrl(event.target.value)}
                placeholder="http://localhost/?state=…&code=…"
                aria-label="Полный адрес возврата Google"
              />
              <Button size="sm" disabled={!callbackUrl.trim() || Boolean(busy)} onClick={() => void complete()}>
                Завершить
              </Button>
            </div>
          </div>
        ) : null}

        {connected || needsReauth || sharedFrom ? (
          <div className="grid gap-3">
            {(connected || legacyServices.length > 0) && <p className="text-sm font-medium">Доступны сейчас · нажмите, чтобы проверить</p>}
            <div className="flex flex-wrap gap-2">
              {(connected ? status?.connection.services ?? [] : legacyServices).filter(service => service !== "all").map(service => (
                <Button key={service} size="sm" outlined title={`Проверить доступ к ${SERVICE_LABELS[service] ?? service}`} disabled={Boolean(busy)} onClick={() => void check(service)}>
                  <RefreshCw className="mr-1 h-3.5 w-3.5" />
                  {SERVICE_LABELS[service] ?? service}
                  {checks[service] === "ok" ? " ✓" : checks[service] === "error" ? " !" : ""}
                </Button>
              ))}
            </div>
            <Button size="sm" outlined disabled={Boolean(busy) || (!sharedFrom && sharedWith.length > 0)} onClick={() => void revoke()} className="w-fit">
              {sharedFrom ? "Отключить общий доступ" : needsReauth ? "Отключить для переподключения" : "Отключить Google"}
            </Button>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
