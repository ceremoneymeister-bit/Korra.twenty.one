/**
 * ProfileLearningPanel — «Обучение и настройки» одного агента.
 *
 * Сердце миссии: предприниматель без технических навыков должен сам
 * научить агента своему делу и увидеть, что агент это усвоил. Панель
 * собирает в одном месте четыре настоящих механизма движка и называет их
 * человеческими словами (парная работа с Астрой, РЕШЕНО 06.09):
 *
 * - **Роль и правила** — `SOUL.md` профиля: правила, которые попадают в
 *   системный промпт на каждый разговор. Дефолт движка — английский
 *   «You are Korra. Be direct…» — здесь распознаётся как «роль не задана».
 * - **Что важно помнить** — встроенная память `memories/MEMORY.md` и
 *   `USER.md`: записи, которые агент видит в начале каждого нового разговора.
 *   Пишем через `MemoryStore` сервера (лимиты, блокировки, проверка текста),
 *   поэтому запись идентифицируется своим полным текстом, а не номером.
 * - **Материалы и инструкции** — обычные навыки (`SKILL.md` + references):
 *   текст, ссылка или файл. Сохранение не значит чтения: агент открывает
 *   материал, когда его об этом просят, — поэтому рядом «Проверить вопросом».
 * - **Проверить вопросом** — тот же маршрут, что и чат (`probeProfileChat`):
 *   каждая проверка — новый разговор, агент читает роль и память заново.
 *
 * Панель не рисует подложку и не решает, где ей жить: список агентов
 * монтирует её в свой модальный контейнер по адресу `?agent=…&edit=learning`
 * (чтобы открыть другой раздел или другого агента, смените `key`). Методы
 * памяти и материалов — `api.getProfileMemory`/`…Materials` (Астра,
 * `7fb215d67c`): тонкий слой над `MemoryStore` и менеджером навыков сервера.
 */

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { useNavigate } from "react-router";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import { Textarea } from "@nous-research/ui/ui/components/textarea";
import { api, probeProfileChat } from "@/lib/api";
import type {
  ProfileInfo,
  ProfileMaterialInfo,
  ProfileMemoryData,
} from "@/lib/api";
import {
  formatChars,
  MATERIAL_FILE_EXTENSIONS,
  materialFileProblem,
  materialProbePrompt,
} from "@/lib/agent-learning";
import { agentSettingsHref } from "@/lib/agent-tabs";
import {
  composeSoul,
  explainProbeFailure,
  isEngineDefaultSoul,
} from "@/lib/agent-wizard";
import { ownerFacingError } from "@/lib/owner-facing-error";
import { cn } from "@/lib/utils";

type MemoryTarget = "memory" | "user";
type MaterialKind = ProfileMaterialInfo["kind"];
type CreateMaterialBody = Parameters<typeof api.createProfileMaterial>[1];

export type LearningSection = "role" | "memory" | "materials" | "check";

const SECTIONS: ReadonlyArray<{ id: LearningSection; label: string }> = [
  { id: "role", label: "Роль и правила" },
  { id: "memory", label: "Что важно помнить" },
  { id: "materials", label: "Материалы и инструкции" },
  { id: "check", label: "Проверить вопросом" },
];

const MEMORY_TARGETS: ReadonlyArray<{
  id: MemoryTarget;
  title: string;
  hint: string;
  placeholder: string;
}> = [
  {
    id: "memory",
    title: "О бизнесе и работе",
    hint: "Факты, правила и договорённости, которые агент должен держать в голове: цены, сроки, кто за что отвечает.",
    placeholder:
      "Например: минимальный заказ — 12 изделий; срок изготовления — 10 рабочих дней.",
  },
  {
    id: "user",
    title: "Обо мне",
    hint: "Кто вы и как с вами общаться: имя, роль в компании, что вы любите и не любите в ответах.",
    placeholder: "Например: меня зовут Дмитрий, обращайся на «ты», отвечай коротко.",
  },
];

const MATERIAL_KIND_LABEL: Record<MaterialKind, string> = {
  text: "Текст",
  link: "Ссылка",
  file: "Файл",
};

/* ------------------------------------------------------------------ */
/*  Загрузка ресурса профиля без setState внутри эффекта               */
/* ------------------------------------------------------------------ */

type LoadState = "loading" | "ready" | "error";

interface Snapshot<T> {
  profile: string;
  value?: T;
  failed?: boolean;
}

/**
 * Ресурс одного профиля (роль, память, материалы): снимок хранится вместе с
 * именем профиля, поэтому смена агента сама даёт «загружаю», а поздний ответ
 * прошлого агента не показывается. Эффект только запускает запрос и пишет
 * результат; состояние «загружаю» выводится, а не выставляется.
 */
function useProfileResource<T>(
  profileName: string,
  fetcher: (name: string) => Promise<T>,
) {
  const [snapshot, setSnapshot] = useState<Snapshot<T> | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    fetcher(profileName).then(
      (value) => {
        if (alive) setSnapshot({ profile: profileName, value });
      },
      () => {
        if (alive) setSnapshot({ profile: profileName, failed: true });
      },
    );
    return () => {
      alive = false;
    };
  }, [fetcher, profileName, attempt]);

  const current = snapshot?.profile === profileName ? snapshot : null;
  const state: LoadState = !current ? "loading" : current.failed ? "error" : "ready";

  const retry = useCallback(() => {
    setSnapshot(null);
    setAttempt((count) => count + 1);
  }, []);

  /** Перечитать после записи, не показывая «загружаю»: список остаётся на
   *  экране, пока сервер отдаёт свежий снимок. */
  const reload = useCallback(async () => {
    const value = await fetcher(profileName);
    setSnapshot({ profile: profileName, value });
  }, [fetcher, profileName]);

  const setValue = useCallback(
    (value: T) => setSnapshot({ profile: profileName, value }),
    [profileName],
  );

  return { state, value: current?.value, retry, reload, setValue };
}

/* ------------------------------------------------------------------ */
/*  Панель                                                             */
/* ------------------------------------------------------------------ */

export interface ProfileLearningPanelProps {
  profile: ProfileInfo;
  /** Раздел при открытии. Чтобы переключить снаружи — смените `key`. */
  section?: LearningSection;
  onClose: () => void;
  /** Роль или память изменились — список может перечитать профиль. */
  onProfileChanged?: () => void;
}

export default function ProfileLearningPanel({
  profile,
  section: initialSection = "role",
  onClose,
  onProfileChanged,
}: ProfileLearningPanelProps) {
  const navigate = useNavigate();
  const baseId = useId();
  const [section, setSection] = useState<LearningSection>(initialSection);
  const [checkPrompt, setCheckPrompt] = useState("");
  const [checkMaterial, setCheckMaterial] = useState<ProfileMaterialInfo | null>(null);
  const displayName = profile.display_name?.trim() || profile.name;

  const openCheck = useCallback((material: ProfileMaterialInfo) => {
    setCheckMaterial(material);
    setCheckPrompt("");
    setSection("check");
  }, []);

  return (
    <div className="grid gap-5" data-learning-panel={profile.name}>
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id={`${baseId}-title`} className="text-base">
          Обучение и настройки
          <span className="text-[var(--neo-text-secondary)]"> · {displayName}</span>
        </h2>
        <Button ghost size="sm" onClick={onClose}>
          Закрыть
        </Button>
      </header>

      <div
        role="tablist"
        aria-label="Разделы обучения"
        className="neo-tabs-list flex flex-wrap items-center gap-1 p-1.5"
      >
        {SECTIONS.map((item) => (
          <button
            key={item.id}
            id={`${baseId}-tab-${item.id}`}
            type="button"
            role="tab"
            aria-selected={section === item.id}
            aria-controls={`${baseId}-panel-${item.id}`}
            data-active={section === item.id ? "true" : undefined}
            onClick={() => setSection(item.id)}
            className={cn(
              "neo-tab flex min-h-9 items-center px-3 py-2 font-sans text-sm normal-case tracking-normal",
              section === item.id && "font-semibold",
            )}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div
        id={`${baseId}-panel-${section}`}
        role="tabpanel"
        aria-labelledby={`${baseId}-tab-${section}`}
        className="grid gap-4"
      >
        {section === "role" && (
          <RoleSection
            profileName={profile.name}
            displayName={displayName}
            onChanged={onProfileChanged}
          />
        )}
        {section === "memory" && (
          <MemorySection profileName={profile.name} onChanged={onProfileChanged} />
        )}
        {section === "materials" && (
          <MaterialsSection profileName={profile.name} onCheck={openCheck} />
        )}
        {section === "check" && (
          <CheckSection
            key={checkMaterial?.name ?? "general"}
            material={checkMaterial}
            onClearMaterial={() => setCheckMaterial(null)}
            profileName={profile.name}
            prompt={checkPrompt}
            onPromptChange={setCheckPrompt}
          />
        )}
      </div>

      {/* Остальные механизмы обучения живут в своих разделах — открываем их
          сразу для этого агента, а не для запомненного в разделе. */}
      <p className="text-xs text-[var(--neo-text-secondary)]">
        В «Навыках и инструментах» выберите, что агент умеет делать. В «Расписании»
        опишите регулярную задачу и время запуска. Добавьте в неё все важные условия:
        по умолчанию такие задания выполняются без заметок памяти.
      </p>
      <nav aria-label="Ещё для этого агента" className="flex flex-wrap gap-2">
        <Button ghost size="sm" onClick={() => navigate(agentSettingsHref(profile.name, "skills"))}>
          Навыки и инструменты
        </Button>
        <Button ghost size="sm" onClick={() => navigate(agentSettingsHref(profile.name, "schedule"))}>
          Расписание
        </Button>
        <Button ghost size="sm" onClick={() => navigate(agentSettingsHref(profile.name, "model"))}>
          Модель
        </Button>
        <Button
          size="sm"
          onClick={() =>
            navigate(
              `/agents?agent=${encodeURIComponent(profile.is_default ? "default" : profile.name)}`,
            )
          }
        >
          Открыть чат
        </Button>
      </nav>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Роль и правила                                                     */
/* ------------------------------------------------------------------ */

function RoleSection({
  profileName,
  displayName,
  onChanged,
}: {
  profileName: string;
  displayName: string;
  onChanged?: () => void;
}) {
  const soul = useProfileResource(profileName, api.getProfileSoul);
  const original = soul.value?.content ?? "";
  // Правка хранится вместе с текстом, от которого она сделана: пришла новая
  // роль (сохранили или перечитали) — правка сбрасывается сама.
  const [edited, setEdited] = useState<{ base: string; text: string } | null>(null);
  const text = edited && edited.base === original ? edited.text : original;
  const dirty = text !== original;
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const fieldRef = useRef<HTMLTextAreaElement | null>(null);

  const roleMissing = soul.state === "ready" && isEngineDefaultSoul(original);

  const save = async () => {
    if (soul.state !== "ready" || saving || !dirty) return;
    setSaving(true);
    setNote(null);
    try {
      await api.updateProfileSoul(profileName, text);
      soul.setValue({ content: text, exists: true });
      setEdited(null);
      setNote({ tone: "ok", text: "Сохранено. Изменения применятся в новом разговоре." });
      onChanged?.();
    } catch (error) {
      setNote({ tone: "error", text: ownerFacingError(error, "Не удалось сохранить роль.") });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="grid gap-3">
      <p className="text-sm text-[var(--neo-text-secondary)]">
        Напишите обычными словами, что агент делает, какие правила соблюдает и
        что должен знать о вашем бизнесе. Это его инструкция на каждый разговор.
      </p>

      {roleMissing && (
        <div className="grid gap-2 text-sm" role="note">
          <p className="flex flex-wrap items-center gap-2">
            <Badge tone="warning" className="shrink-0">
              роль не задана
            </Badge>
            Сейчас агент работает по стандартным правилам движка на английском и
            не знает, кто он.
          </p>
          <div>
            <Button
              size="sm"
              onClick={() => {
                setEdited({ base: original, text: composeSoul(displayName, "") });
                setNote(null);
                window.requestAnimationFrame(() => fieldRef.current?.focus());
              }}
            >
              Задать роль по-русски
            </Button>
          </div>
        </div>
      )}

      {soul.state === "loading" && <p role="status">Загружаю роль…</p>}
      {soul.state === "error" && (
        <div role="alert" className="grid gap-2 text-sm">
          <p>Не удалось загрузить роль. Повторите загрузку, чтобы не затереть её пустым текстом.</p>
          <div>
            <Button size="sm" onClick={soul.retry}>
              Повторить загрузку
            </Button>
          </div>
        </div>
      )}

      <Label htmlFor="learning-role" className="sr-only">
        Роль и правила
      </Label>
      <Textarea
        id="learning-role"
        ref={fieldRef}
        className="min-h-64"
        disabled={soul.state !== "ready" || saving}
        placeholder="Опишите задачи агента, правила работы и важные факты о вашем бизнесе."
        value={text}
        onChange={(event: React.ChangeEvent<HTMLTextAreaElement>) => {
          setEdited({ base: original, text: event.target.value });
          setNote(null);
        }}
      />

      <div className="flex flex-wrap items-center justify-between gap-2">
        <p
          className={cn(
            "text-sm",
            note?.tone === "error" ? "text-destructive" : "text-[var(--neo-text-secondary)]",
          )}
          role={note?.tone === "error" ? "alert" : "status"}
        >
          {note?.text ?? "Изменения применятся в новом разговоре."}
        </p>
        <Button
          size="sm"
          onClick={() => void save()}
          disabled={soul.state !== "ready" || saving || !dirty}
        >
          {saving ? "Сохраняю…" : "Сохранить роль"}
        </Button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Что важно помнить                                                  */
/* ------------------------------------------------------------------ */

function MemorySection({
  profileName,
  onChanged,
}: {
  profileName: string;
  onChanged?: () => void;
}) {
  const resource = useProfileResource<ProfileMemoryData>(profileName, api.getProfileMemory);
  const memory = resource.value ?? null;
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [drafts, setDrafts] = useState<Record<MemoryTarget, string>>({ memory: "", user: "" });
  const [editing, setEditing] = useState<{ target: MemoryTarget; text: string; draft: string } | null>(null);
  const [removing, setRemoving] = useState<{ target: MemoryTarget; text: string } | null>(null);

  /** Любая запись: ошибка сервера показывается дословно (лимит, проверка
   *  текста), после успеха список перечитывается — снимок делает сервер. */
  const run = async (action: () => Promise<unknown>, after?: () => void) => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await action();
      after?.();
      await resource.reload();
      onChanged?.();
    } catch (caught) {
      setError(ownerFacingError(caught, "Не удалось изменить память агента."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-4">
      <p className="text-sm text-[var(--neo-text-secondary)]">
        Записи, которые агент перечитывает в начале каждого нового разговора.
        Он и сам добавляет их, когда вы говорите «запомни»; здесь их можно
        посмотреть, поправить и удалить.
      </p>

      {resource.state === "loading" && <p role="status">Загружаю память…</p>}
      {resource.state === "error" && (
        <div role="alert" className="grid gap-2 text-sm">
          <p>Не удалось загрузить память агента.</p>
          <div>
            <Button size="sm" onClick={resource.retry}>
              Повторить
            </Button>
          </div>
        </div>
      )}
      {error && (
        <div className="grid gap-2">
          <p role="alert" className="text-sm text-destructive">{error}</p>
          <div>
            <Button ghost size="sm" disabled={busy} onClick={() => { setError(""); resource.retry(); }}>
              Обновить память
            </Button>
          </div>
        </div>
      )}

      {memory &&
        MEMORY_TARGETS.map((target) => {
          const entries = memory[target.id];
          const limit = memory.limits[target.id];
          const used = memory.used[target.id];
          const enabled = memory.enabled[target.id];
          const draft = drafts[target.id];
          return (
            <section
              key={target.id}
              aria-labelledby={`learning-memory-${target.id}`}
              className="grid gap-2"
              data-memory-target={target.id}
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h3 id={`learning-memory-${target.id}`} className="text-sm font-semibold">
                  {target.title}
                </h3>
                <span className="text-xs text-[var(--neo-text-secondary)]">
                  занято {formatChars(used)} из {formatChars(limit)} знаков
                </span>
              </div>
              <p className="text-sm text-[var(--neo-text-secondary)]">{target.hint}</p>
              {!enabled && (
                <p className="flex flex-wrap items-center gap-2 text-sm">
                  <Badge tone="warning" className="shrink-0">
                    отключено
                  </Badge>
                  Этот вид памяти выключен. Сохранённые записи останутся, но
                  агент их не прочтёт, пока память не включат.
                </p>
              )}

              {entries.length === 0 ? (
                <p className="text-sm text-[var(--neo-text-secondary)]">Пока пусто.</p>
              ) : (
                <ul className="grid gap-2">
                  {entries.map((entry, index) => {
                    const isEditing =
                      editing?.target === target.id && editing.text === entry;
                    const isRemoving =
                      removing?.target === target.id && removing.text === entry;
                    return (
                      <li
                        key={`${index}-${entry.slice(0, 40)}`}
                        className="neo-field grid gap-2 px-3 py-2 text-sm"
                      >
                        {isEditing ? (
                          <>
                            <Label htmlFor={`learning-memory-edit-${target.id}`} className="sr-only">
                              Изменить запись
                            </Label>
                            <Textarea
                              id={`learning-memory-edit-${target.id}`}
                              className="min-h-20"
                              value={editing.draft}
                              disabled={busy || !enabled}
                              onChange={(event: React.ChangeEvent<HTMLTextAreaElement>) =>
                                setEditing({ ...editing, draft: event.target.value })
                              }
                            />
                            <div className="flex flex-wrap justify-end gap-2">
                              <Button ghost size="sm" disabled={busy || !enabled} onClick={() => setEditing(null)}>
                                Отмена
                              </Button>
                              <Button
                                size="sm"
                                disabled={busy || !enabled || !editing.draft.trim() || editing.draft.trim() === entry}
                                onClick={() =>
                                  void run(
                                    () =>
                                      api.replaceProfileMemory(
                                        profileName,
                                        target.id,
                                        entry,
                                        editing.draft.trim(),
                                      ),
                                    () => setEditing(null),
                                  )
                                }
                              >
                                Сохранить запись
                              </Button>
                            </div>
                          </>
                        ) : isRemoving ? (
                          <>
                            <p className="whitespace-pre-wrap">{entry}</p>
                            <div className="flex flex-wrap items-center justify-end gap-2">
                              <span className="mr-auto text-[var(--neo-text-secondary)]">
                                Удалить эту запись?
                              </span>
                              <Button ghost size="sm" disabled={busy || !enabled} onClick={() => setRemoving(null)}>
                                Отмена
                              </Button>
                              <Button
                                size="sm"
                                disabled={busy || !enabled}
                                onClick={() =>
                                  void run(
                                    () => api.removeProfileMemory(profileName, target.id, entry),
                                    () => setRemoving(null),
                                  )
                                }
                              >
                                Да, удалить
                              </Button>
                            </div>
                          </>
                        ) : (
                          <>
                            <p className="whitespace-pre-wrap">{entry}</p>
                            <div className="flex flex-wrap justify-end gap-2">
                              <Button
                                ghost
                                size="sm"
                                disabled={busy || !enabled}
                                aria-label={`Изменить запись: ${entry.slice(0, 40)}`}
                                onClick={() => {
                                  setRemoving(null);
                                  setEditing({ target: target.id, text: entry, draft: entry });
                                }}
                              >
                                Изменить
                              </Button>
                              <Button
                                ghost
                                size="sm"
                                disabled={busy || !enabled}
                                aria-label={`Удалить запись: ${entry.slice(0, 40)}`}
                                onClick={() => {
                                  setEditing(null);
                                  setRemoving({ target: target.id, text: entry });
                                }}
                              >
                                Удалить
                              </Button>
                            </div>
                          </>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}

              <form
                className="grid gap-2"
                onSubmit={(event: FormEvent<HTMLFormElement>) => {
                  event.preventDefault();
                  const value = draft.trim();
                  if (!value || !enabled) return;
                  void run(
                    () => api.addProfileMemory(profileName, target.id, value),
                    () => setDrafts((previous) => ({ ...previous, [target.id]: "" })),
                  );
                }}
              >
                <Label htmlFor={`learning-memory-add-${target.id}`} className="sr-only">
                  Новая запись: {target.title}
                </Label>
                <Textarea
                  id={`learning-memory-add-${target.id}`}
                  className="min-h-20"
                  placeholder={target.placeholder}
                  value={draft}
                  disabled={busy || !enabled}
                  onChange={(event: React.ChangeEvent<HTMLTextAreaElement>) =>
                    setDrafts((previous) => ({ ...previous, [target.id]: event.target.value }))
                  }
                />
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-xs text-[var(--neo-text-secondary)]">
                    Агент увидит запись в новом разговоре.
                  </span>
                  <Button type="submit" size="sm" disabled={busy || !enabled || !draft.trim()}>
                    Добавить запись
                  </Button>
                </div>
              </form>
            </section>
          );
        })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Материалы и инструкции                                             */
/* ------------------------------------------------------------------ */

function MaterialsSection({
  profileName,
  onCheck,
}: {
  profileName: string;
  onCheck: (material: ProfileMaterialInfo) => void;
}) {
  const resource = useProfileResource(profileName, api.getProfileMaterials);
  const materials = resource.value?.materials ?? null;
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [saved, setSaved] = useState<ProfileMaterialInfo | null>(null);

  const [kind, setKind] = useState<MaterialKind>("text");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileProblem, setFileProblem] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);

  const canSubmit =
    title.trim() !== "" &&
    !busy &&
    (kind === "text"
      ? text.trim() !== ""
      : kind === "link"
        ? url.trim() !== ""
        : file !== null && !fileProblem);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setError("");
    setSaved(null);
    const body: CreateMaterialBody = { title: title.trim() };
    if (kind === "text") body.text = text.trim();
    if (kind === "link") body.url = url.trim();
    if (kind === "file" && file) body.file = file;
    try {
      const result = await api.createProfileMaterial(profileName, body);
      setSaved({ name: result.name, title: body.title, kind, filename: file?.name });
      setTitle("");
      setText("");
      setUrl("");
      setFile(null);
      setFileProblem(null);
      if (fileInput.current) fileInput.current.value = "";
      await resource.reload();
    } catch (caught) {
      setError(ownerFacingError(caught, "Не удалось сохранить материал."));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (materialName: string) => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await api.deleteProfileMaterial(profileName, materialName);
      setRemoving(null);
      if (saved?.name === materialName) setSaved(null);
      await resource.reload();
    } catch (caught) {
      setError(ownerFacingError(caught, "Не удалось удалить материал."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-4">
      <p className="text-sm text-[var(--neo-text-secondary)]">
        Прайс, регламент, инструкция, ссылка на документ — всё, к чему агент
        должен обращаться по делу. Сохранение не значит, что агент это прочитал:
        он открывает материал, когда его просят, — проверьте вопросом.
      </p>

      {resource.state === "loading" && <p role="status">Загружаю материалы…</p>}
      {resource.state === "error" && (
        <div role="alert" className="grid gap-2 text-sm">
          <p>Не удалось загрузить материалы агента.</p>
          <div>
            <Button size="sm" onClick={resource.retry}>
              Повторить
            </Button>
          </div>
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      {materials && materials.length === 0 && (
        <p className="text-sm text-[var(--neo-text-secondary)]">Материалов пока нет.</p>
      )}
      {materials && materials.length > 0 && (
        <ul className="grid gap-2" aria-label="Материалы агента">
          {materials.map((material) => (
            <li
              key={material.name}
              className="neo-field flex flex-wrap items-center gap-2 px-3 py-2 text-sm"
              data-material={material.name}
            >
              <div className="flex min-w-0 flex-1 basis-full items-start gap-2 sm:basis-auto">
              <Badge tone="secondary" className="shrink-0">
                {MATERIAL_KIND_LABEL[material.kind]}
              </Badge>
              <span className="min-w-0 flex-1 break-words">
                {material.title}
                {material.filename ? (
                  <span className="text-[var(--neo-text-secondary)]"> · {material.filename}</span>
                ) : null}
              </span>
              </div>
              <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
              {removing === material.name ? (
                <>
                  <span className="text-[var(--neo-text-secondary)]">Удалить материал?</span>
                  <Button ghost size="sm" disabled={busy} onClick={() => setRemoving(null)}>
                    Отмена
                  </Button>
                  <Button size="sm" disabled={busy} onClick={() => void remove(material.name)}>
                    Да, удалить
                  </Button>
                </>
              ) : (
                <>
                  <Button
                    ghost
                    size="sm"
                    aria-label={`Проверить материал «${material.title}»`}
                    onClick={() => onCheck(material)}
                  >
                    Проверить
                  </Button>
                  <Button
                    ghost
                    size="sm"
                    disabled={busy}
                    aria-label={`Удалить материал «${material.title}»`}
                    onClick={() => setRemoving(material.name)}
                  >
                    Удалить
                  </Button>
                </>
              )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {saved && (
        <p role="status" className="flex flex-wrap items-center gap-2 text-sm">
          <Badge tone="success" className="shrink-0">
            сохранено
          </Badge>
          Материал сохранён. Проверьте вопросом, как агент его использует.
          <Button ghost size="sm" onClick={() => onCheck(saved)}>
            Проверить вопросом
          </Button>
        </p>
      )}

      <form className="grid gap-3" onSubmit={(event) => void submit(event)} aria-label="Добавить материал">
        <div className="grid gap-2">
          <Label htmlFor="learning-material-title">Название материала</Label>
          <Input
            id="learning-material-title"
            placeholder="Например, Прайс на корпусную мебель"
            value={title}
            maxLength={120}
            disabled={busy}
            onChange={(event: React.ChangeEvent<HTMLInputElement>) => setTitle(event.target.value)}
          />
        </div>

        <div role="radiogroup" aria-label="Вид материала" className="flex flex-wrap gap-1.5">
          {(Object.keys(MATERIAL_KIND_LABEL) as MaterialKind[]).map((item) => (
            <button
              key={item}
              type="button"
              role="radio"
              aria-checked={kind === item}
              data-material-kind={item}
              disabled={busy}
              onClick={() => setKind(item)}
              className={cn(
                "neo-tab flex min-h-8 items-center px-3 py-1.5 font-sans text-sm normal-case tracking-normal",
                kind === item && "font-semibold",
              )}
            >
              {MATERIAL_KIND_LABEL[item]}
            </button>
          ))}
        </div>

        {kind === "text" && (
          <div className="grid gap-2">
            <Label htmlFor="learning-material-text">Текст материала</Label>
            <Textarea
              id="learning-material-text"
              className="min-h-32"
              placeholder="Вставьте регламент, условия, инструкцию — как есть."
              value={text}
              disabled={busy}
              onChange={(event: React.ChangeEvent<HTMLTextAreaElement>) => setText(event.target.value)}
            />
          </div>
        )}
        {kind === "link" && (
          <div className="grid gap-2">
            <Label htmlFor="learning-material-url">Ссылка</Label>
            <Input
              id="learning-material-url"
              type="url"
              inputMode="url"
              placeholder="https://…"
              value={url}
              disabled={busy}
              onChange={(event: React.ChangeEvent<HTMLInputElement>) => setUrl(event.target.value)}
            />
            <p className="text-xs text-[var(--neo-text-secondary)]">
              Сохраняется адрес; агент откроет страницу, когда его попросят.
            </p>
          </div>
        )}
        {kind === "file" && (
          <div className="grid gap-2">
            <Label htmlFor="learning-material-file">Файл</Label>
            <input
              id="learning-material-file"
              ref={fileInput}
              type="file"
              accept={MATERIAL_FILE_EXTENSIONS.join(",")}
              disabled={busy}
              className="text-sm"
              onChange={(event: React.ChangeEvent<HTMLInputElement>) => {
                const picked = event.target.files?.[0] ?? null;
                setFile(picked);
                setFileProblem(picked ? materialFileProblem(picked) : null);
              }}
            />
            <p className={cn("text-xs", fileProblem ? "text-destructive" : "text-[var(--neo-text-secondary)]")}>
              {fileProblem ?? "TXT, MD, CSV, JSON, PDF, DOCX или XLSX до 10 МБ."}
            </p>
          </div>
        )}

        <div className="flex justify-end">
          <Button type="submit" size="sm" disabled={!canSubmit}>
            {busy ? "Сохраняю…" : "Сохранить материал"}
          </Button>
        </div>
      </form>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Проверить вопросом                                                 */
/* ------------------------------------------------------------------ */

function CheckSection({
  material,
  onClearMaterial,
  profileName,
  prompt,
  onPromptChange,
}: {
  material: ProfileMaterialInfo | null;
  onClearMaterial: () => void;
  profileName: string;
  prompt: string;
  onPromptChange: (value: string) => void;
}) {
  const [state, setState] = useState<"idle" | "sending" | "ok" | "error">("idle");
  const [reply, setReply] = useState("");
  const [failure, setFailure] = useState<{ title: string; advice: string; detail: string } | null>(null);
  const ticket = useRef(0);
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => { ticket.current += 1; abort.current?.abort(); }, []);

  const ask = async () => {
    const question = prompt.trim();
    if (!question || state === "sending") return;
    const mine = ++ticket.current;
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    setState("sending");
    setReply("");
    setFailure(null);
    try {
      const outcome = await probeProfileChat(
        profileName, material ? materialProbePrompt(material, question) : question,
        { signal: controller.signal },
      );
      if (ticket.current !== mine) return;
      if (outcome.ok) {
        setReply(outcome.reply);
        setState("ok");
        return;
      }
      const advice = explainProbeFailure(outcome.error, outcome.detail);
      setFailure({ title: advice.title, advice: advice.advice, detail: outcome.detail || outcome.error });
      setState("error");
    } catch (caught) {
      if (ticket.current !== mine) return;
      const message = ownerFacingError(caught, "Не удалось отправить вопрос.");
      const advice = explainProbeFailure(message);
      setFailure({ title: advice.title, advice: advice.advice, detail: message });
      setState("error");
    }
  };

  return (
    <div className="grid gap-3">
      <p className="text-sm text-[var(--neo-text-secondary)]">
        Это новый разговор с актуальными ролью и памятью. Задайте вопрос,
        ответ на который вам известен, и сравните результат.
        Материалы агент открывает по необходимости; проверка выбранного материала
        отдельно попросит прочитать его источники.
      </p>
      {material && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span>Проверяем материал: <strong>{material.title}</strong></span>
          <Button ghost size="sm" disabled={state === "sending"} onClick={onClearMaterial}>
            Убрать материал из вопроса
          </Button>
        </div>
      )}
      <Label htmlFor="learning-check-prompt" className="sr-only">
        Вопрос агенту
      </Label>
      <Textarea
        id="learning-check-prompt"
        disabled={state === "sending"}
        className="min-h-24"
        placeholder="Например: какой у нас минимальный заказ и срок изготовления?"
        value={prompt}
        onChange={(event: React.ChangeEvent<HTMLTextAreaElement>) => onPromptChange(event.target.value)}
      />
      <div className="flex justify-end">
        <Button size="sm" onClick={() => void ask()} disabled={!prompt.trim() || state === "sending"}>
          {state === "sending" ? "Спрашиваю…" : "Спросить агента"}
        </Button>
      </div>

      <div role="status" aria-live="polite" className="grid gap-2 text-sm">
        {state === "ok" && (
          <>
            <Badge tone="success" className="w-fit">
              ответил
            </Badge>
            <p className="whitespace-pre-wrap">{reply}</p>
          </>
        )}
        {state === "error" && failure && (
          <>
            <p className="flex flex-wrap items-center gap-2">
              <Badge tone="warning" className="shrink-0">
                не отвечает
              </Badge>
              <span className="font-medium">{failure.title}</span>
            </p>
            <p>{failure.advice}</p>
            {failure.detail && (
              <p className="break-words text-xs text-[var(--neo-text-secondary)]">
                Ответ сервера: {failure.detail.slice(0, 300)}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
