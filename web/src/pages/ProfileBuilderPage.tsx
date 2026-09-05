/**
 * ProfileBuilderPage — мастер создания агента.
 *
 * Единственный путь завести агента из панели (парная работа с Астрой 05.09:
 * быстрая модалка из списка профилей снята, «+» на экране агентов и «Создать»
 * в списке ведут сюда). Человек отвечает на два вопроса своими словами — как
 * зовут агента и чем он занимается, — а сущности движка выводятся сами:
 * идентификатор профиля (транслит имени), SOUL.md (роль + русские правила
 * общения) и короткое описание для карточки. Модель по умолчанию — та, на
 * которой работает главный агент: у неё точно есть ключ.
 *
 * Мастер обещает не «профиль создан», а «агент отвечает»: после создания он
 * тем же маршрутом, что и чат, просит агента представиться. Так видно, что
 * роль дошла до модели (описание карточки агент не видит — только SOUL.md),
 * а не только что ключ провайдера жив.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { ChevronDown, ChevronUp } from "lucide-react";
import { ThinkingOrb } from "thinking-orbs";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Checkbox } from "@nous-research/ui/ui/components/checkbox";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import {
  Select,
  SelectOption,
} from "@nous-research/ui/ui/components/select";
import { Textarea } from "@nous-research/ui/ui/components/textarea";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { api, probeProfileChat } from "@/lib/api";
import type { ProfileInfo } from "@/lib/api";
import {
  composeSoul,
  descriptionFromRole,
  profileIdProblem,
  slugFromDisplayName,
  uniqueProfileId,
} from "@/lib/agent-wizard";
import {
  buildModelChoices,
  choiceKey,
  modelKey,
  NO_KEY_MARK,
  READY_MARK,
  type ModelChoice,
} from "@/lib/model-choices";
import { ownerFacingError } from "@/lib/owner-facing-error";
import { usePageHeader } from "@/contexts/usePageHeader";
import { useTheme } from "@/themes";

/**
 * Контрольный вопрос. Не «готов?», а «представься»: ответ показывает, что
 * агент получил роль, а не только ключ провайдера. Проверено живьём 05.09:
 * агент с ролью в SOUL.md отвечает «Я — Секретарь…», без неё — «Я Korra,
 * ИИ-агент для кода».
 */
export const PROBE_PROMPT = "Представься одной фразой: кто ты и чем занимаешься?";

// Пример роли не обещает того, чего SOUL.md сам не даёт (подключённой почты,
// календаря, расписания): только то, что агент умеет из коробки — читать,
// уточнять, готовить текст (замечание Астры по ревью 05.09).
const ROLE_PLACEHOLDER =
  "Например: помогаешь разбирать заявки клиентов. Уточняешь количество, сроки " +
  "и бюджет. Готовишь ответ клиенту, спорные вопросы передаёшь мне.";

type ProbeState = "sending" | "ok" | "error";

/** Подпись профиля в списках: человеческое имя, а системное — в скобках. */
function profileLabel(profile: ProfileInfo): string {
  const display = profile.display_name?.trim();
  if (profile.is_default) return display ? `${display} (главный агент)` : "Главный агент";
  return display && display !== profile.name ? `${display} (${profile.name})` : profile.name;
}

export default function ProfileBuilderPage() {
  const navigate = useNavigate();
  const { toast, showToast } = useToast();
  const { setTitle } = usePageHeader();
  // Орбита шага «Проверка» рисуется в теме панели — как в чате.
  const { themeName } = useTheme();

  useEffect(() => {
    setTitle("Новый агент");
    return () => setTitle(null);
  }, [setTitle]);

  // ── Кто это ────────────────────────────────────────────────────────
  const [profiles, setProfiles] = useState<ProfileInfo[] | null>(null);
  const [displayName, setDisplayName] = useState("");
  // null — системное имя выводится из человеческого; строка — владелец
  // поправил его руками, и транслит больше не вмешивается.
  const [customId, setCustomId] = useState<string | null>(null);
  const [role, setRole] = useState("");

  // ── Модель ─────────────────────────────────────────────────────────
  const [modelChoices, setModelChoices] = useState<ModelChoice[] | null>(null);
  // Модель профиля, которым управляет панель, — запасной вариант, когда у
  // источника своей нет: этот провайдер на контуре точно рабочий.
  const [currentModelChoice, setCurrentModelChoice] = useState("");
  const [modelChoice, setModelChoice] = useState("");
  // Владелец выбрал модель руками — подстановка по умолчанию замолкает.
  const modelChoiceTouched = useRef(false);

  // ── Дополнительно ──────────────────────────────────────────────────
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [cloneFrom, setCloneFrom] = useState<string | null>(null);
  const [noSkills, setNoSkills] = useState(false);

  // ── Создание и проверка ────────────────────────────────────────────
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const [probeFor, setProbeFor] = useState<{ id: string; label: string } | null>(
    null,
  );
  const [probeState, setProbeState] = useState<ProbeState>("sending");
  const [probeReply, setProbeReply] = useState("");
  const [probeError, setProbeError] = useState("");
  const [probeDetail, setProbeDetail] = useState("");
  // Номер текущей проверки: ответ прошлой попытки не должен переписать
  // результат той, что запустил «Повторить проверку».
  const probeRequest = useRef(0);

  useEffect(() => {
    let alive = true;
    api
      .getProfiles()
      .then((res) => {
        if (alive) setProfiles(res.profiles ?? []);
      })
      .catch(() => {
        // Без списка мастер всё равно работает: занятое имя отвергнет сервер.
        if (alive) setProfiles([]);
      });
    api
      .getModelOptions()
      .then((res) => {
        if (!alive) return;
        setModelChoices(buildModelChoices(res.providers));
        setCurrentModelChoice(modelKey(res.provider ?? null, res.model ?? null));
      })
      .catch(() => {
        if (alive) setModelChoices([]);
      });
    return () => {
      alive = false;
    };
  }, []);

  const takenIds = useMemo(
    () => (profiles ?? []).map((profile) => profile.name),
    [profiles],
  );
  const derivedId = useMemo(
    () => uniqueProfileId(slugFromDisplayName(displayName), takenIds),
    [displayName, takenIds],
  );
  const profileId = customId ?? derivedId;
  const idProblem = profileIdProblem(profileId, takenIds);
  const nameReady = displayName.trim() !== "";
  const idReady = profileId !== "" && idProblem === null;

  /** Профиль, с которого мастер снимает модель по умолчанию: выбранный
   *  источник, а без копирования — главный агент. */
  const sourceProfile = useMemo(() => {
    const list = profiles ?? [];
    const name = cloneFrom ?? list.find((profile) => profile.is_default)?.name ?? null;
    return (name && list.find((profile) => profile.name === name)) || null;
  }, [cloneFrom, profiles]);

  // Модель по умолчанию — та, на которой работает источник, а не первая
  // строка списка: первая строка уводила владельца на провайдера без ключа.
  useEffect(() => {
    if (modelChoices === null || modelChoiceTouched.current) return;
    const wanted =
      modelKey(sourceProfile?.provider ?? null, sourceProfile?.model ?? null) ||
      currentModelChoice;
    setModelChoice(
      wanted && modelChoices.some((choice) => choiceKey(choice) === wanted)
        ? wanted
        : "",
    );
  }, [modelChoices, sourceProfile, currentModelChoice]);

  const pickedModel = useMemo(
    () => modelChoices?.find((choice) => choiceKey(choice) === modelChoice) ?? null,
    [modelChoices, modelChoice],
  );

  const runProbe = useCallback(async (id: string) => {
    probeRequest.current += 1;
    const ticket = probeRequest.current;
    setProbeState("sending");
    setProbeReply("");
    setProbeError("");
    setProbeDetail("");
    try {
      const outcome = await probeProfileChat(id, PROBE_PROMPT);
      if (probeRequest.current !== ticket) return;
      if (outcome.ok) {
        setProbeReply(outcome.reply);
        setProbeState("ok");
        return;
      }
      setProbeError(outcome.error);
      setProbeDetail(outcome.detail);
      setProbeState("error");
    } catch (error) {
      if (probeRequest.current !== ticket) return;
      setProbeError(
        ownerFacingError(error, "Не удалось отправить контрольное сообщение."),
      );
      setProbeDetail("");
      setProbeState("error");
    }
  }, []);

  const handleCreate = async () => {
    const name = displayName.trim();
    if (!name || !idReady || creating) return;
    setCreating(true);
    setCreateError("");
    const picked = pickedModel;
    // Один POST на всё, что нужно агенту для первого ответа: модель и ключи,
    // имя для вкладки, роль. Второго вызова у мастера нет — упади он, агент
    // остался бы без роли молча (урок 8161d4b926).
    const body = {
      name: profileId,
      clone_from: cloneFrom,
      clone_all: false,
      no_skills: cloneFrom ? false : noSkills,
      description: descriptionFromRole(role) || undefined,
      provider: picked?.provider,
      model: picked?.model,
      display_name: name,
      soul: composeSoul(name, role),
    };
    try {
      const res = await api.createProfile(body);
      // Каноническое имя решает сервер — и вкладка, и контрольное сообщение
      // адресуются им, а не тем, что вывел транслит.
      const created = res.name || profileId;
      if (picked && res.model_set === false) {
        showToast(
          "Агент создан, но модель не сохранилась — задайте её в настройках агента.",
          "error",
        );
      }
      setProbeFor({ id: created, label: name });
      void runProbe(created);
    } catch (error) {
      setCreateError(ownerFacingError(error, "Не удалось создать агента."));
    } finally {
      setCreating(false);
    }
  };

  /** Сбросить мастер целиком — под «Создать ещё одного». */
  const resetWizard = () => {
    probeRequest.current += 1;
    setProbeFor(null);
    setProbeState("sending");
    setProbeReply("");
    setProbeError("");
    setProbeDetail("");
    setDisplayName("");
    setCustomId(null);
    setRole("");
    setCloneFrom(null);
    setNoSkills(false);
    setCreateError("");
    modelChoiceTouched.current = false;
    // Список профилей пополнился только что созданным — системное имя
    // следующего агента не должно с ним совпасть.
    api
      .getProfiles()
      .then((res) => setProfiles(res.profiles ?? []))
      .catch(() => undefined);
  };

  const goBack = () => {
    if (window.history.length > 1) navigate(-1);
    else navigate("/agents");
  };

  const cloning = cloneFrom !== null;

  if (probeFor !== null) {
    return (
      <div className="mx-auto w-full max-w-3xl space-y-6 p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <H2>Проверка агента</H2>
          <span className="text-sm text-[var(--neo-text-secondary)]">
            {probeFor.label}
          </span>
        </div>

        <Card>
          {/* Одна живая область на весь шаг: содержимое меняется на месте,
              поэтому скринридер слышит и ожидание, и итог. */}
          <CardContent
            className="grid gap-4 p-5"
            role="status"
            aria-live="polite"
          >
            {probeState === "sending" && (
              <div className="flex items-center gap-3">
                <ThinkingOrb
                  state="working"
                  size={20}
                  speed={1.3}
                  theme={themeName === "dark" ? "dark" : "light"}
                  aria-label="Проверяю агента"
                  style={{ transform: "scale(1.4)", margin: "0.25rem" }}
                />
                <span className="text-sm text-[var(--neo-text-secondary)]">
                  Спрашиваю агента, кто он…
                </span>
              </div>
            )}

            {probeState === "ok" && (
              <>
                <div className="flex items-start gap-2">
                  <Badge tone="success" className="shrink-0">
                    {READY_MARK}
                  </Badge>
                  {/* Просили одну фразу, но ответить агент может абзацем —
                      показываем только начало. */}
                  <p className="text-sm">
                    Агент отвечает: «{probeReply.slice(0, 300)}»
                  </p>
                </div>
                <p className="text-sm text-[var(--neo-text-secondary)]">
                  Вкладка «{probeFor.label}» уже есть на экране «Агенты». Роль и
                  модель меняются в меню вкладки; умения добавляются в «Навыках»,
                  регулярная работа — в «Задачах» с выбором этого агента.
                </p>
                <div className="flex flex-wrap justify-end gap-2">
                  <Button ghost onClick={resetWizard}>
                    Создать ещё одного
                  </Button>
                  <Button
                    onClick={() =>
                      navigate(`/agents?agent=${encodeURIComponent(probeFor.id)}`)
                    }
                  >
                    Открыть чат
                  </Button>
                </div>
              </>
            )}

            {probeState === "error" && (
              <>
                <div className="grid gap-2">
                  <div className="flex items-start gap-2">
                    <Badge tone="warning" className="shrink-0">
                      не отвечает
                    </Badge>
                    <p className="text-sm">{probeError}</p>
                  </div>
                  {probeDetail && probeDetail !== probeError && (
                    <p className="break-words text-xs text-[var(--neo-text-secondary)]">
                      {probeDetail.slice(0, 300)}
                    </p>
                  )}
                  <p className="text-sm text-[var(--neo-text-secondary)]">
                    Агент создан, но не ответил. Чаще всего дело в ключе
                    провайдера — проверьте его в «Ключах» для агента{" "}
                    {probeFor.id}.
                  </p>
                </div>
                <div className="flex flex-wrap justify-end gap-2">
                  <Button
                    ghost
                    onClick={() =>
                      navigate(`/env?profile=${encodeURIComponent(probeFor.id)}`)
                    }
                  >
                    Открыть «Ключи»
                  </Button>
                  <Button
                    ghost
                    onClick={() =>
                      navigate(`/agents?agent=${encodeURIComponent(probeFor.id)}`)
                    }
                  >
                    Открыть чат
                  </Button>
                  <Button onClick={() => void runProbe(probeFor.id)}>
                    Повторить проверку
                  </Button>
                </div>
              </>
            )}
          </CardContent>
        </Card>

        <Toast toast={toast} />
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 p-4">
      <div className="flex items-center justify-between">
        <H2>Новый агент</H2>
        <Button ghost onClick={goBack}>
          Отмена
        </Button>
      </div>

      <Card>
        <CardContent className="grid gap-5 p-5">
          <div className="grid gap-2">
            <Label htmlFor="pb-name">Как зовут агента</Label>
            <Input
              id="pb-name"
              autoFocus
              placeholder="Например, Секретарь или Учитель китайского"
              value={displayName}
              maxLength={64}
              onChange={(event: React.ChangeEvent<HTMLInputElement>) =>
                setDisplayName(event.target.value)
              }
            />
            <div className="flex flex-wrap items-center gap-2 text-sm text-[var(--neo-text-secondary)]">
              <label htmlFor="pb-id" className="shrink-0">
                Системное имя:
              </label>
              <Input
                id="pb-id"
                className="h-9 min-h-9 w-56 text-sm"
                value={profileId}
                aria-invalid={idProblem !== null}
                placeholder="латиницей"
                onChange={(event: React.ChangeEvent<HTMLInputElement>) => {
                  const value = event.target.value.trim().toLowerCase();
                  // Пустое поле — снова выводить из имени.
                  setCustomId(value === "" ? null : value);
                }}
              />
            </div>
            <p
              className={
                idProblem
                  ? "text-sm text-destructive"
                  : "text-sm text-[var(--neo-text-secondary)]"
              }
            >
              {idProblem ??
                "Латиницей, для адреса чата и папки на диске. Обычно менять не нужно."}
            </p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="pb-role">Что он делает и как себя ведёт</Label>
            <Textarea
              id="pb-role"
              className="min-h-40"
              placeholder={ROLE_PLACEHOLDER}
              value={role}
              onChange={(event: React.ChangeEvent<HTMLTextAreaElement>) =>
                setRole(event.target.value)
              }
            />
            <p className="text-sm text-[var(--neo-text-secondary)]">
              Напишите своими словами, как объяснили бы новому сотруднику. Это
              станет инструкцией агента; потом её можно менять в меню вкладки —
              «Роль и поведение».
            </p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="pb-model">Модель</Label>
            <Select
              id="pb-model"
              value={modelChoice}
              disabled={modelChoices === null}
              onValueChange={(value) => {
                modelChoiceTouched.current = true;
                setModelChoice(value);
              }}
            >
              <SelectOption value="">
                {modelChoices === null
                  ? "Загружаю список моделей…"
                  : cloning
                    ? "Как у агента-источника"
                    : "Как у главного агента"}
              </SelectOption>
              {(modelChoices ?? []).map((choice) => (
                <SelectOption key={choiceKey(choice)} value={choiceKey(choice)}>
                  {choice.label}
                </SelectOption>
              ))}
            </Select>

            {pickedModel && !pickedModel.ready && (
              <p className="flex flex-wrap items-center gap-2 text-sm text-[var(--neo-text-secondary)]">
                <Badge tone="warning" className="shrink-0">
                  {NO_KEY_MARK}
                </Badge>
                Агент не ответит, пока ключ провайдера {pickedModel.providerName}{" "}
                не появится в «Ключах».
              </p>
            )}

            {pickedModel?.ready && (
              <p className="flex flex-wrap items-center gap-2 text-sm text-[var(--neo-text-secondary)]">
                <Badge tone="success" className="shrink-0">
                  {READY_MARK}
                </Badge>
                Ключ провайдера {pickedModel.providerName} настроен.
              </p>
            )}

            {modelChoices !== null && modelChoices.length === 0 && (
              <p className="text-sm text-[var(--neo-text-secondary)]">
                Нет провайдеров с ключами — добавьте ключ в «Ключах».
              </p>
            )}
          </div>

          <div className="grid gap-3">
            <button
              type="button"
              className="flex w-fit items-center gap-1.5 text-sm text-[var(--neo-text-secondary)] hover:text-[var(--neo-text-primary)]"
              aria-expanded={advancedOpen}
              aria-controls="pb-advanced"
              onClick={() => setAdvancedOpen((open) => !open)}
            >
              {advancedOpen ? (
                <ChevronUp size={16} aria-hidden />
              ) : (
                <ChevronDown size={16} aria-hidden />
              )}
              Дополнительно
            </button>

            {advancedOpen && (
              <div id="pb-advanced" className="grid gap-4">
                <div className="grid gap-2">
                  <Label htmlFor="pb-clone">Скопировать настройки у агента</Label>
                  <Select
                    id="pb-clone"
                    value={cloneFrom ?? ""}
                    onValueChange={(value) => {
                      setCloneFrom(value || null);
                      // Источник сменился — модель по умолчанию снова идёт от
                      // него, пока владелец не выберет другую руками.
                      modelChoiceTouched.current = false;
                    }}
                  >
                    <SelectOption value="">Не копировать (обычно так)</SelectOption>
                    {(profiles ?? []).map((profile) => (
                      <SelectOption key={profile.name} value={profile.name}>
                        {profileLabel(profile)}
                      </SelectOption>
                    ))}
                  </Select>
                  <p className="text-sm text-[var(--neo-text-secondary)]">
                    Новый агент получит навыки, ключи и настройки выбранного
                    агента. Роль всё равно возьмётся из этого мастера.
                  </p>
                </div>

                <label className="flex items-center gap-2.5 text-sm">
                  <Checkbox
                    id="pb-no-skills"
                    checked={noSkills}
                    disabled={cloning}
                    onCheckedChange={(checked) => setNoSkills(checked === true)}
                  />
                  <span className={cloning ? "opacity-50" : undefined}>
                    Без встроенных навыков — только базовые умения; навыки
                    можно добавить позже
                  </span>
                </label>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {createError && (
        <p role="alert" className="text-sm text-destructive">
          {createError}
        </p>
      )}

      <div className="flex items-center justify-end gap-2">
        <Button
          onClick={handleCreate}
          disabled={!nameReady || !idReady || creating || profiles === null}
        >
          {creating ? "Создаю…" : "Создать агента"}
        </Button>
      </div>

      <Toast toast={toast} />
    </div>
  );
}
