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
  speak: (profile: string, text: string) => fetchJSON<{ clips: string[] }>(voicePath(profile) + "/speak", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
  }),
};
