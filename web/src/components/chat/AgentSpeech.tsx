import { useEffect, useRef, useState } from "react";
import { Volume2 } from "lucide-react";
import { agentVoiceApi, releaseSpeechClips, type AgentVoiceSettings } from "@/lib/agent-voice";

interface AgentSpeechProps {
  profile: string;
  text: string;
  streaming: boolean;
  active: boolean;
  settings: AgentVoiceSettings | null;
}

export function AgentSpeech({ profile, text, streaming, active, settings }: AgentSpeechProps) {
  const [clips, setClips] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [play, setPlay] = useState(false);
  const request = useRef(0);
  const inFlight = useRef(false);
  const streamed = useRef(false);
  const audio = useRef<HTMLAudioElement>(null);
  const activeRef = useRef(active);
  useEffect(() => { activeRef.current = active; }, [active]);
  useEffect(() => () => releaseSpeechClips(clips), [clips]);

  async function speak(autoplay: boolean) {
    if (inFlight.current || clips.length || !settings?.enabled) return;
    if (text.length > 5000) { setError("Для длинного ответа попросите агента подготовить краткую версию для озвучки."); return; }
    const current = ++request.current;
    inFlight.current = true; setLoading(true); setError("");
    try {
      const result = await agentVoiceApi.speak(profile, text);
      if (request.current === current) { setClips(result.clips); setPlay(autoplay && activeRef.current); }
      else releaseSpeechClips(result.clips);
    } catch { if (request.current === current) setError("Озвучка недоступна. Можно повторить; текст ответа сохранён."); }
    finally { if (request.current === current) { inFlight.current = false; setLoading(false); } }
  }
  useEffect(() => () => { request.current += 1; }, []);
  useEffect(() => {
    if (streaming) { streamed.current = true; return; }
    // Never synthesize history on mount or when returning to a hidden tab.
    if (!streamed.current) return;
    streamed.current = false;
    if (active && settings?.enabled && settings.web_mode === "auto") void speak(true);
    // A completed stream is the event. Settings/text changes do not replay it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streaming]);
  useEffect(() => {
    if (!active) { audio.current?.pause(); setPlay(false); }
  }, [active]);
  useEffect(() => {
    if (play && audio.current) {
      void audio.current.play().catch(() => { setPlay(false); setError("Нажмите ▶, чтобы включить звук."); });
    }
  }, [play, clips]);
  if (!settings?.enabled || streaming) return null;
  return <div className="mt-2 grid gap-2">
    {!clips.length && <button type="button" className="korra-chat-copy min-h-[44px] justify-self-start" disabled={loading} onClick={() => void speak(true)}><Volume2 size={15} aria-hidden /><span>{loading ? "Готовлю голос…" : "Послушать"}</span></button>}
    {clips.map((clip, i) => <audio key={clip} ref={i === 0 ? audio : undefined} controls src={clip} aria-label={`Голос агента, часть ${i + 1}`} className="max-w-full" />)}
    {error && <p role="status" className="text-xs text-muted-foreground">{error}</p>}
  </div>;
}
