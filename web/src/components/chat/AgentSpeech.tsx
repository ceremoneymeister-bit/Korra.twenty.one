import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ChevronDown, ChevronUp, Volume2 } from "lucide-react";
import { agentVoiceApi, releaseSpeechClips, type AgentVoiceSettings } from "@/lib/agent-voice";

interface AgentSpeechProps {
  profile: string;
  text: string;
  streaming: boolean;
  active: boolean;
  settings: AgentVoiceSettings | null;
  children: ReactNode;
}

export function AgentSpeech({ profile, text, streaming, active, settings, children }: AgentSpeechProps) {
  const [clips, setClips] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [play, setPlay] = useState(false);
  const [voiceRequested, setVoiceRequested] = useState(Boolean(streaming && settings?.enabled && settings.web_mode === "auto"));
  const [expanded, setExpanded] = useState(false);
  const [failed, setFailed] = useState(false);
  const transcriptId = useId();
  const request = useRef(0);
  const inFlight = useRef(false);
  const streamed = useRef(false);
  const audio = useRef<HTMLAudioElement>(null);
  const activeRef = useRef(active);
  useEffect(() => { activeRef.current = active; }, [active]);
  useEffect(() => () => releaseSpeechClips(clips), [clips]);

  async function speak(autoplay: boolean) {
    if (inFlight.current || clips.length || !settings?.enabled) return;
    setVoiceRequested(true);
    if (text.length > 5000) { setFailed(true); setError("Для длинного ответа попросите агента подготовить краткую версию для озвучки."); return; }
    const current = ++request.current;
    inFlight.current = true; setLoading(true); setError(""); setFailed(false);
    try {
      const result = await agentVoiceApi.speak(profile, text);
      if (!result.clips.length) throw new Error("Empty audio");
      if (request.current === current) { setClips(result.clips); setPlay(autoplay && activeRef.current); }
      else releaseSpeechClips(result.clips);
    } catch { if (request.current === current) { setFailed(true); setError("Озвучка недоступна. Можно повторить; текст ответа сохранён."); } }
    finally { if (request.current === current) { inFlight.current = false; setLoading(false); } }
  }
  useEffect(() => () => { request.current += 1; }, []);
  useEffect(() => {
    if (streaming) {
      streamed.current = true;
      if (settings?.enabled && settings.web_mode === "auto") setVoiceRequested(true);
      return;
    }
    // Never synthesize history on mount or when returning to a hidden tab.
    if (!streamed.current) return;
    streamed.current = false;
    if (active && settings?.enabled && settings.web_mode === "auto") void speak(true);
    // A completed stream is the event. The cleared flag prevents history replay.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streaming, settings?.enabled, settings?.web_mode]);
  useEffect(() => {
    if (!active) { audio.current?.pause(); setPlay(false); }
  }, [active]);
  useEffect(() => {
    if (play && audio.current) {
      void audio.current.play().catch(() => { setPlay(false); setError("Нажмите ▶, чтобы включить звук."); });
    }
  }, [play, clips]);
  if (!settings?.enabled) return <>{children}</>;
  const voiceFirst = voiceRequested || (streaming && settings.web_mode === "auto");
  return <div className="grid min-w-0 gap-2">
    {!voiceFirst && children}
    {voiceFirst && streaming && <p role="status" className="flex min-h-[44px] items-center gap-2 text-sm text-muted-foreground"><Volume2 size={16} aria-hidden />Готовлю голосовой ответ…</p>}
    {!streaming && !clips.length && <button type="button" className="korra-chat-copy min-h-[44px] justify-self-start" disabled={loading} onClick={() => void speak(true)}><Volume2 size={15} aria-hidden /><span>{loading ? "Готовлю голос…" : "Послушать"}</span></button>}
    {clips.map((clip, i) => <audio key={clip} ref={i === 0 ? audio : undefined} controls src={clip} aria-label={`Голос агента, часть ${i + 1}`} className="max-w-full" />)}
    {error && <p role="status" className="text-xs text-muted-foreground">{error}</p>}
    {voiceFirst && !failed && <button type="button" className="korra-chat-copy min-h-[44px] justify-self-start" aria-expanded={expanded} aria-controls={transcriptId} onClick={() => setExpanded(value => !value)}>{expanded ? <ChevronUp size={16} aria-hidden /> : <ChevronDown size={16} aria-hidden />}<span>{expanded ? "Скрыть текст" : "Показать текст"}</span></button>}
    {voiceFirst && <div id={transcriptId} hidden={!expanded && !failed}>{(expanded || failed) && children}</div>}
  </div>;
}
