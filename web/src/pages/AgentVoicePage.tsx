import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { ArrowLeft, Volume2 } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import { Select, SelectOption } from "@nous-research/ui/ui/components/select";
import { useProfileScope } from "@/contexts/useProfileScope";
import { usePageHeader } from "@/contexts/usePageHeader";
import { agentVoiceApi, type AgentVoiceSettings } from "@/lib/agent-voice";

export default function AgentVoicePage() {
  const { profile, profiles } = useProfileScope();
  const { setTitle } = usePageHeader();
  useEffect(() => { setTitle("Голос агента"); return () => setTitle(null); }, [setTitle]);
  const name = profiles.find(item => item.name === (profile || "default"))?.display_name || profile || "Корра";
  return <VoiceForm key={profile} profile={profile} name={name} />;
}

export function VoiceForm({ profile, name }: { profile: string; name: string }) {
  const navigate = useNavigate();
  const [value, setValue] = useState<AgentVoiceSettings | null>(null);
  const [key, setKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [voices, setVoices] = useState<{ id: string; name: string }[]>([]);
  const [clips, setClips] = useState<string[]>([]);
  useEffect(() => {
    let current = true;
    void agentVoiceApi.get(profile).then(result => { if (current) setValue(result); })
      .catch(() => { if (current) setError("Не удалось загрузить настройки голоса. Откройте раздел ещё раз."); });
    return () => { current = false; };
  }, [profile]);
  function change(patch: Partial<AgentVoiceSettings>) {
    setValue(current => current ? { ...current, ...patch } : current);
    setDirty(true); setNotice(""); setClips([]);
  }
  async function save() {
    if (!value) return;
    setBusy(true); setError(""); setNotice("");
    try {
      setValue(await agentVoiceApi.save(profile, value, key, clearKey));
      setKey(""); setClearKey(false); setDirty(false); setNotice("Сохранено для этого агента");
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось сохранить настройки"); }
    finally { setBusy(false); }
  }
  async function loadVoices() {
    setBusy(true); setError("");
    try { setVoices((await agentVoiceApi.voices(profile)).voices); }
    catch { setError("Не удалось загрузить голоса. Проверьте сохранённый ключ ElevenLabs."); }
    finally { setBusy(false); }
  }
  async function preview() {
    setBusy(true); setError(""); setClips([]);
    try { setClips((await agentVoiceApi.speak(profile, "Здравствуйте! Это мой голос. Я рядом и готов помочь.")).clips); }
    catch { setError("Не удалось создать пробу. Проверьте подключение, модель и голос."); }
    finally { setBusy(false); }
  }
  return <div data-agent-voice className="mx-auto grid w-full max-w-3xl gap-5 p-4">
    <div className="flex items-center gap-3"><Button ghost size="icon" aria-label="К агентам" onClick={() => navigate(`/agents?agent=${encodeURIComponent(profile || "default")}`)}><ArrowLeft /></Button><div><h2 className="text-xl font-semibold">Голос · {name}</h2><p className="mt-1 text-sm text-muted-foreground">Один голос в кабинете и Telegram.</p></div></div>
    {!value && !error && <p role="status">Загружаю настройки…</p>}
    {value && <>
      <Card><CardContent className="grid gap-5 p-5">
        <label className="flex min-h-[44px] items-center gap-3 text-base font-medium"><input type="checkbox" checked={value.enabled} disabled={busy} onChange={event => change({ enabled: event.target.checked })} />Включить голос агента</label>
        <fieldset disabled={busy} className="grid min-w-0 gap-4">
          <div className="grid gap-2"><Label htmlFor="voice-provider">Сервис озвучки</Label><Select id="voice-provider" value={value.provider} onValueChange={provider => {
            change({ provider: provider as AgentVoiceSettings["provider"], voice: "", model: provider === "elevenlabs" ? "eleven_multilingual_v2" : "", base_url: "", has_key: false });
            setKey(""); setClearKey(false); setVoices([]);
          }}><SelectOption value="elevenlabs">ElevenLabs</SelectOption><SelectOption value="compatible">Свой сервер / совместимый API</SelectOption></Select></div>
          <p className="text-xs text-muted-foreground">{value.provider === "elevenlabs" ? "Используется ваш аккаунт ElevenLabs. Озвучка и пробы расходуют его лимит." : "Подключите отдельно установленный сервис с API /v1/audio/speech. Модель выбирается на вашем сервере; её условия использования действуют отдельно."}</p>
          {value.provider === "compatible" && <div className="grid gap-2"><Label htmlFor="voice-url">Адрес API</Label><Input id="voice-url" value={value.base_url} placeholder="http://voice-server:8000/v1" onChange={e => change({ base_url: e.target.value })} /><p className="text-xs text-muted-foreground">Адрес должен быть доступен серверу Korra.</p></div>}
          <div className="grid gap-2"><Label htmlFor="voice-key">API-ключ {value.provider === "compatible" ? "· если требуется" : ""}</Label><Input id="voice-key" type="password" autoComplete="new-password" value={key} placeholder={value.has_key && !clearKey ? "Ключ сохранён · оставьте пустым, чтобы сохранить" : "Вставьте ключ сервиса"} onChange={e => { setKey(e.target.value); setClearKey(false); setDirty(true); }} />{value.has_key && <label className="flex min-h-[44px] items-center gap-2 text-xs"><input type="checkbox" checked={clearKey} onChange={e => { setClearKey(e.target.checked); setDirty(true); }} />Удалить сохранённый ключ</label>}<p className="text-xs text-muted-foreground">Хранится на сервере только у этого агента.</p></div>
          <div className="grid gap-2"><Label htmlFor="voice-model">Модель озвучки</Label>{value.provider === "elevenlabs" ? <Select id="voice-model" value={value.model} onValueChange={model => change({ model })}><SelectOption value="eleven_multilingual_v2">Multilingual v2 · выразительность</SelectOption><SelectOption value="eleven_flash_v2_5">Flash v2.5 · скорость</SelectOption>{!["eleven_multilingual_v2", "eleven_flash_v2_5"].includes(value.model) && <SelectOption value={value.model}>{value.model}</SelectOption>}</Select> : <Input id="voice-model" value={value.model} placeholder="Имя модели в вашем сервисе" onChange={e => change({ model: e.target.value })} />}</div>
          <div className="grid gap-2"><Label htmlFor="voice-id">Голос</Label>{voices.length > 0 ? <Select id="voice-id" value={value.voice} onValueChange={voice => change({ voice })}><SelectOption value="">Выберите голос</SelectOption>{voices.map(voice => <SelectOption key={voice.id} value={voice.id}>{voice.name}</SelectOption>)}</Select> : <Input id="voice-id" value={value.voice} placeholder="ID голоса в выбранном сервисе" onChange={e => change({ voice: e.target.value })} />}{value.provider === "elevenlabs" && <><Button ghost size="sm" className="justify-self-start" disabled={dirty || !value.has_key} onClick={() => void loadVoices()}>Загрузить мои голоса</Button>{dirty && <p className="text-xs text-muted-foreground">Сохраните подключение, чтобы загрузить список голосов. Можно сначала сохранить с выключенным голосом.</p>}</>}</div>
          <div className="grid gap-2"><Label htmlFor="voice-speed">Скорость речи</Label><Select id="voice-speed" value={String(value.speed)} onValueChange={speed => change({ speed: Number(speed) })}>{[{ value: 0.7, label: "Медленно · 0,7×" }, { value: 0.85, label: "Спокойно · 0,85×" }, { value: 1, label: "Обычная · 1×" }, { value: 1.1, label: "Бодро · 1,1×" }, { value: 1.2, label: "Быстро · 1,2×" }, ...(![0.7, 0.85, 1, 1.1, 1.2].includes(value.speed) ? [{ value: value.speed, label: `${value.speed}×` }] : [])].map(item => <SelectOption key={item.value} value={String(item.value)}>{item.label}</SelectOption>)}</Select></div>
        </fieldset>
      </CardContent></Card>
      <Card><CardContent className="grid gap-4 p-5">
        <h3 className="font-semibold">Как отвечать</h3>
        <div className="grid gap-2"><Label htmlFor="voice-web">В кабинете</Label><Select id="voice-web" value={value.web_mode} onValueChange={mode => change({ web_mode: mode as AgentVoiceSettings["web_mode"] })}><SelectOption value="manual">По кнопке «Послушать»</SelectOption><SelectOption value="auto">Озвучивать новые ответы в открытом чате</SelectOption></Select></div>
        <div className="grid gap-2"><Label htmlFor="voice-telegram">В Telegram</Label><Select id="voice-telegram" value={value.telegram_mode} onValueChange={mode => change({ telegram_mode: mode as AgentVoiceSettings["telegram_mode"] })}><SelectOption value="off">Только текст</SelectOption><SelectOption value="voice_only">Голосом на голосовое сообщение</SelectOption><SelectOption value="all">Текст и голос на каждое сообщение</SelectOption></Select></div>
        <p className="text-xs text-muted-foreground">Если озвучка недоступна, ответ останется текстом. Другой сервис автоматически не подключается.</p>
      </CardContent></Card>
      <div className="flex flex-wrap gap-3"><Button disabled={busy || !dirty} onClick={() => void save()}>{busy ? "Подождите…" : "Сохранить"}</Button><Button ghost disabled={busy || dirty || !value.enabled} onClick={() => void preview()}><Volume2 size={17} />Проба голоса</Button></div>
      {clips.map((clip, i) => <audio key={clip} controls src={clip} aria-label={`Проба голоса ${i + 1}`} className="w-full" />)}
      {notice && <p role="status" className="text-sm">{notice}</p>}
    </>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
  </div>;
}
