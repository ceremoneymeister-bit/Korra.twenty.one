/**
 * Ход работы агента — сворачиваемый след над текстом ответа.
 *
 * Показывает ровно то, что реально доезжает до браузера, и ничего сверх:
 *
 *   • Живой поток `POST /api/chat/completions` даёт только события
 *     `hermes.tool.progress` вида {tool, emoji, label, toolCallId, status}
 *     (gateway/platforms/api_server.py, `_on_tool_start`/`_on_tool_complete`).
 *     `label` — это `build_tool_preview(имя, аргументы)`, то есть главный
 *     аргумент вызова человеческим текстом; он и попадает в чип справа.
 *     Ни результата, ни ошибки, ни размышления в этом потоке нет:
 *     `_on_tool_start` отбрасывает всё, чьё имя начинается с `_`,
 *     а `tool_progress_callback` на этом маршруте сознательно не подключён.
 *
 *   • История сессии (`GET /api/sessions/{id}/messages`) богаче: там есть
 *     аргументы вызова, результат инструмента и `reasoning_content`. Оттуда
 *     и берётся строка «Размышление» — у моделей, которые reasoning отдают.
 *
 * Поэтому у живой строки раскрытие показывает полный аргумент (в чипе он
 * обрезан), а у исторической — ещё и результат. Пустых разделов не рисуем.
 */

import { useState } from "react";
import {
  AppWindow,
  BookOpen,
  Brain,
  CalendarClock,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  FileText,
  Globe,
  Image as ImageIcon,
  ListChecks,
  MessageSquare,
  PenLine,
  Search,
  Send,
  Sparkles,
  Terminal,
  Users,
  Zap,
} from "lucide-react";
import type { ComponentType } from "react";
import { ThinkingOrb } from "thinking-orbs";

import type { ToolEntry } from "@/components/ToolCall";
import { cn } from "@/lib/utils";
import { useTheme } from "@/themes";
import { pluralCalls, toolMeta, type ToolKind } from "./tool-labels";
import "./agent-trace.css";

type IconComponent = ComponentType<{ size?: number; className?: string; "aria-hidden"?: boolean }>;

const KIND_ICON: Record<ToolKind, IconComponent> = {
  read: FileText,
  write: PenLine,
  run: Terminal,
  search: Search,
  web: Globe,
  browser: AppWindow,
  think: Sparkles,
  delegate: Users,
  memory: Brain,
  message: Send,
  plan: ListChecks,
  skill: BookOpen,
  schedule: CalendarClock,
  image: ImageIcon,
  ask: MessageSquare,
  other: Zap,
};

/** Идентификатор строки размышления — своего `tool_call_id` у неё нет. */
const REASONING_ROW_ID = "__reasoning__";

/** Шаг появления строк, мс. Ограничен, иначе длинная история въезжает секундами. */
const STAGGER_MS = 80;
const MAX_STAGGER_STEPS = 8;

function formatSeconds(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  return `${seconds} с`;
}

/** Верхняя граница правдоподобного хода. Отсекает разъезд единиц времени:
 *  живой поток пишет метки в миллисекундах, а история сессии — в секундах,
 *  и вычитание одного из другого дало бы «думал 20 000 суток». */
const MAX_PLAUSIBLE_TURN_MS = 24 * 60 * 60 * 1000;

/**
 * Сколько агент работал — по фактическим меткам событий, без чтения часов
 * во время рендера. Начало — момент создания пузыря, конец — завершение
 * последнего вызова.
 *
 * `null` означает «не измеряли»: у истории сессии меток длительности нет
 * (`startedAt === 0`, `completedAt` отсутствует), и «Думал 0 с» там был бы
 * выдумкой, а не фактом.
 */
function measuredElapsed(tools: ToolEntry[], startedAt: number): number | null {
  if (startedAt <= 0) return null;
  let lastCompletedAt = 0;
  for (const tool of tools) {
    if (tool.completedAt && tool.completedAt > lastCompletedAt) {
      lastCompletedAt = tool.completedAt;
    }
  }
  if (lastCompletedAt <= 0) return null;
  const elapsed = lastCompletedAt - startedAt;
  if (elapsed < 0 || elapsed > MAX_PLAUSIBLE_TURN_MS) return null;
  return elapsed;
}

export interface AgentTraceProps {
  /** Вызовы инструментов — живые из потока или восстановленные из истории. */
  tools: ToolEntry[];
  /** Размышление модели. Только из истории: живой поток его не передаёт. */
  reasoning?: string;
  /** Агент ещё работает: краткий статус; детали раскрываются по запросу. */
  active?: boolean;
  /** Текст уже приходит; завершённые шаги уступают место чтению. */
  answering?: boolean;
  /** Момент начала хода — от него считается «Думал N с». */
  startedAt: number;
}

export function AgentTrace({
  tools,
  reasoning,
  active = false,
  answering = false,
  startedAt,
}: AgentTraceProps) {
  const { themeName } = useTheme();
  const writing = active && answering && !tools.some(tool => tool.status === "running");
  const working = active && !writing;
  // Ручной выбор действует до смены этапа: работа → текст → завершение.
  const [override, setOverride] = useState<boolean | null>(null);
  const [openRow, setOpenRow] = useState<string | null>(null);
  // Пересчёт производного состояния прямо в рендере, а не в эффекте: эффект
  // здесь дал бы лишний каскадный рендер на каждый ход агента.
  const phase = working ? "working" : writing ? "writing" : "done";
  const [previousPhase, setPreviousPhase] = useState(phase);
  if (previousPhase !== phase) {
    setPreviousPhase(phase);
    setOverride(null);
  }
  const open = override ?? false;
  const elapsedMs = measuredElapsed(tools, startedAt);

  const trimmedReasoning = reasoning?.trim() ?? "";
  const hasReasoning = trimmedReasoning.length > 0;
  if (tools.length === 0 && !hasReasoning) return null;

  const summary = headerSummary({
    active,
    writing,
    elapsedMs,
    calls: tools.length,
    errors: tools.filter((tool) => tool.status === "error").length,
    hasReasoning,
  });

  const currentTool = tools.findLast(tool => tool.status === "running");

  return (
    <div className="mb-4 font-sans normal-case tracking-normal">
      <button
        type="button"
        onClick={() => setOverride(!open)}
        aria-expanded={open}
        className={cn(
          "flex items-center gap-2 rounded-[var(--neo-radius-round)] px-1 py-0.5",
          "border-0 bg-transparent text-left outline-0",
          "text-xs text-[var(--neo-text-secondary)]",
          "cursor-pointer",
        )}
      >
        {active ? (
          <ThinkingOrb
            state="working"
            size={20}
            speed={1.3}
            theme={themeName === "dark" ? "dark" : "light"}
            aria-hidden
          />
        ) : (
          <Sparkles size={14} className="text-[var(--neo-text-secondary)]" aria-hidden />
        )}
        <span className={cn("font-medium", active && "korra-trace__shimmer")}>
          {summary}{!open && currentTool ? ` · ${toolMeta(currentTool.name).label}` : ""}
        </span>
        <ChevronDown
          size={13}
          aria-hidden
          className="korra-trace__chevron"
          style={{ transform: open ? "rotate(180deg)" : "rotate(0deg)" }}
        />
      </button>

      <div className="korra-trace__panel" data-open={open} inert={!open} aria-hidden={!open}>
        <div className="korra-trace__panel-inner">
          <div className="flex gap-2.5 pt-1.5 pl-2">
            <ul className="flex min-w-0 flex-1 list-none flex-col gap-0.5 p-0">
              {tools.map((tool, index) => {
                const meta = toolMeta(tool.name);
                return (
                  <TraceRow
                    key={tool.id}
                    index={index}
                    name={meta.label}
                    kind={meta.kind}
                    chip={tool.context}
                    status={tool.status}
                    detail={toolDetail(tool)}
                    expanded={openRow === tool.id}
                    onToggle={() =>
                      setOpenRow((current) => (current === tool.id ? null : tool.id))
                    }
                  />
                );
              })}
              {hasReasoning && (
                <TraceRow
                  index={tools.length}
                  name={toolMeta("_thinking").label}
                  kind="think"
                  status="done"
                  prose={trimmedReasoning}
                  expanded={openRow === REASONING_ROW_ID}
                  onToggle={() =>
                    setOpenRow((current) =>
                      current === REASONING_ROW_ID ? null : REASONING_ROW_ID,
                    )
                  }
                />
              )}
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}

function headerSummary({
  active,
  writing,
  elapsedMs,
  calls,
  errors,
  hasReasoning,
}: {
  active: boolean;
  writing: boolean;
  elapsedMs: number | null;
  calls: number;
  errors: number;
  hasReasoning: boolean;
}): string {
  const parts: string[] = [];
  if (active) parts.push(writing ? "Пишу ответ…" : "Думаю…");
  else if (elapsedMs !== null) parts.push(`Думал ${formatSeconds(elapsedMs)}`);
  if (!active && calls > 0) parts.push(pluralCalls(calls));
  if (errors > 0) parts.push(`${errors} с ошибкой`);
  if (parts.length > 0) return parts.join(" · ");
  return hasReasoning ? "Размышление" : "Ход работы";
}

/** Что показать под строкой инструмента. Живой поток даёт только полный
 *  аргумент (в чипе он обрезан), история — ещё результат и ошибку. */
function toolDetail(tool: ToolEntry): string {
  const blocks = [
    tool.status === "running" ? tool.preview : "",
    tool.summary,
    tool.error,
    tool.context,
  ].filter((block): block is string => Boolean(block && block.trim()));
  return blocks.length > 0 ? blocks[0].trim() : "";
}

function TraceRow({
  index,
  name,
  kind,
  chip,
  status,
  detail,
  prose,
  expanded,
  onToggle,
}: {
  index: number;
  name: string;
  kind: ToolKind;
  chip?: string;
  status: ToolEntry["status"];
  /** Технический текст: команда, путь, результат. Показывается моно. */
  detail?: string;
  /** Человеческий текст: размышление. Показывается обычным шрифтом. */
  prose?: string;
  expanded: boolean;
  onToggle: () => void;
}) {
  const Icon = KIND_ICON[kind];
  const hasBody = Boolean(detail?.trim() || prose?.trim());
  const delayMs = Math.min(index, MAX_STAGGER_STEPS) * STAGGER_MS;

  return (
    <li style={{ animationDelay: `${delayMs}ms` }} className="korra-trace__row">
      <button
        type="button"
        onClick={onToggle}
        disabled={!hasBody}
        aria-expanded={hasBody ? expanded : undefined}
        className={cn(
          "flex h-7 w-full min-w-0 items-center gap-2 rounded-[var(--neo-radius-control)] px-1.5",
          "border-0 bg-transparent text-left outline-0",
          "text-xs text-[var(--neo-text-secondary)]",
          hasBody ? "cursor-pointer" : "cursor-default",
        )}
      >
        <span className="relative grid size-3.5 flex-none place-items-center">
          <Icon
            size={13}
            aria-hidden
            className={cn(
              "korra-trace__row-icon absolute",
              status === "running" && "text-[var(--neo-text-primary)]",
            )}
          />
          {hasBody && (
            <ChevronRight
              size={13}
              aria-hidden
              className="korra-trace__row-chevron absolute"
              style={expanded ? { opacity: 1, transform: "rotate(90deg)" } : undefined}
            />
          )}
        </span>

        <span className="flex-none font-medium text-[var(--neo-text-primary)]">
          {name}
        </span>

        {chip && (
          <span
            className={cn(
              "min-w-0 flex-1 truncate rounded-[var(--neo-radius-round)] px-2 py-0.5",
              "bg-[var(--neo-surface)] font-mono text-[10.5px]",
              "text-[var(--neo-text-secondary)] shadow-[var(--neo-inset-compact)]",
            )}
            title={chip}
          >
            {chip}
          </span>
        )}
        {!chip && <span className="min-w-0 flex-1" />}

        {status === "running" && (
          <span
            role="img"
            aria-label="выполняется"
            className="korra-trace__pulse size-1.5 flex-none rounded-[var(--neo-radius-round)] bg-[var(--neo-accent)]"
          />
        )}
        {status === "error" && (
          <CircleAlert
            size={12}
            aria-label="ошибка"
            className="flex-none text-[var(--destructive)]"
          />
        )}
      </button>

      {expanded && hasBody && (
        <div className="korra-trace__detail pb-1 pl-[1.4rem]">
          {prose ? (
            <p className="max-h-64 overflow-y-auto text-xs leading-relaxed whitespace-pre-wrap text-[var(--neo-text-secondary)]">
              {prose}
            </p>
          ) : (
            <p className="max-h-64 overflow-y-auto text-xs leading-relaxed whitespace-pre-wrap break-words text-[var(--neo-text-secondary)]">
              {detail}
            </p>
          )}
        </div>
      )}
    </li>
  );
}
