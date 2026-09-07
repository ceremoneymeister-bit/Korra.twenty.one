import { useStore } from "@nanostores/react";
import { $chatRuns } from "@/lib/chat-runs";

export function useSessionRun(profile: string, sessionId: string | null) {
  return useStore($chatRuns).find(run => run.profile === profile && run.session_id === sessionId);
}
