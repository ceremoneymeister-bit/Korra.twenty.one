import { authedFetch, fetchJSON } from "@/lib/api";

export interface AgentVoiceSettings {
  enabled: boolean;
  provider: "elevenlabs" | "compatible" | "openrouter_fish";
  voice: string;
  model: string;
  base_url: string;
  speed: number;
  web_mode: "manual" | "auto";
  telegram_mode: "off" | "voice_only" | "all";
  has_key: boolean;
}

export interface RecognitionStatus {
  configured: string;
  actual: string;
}

export interface BundledVoice {
  id: string;
  name: string;
  description: string;
  model: string;
  default_speed: number;
  sample_url: string;
}

const voicePath = (profile: string) => `/api/profiles/${encodeURIComponent(profile || "default")}/voice`;
export function releaseSpeechClips(clips: string[]) {
  for (const clip of clips) if (clip.startsWith("blob:")) URL.revokeObjectURL(clip);
}

export const agentVoiceApi = {
  catalog: () => fetchJSON<{ voices: BundledVoice[] }>("/api/voices"),
  /** Записанный образец встроенного голоса как Blob URL. Путь от корня в
   *  `<audio src>` шёл мимо кабинета и без токена панели (0.21.15, ревью
   *  Astra §2.4). URL освобождает вызывающий. */
  sample: async (sampleUrl: string, signal?: AbortSignal) => {
    const response = await authedFetch(sampleUrl, signal ? { signal } : undefined);
    if (!response.ok) throw new Error(`Образец голоса недоступен: ${response.status}`);
    return URL.createObjectURL(await response.blob());
  },
  recognition: (profile: string) => fetchJSON<RecognitionStatus>(voicePath(profile) + "/recognition"),
  get: (profile: string) => fetchJSON<AgentVoiceSettings>(voicePath(profile)),
  save: (profile: string, settings: AgentVoiceSettings, key: string, clearKey: boolean) => {
    const data: Partial<AgentVoiceSettings> = { ...settings };
    delete data.has_key;
    return fetchJSON<AgentVoiceSettings>(voicePath(profile), {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...data, api_key: key || undefined, clear_key: clearKey }),
    });
  },
  voices: (profile: string) => fetchJSON<{ voices: { id: string; name: string }[] }>(voicePath(profile) + "/voices"),
  speak: async (profile: string, text: string) => {
    const result = await fetchJSON<{ clips: string[] }>(voicePath(profile) + "/speak", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
    });
    // The cabinet permits blob: audio, while its CSP deliberately excludes data:.
    const clips: string[] = [];
    try {
      for (const clip of result.clips) {
        const match = /^data:(audio\/[\w.+-]+);base64,(.+)$/.exec(clip);
        if (!match) throw new Error("Некорректное аудио");
        const bytes = Uint8Array.from(atob(match[2]), character => character.charCodeAt(0));
        clips.push(URL.createObjectURL(new Blob([bytes], { type: match[1] })));
      }
      return { clips };
    } catch (error) { releaseSpeechClips(clips); throw error; }
  },
};
