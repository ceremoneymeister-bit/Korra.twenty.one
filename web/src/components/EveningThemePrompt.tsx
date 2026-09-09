import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useStore } from '@nanostores/react';
import { Moon, Sparkles } from 'lucide-react';
import { useTheme } from '@/themes';
import { $chatRuns, $chatRunsReachable, isRunBusy } from '@/lib/chat-runs';
import { claimEvening, eveningEligible, episodeKey } from '@/themes/evening';
import { preferenceScope } from '@/themes/preference';
import './evening-theme-prompt.css';

function visible(element: Element): boolean {
  return element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden';
}
/** Persistent shell companion. It never takes focus or reserves layout space. */
export function EveningThemePrompt({ blocked = false }: { blocked?: boolean }) {
  const { themeName, preference, setTheme, setEvening, retryTheme, saveState, saveError } = useTheme();
  const runs = useStore($chatRuns);
  const runsReachable = useStore($chatRunsReachable);
  const [now, setNow] = useState(() => new Date());
  const [domBusy, setDomBusy] = useState(true);
  const [phase, setPhase] = useState<'hidden' | 'visible' | 'leaving'>('hidden');
  const [announcement, setAnnouncement] = useState('');
  const [ownsSave, setOwnsSave] = useState(false);
  const ownedSave = useRef(false);
  useLayoutEffect(() => { ownedSave.current = ownsSave; }, [ownsSave]);
  const episode = useRef(false);
  const claimPending = useRef(false);
  const allowed = useRef(false);
  const readBusy = useRef<() => boolean>(() => true);
  const mounted = useRef(true);
  const capsule = useRef<HTMLElement>(null);
  const activityAt = useRef(0);
  const composing = useRef(false);
  const [openedAt] = useState(() => Date.now());
  const exitTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const close = useCallback(() => {
    setAnnouncement('');
    setPhase(previous => previous === 'visible' ? 'leaving' : previous);
    clearTimeout(exitTimer.current);
    exitTimer.current = setTimeout(() => setPhase('hidden'), 250);
  }, []);

  useEffect(() => {
    mounted.current = true;
    const check = () => {
      const focused = document.activeElement;
      const editing = !!focused?.matches('input:not([type=button]):not([type=checkbox]):not([type=radio]),textarea,[contenteditable=true]') && !focused.closest('[data-evening-capsule]');
      const conflict = [...document.querySelectorAll('[aria-modal=true],[role=dialog],dialog[open],[role=alert],[aria-busy=true],[data-theme-save-status],.neo-toast[data-visible=true],[role=group][aria-label^="Решение по"]')].some(element => !element.closest('[data-evening-capsule]') && !(ownedSave.current && element.closest('[data-theme-save-status]')) && visible(element) && (element.getAttribute('role') !== 'alert' || !!element.textContent?.trim()));
      const box = capsule.current?.getBoundingClientRect();
      const overlap = box && [...document.querySelectorAll('textarea,[contenteditable=true]')].some(element => {
        if (!visible(element)) return false;
        const rect = element.getBoundingClientRect();
        return rect.left < box.right && rect.right > box.left && rect.top < box.bottom && rect.bottom > box.top;
      });
      const nextBusy = document.hidden || !navigator.onLine || composing.current || editing || Date.now() - activityAt.current < 1500 || conflict || !!overlap;
      setDomBusy(nextBusy);
      return nextBusy;
    };
    readBusy.current = check;
    const input = (event: Event) => { if (!(event.target as Element)?.closest('[data-evening-capsule]')) activityAt.current = Date.now(); check(); };
    const compositionStart = () => { composing.current = true; check(); };
    const compositionEnd = () => { composing.current = false; activityAt.current = Date.now(); check(); };
    let checkFrame = 0;
    const observer = new MutationObserver(() => {
      if (!checkFrame) checkFrame = requestAnimationFrame(() => { checkFrame = 0; check(); });
    });
    observer.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['aria-modal','aria-busy','data-visible','open','class','style'] });
    const timer = setInterval(() => { setNow(new Date()); check(); }, 1000); // Local clock / OS timezone changes, no API polling.
    window.addEventListener('online', check); window.addEventListener('offline', check);
    document.addEventListener('visibilitychange', check);
    document.addEventListener('focusin', check); document.addEventListener('focusout', check);
    document.addEventListener('input', input); document.addEventListener('compositionstart', compositionStart); document.addEventListener('compositionend', compositionEnd);
    check();
    return () => {
      mounted.current = false; observer.disconnect(); cancelAnimationFrame(checkFrame); clearInterval(timer); clearTimeout(exitTimer.current);
      window.removeEventListener('online', check); window.removeEventListener('offline', check);
      document.removeEventListener('visibilitychange', check); document.removeEventListener('focusin', check); document.removeEventListener('focusout', check);
      document.removeEventListener('input', input); document.removeEventListener('compositionstart', compositionStart); document.removeEventListener('compositionend', compositionEnd);
    };
  }, []);

  const runBusy = runs.some(run => isRunBusy(run) || (run.status === 'failed' && run.updated_at * 1000 >= openedAt));
  const busy = blocked || domBusy || runBusy || runsReachable !== true || (saveState === 'pending' && !ownsSave);
  const eligible = eveningEligible(preference, now) && (themeName === 'light' || ownsSave);
  useLayoutEffect(() => { allowed.current = eligible && !busy; }, [eligible, busy]);

  useEffect(() => {
    if (phase === 'visible' && (!eligible || busy)) { close(); return; }
    if (!eligible || busy || episode.current || claimPending.current || !preference) return;
    claimPending.current = true;
    episode.current = true;
    void claimEvening(preference, now).then(claimed => {
      claimPending.current = false;
      if (!mounted.current || !claimed) return;
      episode.current = true;
      if (!allowed.current || readBusy.current()) return;
      setPhase('visible');
      setAnnouncement('Наступил вечер. Можно включить тёмную тему.');
    });
  }, [eligible, busy, preference, now, phase, close]);

  useEffect(() => {
    if (!preference?.installation_id) return;
    const changed = (event: StorageEvent) => {
      // Another tab's claim or acknowledged preference ends our episode.
      // No browser cache is promoted to authoritative theme state.
      if (event.key === episodeKey(preference) || event.key === `${preferenceScope(preference)}:theme`) {
        episode.current = true; close();
      }
    };
    window.addEventListener('storage', changed);
    return () => window.removeEventListener('storage', changed);
  }, [preference, close]);

  const choose = async (action: 'dark' | 'later' | 'disable' | 'retry') => {
    setOwnsSave(true);
    const ok = await (action === 'dark' ? setTheme('dark') : action === 'retry' ? retryTheme() : setEvening(action));
    if (!mounted.current) return;
    if (ok) { close(); setOwnsSave(false); }
    // On failure keep the offer and its retry visible, even when the local
    // palette preview is already dark. Only the durable ACK dismisses it.
  };
  return <>
    <span className="evening-live" aria-live="polite" aria-atomic="true">{announcement}</span>
    {phase !== 'hidden' && <aside ref={capsule} data-evening-capsule data-phase={phase} className="evening-capsule" aria-labelledby="evening-theme-title" aria-busy={ownsSave && saveState === 'pending'}>
      <div className="evening-capsule__halo" aria-hidden="true"><Moon/><Sparkles/></div>
      <p className="evening-capsule__eyebrow">KORRA · ВЕЧЕРНИЙ РЕЖИМ</p>
      <h2 id="evening-theme-title">Вечер. Чуть мягче свет.</h2>
      <p className="evening-capsule__description">Если хочется, приглушим панель. Ваш разговор продолжится в том же ритме.</p>
      <button className="evening-capsule__primary" type="button" disabled={saveState === 'pending'} onClick={() => void choose('dark')}><Moon size={16} aria-hidden/>{saveState === 'pending' && ownsSave ? 'Сохраняем…' : 'Включить тёмную'}</button>
      <div className="evening-capsule__choices"><button type="button" disabled={saveState === 'pending'} onClick={() => void choose('later')}>Позже · через 14 дней</button><button type="button" disabled={saveState === 'pending'} onClick={() => void choose('disable')}>Больше не предлагать</button></div>
      {ownsSave && saveState === 'error' && <div className="evening-capsule__error"><p role="alert">{saveError}</p><button type="button" onClick={() => void choose('retry')}>Повторить сохранение</button></div>}
    </aside>}
  </>;
}
