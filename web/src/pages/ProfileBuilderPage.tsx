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
 *
 * 06.09 (аудит эталона владельца): заготовки ролей — с чего начать, когда не
 * знаешь, что писать; модель выбирается в два шага (провайдер по-русски →
 * модель), а не из 51 строки подряд; шаг проверки отдельно показывает, что
 * сохранено на диске, и отдельно — ответил ли провайдер и почему нет.
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
  explainProbeFailure,
  profileIdProblem,
  ROLE_STARTERS,
  slugFromDisplayName,
  uniqueProfileId,
} from "@/lib/agent-wizard";
import {
  buildModelChoices,
  choiceKey,
  groupModelChoices,
  modelKey,
  NO_KEY_MARK,
  READY_MARK,
  type ModelChoice,
} from "@/lib/model-choices";
import { ownerFacingError } from "@/lib/owner-facing-error";
import { cn } from "@/lib/utils";
import { usePageHeader } from "@/contexts/usePageHeader";
import { useProfileScope } from "@/contexts/useProfileScope";
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

/** Виртуальный агрегатор «смесь моделей» в мастере для предпринимателя не
 *  предлагаем: он не модель, а режим поверх выбранной. Остаётся на странице
 *  «Модели». */
const WIZARD_HIDDEN_PROVIDERS = ["moa"];

type ProbeState = "sending" | "ok" | "error";

/** Сколько ждать ответ на контрольное сообщение. Медленная модель отвечает
 *  за десятки секунд; дольше полутора минут — уже «сервис ответов недоступен»,
 *  и человек должен получить кнопки, а не вечную орбиту. */
const PROBE_TIMEOUT_MS = 90_000;

/** Что мастер положил на диск — показывается на шаге проверки отдельно от
 *  ответа провайдера: сохранённое не зависит от того, жив ли ключ. */
interface CreatedAgent {
  id: string;
  label: string;
  /** Роль: своими словами, из заготовки или не задана. */
  role: "own" | "starter" | "none";
  /** Явно выбранная модель; null — унаследована от источника сервером. */
  model: ModelChoice | null;
  /** Сервер подтвердил запись модели (`model_set`). */
  modelSaved: boolean;
}

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
  // Каталог профилей разделов «Ключи», «Навыки», «Задачи» грузится один раз;
  // без обновления ссылка на нового агента там молча выбирала бы главного
  // (находка Астры, 05.09).
  const { refreshProfiles } = useProfileScope();
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
  // Текст владельца, который заменила заготовка, — чтобы одно нажатие не
  // стёрло написанное безвозвратно.
  const [replacedRole, setReplacedRole] = useState<string | null>(null);

  // ── Модель ─────────────────────────────────────────────────────────
  const [modelChoices, setModelChoices] = useState<ModelChoice[] | null>(null);
  // Модель профиля, которым управляет панель, — запасной вариант, когда у
  // источника своей нет: этот провайдер на контуре точно рабочий.
  const [currentModelChoice, setCurrentModelChoice] = useState("");
  const [modelChoice, setModelChoice] = useState("");
  // Провайдер, выбранный руками; "" — «как у главного агента».
  const [providerChoice, setProviderChoice] = useState("");
  // Владелец выбрал модель руками — подстановка по умолчанию замолкает.
  const modelChoiceTouched = useRef(false);

  // ── Дополнительно ──────────────────────────────────────────────────
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [cloneFrom, setCloneFrom] = useState<string | null>(null);
  const [noSkills, setNoSkills] = useState(false);

  // ── Создание и проверка ────────────────────────────────────────────
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const [probeFor, setProbeFor] = useState<CreatedAgent | null>(null);
  const [probeState, setProbeState] = useState<ProbeState>("sending");
  const [probeReply, setProbeReply] = useState("");
  const [probeError, setProbeError] = useState("");
  const [probeDetail, setProbeDetail] = useState("");
  // Номер текущей проверки: ответ прошлой попытки не должен переписать
  // результат той, что запустил «Повторить проверку».
  const probeRequest = useRef(0);
  // Текущий запрос проверки — чтобы уход со страницы, сброс мастера и
  // предел ожидания его обрывали, а не оставляли висеть (ревью Астры 06.09).
  const probeAbort = useRef<AbortController | null>(null);
  useEffect(() => () => probeAbort.current?.abort(), []);

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
        setModelChoices(
          buildModelChoices(res.providers, { hide: WIZARD_HIDDEN_PROVIDERS }),
        );
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

  /** Ключ модели источника — то, что получит агент, если ничего не выбирать. */
  const defaultModelKey =
    modelKey(sourceProfile?.provider ?? null, sourceProfile?.model ?? null) ||
    currentModelChoice;

  // Модель по умолчанию — та, на которой работает источник, а не первая
  // строка списка: первая строка уводила владельца на провайдера без ключа.
  useEffect(() => {
    if (modelChoices === null || modelChoiceTouched.current) return;
    setModelChoice(
      defaultModelKey &&
        modelChoices.some((choice) => choiceKey(choice) === defaultModelKey)
        ? defaultModelKey
        : "",
    );
  }, [modelChoices, defaultModelKey]);

  const groups = useMemo(
    () => groupModelChoices(modelChoices ?? []),
    [modelChoices],
  );
  const pickedGroup = useMemo(
    () => groups.find((group) => group.provider === providerChoice) ?? null,
    [groups, providerChoice],
  );
  const pickedModel = useMemo(
    () => modelChoices?.find((choice) => choiceKey(choice) === modelChoice) ?? null,
    [modelChoices, modelChoice],
  );

  /** Владелец выбрал провайдера: модель — его же у источника, иначе первая. */
  const chooseProvider = (provider: string) => {
    setProviderChoice(provider);
    if (!provider) {
      // Снова «как у главного агента»: подстановка по умолчанию оживает.
      modelChoiceTouched.current = false;
      setModelChoice(
        defaultModelKey &&
          (modelChoices ?? []).some((choice) => choiceKey(choice) === defaultModelKey)
          ? defaultModelKey
          : "",
      );
      return;
    }
    modelChoiceTouched.current = true;
    const group = groups.find((item) => item.provider === provider);
    const sameAsDefault = group?.choices.find(
      (choice) => choiceKey(choice) === defaultModelKey,
    );
    const first = sameAsDefault ?? group?.choices[0] ?? null;
    setModelChoice(first ? choiceKey(first) : "");
  };

  const applyStarter = (starterId: string) => {
    const starter = ROLE_STARTERS.find((item) => item.id === starterId);
    if (!starter) return;
    if (!displayName.trim()) setDisplayName(starter.name);
    const current = role.trim();
    const isStarterText = ROLE_STARTERS.some((item) => item.role === current);
    if (current && !isStarterText && current !== starter.role) {
      setReplacedRole(role);
    }
    setRole(starter.role);
  };

  const runProbe = useCallback(async (id: string) => {
    probeRequest.current += 1;
    const ticket = probeRequest.current;
    probeAbort.current?.abort();
    const controller = new AbortController();
    probeAbort.current = controller;
    const timer = window.setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
    setProbeState("sending");
    setProbeReply("");
    setProbeError("");
    setProbeDetail("");
    try {
      const outcome = await probeProfileChat(id, PROBE_PROMPT, {
        signal: controller.signal,
      });
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
      if (controller.signal.aborted) {
        setProbeError("Проверка не дождалась ответа.");
        setProbeDetail(`timeout ${PROBE_TIMEOUT_MS / 1000}s`);
      } else {
        setProbeError(
          ownerFacingError(error, "Не удалось отправить контрольное сообщение."),
        );
        setProbeDetail("");
      }
      setProbeState("error");
    } finally {
      window.clearTimeout(timer);
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
      // Агент уже есть на диске: неудача обновления каталога — не повод
      // считать создание провалившимся и тем более повторять его.
      void refreshProfiles().catch(() => undefined);
      const modelSaved = !picked || res.model_set !== false;
      if (!modelSaved) {
        showToast(
          "Агент создан, но модель не сохранилась — задайте её в настройках агента.",
          "error",
        );
      }
      const roleText = role.trim();
      setProbeFor({
        id: created,
        label: name,
        role: !roleText
          ? "none"
          : ROLE_STARTERS.some((item) => item.role === roleText)
            ? "starter"
            : "own",
        model: picked,
        modelSaved,
      });
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
    probeAbort.current?.abort();
    setProbeFor(null);
    setProbeState("sending");
    setProbeReply("");
    setProbeError("");
    setProbeDetail("");
    setDisplayName("");
    setCustomId(null);
    setRole("");
    setReplacedRole(null);
    setCloneFrom(null);
    setNoSkills(false);
    setCreateError("");
    setProviderChoice("");
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
  const activeStarter =
    ROLE_STARTERS.find((item) => item.role === role.trim())?.id ?? null;

  if (probeFor !== null) {
    const failure =
      probeState === "error" ? explainProbeFailure(probeError, probeDetail) : null;
    const savedModel = probeFor.model
      ? `${probeFor.model.providerName} · ${probeFor.model.model}`
      : cloning
        ? "как у агента-источника"
        : "как у главного агента";
    return (
      <div className="mx-auto w-full max-w-3xl space-y-6 p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <H2>Проверка агента</H2>
          <span className="text-sm text-[var(--neo-text-secondary)]">
            {probeFor.label}
          </span>
        </div>

        {/* Сохранённое — отдельно от ответа провайдера: сегодня провайдер
            может лежать (401 подписки, 503 лимита), а агент при этом уже
            есть на диске целиком. Раньше человек видел только «не отвечает»
            и шёл чинить ключ, который ни при чём (аудит 06.09, F14). */}
        <Card>
          <CardContent className="grid gap-3 p-5">
            <p className="text-sm font-semibold">Сохранено</p>
            <dl className="grid gap-1.5 text-sm sm:grid-cols-[8rem_1fr]">
              <dt className="text-[var(--neo-text-secondary)]">Имя</dt>
              <dd>{probeFor.label}</dd>
              <dt className="text-[var(--neo-text-secondary)]">Роль</dt>
              <dd>
                {probeFor.role === "own" && "своими словами, плюс правила общения по-русски"}
                {probeFor.role === "starter" && "из заготовки, плюс правила общения по-русски"}
                {probeFor.role === "none" &&
                  "не задана — только имя и правила общения. Добавьте роль в меню вкладки: «Роль и поведение»."}
              </dd>
              <dt className="text-[var(--neo-text-secondary)]">Модель</dt>
              <dd className="flex flex-wrap items-center gap-2">
                {probeFor.modelSaved ? (
                  savedModel
                ) : (
                  <>
                    <Badge tone="warning" className="shrink-0">
                      не сохранилась
                    </Badge>
                    задайте модель в меню вкладки
                  </>
                )}
              </dd>
            </dl>
          </CardContent>
        </Card>

        <Card>
          {/* Одна живая область на весь шаг: содержимое меняется на месте,
              поэтому скринридер слышит и ожидание, и итог. */}
          <CardContent
            className="grid gap-4 p-5"
            role="status"
            aria-live="polite"
          >
            <p className="text-sm font-semibold">Ответ агента</p>
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
                {/* Ждать не обязательно: агент уже сохранён, чат откроется и
                    без проверки. Уход со страницы обрывает запрос. */}
                <Button
                  ghost
                  className="ml-auto"
                  onClick={() =>
                    navigate(`/agents?agent=${encodeURIComponent(probeFor.id)}`)
                  }
                >
                  Не ждать — открыть чат
                </Button>
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
                  Вкладка «{probeFor.label}» уже есть на экране «Агенты».
                  Обучать агента дальше — из меню вкладки: «Роль и поведение»
                  (инструкции и факты о бизнесе), «Навыки» (умения),
                  «Расписание» (регулярная работа).
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

            {probeState === "error" && failure && (
              <>
                <div className="grid gap-2">
                  <div className="flex items-start gap-2">
                    <Badge tone="warning" className="shrink-0">
                      не отвечает
                    </Badge>
                    <p className="text-sm font-medium">{failure.title}</p>
                  </div>
                  <p className="text-sm">{failure.advice}</p>
                  {(probeDetail || probeError) && (
                    <p className="break-words text-xs text-[var(--neo-text-secondary)]">
                      Ответ сервера: {(probeDetail || probeError).slice(0, 300)}
                    </p>
                  )}
                </div>
                <div className="flex flex-wrap justify-end gap-2">
                  {failure.keys && (
                    <Button
                      ghost
                      onClick={() =>
                        navigate(`/env?profile=${encodeURIComponent(probeFor.id)}`)
                      }
                    >
                      Открыть «Ключи»
                    </Button>
                  )}
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
            {/* Заготовки — не шаблоны «на выбор», а с чего начать: текст
                попадает в поле и правится как свой. Имя подставляется, только
                пока поле имени пустое. */}
            <div
              role="group"
              aria-label="Заготовки роли"
              className="flex flex-wrap gap-1.5"
            >
              {ROLE_STARTERS.map((starter) => (
                <button
                  key={starter.id}
                  type="button"
                  aria-pressed={activeStarter === starter.id}
                  data-starter={starter.id}
                  onClick={() => applyStarter(starter.id)}
                  className={cn(
                    "neo-tab flex min-h-8 items-center px-3 py-1.5 font-sans text-sm normal-case tracking-normal",
                    activeStarter === starter.id && "font-semibold",
                  )}
                >
                  {starter.name}
                </button>
              ))}
            </div>
            <Textarea
              id="pb-role"
              className="min-h-40"
              placeholder={ROLE_PLACEHOLDER}
              value={role}
              onChange={(event: React.ChangeEvent<HTMLTextAreaElement>) => {
                setRole(event.target.value);
                setReplacedRole(null);
              }}
            />
            {replacedRole !== null ? (
              <p className="text-sm text-[var(--neo-text-secondary)]">
                Заготовка заменила ваш текст.{" "}
                <button
                  type="button"
                  className="underline underline-offset-2 hover:text-[var(--neo-text-primary)]"
                  onClick={() => {
                    setRole(replacedRole);
                    setReplacedRole(null);
                  }}
                >
                  Вернуть мой текст
                </button>
              </p>
            ) : (
              <p className="text-sm text-[var(--neo-text-secondary)]">
                Напишите своими словами, как объяснили бы новому сотруднику. Это
                станет инструкцией агента; потом её можно менять в меню вкладки —
                «Роль и поведение».
              </p>
            )}
          </div>

          <div className="grid gap-2">
            <Label htmlFor="pb-provider">Модель</Label>
            {/* Два шага вместо одного списка на полсотни строк: сначала
                провайдер (по-русски, с признаком ключа), потом его модели.
                По умолчанию — как у главного агента: у него ключ точно есть. */}
            <Select
              id="pb-provider"
              value={providerChoice}
              disabled={modelChoices === null}
              onValueChange={chooseProvider}
            >
              <SelectOption value="">
                {modelChoices === null
                  ? "Загружаю список моделей…"
                  : cloning
                    ? "Как у агента-источника"
                    : "Как у главного агента"}
              </SelectOption>
              {groups.map((group) => (
                <SelectOption key={group.provider} value={group.provider}>
                  {group.ready
                    ? group.providerName
                    : `${group.providerName} — ${NO_KEY_MARK}`}
                </SelectOption>
              ))}
            </Select>

            {pickedGroup && (
              <Select
                id="pb-model"
                value={modelChoice}
                aria-label={`Модель провайдера ${pickedGroup.providerName}`}
                onValueChange={(value) => {
                  modelChoiceTouched.current = true;
                  setModelChoice(value);
                }}
              >
                {pickedGroup.choices.map((choice) => (
                  <SelectOption key={choiceKey(choice)} value={choiceKey(choice)}>
                    {choice.model}
                  </SelectOption>
                ))}
              </Select>
            )}

            {pickedModel && !pickedModel.ready && (
              <p className="flex flex-wrap items-center gap-2 text-sm text-[var(--neo-text-secondary)]">
                <Badge tone="warning" className="shrink-0">
                  {NO_KEY_MARK}
                </Badge>
                Агент не ответит, пока в «Ключах» не появится доступ к «
                {pickedModel.providerName}».
              </p>
            )}

            {pickedModel?.ready && (
              <p className="flex flex-wrap items-center gap-2 text-sm text-[var(--neo-text-secondary)]">
                <Badge tone="success" className="shrink-0">
                  настроено
                </Badge>
                {providerChoice
                  ? `Настройки подключения к «${pickedModel.providerName}» найдены. После создания проверим ответ.`
                  : `${pickedModel.providerName} · ${pickedModel.model} — настройки подключения найдены. После создания проверим ответ.`}
              </p>
            )}

            {modelChoices !== null && !pickedModel && !providerChoice && (
              <p className="text-sm text-[var(--neo-text-secondary)]">
                {modelChoices.length === 0
                  ? "Нет провайдеров с ключами — добавьте ключ в «Ключах»."
                  : "Модель и ключ главного агента перейдут новому автоматически."}
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
                      setProviderChoice("");
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
