import { useEffect, useState } from "react";
import { fetchJSON, HERMES_BASE_PATH } from "@/lib/api";
import { cabinetLogoutPath } from "@/lib/cabinet-session";

/** Presentation hint only; actual authorization stays in the cabinet. */
export function useCabinetSession() {
  const fallbackLogout = cabinetLogoutPath(HERMES_BASE_PATH);
  const [capabilities, setCapabilities] = useState<Record<string, unknown>>({});
  const [logout, setLogout] = useState(fallbackLogout);
  useEffect(() => {
    if (!fallbackLogout) return;
    const controller = new AbortController();
    void fetchJSON<{ kind: string; capabilities?: Record<string, unknown>; logout_url: string }>("/api/cabinet/session", { signal: controller.signal })
      .then(session => {
        if (controller.signal.aborted || session.kind !== "cabinet") return;
        setCapabilities(session.capabilities ?? {});
        // Same-origin absolute path only. A failed/old cabinet keeps the safe
        // limited UI and its legacy logout rather than promising blocked ops.
        if (/^\/(?!\/)[a-zA-Z0-9_/-]+$/.test(session.logout_url)) setLogout(session.logout_url);
      }).catch(() => {});
    return () => controller.abort();
  }, [fallbackLogout]);
  const allowed = (name: string) => !fallbackLogout || capabilities[name] === true;
  return { logout, restrictedFiles: !allowed("files_manage"),
    canManageSkills: allowed("skills_manage"), canBrowseSkillsHub: allowed("skills_hub"),
    canConfigureToolsets: allowed("toolsets_config") };
}
