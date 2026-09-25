import { Check, HeartHandshake, Languages, NotebookPen, Palette, PenLine, Sparkles, type LucideIcon } from "lucide-react";
import type { AgentTemplate } from "@/lib/api";
import { cn } from "@/lib/utils";

export type AgentCreationMode = "catalog" | "custom";

// Slots become installable only when the engine returns a real versioned
// package with this ID. Pending cards never create a substitute profile.
const PLANNED_AGENTS: { id: string; name: string; description: string; icon: LucideIcon; mark?: string }[] = [
  { id: "korra.secretary", name: "Секретарь", description: "Повседневные дела и организация работы", icon: NotebookPen },
  { id: "korra.psychologist", name: "Психолог", description: "Беседы о переживаниях и самопознании", icon: HeartHandshake },
  { id: "korra.chinese-teacher", name: "Учитель китайского", description: "Занятия и языковая практика", icon: Languages, mark: "中文" },
  { id: "korra.english-teacher", name: "Учитель английского", description: "Занятия и языковая практика", icon: Languages, mark: "EN" },
];

export function AgentTemplatePicker({ mode, onModeChange, templates, loading, error, selected, onSelect, onRetry, disabled }: {
  mode: AgentCreationMode;
  onModeChange: (mode: AgentCreationMode) => void;
  templates: AgentTemplate[];
  loading: boolean;
  error: string;
  selected: AgentTemplate | null;
  onSelect: (template: AgentTemplate) => void;
  onRetry: () => void;
  disabled: boolean;
}) {
  const cards = [
    ...templates.map(template => ({ ...template, icon: template.id === "korra.designer" ? Palette : PLANNED_AGENTS.find(item => item.id === template.id)?.icon ?? Sparkles, template, mark: PLANNED_AGENTS.find(item => item.id === template.id)?.mark })),
    ...PLANNED_AGENTS.filter(item => !templates.some(template => template.id === item.id)).map(item => ({ ...item, template: null })),
  ];
  return (
    <section id="pb-template-picker" className="grid gap-5" aria-label="Способ создания агента">
      <div className="grid grid-cols-2 gap-3" role="group" aria-label="Способ создания">
        {([
          { value: "catalog", title: "Готовый агент", hint: "С настроенной ролью и навыками", icon: Sparkles },
          { value: "custom", title: "Создать своего", hint: "Под вашу задачу", icon: PenLine },
        ] as const).map(item => (
          <button key={item.value} type="button" aria-pressed={mode === item.value} disabled={disabled} onClick={() => onModeChange(item.value)}
            className={cn("flex min-h-[72px] items-center gap-3 p-4 text-left transition-[box-shadow,color]", mode === item.value ? "bg-[var(--neo-surface)] shadow-[var(--neo-depth-2)]" : "text-[var(--neo-text-secondary)] hover:shadow-[var(--neo-inset-compact)]")}
            style={{ borderRadius: "var(--neo-radius-control)" }}>
            <item.icon aria-hidden className="hidden size-[20px] shrink-0 sm:block" />
            <span className="min-w-0 flex-1"><span className="block text-sm font-semibold">{item.title}</span><span className="mt-1 hidden text-xs text-[var(--neo-text-secondary)] sm:block">{item.hint}</span></span>
            {mode === item.value && <Check aria-hidden className="size-[17px] shrink-0 text-[var(--neo-text-primary)]" />}
          </button>
        ))}
      </div>
      {mode === "catalog" && (
        <div className="grid gap-3" aria-label="Готовые агенты">
          <div className="flex items-baseline justify-between gap-3"><h3 className="text-base font-semibold">Выберите помощника</h3><span className="hidden text-xs text-[var(--neo-text-secondary)] sm:inline">Можно переименовать</span></div>
          {loading && <p role="status" className="text-sm text-[var(--neo-text-secondary)]">Загружаю каталог…</p>}
          {error && <div role="alert" className="grid gap-2 text-sm"><p>{error}</p><button type="button" disabled={loading} onClick={onRetry} className="min-h-[44px] justify-self-start px-3 underline underline-offset-4">Повторить загрузку</button></div>}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
            {cards.map(item => {
              const active = selected?.id === item.id;
              return (
                <button key={item.id} type="button" disabled={disabled || !item.template} aria-pressed={active} data-agent-template={item.id}
                  onClick={() => { if (item.template) onSelect(item.template); }}
                  className={cn("grid min-h-[112px] content-start gap-2 bg-[var(--neo-surface)] p-3 text-left transition-[box-shadow,color] sm:p-4", item.template ? "hover:shadow-[var(--neo-inset-compact)]" : "text-[var(--neo-text-secondary)]", active ? "shadow-[var(--neo-depth-2)]" : "shadow-[var(--neo-depth-1)]")}
                  style={{ borderRadius: "var(--neo-radius-control)" }}>
                  <span className="flex items-center justify-between gap-2">
                    {item.mark ? <span aria-hidden className="text-sm font-semibold">{item.mark}</span> : <item.icon aria-hidden className="size-[20px]" strokeWidth={1.75} />}
                    {active ? <span className="inline-flex items-center gap-1 text-xs font-medium text-[var(--neo-text-primary)]"><Check className="size-[14px]" aria-hidden />Выбран</span> : !item.template ? <span className="text-xs">Скоро</span> : null}
                  </span>
                  <span className="text-sm font-semibold">{item.name}</span>
                  <span className="hidden text-xs leading-relaxed text-[var(--neo-text-secondary)] sm:block">{item.id === "korra.designer" ? "Презентации, изображения и визуальные материалы" : item.description}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}
