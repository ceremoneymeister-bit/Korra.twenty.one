import { useCallback, useEffect, useState } from "react";
import { Clock, Wand2 } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Select, SelectOption } from "@nous-research/ui/ui/components/select";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { api } from "@/lib/api";
import type { AutomationBlueprint, AutomationBlueprintField } from "@/lib/api";
import { cn, themedBody } from "@/lib/utils";
import { ownerFacingError } from "@/lib/owner-facing-error";
import { useI18n } from "@/i18n";

interface AutomationBlueprintsProps {
  profile: string;
  /** Called after a blueprint is instantiated so the parent can refresh its job list. */
  onCreated?: () => void;
}

/** Initial form values for a blueprint = each field's default (or ""). */
const BLUEPRINT_COPY: Record<string, { title: string; description: string }> = {
  "morning-brief": { title: "Утренний обзор", description: "Календарь, погода и срочные дела на сегодня." },
  "important-mail": { title: "Важная почта", description: "Проверка входящих писем, которые требуют внимания." },
  "weekly-review": { title: "Итоги недели", description: "Выполненные дела, открытые вопросы и планы." },
  "workday-start": { title: "Начало рабочего дня", description: "Повестка дня и главные приоритеты по будням." },
  "custom-reminder": { title: "Своё напоминание", description: "Регулярное напоминание по вашему расписанию." },
  "evening-winddown": { title: "Завершение дня", description: "Короткая подготовка к завтрашнему дню." },
  "news-digest": { title: "Новости по теме", description: "Подборка новых материалов по выбранной теме." },
  "bill-renewal-watch": { title: "Платежи и продления", description: "Напоминание о платеже, подписке или сроке." },
  "price-watch": { title: "Цена и наличие", description: "Наблюдение за ценой или доступностью предложения." },
  "competitor-watch": { title: "Новости конкурентов", description: "Значимые события выбранных компаний со ссылками." },
  "habit-checkin": { title: "Проверка привычки", description: "Регулярное мягкое напоминание о привычке." },
  "hydration-move": { title: "Вода и движение", description: "Напоминание размяться и выпить воды в течение дня." },
  "meal-plan": { title: "Меню на неделю", description: "План питания и общий список покупок." },
  "learn-daily": { title: "Ежедневное обучение", description: "Небольшой последовательный урок каждый день." },
  "gratitude-journal": { title: "Благодарность и рефлексия", description: "Вечерний вопрос для спокойного подведения итогов." },
  "on-this-day": { title: "Открытие дня", description: "Короткий исторический факт, слово или цитата дня." },
};

const FIELD_LABELS: Record<string, string> = {
  time: "Во сколько?",
  deliver: "Куда отправлять?",
  interval_min: "Как часто проверять?",
  criteria: "О каких письмах сообщать?",
  day: "В какой день?",
  what: "О чём напомнить?",
  recurrence: "В какие дни повторять?",
  topic: "Какая тема?",
  count: "Сколько пунктов?",
  item: "За чем наблюдать?",
  condition: "Когда сообщить?",
  interval_h: "Как часто проверять?",
  companies: "Какие компании?",
  categories: "Какие события важны?",
  habit: "Какая привычка?",
  interval_hours: "Как часто напоминать?",
  start_hour: "Час начала",
  end_hour: "Час завершения",
  diet: "Предпочтения в питании",
  meals: "Сколько приёмов пищи?",
  effort: "Сложность приготовления",
  flavor: "Какой формат?",
};

const OPTION_LABELS: Record<string, string> = {
  origin: "Исходный чат",
  local: "Только сохранить",
  email: "Почта",
  sunday: "Воскресенье",
  monday: "Понедельник",
  friday: "Пятница",
  saturday: "Суббота",
  everyday: "Каждый день",
  weekdays: "По будням",
  "no restrictions": "Без ограничений",
  vegetarian: "Вегетарианское",
  vegan: "Веганское",
  "high-protein": "Высокобелковое",
  "low-carb": "Низкоуглеводное",
  "dinner only": "Только ужин",
  "lunch and dinner": "Обед и ужин",
  "all three": "Три приёма пищи",
  quick: "Быстро",
  medium: "Средне",
  ambitious: "Сложно",
  "on this day in history": "Событие этого дня",
  "word of the day": "Слово дня",
  "science fact": "Научный факт",
  "quote of the day": "Цитата дня",
};

function blueprintCopy(blueprint: AutomationBlueprint) {
  return BLUEPRINT_COPY[blueprint.key] ?? {
    title: "Сценарий автоматизации",
    description: "Готовый сценарий для задачи по расписанию.",
  };
}

function initialValues(blueprint: AutomationBlueprint): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of blueprint.fields) {
    out[f.name] = f.type === "text" ? "" : (f.default ?? "");
  }
  return out;
}

function FieldInput({
  field,
  value,
  onChange,
}: {
  field: AutomationBlueprintField;
  value: string;
  onChange: (v: string) => void;
}) {
  const label = FIELD_LABELS[field.name] ?? `Параметр ${field.name}`;
  if (field.type === "enum" || field.type === "weekdays") {
    return (
      <Select value={value} onValueChange={(v) => onChange(v)}>
        {field.options.map((opt) => (
          <SelectOption key={opt} value={opt}>
            {OPTION_LABELS[opt] ?? opt}
          </SelectOption>
        ))}
      </Select>
    );
  }
  if (field.type === "time") {
    return (
      <Input
        type="time"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }
  // text
  return (
    <Input
      type="text"
      value={value}
      placeholder={label}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

function BlueprintCard({
  blueprint,
  profile,
  showToast,
  onCreated,
}: {
  blueprint: AutomationBlueprint;
  profile: string;
  showToast: (message: string, type: "error" | "success") => void;
  onCreated?: () => void;
}) {
  const { t, tr } = useI18n();
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, string>>(() => initialValues(blueprint));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const copy = blueprintCopy(blueprint);

  const submit = useCallback(async () => {
    setSubmitting(true);
    setError(null);
    try {
      const job = await api.instantiateAutomationBlueprint({ blueprint: blueprint.key, values }, profile);
      const when = job.schedule_display ? ` — ${job.schedule_display}` : "";
      showToast(tr("{title} scheduled{when}", { title: copy.title, when }), "success");
      setOpen(false);
      setValues(initialValues(blueprint));
      onCreated?.();
    } catch (e) {
      // 422 from the API carries the slot-level validation message.
      setError(ownerFacingError(e, "Не удалось создать задачу по расписанию."));
    } finally {
      setSubmitting(false);
    }
  }, [blueprint, copy.title, values, profile, showToast, onCreated, tr]);

  return (
    <Card className={cn("overflow-hidden", themedBody)}>
      <CardContent className="space-y-3 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Wand2 className="h-4 w-4 shrink-0 opacity-70" />
              <span className="font-medium">{copy.title}</span>
            </div>
            <p className="mt-1 text-sm opacity-70">{copy.description}</p>
          </div>
          <Button
            ghost={open}
            size="sm"
            onClick={() => setOpen((o) => !o)}
          >
            {open ? t.common.cancel : tr("Set up")}
          </Button>
        </div>

        {open && (
          <div className="space-y-3 border-t pt-3">
            {blueprint.fields.map((f) => (
              <div key={f.name} className="space-y-1">
                <Label htmlFor={`${blueprint.key}-${f.name}`}>
                  {FIELD_LABELS[f.name] ?? `Параметр ${f.name}`}
                </Label>
                <FieldInput
                  field={f}
                  value={values[f.name] ?? ""}
                  onChange={(v) => setValues((prev) => ({ ...prev, [f.name]: v }))}
                />
              </div>
            ))}
            {error ? (
              <p className="text-sm text-red-500" role="alert">
                {error}
              </p>
            ) : null}
            <div className="flex items-center gap-2">
              <Button
                onClick={() => void submit()}
                disabled={submitting}
                prefix={submitting ? <Spinner /> : <Clock />}
              >
                {tr("Schedule it")}
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Automation Blueprints gallery — the form-where-there's-a-screen surface. Each blueprint
 * card expands into an inline form (one field per typed slot); submitting POSTs
 * to /api/cron/blueprints/instantiate which fills the blueprint and creates the job
 * via the same create_job path as everything else.
 */
export function AutomationBlueprints({ profile, onCreated }: AutomationBlueprintsProps) {
  const { toast, showToast } = useToast();
  const { tr } = useI18n();
  const [blueprints, setBlueprints] = useState<AutomationBlueprint[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getAutomationBlueprints()
      .then((r) => {
        if (!cancelled) setBlueprints(r.blueprints);
      })
      .catch((e) => {
        if (!cancelled) {
          setLoadError(ownerFacingError(e, "Не удалось загрузить шаблоны."));
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loadError) {
    return <p className="text-sm text-red-500">{tr("Couldn't load blueprints: {error}", { error: loadError })}</p>;
  }
  if (blueprints === null) {
    return (
      <div className="flex items-center gap-2 opacity-70">
        <Spinner className="h-4 w-4" /> {tr("Loading blueprints…")}
      </div>
    );
  }
  if (blueprints.length === 0) {
    return <p className="opacity-70">{tr("No automation blueprints available.")}</p>;
  }

  return (
    <>
      <Toast toast={toast} />
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {blueprints.map((r) => (
          <BlueprintCard
            key={r.key}
            blueprint={r}
            profile={profile}
            showToast={showToast}
            onCreated={onCreated}
          />
        ))}
      </div>
    </>
  );
}

export default AutomationBlueprints;
