import { useEffect, useState } from "react";
import { agentVoiceApi, type AgentVoiceSettings } from "@/lib/agent-voice";

export function useAgentVoice(profile: string, active: boolean) {
  const [voice, setVoice] = useState<AgentVoiceSettings | null>(null);
  useEffect(() => {
    if (!active) return;
    let current = true;
    async function refresh() {
      try { const next = await agentVoiceApi.get(profile); if (current) setVoice(next); }
      catch { if (current) setVoice(null); }
    }
    void refresh();
    window.addEventListener("focus", refresh);
    return () => { current = false; window.removeEventListener("focus", refresh); };
  }, [profile, active]);
  return voice;
}
