import { useEffect, useState } from "react";
import { useStore } from "@nanostores/react";
import { Link, useLocation } from "react-router";
import { Sparkles, X } from "lucide-react";
import { api, type ReleaseNote } from "@/lib/api";
import { $seenReleaseIds, markReleaseSeen } from "@/store/release-notice";
import "./release-notice.css";

export function ReleaseNotice() {
  const { pathname } = useLocation();
  const seen = useStore($seenReleaseIds);
  const [release, setRelease] = useState<ReleaseNote | null>(null);
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const state = await api.getUpdatesState();
        if (alive) setRelease(state.installed);
      } catch { /* A quiet invitation can wait until the backend returns. */ }
    };
    void load();
    const timer = setInterval(() => void load(), 60_000);
    return () => { alive = false; clearInterval(timer); };
  }, []);

  if (!release?.release_id || seen.includes(release.release_id) || pathname.replace(/\/$/, "") === "/updates") return null;
  const dismiss = () => markReleaseSeen(release.release_id);
  return (
    <aside className="release-notice" aria-label="Что нового в Korra">
      <Sparkles size={18} aria-hidden />
      <div>
        <strong>Что нового в Korra</strong>
        <p>{release.title}</p>
      </div>
      <Link to="/updates" onClick={dismiss}>Посмотреть</Link>
      <button type="button" onClick={dismiss} aria-label="Закрыть приглашение"><X size={18} aria-hidden /></button>
    </aside>
  );
}
