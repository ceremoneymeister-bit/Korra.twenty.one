import { useCallback, useEffect, useMemo, useState } from "react";
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


interface Props {
  onError: (message: string) => void;
  onSuccess: (message: string) => void;
}


export function GoogleWorkspaceCard({ onError, onSuccess }: Props) {
  const { profile, currentProfile } = useProfileScope();
  const profileKey = profile || currentProfile;
  const [status, setStatus] = useState<GoogleWorkspaceStatus | null>(null);
  const [selected, setSelected] = useState<string[]>(["drive", "sheets", "calendar"]);
  const [authUrl, setAuthUrl] = useState("");
  const [callbackUrl, setCallbackUrl] = useState("");
  const [busy, setBusy] = useState("");
  const [checks, setChecks] = useState<Record<string, "ok" | "error">>({});

  const load = useCallback(async () => {
    try {
      const next = await api.getGoogleWorkspaceStatus();
      setStatus(next);
      if (next.pending.active && next.pending.services?.length) {
        setSelected(next.pending.services);
      } else if (next.connection.services?.length) {
        setSelected(next.connection.services.filter(service => service !== "all"));
      }
    } catch (error) {
      onError(ownerFacingError(error, "Не удалось получить статус Google."));
    }
  }, [onError, profileKey]);

  useEffect(() => {
    setStatus(null);
    setAuthUrl("");
    setCallbackUrl("");
    setChecks({});
    void load();
  }, [load]);

  const available = status?.available_services ?? [];
  const connected = status?.connection.state === "connected";
  const needsReauth = status?.connection.state === "reauthorization_required";
  const legacyServices = status?.connection.usable_services ?? [];
  const legacyCompatible = Boolean(needsReauth && status?.connection.legacy_compatible);
  const canStart = Boolean(status?.app.configured && selected.length && !busy && !needsReauth);
  const stateLabel = useMemo(() => {
    if (!status) return "Проверяем";
    if (!status.app.configured) return "Приложение не установлено";
    if (connected) return "Подключено";
    if (needsReauth) return "Нужно переподключить";
    if (status.pending.active) return "Ожидает подтверждения";
    return "Не подключено";
  }, [connected, needsReauth, status]);

  const start = async () => {
    setBusy("start");
    try {
      const result = await api.startGoogleWorkspace(selected);
      setAuthUrl(result.authorization_url);
      window.open(result.authorization_url, "_blank", "noopener,noreferrer");
      await load();
    } catch (error) {
      onError(ownerFacingError(error, "Не удалось начать подключение Google."));
    } finally {
      setBusy("");
    }
  };

  const complete = async () => {
    setBusy("complete");
    try {
      await api.completeGoogleWorkspace(callbackUrl);
      setCallbackUrl("");
      setAuthUrl("");
      onSuccess("Google Workspace подключён к выбранному агенту.");
      await load();
    } catch (error) {
      onError(ownerFacingError(error, "Не удалось завершить подключение Google."));
    } finally {
      setBusy("");
    }
  };

  const cancel = async () => {
    setBusy("cancel");
    try {
      await api.cancelGoogleWorkspace();
      setAuthUrl("");
      setCallbackUrl("");
      await load();
    } catch (error) {
      onError(ownerFacingError(error, "Не удалось отменить подключение Google."));
    } finally {
      setBusy("");
    }
  };

  const revoke = async () => {
    setBusy("revoke");
    try {
      const result = await api.revokeGoogleWorkspace();
      setAuthUrl("");
      setCallbackUrl("");
      setChecks({});
      if (result.remote_revoked) {
        onSuccess("Google Workspace отключён от выбранного агента.");
      } else {
        onError(
          "Локальный доступ выбранного агента удалён, но Google не подтвердил отзыв. Удалите доступ Ceremoneymeister в настройках Google Аккаунта.",
        );
      }
      await load();
    } catch (error) {
      onError(ownerFacingError(error, "Не удалось отключить Google."));
    } finally {
      setBusy("");
    }
  };

  const check = async (service: string) => {
    setBusy(`check:${service}`);
    try {
      await api.checkGoogleWorkspaceService(service);
      setChecks(value => ({ ...value, [service]: "ok" }));
    } catch (error) {
      setChecks(value => ({ ...value, [service]: "error" }));
      onError(ownerFacingError(error, `Проверка ${SERVICE_LABELS[service] ?? service} не прошла.`));
    } finally {
      setBusy("");
    }
  };

  return (
    <Card>
      <CardHeader className="bg-transparent">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            {connected ? <ShieldCheck className="h-5 w-5 text-success" /> : <ShieldOff className="h-5 w-5 text-muted-foreground" />}
            <CardTitle className="text-base">Google Workspace</CardTitle>
          </div>
          <Badge>{stateLabel}</Badge>
        </div>
        <CardDescription>
          Один OAuth-клиент установки, настроенный оператором. Доступ и токены изолированы внутри выбранного агента.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 p-4">
        {!status ? <p className="text-sm text-muted-foreground">Загрузка…</p> : null}

        {status && !status.app.configured ? (
          <div className="rounded-md border border-warning/40 bg-warning/5 p-3 text-sm">
            OAuth-приложение ещё не установлено оператором. Секрет приложения нельзя добавлять через чат или эту страницу.
          </div>
        ) : null}

        {needsReauth ? (
          <div className="rounded-md border border-warning/40 bg-warning/5 p-3 text-sm">
            Найден прежний токен без нового списка сервисов
            {status?.connection.legacy_scope_count ? ` (${status.connection.legacy_scope_count} разрешений)` : ""}.
            {legacyCompatible
              ? " Текущие разрешения продолжают работать только для перечисленных сервисов. Для изменения доступа переподключите Google."
              : " Разрешения нельзя безопасно распознать. Отключите Google и подключите сервисы заново."}
          </div>
        ) : null}

        {!connected ? (
          <fieldset className="grid gap-2" disabled={!status?.app.configured || Boolean(busy)}>
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

        {!connected && !needsReauth && status?.app.configured ? (
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={!canStart} onClick={() => void start()}>
              {busy === "start" ? "Открываем…" : "Подключить Google"}
            </Button>
            {status.pending.active ? <Button size="sm" outlined disabled={Boolean(busy)} onClick={() => void cancel()}>Отменить</Button> : null}
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

        {connected || needsReauth ? (
          <div className="grid gap-3">
            <div className="flex flex-wrap gap-2">
              {(connected ? status?.connection.services ?? [] : legacyServices).filter(service => service !== "all").map(service => (
                <Button key={service} size="sm" outlined disabled={Boolean(busy)} onClick={() => void check(service)}>
                  <RefreshCw className="mr-1 h-3.5 w-3.5" />
                  {SERVICE_LABELS[service] ?? service}
                  {checks[service] === "ok" ? " ✓" : checks[service] === "error" ? " !" : ""}
                </Button>
              ))}
            </div>
            <Button size="sm" outlined disabled={Boolean(busy)} onClick={() => void revoke()} className="w-fit">
              {needsReauth ? "Отключить для переподключения" : "Отключить Google"}
            </Button>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
