import { fetchJSON } from "@/lib/api";

export interface AgentVoiceSettings {
  enabled: boolean;
  provider: "elevenlabs" | "compatible";
  voice: string;
  model: string;
  base_url: string;
  speed: number;
  web_mode: "manual" | "auto";
  telegram_mode: "off" | "voice_only" | "all";
  has_key: boolean;
}

const voicePath = (profile: string) => `/api/profiles/${encodeURIComponent(profile || "default")}/voice`;
export function releaseSpeechClips(clips: string[]) {
  for (const clip of clips) if (clip.startsWith("blob:")) URL.revokeObjectURL(clip);
}

export const agentVoiceApi = {
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
