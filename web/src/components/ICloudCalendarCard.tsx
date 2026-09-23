import { useCallback, useEffect, useState } from "react";
import { CalendarDays, ExternalLink } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@nous-research/ui/ui/components/card";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";

import { ApiError, api, type ICloudCalendarStatus } from "@/lib/api";
import { ownerFacingError } from "@/lib/owner-facing-error";

function icloudError(cause: unknown, fallback: string): string {
  if (cause instanceof ApiError && cause.payload && typeof cause.payload === "object") {
    const detail = (cause.payload as { detail?: unknown }).detail;
    if (detail && typeof detail === "object") {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string" && message.length <= 300) return message;
    }
  }
  return ownerFacingError(cause, fallback);
}

/** Installation-wide calendar connection. Password stays only in the form until submit. */
export function ICloudCalendarCard() {
  const [status, setStatus] = useState<ICloudCalendarStatus | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [editing, setEditing] = useState(false);
  const [probe, setProbe] = useState<"idle" | "checking" | "ok" | "error">("idle");

  const load = useCallback(async () => {
    try {
      setStatus(await api.getICloudCalendarStatus());
      setError("");
      setProbe("idle");
    } catch (cause) {
      setError(icloudError(cause, "Не удалось проверить подключение iCloud."));
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const connect = async () => {
    setBusy(true);
    setError("");
    try {
      const next = await api.connectICloudCalendar(username, password);
      setStatus(next);
      setPassword("");
      setUsername("");
      setEditing(false);
      setProbe("ok");
    } catch (cause) {
      setError(icloudError(cause, "Не удалось подключить iCloud Calendar."));
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true);
    setError("");
    try {
      setStatus(await api.disconnectICloudCalendar());
      setConfirming(false);
      setProbe("idle");
    } catch (cause) {
      setError(icloudError(cause, "Не удалось отключить iCloud Calendar."));
    } finally {
      setBusy(false);
    }
  };

  const checkCalendar = async () => {
    setProbe("checking");
    setError("");
    try {
      await api.getICloudCalendarFeed();
      setProbe("ok");
    } catch (cause) {
      setProbe("error");
      setError(icloudError(cause, "Не удалось прочитать календарь iCloud."));
    }
  };

  return <Card id="section-icloud-calendar" role="region" aria-labelledby="icloud-calendar-title" className="scroll-mt-20">
    <CardHeader className="bg-transparent">
      <div className="flex items-center gap-2">
        <CalendarDays className="size-5 text-muted-foreground" aria-hidden />
        <CardTitle id="icloud-calendar-title" className="text-base">iCloud Calendar</CardTitle>
      </div>
      <CardDescription>Встречи из календарей Apple на дашборде и у всех агентов этой установки.</CardDescription>
    </CardHeader>
    <CardContent className="grid gap-4 p-4">
      {status === null ? <p role="status">Проверяем подключение…</p> : status.state === "connected" ? <>
        <p role="status" className="text-sm">Доступ сохранён для {status.account}. Все агенты и виджет используют это подключение.</p>
        {probe === "ok" ? <p role="status" className="text-sm">Проверка прошла: календарь отвечает.</p> : null}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" outlined onClick={() => void checkCalendar()} disabled={busy || probe === "checking"}>{probe === "checking" ? "Проверяем…" : "Проверить календарь"}</Button>
          <Button size="sm" outlined onClick={() => { setEditing((value) => !value); setUsername(status.account || ""); }} disabled={busy}>{editing ? "Отмена замены" : "Заменить пароль"}</Button>
          {confirming ? <>
            <Button size="sm" onClick={() => void disconnect()} disabled={busy}>Подтвердить отключение</Button>
            <Button size="sm" outlined onClick={() => setConfirming(false)} disabled={busy}>Отмена</Button>
          </> : <Button size="sm" outlined onClick={() => setConfirming(true)} disabled={busy}>Отключить iCloud</Button>}
        </div>
        {confirming ? <p className="text-sm text-muted-foreground">Виджет и все агенты сразу потеряют доступ к этим календарям. Пароль приложения также можно отозвать в Apple Account.</p> : null}
        {editing ? <div className="grid max-w-md gap-2">
          <Label htmlFor="icloud-reconnect-username">Адрес Apple ID</Label>
          <Input id="icloud-reconnect-username" type="email" autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} />
          <Label htmlFor="icloud-reconnect-password">Новый пароль приложения</Label>
          <Input id="icloud-reconnect-password" type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} />
          <Button className="mt-2 w-fit" onClick={() => void connect()} disabled={busy || !username.trim() || !password.trim()}>{busy ? "Проверяем…" : "Проверить и заменить"}</Button>
        </div> : null}
      </> : <>
        <ol className="list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
          <li>Откройте Apple Account и создайте отдельный пароль приложения для Korra. Для этого у Apple должна быть включена двухфакторная защита.</li>
          <li>Введите адрес Apple ID и новый пароль ниже. Korra проверит доступ и сохранит его для этой установки.</li>
        </ol>
        <a href="https://account.apple.com/account/manage" target="_blank" rel="noopener noreferrer" className="inline-flex w-fit items-center gap-1 text-sm text-primary hover:underline">Открыть Apple Account <ExternalLink className="size-4" aria-hidden /></a>
        <div className="grid max-w-md gap-2">
          <Label htmlFor="icloud-username">Адрес Apple ID</Label>
          <Input id="icloud-username" type="email" autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} />
          <Label htmlFor="icloud-app-password">Пароль приложения</Label>
          <Input id="icloud-app-password" type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} />
          <Button className="mt-2 w-fit" onClick={() => void connect()} disabled={busy || !username.trim() || !password.trim()}>{busy ? "Проверяем…" : "Подключить iCloud Calendar"}</Button>
        </div>
      </>}
      {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
    </CardContent>
  </Card>;
}
