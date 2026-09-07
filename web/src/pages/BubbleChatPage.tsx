import { chatViewKey, readChatView, writeChatView } from "@/lib/chat-view-state";
/**
 * BubbleChatPage — bubble-style chat UI (Phase 2.2 live SSE streaming).
 *
 * Three-column layout: thread list · transcript · composer.
 *
 * Phase 2.1 was layout-only with hard-coded DEMO_MESSAGES.
 * Phase 2.2 (this file) wires the composer to `useChatStream` which talks
 * to the Phase 1 backend proxy `/api/chat/completions` and streams the
 * response back via SSE. Tool progress events update `toolCalls[]` live.
 *
 * Phase 2.5 will introduce real session continuity (X-Korra-Session-Id),
 * ChatThreadList backed by /api/sessions, and the /chat takeover under the
 * KORRA_DASHBOARD_CHAT flag. For now the sidebar still shows hard-coded
 * demo sessions for visual orientation.
 */

import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { useSearchParams } from "react-router";
import {
  Plus,
  ArrowUp,
  MessageSquare,
  MessageCircle,
  Terminal,
  Globe,
  BotMessageSquare,
  Square,
  X,
  Paperclip,
  RotateCcw,
  Mic,
} from "lucide-react";
import type { ComponentType } from "react";
import { ThinkingOrb } from "thinking-orbs";

import { Markdown } from "@/components/Markdown";
import { WorkspaceFilePicker } from "@/components/chat/WorkspaceFilePicker";
import { TranscriptViewport } from "@/components/chat/TranscriptViewport";
import { AgentTrace } from "@/components/chat/AgentTrace";
import { CommandApprovalCard } from "@/components/chat/CommandApprovalCard";
import { ChatWorking, type BusyKind } from "@/components/ChatWorking";
import { loadChatOutbox, type ChatOutboxRecord } from "@/lib/chat-outbox";
import { useProfileScope } from "@/contexts/useProfileScope";
import {
  ChatArtifactList,
  type ArtifactDecisionHandler,
} from "@/components/ChatArtifact";
import { splitArtifacts } from "@/lib/chat-artifacts";
import {
  AttachmentCardList,
  AttachmentChip,
} from "@/components/ChatAttachments";
import {
  MAX_ATTACHMENTS,
  MAX_ATTACHMENT_BYTES,
  isImageKind,
  kindOf,
  splitAttachments,
  uploadAttachment,
  type PendingAttachment,
  type UploadedAttachment,
} from "@/lib/chat-attachments";
import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";
import { Button } from "@nous-research/ui/ui/components/button";
import { CopyTextButton } from "@/components/chat/CopyTextButton";
import "@/components/chat/chat-answer.css";
import { ownerFacingError } from "@/lib/owner-facing-error";
import { cn } from "@/lib/utils";
import type { ApprovalChoiceValue, ChatMessage } from "@/lib/chat-types";
import { api, type SessionInfo } from "@/lib/api";
import { useChatStream, type ChatApprovalEntry } from "@/hooks/useChatStream";
import { useDictation, type DictationState } from "@/hooks/useDictation";
import { useSessionList } from "@/hooks/useSessionList";
import { useConfirmDelete } from "@/hooks/useConfirmDelete";
import { useTheme } from "@/themes";
import "./bubble-chat-composer.css";

/* ------------------------------------------------------------------ */
/*  Re-exports                                                         */
/* ------------------------------------------------------------------ */

// Shared chat types live in @/lib/chat-types — re-exported here for any
// consumer that still imports them from this module.
export type { ChatRole, ChatMessage, ChatSession } from "@/lib/chat-types";

/* ------------------------------------------------------------------ */
/*  Source → icon mapping (Telegram, api_server, cli, …)               */
/* ------------------------------------------------------------------ */

const SOURCE_ICON: Record<string, ComponentType<{ size?: number; className?: string; "aria-hidden"?: boolean }>> = {
  telegram: MessageCircle,
  api_server: BotMessageSquare,
  cli: Terminal,
  discord: MessageSquare,
  slack: MessageSquare,
  whatsapp: Globe,
};

function iconForSource(source: string | null): ComponentType<{ size?: number; className?: string; "aria-hidden"?: boolean }> {
  if (!source) return MessageSquare;
  return SOURCE_ICON[source] ?? MessageSquare;
}

function titleFor(session: SessionInfo): string {
  if (session.title) return session.title;
  if (session.preview) {
    // A message that is only an attachment starts with the service block, so
    // without this the thread is titled "[вложения] 1. Презентация…".
    const { text, attachments } = splitAttachments(session.preview);
    if (text.trim()) return text.slice(0, 60);
    if (attachments.length > 0) return attachments[0].name;
  }
  return "Без названия";
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

// Korra session timestamps (SessionInfo.last_active) are Unix epoch SECONDS,
// not milliseconds. Counting via Date.now()-ts treats them as ms → off by
// 1000× → "20570 days ago" for fresh sessions. Дмитрий caught it after
// Phase 2.5.b takeover. Use same convention as @/lib/utils#timeAgo.
function formatRelative(tsSec: number): string {
  const deltaSec = Date.now() / 1000 - tsSec;
  if (deltaSec < 60) return "только что";
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)} мин назад`;
  if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)} ч назад`;
  if (deltaSec < 172800) return "вчера";
  return `${Math.floor(deltaSec / 86400)} д назад`;
}

/* ------------------------------------------------------------------ */
/*  UserBubble                                                         */
/* ------------------------------------------------------------------ */

function UserBubble({
  message,
  onRetry,
  onDiscard,
}: {
  message: ChatMessage;
  onRetry?: () => void;
  onDiscard?: () => void;
}) {
  // The server appends an `[вложения]` block carrying absolute paths so the
  // agent can open the files. That block is addressed to the model — the owner
  // gets cards instead. Showing the raw path is how a product chat starts
  // looking like a terminal.
  const parsed = splitAttachments(message.content);
  const text = parsed.text;
  const attachments = message.attachments ?? parsed.attachments;
  return (
    <div className="flex flex-col items-end gap-1">
      <div
        className={cn(
          "max-w-[75%] rounded-[18px] bg-[var(--neo-surface)] px-3 py-2",
          "text-[var(--neo-text-primary)] shadow-[var(--neo-depth-1)]",
          // Chat content must be readable — opt out of Korra's UPPERCASE body style.
          "font-sans normal-case tracking-normal",
        )}
      >
        <AttachmentCardList items={attachments} />
        {text && (
          <p className="text-sm leading-relaxed whitespace-pre-wrap break-words">
            {text}
          </p>
        )}
      </div>
      {message.delivery === "sending" ? (
        <Button
          type="button"
          size="sm"
          ghost
          disabled
          tabIndex={-1}
          className="min-h-0 normal-case tracking-normal"
          role="status"
        >
          Отправляется…
        </Button>
      ) : message.delivery === "failed" ? (
        <div
          className="flex flex-wrap justify-end gap-1"
          aria-label="Действия с сообщением, оставшимся без ответа"
        >
          <Button
            type="button"
            size="sm"
            outlined
            onClick={onRetry}
            prefix={<RotateCcw aria-hidden />}
            className="normal-case tracking-normal"
          >
            {/* «Не отправлено» годилось, пока сюда попадал только обрыв
                доставки. Ход, который сервер принял и завершил отказом
                (например, ключ провайдера ещё не введён), тоже приходит
                сюда — и такому сообщению надпись врала. */}
            Без ответа · Повторить
          </Button>
          <Button
            type="button"
            size="sm"
            ghost
            onClick={onDiscard}
            className="normal-case tracking-normal"
          >
            Историю проверил(а) · убрать
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  AssistantBubble                                                    */
/* ------------------------------------------------------------------ */

function AssistantBubble({
  message,
  streaming,
  busyState,
  onDecision,
  decisionsBusy,
  decided,
}: {
  message: ChatMessage;
  streaming?: boolean;
  /** Что агент делает прямо сейчас — ровно настолько, насколько мы это знаем. */
  busyState?: BusyKind;
  onDecision?: ArtifactDecisionHandler;
  decisionsBusy?: boolean;
  decided?: Map<string, "approve" | "defer">;
}) {
  const tools = message.toolCalls ?? [];
  const hasTrace = tools.length > 0 || Boolean(message.reasoning?.trim());
  const artifactSplit = splitArtifacts(message.content ?? "");

  // Пузырь ответа рождается пустым в момент отправки. Пока не пришло ни
  // одного события — показываем орбиту и честную подпись «работает»: сказать
  // больше нечего, сервер ещё ничего не сообщил. Как только приходит первый
  // `tool.progress`, орбита переезжает в заголовок AgentTrace.
  // (Хуки выше выполняются безусловно; ранний выход стоит после них.)
  if (!message.content && !hasTrace) {
    if (!streaming) return null;
    return (
      <div className="flex justify-start">
        <ChatWorking startedAt={message.timestamp} state={busyState} />
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div
        className={cn(
          // Владелец 03.09: текст агента не должен упираться в правый край.
          "max-w-[780px]",
          "w-full min-w-0",
          "text-[var(--neo-text-primary)]",
          // Chat content must be readable — opt out of Korra's UPPERCASE body style.
          "font-sans normal-case tracking-normal",
        )}
      >
        {hasTrace && (
          <AgentTrace
            tools={tools}
            reasoning={message.reasoning}
            active={Boolean(streaming)}
            answering={Boolean(message.content)}
            startedAt={message.timestamp}
          />
        )}
        {message.content && (
          <article className="korra-chat-answer" aria-label="Ответ агента">
            <Markdown content={artifactSplit.text} streaming={streaming} />
            {/* Готовое вложение идёт после пояснения. Недописанный маркер
                потока пока не превращаем в карточку файла. */}
            {!streaming && (
              <>
                <ChatArtifactList
                  items={artifactSplit.artifacts}
                  onDecision={onDecision}
                  busy={decisionsBusy}
                  decided={decided}
                />
                <div className="korra-chat-answer__footer">
                  <CopyTextButton text={message.content} label="Скопировать ответ" />
                </div>
              </>
            )}
          </article>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  BubbleChatSidebar                                                  */
/* ------------------------------------------------------------------ */

interface BubbleChatSidebarProps {
  sessions: SessionInfo[];
  activeId: string | null;
  loading: boolean;
  error: string | null;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  onRequestDelete: (id: string) => void;
}

function BubbleChatSidebar({
  sessions,
  activeId,
  loading,
  error,
  onSelect,
  onNewChat,
  onRequestDelete,
}: BubbleChatSidebarProps) {
  return (
    // No bg- override — let the parent dashboard background show through.
    <aside
      // Список чатов — вдавленная панель на холсте: отделяет его от переписки
      // без линии-разделителя (владелец 03.09: «слились»).
      className="mb-3 ml-3 mt-3 hidden w-60 shrink-0 flex-col rounded-[var(--neo-radius-card)] bg-[var(--neo-surface)] shadow-[var(--neo-inset-compact)] md:flex"
    >
      <div className="p-3 pb-0">
        <Button
          type="button"
          onClick={onNewChat}
          outlined
          prefix={<Plus size={16} aria-hidden />}
          className="w-full"
        >
          <span>Новый чат</span>
        </Button>
      </div>
      <nav
        aria-label="Список чатов"
        className="mt-4 flex flex-1 flex-col gap-1 overflow-y-auto px-1 pb-1"
      >
        {loading && sessions.length === 0 && (
          <p className="px-5 py-2 text-sm text-[var(--neo-text-secondary)]">
            Загрузка…
          </p>
        )}
        {error && (
          <p
            role="alert"
            className="px-5 py-2 text-sm text-destructive"
          >
            {error}
          </p>
        )}
        {sessions.map((s) => {
          const active = s.id === activeId;
          const Icon = iconForSource(s.source);
          return (
            <div key={s.id} className="group relative w-full">
              <button
                type="button"
                onClick={() => onSelect(s.id)}
                className={cn(
                  "relative w-full rounded-[var(--neo-radius-control)] border-0 bg-transparent text-left outline-0",
                  "px-5 py-2.5 pr-9 flex items-start gap-2.5",
                  "cursor-pointer",
                  "font-sans text-sm transition-[box-shadow,color]",
                  "focus:outline-0 focus-visible:outline-0",
                  active
                    ? "bg-[var(--neo-surface)] text-[var(--neo-text-primary)] shadow-[var(--neo-depth-1)]"
                    : "text-[var(--neo-text-secondary)] shadow-none hover:shadow-[var(--neo-inset-compact)]",
                )}
                aria-current={active ? "page" : undefined}
              >
                <Icon
                  size={14}
                  className="mt-1 shrink-0 relative"
                  aria-hidden
                />
                <span className="flex-1 min-w-0 relative">
                  <span className="block truncate">{titleFor(s)}</span>
                  <span className="mt-0.5 block text-xs text-[var(--neo-text-secondary)]">
                    {formatRelative(s.last_active)}
                  </span>
                </span>
              </button>
              {/* Delete button — shows on hover only. Stopping propagation so
                  clicking X doesn't also select the session. */}
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onRequestDelete(s.id);
                }}
                className={cn(
                  "absolute top-1/2 right-2 -translate-y-1/2",
                  "rounded-[var(--neo-radius-round)] border-0 bg-transparent p-1 outline-0",
                  "opacity-0 group-hover:opacity-60 hover:!opacity-100",
                  "text-[var(--neo-text-secondary)] hover:text-destructive hover:shadow-[var(--neo-inset-compact)]",
                  "focus-visible:opacity-100 focus-visible:outline-0",
                  "transition-opacity",
                )}
                aria-label={`Удалить чат «${titleFor(s)}»`}
                title="Удалить чат"
              >
                <X size={12} aria-hidden />
              </button>
            </div>
          );
        })}
      </nav>
    </aside>
  );
}

/* ------------------------------------------------------------------ */
/*  BubbleChatTranscript                                               */
/* ------------------------------------------------------------------ */

function BubbleChatTranscript({
  messages,
  streaming,
  error,
  onDecision,
  busy,
  decided,
  onRetry,
  onDiscard,
  pendingElsewhere,
  agentLabel,
  approvals,
  onApprovalDecision,
}: {
  /** Имя агента вкладки для пустого экрана; пусто — главная Корра. */
  agentLabel?: string;
  /** Черновик этого профиля из другого чата — напоминаем баннером. */
  pendingElsewhere?: ChatOutboxRecord | null;
  messages: ChatMessage[];
  streaming?: boolean;
  error?: string | null;
  onDecision?: ArtifactDecisionHandler;
  busy?: boolean;
  decided?: Map<string, "approve" | "defer">;
  onRetry?: () => void;
  onDiscard?: () => void;
  /** Вопросы агента по опасным командам этого чата — живые и отвеченные. */
  approvals?: ChatApprovalEntry[];
  onApprovalDecision?: (requestId: string, choice: ApprovalChoiceValue) => void;
}) {
  // Mark the last assistant message as streaming so Markdown shows a caret.
  const lastIdx = messages.length - 1;
  const lastIsAssistant =
    lastIdx >= 0 && messages[lastIdx]!.role === "assistant";

  const lastUser = [...messages].reverse().find(message => message.role === "user");

  return (
    <TranscriptViewport
      key={messages[0]?.id ?? "empty"}
      followKey={lastUser?.id}
      awaitingApproval={approvals?.some(entry => entry.status === "pending")}
    >
      <div className="px-4">
        <div className="korra-chat-transcript__content mx-auto w-full max-w-[880px] space-y-8 pt-6">
          {pendingElsewhere && (
            <div
              role="status"
              className="flex flex-wrap items-center gap-3 rounded-[var(--neo-radius-control)] bg-[var(--neo-surface)] px-4 py-3 text-sm shadow-[var(--neo-inset-compact)]"
            >
              <span className="min-w-0 flex-1 text-[var(--neo-text-secondary)]">
                Неотправленное сообщение в другом чате: «{pendingElsewhere.text.slice(0, 80)}
                {pendingElsewhere.text.length > 80 ? "…" : ""}»
              </span>
              <Button size="sm" onClick={onRetry}>
                Повторить
              </Button>
              <Button ghost size="sm" onClick={onDiscard}>
                Убрать
              </Button>
            </div>
          )}
          {messages.length === 0 ? (
            <div className="flex min-h-[40vh] items-center justify-center">
              <p className="text-base text-muted-foreground">
                {agentLabel ? `Это чат с агентом «${agentLabel}» — напишите ему.` : "Напишите Корре своими словами — она на связи."}
              </p>
            </div>
          ) : (
            messages.map((m, i) =>
              m.role === "user" ? (
                <UserBubble
                  key={m.id}
                  message={m}
                  onRetry={onRetry}
                  onDiscard={onDiscard}
                />
              ) : (
                <AssistantBubble
                  key={m.id}
                  message={m}
                  streaming={
                    streaming === true && lastIsAssistant && i === lastIdx
                  }
                  // Единственное, что мы про работу агента действительно знаем:
                  // были ли в предыдущем сообщении вложения. Значит, он сейчас
                  // их открывает. Остальное не выдумываем.
                  onDecision={onDecision}
                  decisionsBusy={busy}
                  decided={decided}
                  busyState={
                    messages[i - 1]?.role === "user" &&
                    ((messages[i - 1]?.attachments?.length ?? 0) > 0 ||
                      messages[i - 1]?.content.includes("[вложения]"))
                      ? "reading"
                      : "working"
                  }
                />
              ),
            )
          )}
          {/* Вопросы по опасным командам живут ниже переписки: ход агента
              стоит, пока на них не ответят, и ответ должен быть первым, что
              видно внизу ленты. Отвеченные остаются на месте — решение по
              команде такая же часть разговора, как и сообщение. */}
          {(approvals ?? []).map((entry) => (
            <div key={entry.request.request_id} className="flex justify-start pl-2">
              <CommandApprovalCard
                command={entry.request.command}
                description={entry.request.description}
                choices={entry.request.choices ?? ["once", "deny"]}
                sending={entry.status === "sending"}
                expired={entry.status === "expired"}
                {...(entry.decision ? { decision: entry.decision } : {})}
                {...(entry.error ? { error: entry.error } : {})}
                {...(entry.note ? { note: entry.note } : {})}
                onDecide={(choice) =>
                  onApprovalDecision?.(entry.request.request_id, choice)
                }
              />
            </div>
          ))}
          {error && (
            <div
              role="alert"
              className="mx-auto max-w-[85%] rounded-[var(--neo-radius-control)] bg-[var(--neo-surface)] px-4 py-3 text-sm text-[var(--destructive)] shadow-[var(--neo-inset-compact)]"
            >
              Корра не смогла ответить: {error}
            </div>
          )}
        </div>
      </div>
    </TranscriptViewport>
  );
}

/* ------------------------------------------------------------------ */
/*  BubbleChatComposer                                                 */
/* ------------------------------------------------------------------ */

interface BubbleChatComposerProps {
  /** Имя агента вкладки для подсказок; пусто — главная Корра. */
  agentLabel?: string;
  disabled?: boolean;
  streaming?: boolean;
  /** Поток уже прислал текст или событие инструмента. До этого честнее
   *  показывать «Отправляется», хотя остановка уже доступна. */
  responding?: boolean;
  onSend: (
    text: string,
    attachments: UploadedAttachment[],
  ) => boolean | void | Promise<boolean | void>;
  onAbort?: () => void;
  /** Текст, подставляемый в поле извне — кнопкой «Изменить» на артефакте. */
  prefill?: string | null;
  onPrefillConsumed?: () => void;
  /** Профиль передаётся и в upload, и в completion: путь файла проверяется
   *  относительно дома того же агента. */
  profile?: string;
  allowAttachments?: boolean;
  draftKey?: string;
}

const TEXTAREA_MIN_HEIGHT = 56;
const TEXTAREA_MAX_HEIGHT = 200;

/** Подписи кнопки-микрофона по состоянию диктовки. */
const DICTATION_LABEL: Record<DictationState, string> = {
  idle: "Надиктовать сообщение",
  starting: "Включаю микрофон",
  recording: "Остановить запись",
  transcribing: "Распознаю речь",
};

export function BubbleChatComposer({
  disabled,
  streaming,
  responding,
  onSend,
  onAbort,
  prefill,
  onPrefillConsumed,
  profile,
  agentLabel,
  allowAttachments = true,
  draftKey,
}: BubbleChatComposerProps) {
  const [value, setValue] = useState(() => draftKey ? readChatView(draftKey) : "");
  useEffect(() => { if (draftKey) writeChatView(draftKey, value); }, [draftKey, value]);
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [dragging, setDragging] = useState(false);
  // Одна строка отказа на весь композер: вложения и диктовка спорить за неё
  // не могут — обе операции запускает владелец, по одной за раз.
  const [composerError, setComposerError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const abortsRef = useRef<Record<string, () => void>>({});
  const textareaId = useId();
  const shortcutId = useId();
  const { themeName } = useTheme();

  const patch = useCallback((id: string, next: Partial<PendingAttachment>) => {
    setAttachments((list) =>
      list.map((item) => (item.id === id ? { ...item, ...next } : item)),
    );
  }, []);

  const pickWorkspaceFile = useCallback((uploaded: UploadedAttachment) => {
    setAttachments(list => {
      if (list.some(item => item.uploaded?.path === uploaded.path)) return list;
      if (list.length >= MAX_ATTACHMENTS) return list;
      return [...list, { ...uploaded, id: crypto.randomUUID(), status: "ready",
        progress: 100, uploaded, file: new File([], uploaded.name) }];
    });
  }, []);

  const startUpload = useCallback(
    (item: PendingAttachment) => {
      patch(item.id, { status: "uploading", progress: 0, error: undefined });
      const { promise, abort } = uploadAttachment(
        item.file,
        (percent) => patch(item.id, { progress: percent }),
        profile,
      );
      abortsRef.current[item.id] = abort;
      promise
        .then((uploaded) =>
          patch(item.id, { status: "ready", progress: 100, uploaded }),
        )
        .catch((err: Error) =>
          patch(item.id, {
            status: "error",
            error: ownerFacingError(err, "Не удалось загрузить вложение."),
          }),
        )
        .finally(() => {
          delete abortsRef.current[item.id];
        });
    },
    [patch, profile],
  );

  const addFiles = useCallback(
    (files: FileList | File[]) => {
      const incoming = Array.from(files);
      if (incoming.length === 0) return;
      setComposerError(null);

      const list = attachmentsRef.current;
      const seen = new Set(list.map(item => `${item.name}:${item.size}:${item.file.lastModified}`));
      const unique = incoming.filter(file => {
        const key = `${file.name}:${file.size}:${file.lastModified}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
      const room = MAX_ATTACHMENTS - list.length;
      if (unique.length > room) {
        setComposerError(`Не больше ${MAX_ATTACHMENTS} файлов в сообщении`);
      }
      const accepted: PendingAttachment[] = [];
      for (const file of unique.slice(0, Math.max(0, room))) {
        if (file.size === 0 || file.size > MAX_ATTACHMENT_BYTES) {
          setComposerError(file.size === 0 ? `«${file.name}» — пустой файл` : `«${file.name}» больше 50 МБ`);
          continue;
        }
        const kind = kindOf(file.name);
        accepted.push({
          id: crypto.randomUUID(), name: file.name, size: file.size, kind,
          status: "uploading", progress: 0, file,
          previewUrl: isImageKind(kind) ? URL.createObjectURL(file) : undefined,
        });
      }
      // Reserve immediately, then upload outside React's replayable updater.
      // Repeated drops and StrictMode must never create duplicate disk files.
      attachmentsRef.current = [...list, ...accepted];
      setAttachments(attachmentsRef.current);
      accepted.forEach(startUpload);
    },
    [startUpload],
  );

  const removeAttachment = useCallback((id: string) => {
    abortsRef.current[id]?.();
    delete abortsRef.current[id];
    setAttachments((list) => {
      const gone = list.find((item) => item.id === id);
      if (gone?.previewUrl) URL.revokeObjectURL(gone.previewUrl);
      return list.filter((item) => item.id !== id);
    });
  }, []);

  // Освобождение object URL при размонтировании. Через пустой список
  // зависимостей это не работает: замыкание держит ПЕРВЫЙ (пустой) массив
  // вложений, и все добавленные позже превью остаются в памяти вкладки.
  // Держим актуальный список в ref и чистим по нему. Находка ревью 20.08.
  const attachmentsRef = useRef<PendingAttachment[]>([]);
  useEffect(() => {
    attachmentsRef.current = attachments;
  }, [attachments]);
  useEffect(
    () => () => {
      attachmentsRef.current.forEach((item) => {
        if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
      });
    },
    [],
  );

  const resizeTextarea = useCallback((el: HTMLTextAreaElement) => {
    const previousHeight = Math.min(
      Math.max(
        Number.parseFloat(el.style.height) ||
          el.getBoundingClientRect().height ||
          TEXTAREA_MIN_HEIGHT,
        TEXTAREA_MIN_HEIGHT,
      ),
      TEXTAREA_MAX_HEIGHT,
    );

    // A pixel start and end are required because CSS cannot interpolate from
    // `auto`. Collapse only for measurement, then restore the current height
    // before releasing the transition toward the new content height.
    el.style.height = "0px";
    const naturalHeight = el.scrollHeight;
    const nextHeight = Math.min(
      Math.max(naturalHeight, TEXTAREA_MIN_HEIGHT),
      TEXTAREA_MAX_HEIGHT,
    );
    el.style.height = `${previousHeight}px`;
    void el.offsetHeight;
    el.style.height = `${nextHeight}px`;
    el.style.overflowY = naturalHeight > TEXTAREA_MAX_HEIGHT ? "auto" : "hidden";
  }, []);

  useEffect(() => {
    const input = taRef.current;
    if (input) resizeTextarea(input);
  }, [resizeTextarea, value]);

  // «Изменить» на артефакте подставляет заготовку и отдаёт курсор владельцу:
  // отправлять за него нельзя — он ещё не сказал, что менять.
  useEffect(() => {
    if (!prefill) return;
    setValue(prefill);
    onPrefillConsumed?.();
    requestAnimationFrame(() => {
      const el = taRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
      resizeTextarea(el);
    });
  }, [prefill, onPrefillConsumed, resizeTextarea]);

  // Курсор в конец и фокус в поле — общий финал для подстановки текста
  // извне: и «Изменить» на артефакте, и диктовка возвращают владельцу
  // готовую к правке строку, а не готовую к отправке.
  const focusEnd = useCallback(() => {
    requestAnimationFrame(() => {
      const el = taRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
      resizeTextarea(el);
    });
  }, [resizeTextarea]);

  // Надиктованное дописывается к набранному, а не затирает его: владелец мог
  // начать печатать и договорить голосом. Отправку не запускаем — решение
  // «отправлять» остаётся нажатием.
  const appendDictated = useCallback(
    (text: string) => {
      setValue((prev) =>
        prev && !/\s$/.test(prev) ? `${prev} ${text}` : `${prev}${text}`,
      );
      setComposerError(null);
      focusEnd();
    },
    [focusEnd],
  );

  // Тишина — не отказ: сервер отвечает успехом и пустой строкой, и владельцу
  // честнее сказать «не расслышала», чем молча ничего не вставить.
  const reportSilence = useCallback(
    () => setComposerError("Ничего не расслышала. Попробуйте ещё раз."),
    [],
  );

  const dictation = useDictation({
    profile,
    onText: appendDictated,
    onError: setComposerError,
    onEmpty: reportSilence,
  });

  // Пока идёт ответ агента или уходит сообщение, поле принадлежит не
  // владельцу: недописанную запись бросаем, чтобы текст не прилетел в уже
  // очищенное поле.
  const cancelDictation = dictation.cancel;
  useEffect(() => {
    if (disabled || submitting) cancelDictation();
  }, [cancelDictation, disabled, submitting]);

  const uploading = attachments.some((item) => item.status === "uploading");
  const failed = attachments.some((item) => item.status === "error");
  const ready = attachments.filter((item) => item.status === "ready");

  const acceptedRef = useRef(false);
  const clearComposer = useCallback(() => {
    setValue("");
    setAttachments((current) => {
      current.forEach((item) => {
        if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
      });
      return [];
    });
    setComposerError(null);
    // Let React paint the empty value, then return to the resting height.
    requestAnimationFrame(() => {
      const el = taRef.current;
      if (el) resizeTextarea(el);
    });
  }, [resizeTextarea]);

  const submit = useCallback(() => {
    const text = value.trim();
    // Sending while a file is still uploading would hand the agent a message
    // whose attachments do not exist yet — the exact failure this feature is
    // meant to prevent. The button is disabled too; this is the second gate.
    if (disabled || submitting || uploading || failed) return;
    if (!text && ready.length === 0) return;
    setSubmitting(true);
    acceptedRef.current = false;
    let result: boolean | void | Promise<boolean | void>;
    try {
      result = onSend(
        text,
        ready.map((item) => item.uploaded!).filter(Boolean),
      );
    } catch (error) {
      setSubmitting(false);
      throw error;
    }
    // Поле очищается, как только отправка принята — сообщение легло в
    // переписку и пошёл стрим (см. эффект на `streaming` ниже), а не после
    // всего ответа агента (владелец 03.09). Отказ до отправки (черновик той
    // же сессии ждёт решения) текст не стирает.
    void Promise.resolve(result).then(
      (ok) => {
        setSubmitting(false);
        if (ok !== false && !acceptedRef.current) clearComposer();
      },
      () => setSubmitting(false),
    );
  }, [
    value,
    disabled,
    submitting,
    onSend,
    uploading,
    failed,
    ready,
    clearComposer,
  ]);

  useEffect(() => {
    if (submitting && streaming && !acceptedRef.current) {
      acceptedRef.current = true;
      clearComposer();
    }
  }, [submitting, streaming, clearComposer]);

  // Красная строка ошибки не должна висеть вечно: гаснет сама через 8 с
  // и при следующем наборе текста (владелец 03.09: «остаётся висеть»).
  useEffect(() => {
    if (!composerError) return;
    const timer = window.setTimeout(() => setComposerError(null), 8000);
    return () => window.clearTimeout(timer);
  }, [composerError]);

  const canSend =
    !disabled &&
    !submitting &&
    !uploading &&
    !failed &&
    Boolean(value.trim() || ready.length > 0);
  const recording = dictation.state === "recording";
  // «Включаю микрофон» и «Распознаю речь» — короткие ожидания, на них кнопка
  // занята: второе нажатие в этот момент означало бы отмену, а отменять
  // владелец собирался запись, которой уже нет.
  const dictationBusy =
    dictation.state === "starting" || dictation.state === "transcribing";
  const micDisabled =
    !dictation.supported || Boolean(disabled) || submitting || dictationBusy;
  const activity = streaming
    ? responding
      ? "Корра отвечает"
      : "Отправляется"
    : submitting
      ? "Отправляется"
      : uploading
        ? "Файлы загружаются"
        : recording
          ? "Идёт запись"
          : dictation.state === "transcribing"
            ? "Распознаю речь"
            : "";
  const composerState = streaming
    ? "streaming"
    : submitting
      ? "sending"
      : disabled
        ? "disabled"
        : "idle";

  const hasDraggedFiles = (types: readonly string[]) => types.includes("Files");

  return (
    <div
      className="korra-chat-composer"
      onDragOver={
        allowAttachments
          ? (e) => {
              if (!hasDraggedFiles(Array.from(e.dataTransfer.types))) return;
              e.preventDefault();
              setDragging(true);
            }
          : undefined
      }
      onDragLeave={
        allowAttachments
          ? (e) => {
              // Only clear when the pointer actually leaves the composer, not
              // when it crosses a child element.
              if (e.currentTarget.contains(e.relatedTarget as Node)) return;
              setDragging(false);
            }
          : undefined
      }
      onDrop={
        allowAttachments
          ? (e) => {
              if (!hasDraggedFiles(Array.from(e.dataTransfer.types))) return;
              e.preventDefault();
              setDragging(false);
              if (e.dataTransfer?.files?.length) addFiles(e.dataTransfer.files);
            }
          : undefined
      }
    >
      <div
        className="korra-chat-composer__dropzone"
        data-dragging={dragging ? "true" : "false"}
      >
        <div className="korra-chat-composer__drop-overlay" aria-hidden="true">
          <Paperclip size={18} />
          <span className="text-sm font-medium normal-case tracking-normal">
            Отпустите файлы, чтобы прикрепить
          </span>
        </div>

        {allowAttachments && (
          <input
            ref={fileRef}
            id={`${textareaId}-files`}
            type="file"
            multiple
            className="hidden"
            tabIndex={-1}
            onChange={(e) => {
              if (e.target.files?.length) addFiles(e.target.files);
              e.target.value = "";
            }}
          />
        )}

        <div
          role="group"
          aria-label="Сообщение и вложения"
          aria-busy={streaming || submitting || uploading}
          aria-describedby={shortcutId}
          data-state={composerState}
          className="korra-chat-composer__surface overflow-hidden"
        >
          <label htmlFor={textareaId} className="sr-only">
            Сообщение Корре
          </label>
          <textarea
            ref={taRef}
            id={textareaId}
            rows={2}
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
            }}
            onPaste={
              allowAttachments
                ? (e) => {
                    const files = Array.from(e.clipboardData?.files ?? []);
                    if (files.length) {
                      e.preventDefault();
                      addFiles(files);
                    }
                  }
                : undefined
            }
            onKeyDown={(e) => {
              if (
                e.key === "Enter" &&
                !e.shiftKey &&
                !e.nativeEvent.isComposing
              ) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder={
              recording ? "Слушаю…" : agentLabel ? `Напишите агенту «${agentLabel}»…` : "Напишите Корре…"
            }
            disabled={disabled || submitting}
            className="korra-chat-composer__textarea min-w-0 w-full resize-none bg-transparent text-sm leading-6 normal-case tracking-normal"
            aria-describedby={shortcutId}
          />

          {attachments.length > 0 && (
            <div
              className="korra-chat-composer__attachments flex flex-wrap gap-1.5"
              role="list"
              aria-label="Прикреплённые файлы"
            >
              {attachments.map((item) => (
                <AttachmentChip
                  key={item.id}
                  item={item}
                  onRemove={() => removeAttachment(item.id)}
                  onRetry={() => startUpload(item)}
                />
              ))}
            </div>
          )}

          {composerError && (
            <p
              role="alert"
              className="korra-chat-composer__error text-xs normal-case tracking-normal"
            >
              {composerError}
            </p>
          )}

          <div
            className="korra-chat-composer__controls"
            data-attachments={allowAttachments ? "true" : "false"}
          >
            <div className="korra-chat-composer__left-controls">
              {allowAttachments && <WorkspaceFilePicker
                disabled={disabled || submitting || attachments.length >= MAX_ATTACHMENTS}
                onPick={pickWorkspaceFile}
              />}
              {allowAttachments && (
                <button
                  type="button"
                  onClick={() => fileRef.current?.click()}
                  disabled={
                    disabled ||
                    submitting ||
                    attachments.length >= MAX_ATTACHMENTS
                  }
                  className="korra-chat-composer__control korra-chat-composer__attach"
                  aria-label="Прикрепить файл"
                  title="Прикрепить файл"
                >
                  <Plus size={20} strokeWidth={1.5} aria-hidden />
                </button>
              )}
              <button
                type="button"
                onClick={dictation.toggle}
                disabled={micDisabled}
                data-state={dictation.state}
                aria-pressed={recording}
                className="korra-chat-composer__control korra-chat-composer__microphone"
                aria-label={
                  dictation.supported
                    ? DICTATION_LABEL[dictation.state]
                    : "Диктовка недоступна"
                }
                title={
                  dictation.supported
                    ? DICTATION_LABEL[dictation.state]
                    : (dictation.unavailableReason ?? "Диктовка недоступна")
                }
              >
                {recording ? (
                  <Square size={15} fill="currentColor" aria-hidden />
                ) : dictationBusy ? (
                  <ThinkingOrb
                    state="working"
                    size={20}
                    speed={1.3}
                    theme={themeName === "dark" ? "dark" : "light"}
                    aria-label={DICTATION_LABEL[dictation.state]}
                  />
                ) : (
                  <Mic size={18} strokeWidth={1.5} aria-hidden />
                )}
              </button>
            </div>

            {streaming ? (
              <button
                type="button"
                onClick={onAbort}
                disabled={!onAbort}
                className="korra-chat-composer__control korra-chat-composer__submit"
                aria-label="Остановить генерацию"
                title="Остановить генерацию"
              >
                <Square size={15} fill="currentColor" aria-hidden />
              </button>
            ) : (
              <button
                type="button"
                onClick={submit}
                disabled={!canSend}
                title={
                  uploading
                    ? "Дождитесь загрузки файлов"
                    : failed
                      ? "Повторите загрузку или уберите файл"
                      : submitting
                        ? "Отправляется"
                        : "Отправить"
                }
                className="korra-chat-composer__control korra-chat-composer__submit"
                aria-label={submitting ? "Отправляется" : "Отправить"}
                aria-busy={submitting}
              >
                <ArrowUp size={20} strokeWidth={2} aria-hidden />
              </button>
            )}
          </div>

          <span className="sr-only" role="status" aria-live="polite">
            {activity}
          </span>
          <span id={shortcutId} className="sr-only">
            Enter — отправить · Shift+Enter — новая строка
          </span>
        </div>

        <span className="sr-only" role="status" aria-live="polite">
          {dragging ? "Отпустите файлы, чтобы прикрепить" : ""}
        </span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  BubbleChatPage (default export)                                    */
/* ------------------------------------------------------------------ */

export interface BubbleChatPageProps {
  /** Вкладка видна: скрытые вкладки не опрашивают список чатов. */
  active?: boolean;
  /** Профиль агента, которому адресован ЭТОТ экземпляр чата. Задан — все
   *  запросы (чат, список сессий, история, удаление) уезжают с
   *  `?profile=<agentProfile>`, и страницу можно смонтировать несколько раз
   *  рядом, по одной на агента. Не задан — прежнее поведение одиночного
   *  /chat: профиль берётся из глобального переключателя панели. */
  agentProfile?: string;
  /** Сообщает наружу, идёт ли сейчас поток в этом чате — рабочему месту
   *  расчётчиков это нужно, чтобы показать активность на неактивной вкладке.
   *  Первым аргументом отдаём профиль ("" — собственный профиль панели),
   *  чтобы получатель различал экземпляры. */
  onStreamingChange?: (profile: string, streaming: boolean) => void;
  /** Текст, который нужно подставить в поле ввода этого экземпляра.
   *
   *  Профильный экземпляр не читает `?draft=` из URL (диплинк адресует один
   *  чат, а экземпляров на странице несколько — см. эффект ниже). Черновик
   *  ему передаёт хозяин экрана: он уже решил, кому именно адресовано
   *  действие, и подставляет текст ровно в ту вкладку. Меняется значение —
   *  подставляем снова, поэтому повторный переход с тем же текстом хозяин
   *  гасит сам, обнулив проп. */
  draft?: string | null;
  /** Позвать, когда черновик доехал до поля: хозяин экрана гасит его у себя,
   *  иначе возврат на вкладку подставлял бы тот же текст поверх набранного. */
  onDraftConsumed?: () => void;
  /** Счётчик команд «Новый чат» из вкладки агента. Изменение значения
   *  сбрасывает только этот постоянно смонтированный экземпляр чата. */
  newChatRequest?: number;
  resumeSession?: string;
}

export default function BubbleChatPage({
  agentProfile,
  onStreamingChange,
  draft: draftFromOwner,
  onDraftConsumed,
  newChatRequest = 0,
  resumeSession,
  active,
}: BubbleChatPageProps = {}) {
  // Live SSE state from useChatStream. Sends POST to /api/chat/completions
  // and streams response chunks back into messages[]. Tool progress events
  // update the last assistant message's toolCalls[].
  const {
    messages,
    sessionId,
    isStreaming,
    error,
    approvals,
    send,
    resolveApproval,
    retryPending,
    discardPending,
    abort,
    loadSession,
    reset,
  } = useChatStream({ profile: agentProfile, active });
  // Черновик недоставленного сообщения этого профиля из ДРУГОГО чата: пузырь
  // с «Повторить» есть только в своём чате, здесь напоминает баннер.
  // Имя агента вкладки (display_name профиля) для подсказок композера и
  // пустого экрана: во вкладках Секретаря и Учителя «Напишите Корре…» было
  // ложью (QA 03.09).
  const { profiles: scopeProfiles } = useProfileScope();
  const agentLabel = agentProfile
    ? (scopeProfiles.find((item) => item.name === agentProfile)?.display_name?.trim() || agentProfile)
    : undefined;
  const pendingElsewhere = useMemo(() => {
    const pending = loadChatOutbox(agentProfile ?? "");
    return pending && pending.sessionId !== sessionId ? pending : null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentProfile, sessionId, messages, error, isStreaming]);

  // Решение владельца по артефакту уходит ОБЫЧНЫМ сообщением в чат, а не в
  // отдельный журнал. Так агент видит его штатно — у него в регламенте уже
  // есть правило «подтверждение → найти пункт, обновить статус, вернуть
  // сводку», — и решение остаётся в истории разговора наравне со всем
  // остальным, переживая перезагрузку.
  const [prefill, setPrefill] = useState<string | null>(null);
  const handledNewChatRequestRef = useRef(newChatRequest);

  useEffect(() => {
    if (handledNewChatRequestRef.current === newChatRequest) return;
    handledNewChatRequestRef.current = newChatRequest;
    reset();
  }, [newChatRequest, reset]);

  const handleDecision = useCallback<ArtifactDecisionHandler>(
    async (kind, item) => {
      if (kind === "change") {
        // «Изменить» — не решение, а начало разговора: пусть владелец скажет,
        // что именно поменять. Поэтому не отправляем, а подставляем в поле.
        setPrefill(`Изменить «${item.name}»: `);
        return;
      }
      // Во время другого ответа send() молча выходит: не сообщаем об успехе,
      // пока сообщение действительно не ушло.
      if (isStreaming) return false;
      const word = kind === "approve" ? "Согласовано" : "Отложено";
      // В сообщение кладём и путь: имена артефактов повторяются день ото дня
      // («Пакет недели.pptx»), и решение, опознанное по имени, применялось бы
      // к однофамильцу из другой папки. Агенту путь тоже нужен — иначе он
      // угадывает, что именно согласовали (находка ревью 20.08.2026).
      return await send(`${word}: «${item.name}»\nФайл: ${item.path}`);
    },
    [send, isStreaming],
  );

  // Решения, уже принятые в этом разговоре. Берём их из истории сообщений, а
  // не из состояния карточки: история переживает перезагрузку и возобновление
  // сессии, а состояние компонента — нет. Раньше после reload карточка снова
  // предлагала решить то, что владелец уже решил.
  const decidedArtifacts = useMemo(() => {
    const map = new Map<string, "approve" | "defer">();
    for (const message of messages) {
      if (message.role !== "user" || typeof message.content !== "string") continue;
      const match = /^(Согласовано|Отложено):\s*«(.+?)»(?:\s*\nФайл:\s*(\S.*?))?\s*$/.exec(
        message.content.trim(),
      );
      if (!match) continue;
      const decision = match[1] === "Согласовано" ? "approve" : "defer";
      // Ключ — путь: он уникален. Имя кладём тоже, но только для решений,
      // принятых до появления пути в сообщении, иначе старая история
      // перестала бы отображаться как решённая.
      if (match[3]) map.set(match[3], decision);
      else map.set(match[2], decision);
    }
    return map;
  }, [messages]);

  // Live sidebar — real /api/sessions list (TG + web combined). Poll every
  // 15s so sessions started elsewhere (Telegram bot, CLI) show up here too.
  const sessionList = useSessionList({
    pollIntervalMs: active === false ? 0 : 15_000,
    profile: agentProfile,
  });

  // Наружу отдаём только факт «идёт поток». Хозяин колбэка сам решает, что с
  // этим делать; здесь мы ничего не знаем про вкладки.
  useEffect(() => {
    onStreamingChange?.(agentProfile ?? "", isStreaming);
  }, [onStreamingChange, agentProfile, isStreaming]);

  // Deep-link support: /chat?resume=<sessionId> auto-loads that session on
  // mount. Preserves bookmark compatibility with the legacy xterm ChatPage
  // (which also accepted ?resume=) after Phase 2.5.b takeover.
  // Codex stop-gate review #12.
  const [searchParams, setSearchParams] = useSearchParams();
  const resumeHandledRef = useRef(false);
  // Диплинки ?draft= и ?resume= адресуют ОДИН чат в URL. Когда экземпляров на
  // странице несколько (рабочее место расчётчиков), каждый принял бы их на свой
  // счёт: один и тот же черновик подставился бы во все поля, а одна сессия
  // загрузилась бы во все три вкладки. Профильный экземпляр их не читает.
  useEffect(() => {
    if (agentProfile) return;
    const draft = searchParams.get("draft")?.trim();
    if (!draft) return;
    setPrefill(draft.slice(0, 1_500));
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      next.delete("draft");
      return next;
    }, { replace: true });
  }, [agentProfile, searchParams, setSearchParams]);

  // …а тому же профильному экземпляру черновик приносит хозяин экрана —
  // адресно, уже выбрав вкладку. Ограничение длины то же, что у диплинка.
  useEffect(() => {
    const draft = draftFromOwner?.trim();
    if (!draft) return;
    setPrefill(draft.slice(0, 1_500));
    onDraftConsumed?.();
  }, [draftFromOwner, onDraftConsumed]);

  useEffect(() => {
    if (agentProfile !== undefined) return;
    if (resumeHandledRef.current) return;
    const resume = searchParams.get("resume");
    if (!resume || sessionId === resume) return;
    resumeHandledRef.current = true;
    void loadSession(resume);
    // Clear the query param so a navigation in/out doesn't re-trigger
    // (which would also clobber any user-initiated session switch).
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      next.delete("resume");
      return next;
    }, { replace: true });
  }, [agentProfile, searchParams, setSearchParams, sessionId, loadSession]);

  // After the current stream finalizes, refresh the sidebar so the new
  // session (just created server-side via X-Korra-Session-Id) appears
  // without waiting for the next poll tick.
  const prevStreamingRef = useRef(false);
  useEffect(() => {
    if (prevStreamingRef.current && !isStreaming) {
      void sessionList.refresh();
    }
    prevStreamingRef.current = isStreaming;
  }, [isStreaming, sessionList]);

  useEffect(() => { if (resumeSession) void loadSession(resumeSession); }, [resumeSession, loadSession]);

  const handleSelect = useCallback(
    (id: string) => {
      void loadSession(id);
    },
    [loadSession],
  );

  const handleNewChat = useCallback(() => {
    reset();
  }, [reset]);

  // Решение по опасной команде уходит отдельным маршрутом, а не сообщением в
  // чат: ход агента заблокирован внутри вызова инструмента и новую реплику
  // он прочитает только следующим ходом — то есть никогда, пока стоит здесь.
  const handleApprovalDecision = useCallback(
    (requestId: string, choice: ApprovalChoiceValue) => {
      void resolveApproval(requestId, choice);
    },
    [resolveApproval],
  );

  // Delete chat: api.deleteSession then refresh the sidebar. If the deleted
  // thread is the one currently open, clear the bubble state too — otherwise
  // the user would be left with messages from a session that no longer
  // exists on the server.
  const sessionDelete = useConfirmDelete<string>({
    onDelete: async (id) => {
      await api.deleteSession(id, agentProfile || undefined);
      if (id === sessionId) reset();
      void sessionList.refresh();
    },
  });

  return (
    // Inherit parent layout background (App.tsx main wrapper). NO bg- override.
    //
    // Edge-to-edge layout:
    // - Horizontal: -mx-3 sm:-mx-6 cancels the App.tsx wrapper's px padding.
    //   Width stays stable (parent w-full doesn't depend on inner margins).
    // - Vertical: handled at the parent level — App.tsx applies `py-0` for
    //   isChatRoute so this h-full can reliably claim the full viewport
    //   height. Negative -mt/-mb here would race with h-full (Codex review #6).
    <div className="flex h-full min-h-0 -mx-3 sm:-mx-6">
      <BubbleChatSidebar
        sessions={sessionList.sessions}
        activeId={sessionId}
        loading={sessionList.loading}
        error={sessionList.error}
        onSelect={handleSelect}
        onNewChat={handleNewChat}
        onRequestDelete={sessionDelete.requestDelete}
      />
      <DeleteConfirmDialog
        open={sessionDelete.isOpen}
        onCancel={sessionDelete.cancel}
        onConfirm={sessionDelete.confirm}
        loading={sessionDelete.isDeleting}
        title="Удалить чат?"
        // Honest scope: api.deleteSession removes the session + messages
        // rows from state.db, but raw transcript files on disk
        // (.json/.jsonl/request_dump_*) are NOT touched by the server-side
        // endpoint (delete_session_endpoint в web_server.py не передаёт
        // sessions_dir в SessionDB.delete_session). Codex stop-gate #14
        // caught the previous "удалены безвозвратно" wording as misleading.
        description="Чат и сообщения удалятся из базы. Технические логи и архивы на диске сервера могут остаться."
      />
      <section className="flex-1 flex flex-col min-w-0 min-h-0" aria-label="Разговор с Коррой">
        <BubbleChatTranscript
          messages={messages}
          streaming={isStreaming}
          error={error}
          onDecision={handleDecision}
          busy={isStreaming}
          decided={decidedArtifacts}
          onRetry={() => void retryPending()}
          onDiscard={discardPending}
          pendingElsewhere={pendingElsewhere}
          agentLabel={agentLabel}
          approvals={approvals}
          onApprovalDecision={handleApprovalDecision}
        />
        <BubbleChatComposer
          key={sessionId ?? "new"}
          draftKey={chatViewKey(agentProfile, sessionId)}
          agentLabel={agentLabel}
          onSend={send}
          prefill={prefill}
          onPrefillConsumed={() => setPrefill(null)}
          streaming={isStreaming}
          responding={Boolean(
            isStreaming &&
              messages[messages.length - 1]?.role === "assistant" &&
              (messages[messages.length - 1]?.content.trim() ||
                messages[messages.length - 1]?.toolCalls?.length),
          )}
          onAbort={abort}
          profile={agentProfile}
          allowAttachments
          // UI guard: disable Enter-key submits during streaming. The
          // composer also swaps the Send button for Stop, but a stray
          // Enter would still call submit() and bypass the swap. Pair
          // with the ref-guard in useChatStream.send (Codex review #7).
          disabled={isStreaming}
        />
      </section>
    </div>
  );
}
