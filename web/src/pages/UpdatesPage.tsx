/**
 * Раздел «Обновления» — то, чего владельцу не хватало больше всего.
 *
 * Обновление для предпринимателя страшно не технически, а по-человечески: он
 * не знает, что у него стоит, что изменится и надолго ли всё пропадёт. Экран
 * отвечает ровно на эти три вопроса и только потом предлагает кнопку.
 *
 * Кнопка НЕ перезапускает контейнер: у панели внутри образа нет на это прав, и
 * так задумано (см. `korra_cli/web_routers/updates.py`). Она отправляет просьбу
 * в кабинет по уже существующему каналу, а кабинет возвращает сюда ход.
 *
 * Отдельная забота — минута, когда панель недоступна: в этот момент запрос
 * состояния не проходит. Пока идёт известное нам обновление, обрыв связи здесь
 * не ошибка, а ожидаемый шаг, и написан он именно так.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  Circle,
  Clock3,
  Download,
  Info,
  RotateCcw,
  Sparkles,
} from "lucide-react";
import { usePageHeader } from "@/contexts/usePageHeader";
import { api } from "@/lib/api";
import type {
  ReleaseNote,
  ReleaseNoteSection,
  UpdateProgress,
  UpdatesState,
} from "@/lib/api";
import "./updates.css";

/** Как часто спрашиваем состояние: во время работы — часто, в покое — редко. */
const POLL_ACTIVE_MS = 4000;
const POLL_IDLE_MS = 60000;

/** Сколько пунктов «что нового» показываем до нажатия «Показать всё». */
const PREVIEW_ITEMS = 4;

function stepState(steps: string[], current: string, index: number, done: boolean) {
  if (done) return "done";
  const active = steps.indexOf(current);
  if (active < 0) return index === 0 ? "active" : "pending";
  if (index < active) return "done";
  return index === active ? "active" : "pending";
}

function Pulse() {
  return (
    <span className="upd-pulse" aria-hidden>
      <i />
      <i />
      <i />
    </span>
  );
}

/** Первые PREVIEW_ITEMS пунктов подряд по разделам — превью карточки. */
function previewSections(sections: ReleaseNoteSection[], expanded: boolean): ReleaseNoteSection[] {
  if (expanded) return sections;
  const shown: ReleaseNoteSection[] = [];
  let left = PREVIEW_ITEMS;
  for (const section of sections) {
    if (left <= 0) break;
    const items = section.items.slice(0, left);
    left -= items.length;
    if (items.length) shown.push({ ...section, items });
  }
  return shown;
}

function ReleaseSections({ sections, expanded }: { sections: ReleaseNoteSection[]; expanded: boolean }) {
  return (
    <div className="upd-notes">
      {previewSections(sections, expanded).map((section) => {
        const items = section.items;
        if (!items.length) return null;
        return (
          <div className="upd-notes-group" key={section.heading || "list"}>
            {section.heading && <p>{section.heading}</p>}
            <ul>
              {items.map((item) => (
                <li key={item.title}>
                  <Sparkles size={16} aria-hidden />
                  <div>
                    <h4>{item.title}</h4>
                    {item.detail && <p>{item.detail}</p>}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

/** Карточка выпуска: заголовок, одно предложение сути и «что нового». */
function ReleaseCard({
  release,
  eyebrow,
  icon,
  badge,
  badgeTone,
  children,
}: {
  release: ReleaseNote;
  eyebrow: string;
  icon: React.ReactNode;
  badge?: string;
  badgeTone?: string;
  children?: React.ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const total = release.sections.reduce((sum, section) => sum + section.items.length, 0);
  return (
    <section className="upd-card">
      <div className="upd-card-head">
        <div>
          <p className="upd-eyebrow">
            {icon}
            {eyebrow}
          </p>
          <h2>{release.title || release.release_id}</h2>
          {release.summary && <p>{release.summary}</p>}
        </div>
        {badge && (
          <span className="upd-badge" data-tone={badgeTone}>
            {badge}
          </span>
        )}
      </div>
      <p className="upd-facts">
        <span>
          Выпуск <b>{release.release_id || "не указан"}</b>
        </span>
        {release.published_at && (
          <span>
            От <b>{release.published_at}</b>
          </span>
        )}
        {release.version && (
          <span>
            Версия <b>{release.version}</b>
          </span>
        )}
      </p>
      {children}
      {total > 0 && <ReleaseSections sections={release.sections} expanded={expanded} />}
      {total > PREVIEW_ITEMS && (
        <button type="button" className="upd-more" onClick={() => setExpanded(!expanded)}>
          {expanded ? "Свернуть" : `Показать всё (${total})`}
          <ArrowRight size={15} aria-hidden />
        </button>
      )}
    </section>
  );
}

/** Ход обновления: шаги, текущее состояние и честная минута простоя. */
function Progress({
  progress,
  steps,
  offline,
}: {
  progress: UpdateProgress;
  steps: UpdatesState["steps"];
  offline: boolean;
}) {
  const keys = steps.map((step) => step.key);
  const failed = progress.status === "failed" || progress.status === "rollback_failed";
  const back = progress.status === "rolled_back";
  const done = progress.status === "succeeded" || back;
  return (
    <section className="upd-card">
      <div className="upd-card-head">
        <div>
          <p className="upd-eyebrow">
            <Download size={17} aria-hidden />
            Обновление
          </p>
          <h2>
            {back
              ? "Вернули прежнюю версию"
              : done
                ? "Обновление завершено"
                : failed
                  ? "Обновление не прошло"
                  : progress.stale
                    ? "Об обновлении давно нет вестей"
                    : "Идёт обновление"}
          </h2>
          {/* Пока панель перезапускается, молчание — это шаг. Когда молчание
              затянулось, честнее сказать, что мы не знаем, чем оно кончилось,
              чем десятый раз повторить «займёт несколько минут». */}
          <p>
            {progress.stale
              ? "Мы не получаем сведений о ходе обновления. Возможно, оно ещё идёт, а возможно — прервалось. Ваши данные на месте: перед переключением с них снимается резервная копия. Напишите нам, мы посмотрим со своей стороны."
              : offline && !progress.final
                ? "Панель перезапускается — это шаг обновления. Страница вернётся сама."
                : progress.message}
          </p>
        </div>
        {!progress.final && !progress.stale && (
          <span className="upd-badge" data-tone="working">
            <Pulse />
            Выполняется
          </span>
        )}
      </div>
      <ol className="upd-steps">
        {steps.map((step, index) => {
          const state = failed
            ? index <= keys.indexOf(progress.step)
              ? "done"
              : "pending"
            : stepState(keys, progress.step, index, done);
          return (
            <li key={step.key} data-state={state}>
              <span className="upd-step-mark" aria-hidden>
                {state === "done" ? <Check size={16} /> : index + 1}
              </span>
              <div>
                <h4>
                  {step.title}
                  {state === "active" && !progress.final && !progress.stale && <Pulse />}
                </h4>
                <p>{step.detail}</p>
              </div>
            </li>
          );
        })}
      </ol>
      {failed && (
        <div className="upd-note" data-tone="error">
          <AlertTriangle size={18} aria-hidden />
          <div>
            <strong>
              {progress.status === "failed"
                ? "Обновиться не получилось, прежняя версия работает как раньше"
                : "Возврат прежней версии не завершился"}
            </strong>
            <p>
              {progress.error ||
                "Мы уже видим эту ошибку и разбираемся. Данные, память и доступы на месте — их сохраняют до переключения."}
            </p>
          </div>
        </div>
      )}
    </section>
  );
}

export default function UpdatesPage() {
  const { setTitle } = usePageHeader();
  const [state, setState] = useState<UpdatesState | null>(null);
  const [error, setError] = useState("");
  const [offline, setOffline] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  // Помним, что обновление шло: пока оно идёт, обрыв связи — это ожидаемая
  // минута простоя, а не поломка, и текст должен быть соответствующий.
  const running = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useLayoutEffect(() => {
    setTitle("Обновления");
    return () => setTitle(null);
  }, [setTitle]);

  const load = useCallback(async () => {
    try {
      const next = await api.getUpdatesState();
      setState(next);
      setError("");
      setOffline(false);
      running.current = Boolean(next.progress && !next.progress.final) || Boolean(next.request);
    } catch {
      if (running.current) setOffline(true);
      else setError("Не удалось узнать состояние обновлений. Проверим ещё раз через минуту.");
    }
  }, []);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      await load();
      if (!alive) return;
      timer.current = setTimeout(tick, running.current ? POLL_ACTIVE_MS : POLL_IDLE_MS);
    };
    void tick();
    return () => {
      alive = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [load]);

  const request = async () => {
    if (!state?.available) return;
    setBusy(true);
    setNotice("");
    try {
      await api.requestUpdate(state.available.release_id);
      running.current = true;
      setNotice("");
      await load();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Не удалось отправить запрос. Попробуйте ещё раз.");
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    setBusy(true);
    try {
      await api.cancelUpdateRequest();
      await load();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Не удалось отменить запрос.");
    } finally {
      setBusy(false);
    }
  };

  if (!state) {
    return (
      <div className="korra-updates">
        {error ? (
          <section className="upd-card">
            <p className="upd-eyebrow">
              <Info size={17} aria-hidden />
              Обновления
            </p>
            <h2>Пока не видно</h2>
            <p className="upd-muted">{error}</p>
          </section>
        ) : (
          <div className="upd-skeleton" aria-busy="true" aria-label="Загружаем состояние обновлений" />
        )}
      </div>
    );
  }

  const { installed, available, progress, request: pending, steps } = state;
  const active = Boolean(progress && !progress.final);
  // Операция «доехала»: цель установлена, либо это откат — он по смыслу
  // возвращает НЕ целевой выпуск, и шаги обновления после него уже не нужны.
  const finished =
    progress?.final && (progress.installed_target || progress.status === "rolled_back");

  return (
    <div className="korra-updates">
      {progress && (active || !finished) && (
        <Progress progress={progress} steps={steps} offline={offline} />
      )}

      {available && !active && (
        <ReleaseCard
          release={available}
          eyebrow="Доступен новый выпуск"
          icon={<Download size={17} aria-hidden />}
          badge={pending && !pending.stale ? "Запрос отправлен" : "Можно обновиться"}
          badgeTone={pending && !pending.stale ? "working" : "ready"}
        >
          <div className="upd-actions">
            {pending && !pending.stale ? (
              <>
                <button type="button" className="upd-button" onClick={cancel} disabled={busy}>
                  <RotateCcw size={18} aria-hidden />
                  Отменить запрос
                </button>
                <p className="upd-small upd-muted">
                  Мы передали запрос. Обновление начнётся в ближайшие минуты — можно закрыть страницу,
                  оно не прервётся.
                </p>
              </>
            ) : (
              <>
                <button type="button" className="upd-button" data-primary onClick={request} disabled={busy}>
                  <Download size={18} aria-hidden />
                  {busy ? "Отправляем…" : "Обновить"}
                </button>
                <p className="upd-small upd-muted">
                  <Clock3 size={14} aria-hidden style={{ verticalAlign: "-2px", marginRight: 6 }} />
                  Панель будет недоступна {available.pause || "около минуты"}. Память, документы, ключи и
                  расписания останутся на месте — перед переключением с них снимается копия.
                </p>
              </>
            )}
          </div>
          {notice && (
            <div className="upd-note" data-tone="error">
              <AlertTriangle size={18} aria-hidden />
              <div>
                <strong>Запрос не ушёл</strong>
                <p>{notice}</p>
              </div>
            </div>
          )}
        </ReleaseCard>
      )}

      <ReleaseCard
        release={installed}
        eyebrow="У вас установлено"
        icon={<Check size={17} aria-hidden />}
        badge={!available && !active ? "Последняя версия" : undefined}
        badgeTone="ready"
      >
        {finished && (
          <div className="upd-note">
            <Check size={18} aria-hidden />
            <div>
              <strong>
                {progress?.status === "rolled_back"
                  ? "Вернули прежнюю версию"
                  : "Обновление прошло, всё на месте"}
              </strong>
              <p>
                {progress?.status === "rolled_back"
                  ? "Данные, память и доступы остались как были."
                  : "Профили, доступы и ответ модели проверены после переключения."}
              </p>
            </div>
          </div>
        )}
        {!available && !active && !finished && !state.managed_externally && (
          <div className="upd-note">
            <Info size={18} aria-hidden />
            <div>
              <strong>Эта установка обновляется вручную</strong>
              <p>
                Она собрана не из готового образа, поэтому кнопки обновления здесь нет — обновление
                делает тот, кто её поставил.
              </p>
            </div>
          </div>
        )}
        {!available && !active && !finished && state.managed_externally && (
          <div className="upd-note">
            <Circle size={18} aria-hidden />
            <div>
              <strong>Новых выпусков пока нет</strong>
              <p>Как только выйдет новый, он появится здесь вместе со списком изменений.</p>
            </div>
          </div>
        )}
      </ReleaseCard>
    </div>
  );
}
