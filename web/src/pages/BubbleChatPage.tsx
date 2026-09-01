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

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import {
  Plus,
  Send,
  MessageSquare,
  MessageCircle,
  Terminal,
  Globe,
  BotMessageSquare,
  Square,
  X,
  Copy,
  Check,
  Paperclip,
  RotateCcw,
} from "lucide-react";
import type { ComponentType } from "react";

import { Markdown } from "@/components/Markdown";
import { ToolCall } from "@/components/ToolCall";
import { ChatWorking, type BusyKind } from "@/components/ChatWorking";
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
import { copyTextToClipboard } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
import type { ChatMessage } from "@/lib/chat-types";
import { api, type SessionInfo } from "@/lib/api";
import { useChatStream } from "@/hooks/useChatStream";
import { useSessionList } from "@/hooks/useSessionList";
import { useConfirmDelete } from "@/hooks/useConfirmDelete";

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
          "max-w-[80%] rounded-md px-3 py-2",
          "bg-primary/15 text-foreground border border-primary/30",
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
        <span className="px-1 text-[0.7rem] text-muted-foreground" role="status">
          Отправляется…
        </span>
      ) : message.delivery === "failed" ? (
        <div className="flex flex-wrap justify-end gap-1" aria-label="Действия с недоставленным сообщением">
          <button
            type="button"
            onClick={onRetry}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-semibold text-warning hover:bg-warning/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-warning/40"
          >
            <RotateCcw className="size-3.5" aria-hidden />
            Не отправлено · Повторить
          </button>
          <button
            type="button"
            onClick={onDiscard}
            className="inline-flex min-h-11 items-center rounded-lg px-3 py-2 text-xs text-muted-foreground hover:bg-muted/40 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
          >
            Историю проверил(а) · убрать
          </button>
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
  const hasTools = message.toolCalls && message.toolCalls.length > 0;
  const [copied, setCopied] = useState(false);
  const artifactSplit = splitArtifacts(message.content ?? "");

  const onCopy = useCallback(async () => {
    if (!message.content) return;
    if (await copyTextToClipboard(message.content)) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }
  }, [message.content]);

  // An assistant message is created empty the moment you press send, so
  // without this the transcript shows a blank bordered square for the whole
  // turn. Show what is actually true — the agent is working — and nothing
  // more: the API server emits no tool-progress events to report on.
  // (Hooks above run unconditionally; this early return sits after them.)
  if (!message.content && !hasTools) {
    if (!streaming) return null;
    return (
      <div className="flex justify-start">
        <ChatWorking startedAt={message.timestamp} state={busyState} />
      </div>
    );
  }

  return (
    <div className="group flex justify-start">
      <div
        className={cn(
          "relative max-w-[85%] rounded-md px-3 py-2",
          "bg-card border border-border",
          // Chat content must be readable — opt out of Korra's UPPERCASE body style.
          "font-sans normal-case tracking-normal",
        )}
      >
        {hasTools && (
          <div className="mb-2 space-y-1">
            {message.toolCalls!.map((tool) => (
              <ToolCall key={tool.id} tool={tool} />
            ))}
          </div>
        )}
        {message.content && (
          <Markdown content={artifactSplit.text} streaming={streaming} />
        )}
        {/* Артефакты показываем после текста: сначала «что я сделал»,
            потом сам результат. Во время потока не рисуем — маркер может
            быть ещё недописан и путь получится обрезанным. */}
        {!streaming && (
          <ChatArtifactList
            items={artifactSplit.artifacts}
            onDecision={onDecision}
            busy={decisionsBusy}
            decided={decided}
          />
        )}
        {/* Copy button — visible on hover. Streaming bubbles still get one
            (you can grab whatever has already arrived). */}
        {message.content && !streaming && (
          <button
            type="button"
            onClick={onCopy}
            className={cn(
              "absolute top-1 right-1 rounded-md p-1",
              "opacity-0 group-hover:opacity-100 transition-opacity",
              "hover:bg-muted/40 text-muted-foreground",
              "focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground",
            )}
            aria-label={copied ? "Скопировано" : "Скопировать"}
            title={copied ? "Скопировано" : "Скопировать"}
          >
            {copied ? <Check size={12} aria-hidden /> : <Copy size={12} aria-hidden />}
          </button>
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
    // Only border-r separates the sidebar from the transcript area.
    <aside className="hidden md:flex flex-col w-60 shrink-0 border-r border-border">
      <div className="p-2 border-b border-border">
        <button
          type="button"
          onClick={onNewChat}
          className={cn(
            "w-full min-h-[44px] flex items-center justify-center gap-2 px-3 py-2",
            "rounded-lg border border-border bg-background",
            "hover:opacity-90 active:opacity-100",
            "font-sans text-sm",
            "transition-opacity",
          )}
        >
          <Plus size={16} aria-hidden />
          <span>Новый чат</span>
        </button>
      </div>
      <nav
        aria-label="Список чатов"
        className="flex-1 overflow-y-auto py-1"
      >
        {loading && sessions.length === 0 && (
          <p className="px-5 py-2 text-sm text-text-secondary">
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
            <div
              key={s.id}
              className={cn(
                "group relative w-full",
                "transition-colors",
                active
                  ? "text-midground"
                  : "text-text-secondary hover:text-text-primary",
              )}
            >
              <button
                type="button"
                onClick={() => onSelect(s.id)}
                className={cn(
                  "relative w-full text-left",
                  "px-5 py-2.5 pr-9 flex items-start gap-2.5",
                  "cursor-pointer",
                  "font-sans text-sm",
                )}
                aria-current={active ? "page" : undefined}
              >
                {active && (
                  <span
                    aria-hidden
                    className="absolute left-0 top-0 bottom-0 w-px bg-midground"
                    style={{ mixBlendMode: "plus-lighter" }}
                  />
                )}
                <span
                  aria-hidden
                  className="absolute inset-y-0.5 left-1.5 right-1.5 bg-midground opacity-0 pointer-events-none transition-opacity duration-200 group-hover:opacity-5"
                />
                <Icon
                  size={14}
                  className="mt-1 shrink-0 relative"
                  aria-hidden
                />
                <span className="flex-1 min-w-0 relative">
                  <span className="block truncate">{titleFor(s)}</span>
                  <span className="mt-0.5 block text-xs text-text-secondary">
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
                  "rounded-md p-1",
                  "opacity-0 group-hover:opacity-60 hover:!opacity-100",
                  "hover:bg-destructive/20 hover:text-destructive",
                  "focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground",
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
}: {
  messages: ChatMessage[];
  streaming?: boolean;
  error?: string | null;
  onDecision?: ArtifactDecisionHandler;
  busy?: boolean;
  decided?: Map<string, "approve" | "defer">;
  onRetry?: () => void;
  onDiscard?: () => void;
}) {
  // Mark the last assistant message as streaming so Markdown shows a caret.
  const lastIdx = messages.length - 1;
  const lastIsAssistant =
    lastIdx >= 0 && messages[lastIdx]!.role === "assistant";

  // Autoscroll to the bottom whenever a new message is added or the last
  // message's content grows during streaming. We scroll the container
  // directly — scrollIntoView's "closest scrollable ancestor" search was
  // unreliable in our flex layout. useLayoutEffect runs synchronously
  // AFTER the DOM is updated but BEFORE the browser paints, so we always
  // see the newest scrollHeight (no need for requestAnimationFrame, which
  // additionally was flaky in headless Chromium during tests).
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const lastContentLen = messages[lastIdx]?.content.length ?? 0;
  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages.length, lastContentLen, error]);

  return (
    <div ref={scrollContainerRef} className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-4 py-4 space-y-3">
        {messages.length === 0 ? (
          <div className="flex items-center justify-center min-h-[40vh]">
<p className="text-base text-muted-foreground">
              Напишите Корре своими словами — она на связи.
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
        {error && (
          <div className="flex justify-center">
            <p
              role="alert"
              className={cn(
                "text-xs rounded-md px-3 py-2 max-w-md text-center",
                "bg-destructive/10 text-destructive border border-destructive/30",
                "font-sans normal-case tracking-normal",
              )}
            >
              {error}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  BubbleChatComposer                                                 */
/* ------------------------------------------------------------------ */

interface BubbleChatComposerProps {
  disabled?: boolean;
  streaming?: boolean;
  onSend: (text: string, attachments: UploadedAttachment[]) => void;
  onAbort?: () => void;
  /** Текст, подставляемый в поле извне — кнопкой «Изменить» на артефакте. */
  prefill?: string | null;
  onPrefillConsumed?: () => void;
  /** Вложения. Выключены на чатах, адресованных отдельному профилю: загрузка
   *  (/api/chat/upload) кладёт файл в рабочую папку процесса панели, и агент
   *  другого профиля этот путь не увидит. Скрепка, drag-and-drop и вставка
   *  файлов из буфера гаснут вместе — обещать то, чего нет, хуже, чем не
   *  показывать кнопку. */
  allowAttachments?: boolean;
}

function BubbleChatComposer({
  disabled,
  streaming,
  onSend,
  onAbort,
  prefill,
  onPrefillConsumed,
  allowAttachments = true,
}: BubbleChatComposerProps) {
  const [value, setValue] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [dragging, setDragging] = useState(false);
  const [attachError, setAttachError] = useState<string | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const abortsRef = useRef<Record<string, () => void>>({});

  const patch = useCallback((id: string, next: Partial<PendingAttachment>) => {
    setAttachments((list) =>
      list.map((item) => (item.id === id ? { ...item, ...next } : item)),
    );
  }, []);

  const startUpload = useCallback(
    (item: PendingAttachment) => {
      patch(item.id, { status: "uploading", progress: 0, error: undefined });
      const { promise, abort } = uploadAttachment(item.file, (percent) =>
        patch(item.id, { progress: percent }),
      );
      abortsRef.current[item.id] = abort;
      promise
        .then((uploaded) =>
          patch(item.id, { status: "ready", progress: 100, uploaded }),
        )
        .catch((err: Error) =>
          patch(item.id, { status: "error", error: err.message }),
        )
        .finally(() => {
          delete abortsRef.current[item.id];
        });
    },
    [patch],
  );

  const addFiles = useCallback(
    (files: FileList | File[]) => {
      const incoming = Array.from(files);
      if (incoming.length === 0) return;
      setAttachError(null);

      setAttachments((list) => {
        const room = MAX_ATTACHMENTS - list.length;
        if (room <= 0) {
          setAttachError(`Не больше ${MAX_ATTACHMENTS} файлов в сообщении`);
          return list;
        }
        if (incoming.length > room) {
          setAttachError(`Не больше ${MAX_ATTACHMENTS} файлов в сообщении`);
        }
        const accepted: PendingAttachment[] = [];
        for (const file of incoming.slice(0, room)) {
          if (file.size > MAX_ATTACHMENT_BYTES) {
            setAttachError(`«${file.name}» больше 50 МБ`);
            continue;
          }
          const kind = kindOf(file.name);
          accepted.push({
            id: crypto.randomUUID(),
            name: file.name,
            size: file.size,
            kind,
            status: "uploading",
            progress: 0,
            file,
            previewUrl: isImageKind(kind) ? URL.createObjectURL(file) : undefined,
          });
        }
        // Upload outside the state updater so React stays pure.
        queueMicrotask(() => accepted.forEach(startUpload));
        return [...list, ...accepted];
      });
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
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 24 * 6 + 16) + "px";
    });
  }, [prefill, onPrefillConsumed]);

  const uploading = attachments.some((item) => item.status === "uploading");
  const failed = attachments.some((item) => item.status === "error");
  const ready = attachments.filter((item) => item.status === "ready");

  const autoresize = useCallback(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    // Cap at ~6 rows (text-sm line-height ~20px + py-2 → ~24px per row)
    const max = 24 * 6 + 16;
    el.style.height = Math.min(el.scrollHeight, max) + "px";
  }, []);

  const submit = useCallback(() => {
    const text = value.trim();
    // Sending while a file is still uploading would hand the agent a message
    // whose attachments do not exist yet — the exact failure this feature is
    // meant to prevent. The button is disabled too; this is the second gate.
    if (disabled || uploading || failed) return;
    if (!text && ready.length === 0) return;
    onSend(
      text,
      ready.map((item) => item.uploaded!).filter(Boolean),
    );
    setValue("");
    attachments.forEach((item) => {
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
    });
    setAttachments([]);
    setAttachError(null);
    // Reset textarea height after send
    requestAnimationFrame(() => {
      const el = taRef.current;
      if (el) el.style.height = "auto";
    });
  }, [value, disabled, onSend, uploading, failed, ready, attachments]);

  return (
    // No bg- override on the composer wrap either — only border-t separates
    // the input area from the transcript. The textarea + send button retain
    // their own bg-card (it's a real container, not a background overlay).
    <div
      className="border-t border-border"
      onDragOver={
        allowAttachments
          ? (e) => {
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
              e.preventDefault();
              setDragging(false);
              if (e.dataTransfer?.files?.length) addFiles(e.dataTransfer.files);
            }
          : undefined
      }
    >
      <div className="max-w-3xl mx-auto px-4 py-3">
        {allowAttachments && (
          <input
            ref={fileRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => {
              if (e.target.files?.length) addFiles(e.target.files);
              e.target.value = "";
            }}
          />
        )}

        {attachments.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1.5">
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

        {attachError && (
          <div className="mb-2 text-xs text-destructive font-sans normal-case tracking-normal">
            {attachError}
          </div>
        )}

        <div
          className={cn(
            "flex items-end gap-2 rounded-md border bg-card px-2 py-1.5",
            dragging ? "border-primary border-dashed" : "border-border",
            // a11y: visible focus indicator when textarea inside is focused.
            // Restored after Codex review caught its absence. Uses midground
            // (Korra's mid neutral) rather than primary so it doesn't scream;
            // ring-2 + offset matches the focus-visible pattern used in
            // SidebarNavLink / SidebarFooter elsewhere in the dashboard.
            "transition-[box-shadow,border-color]",
            "focus-within:ring-2 focus-within:ring-midground/50",
            "focus-within:ring-offset-0 focus-within:border-midground/60",
          )}
        >
          {allowAttachments && (
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              disabled={disabled || attachments.length >= MAX_ATTACHMENTS}
              className={cn(
                "shrink-0 size-[44px] rounded-lg border border-border",
                "flex items-center justify-center text-muted-foreground",
                "hover:bg-muted/40 disabled:opacity-40 disabled:cursor-not-allowed",
                "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground",
              )}
              aria-label="Прикрепить файл"
              title="Прикрепить файл"
            >
              <Paperclip size={14} aria-hidden />
            </button>
          )}
          <textarea
            ref={taRef}
            rows={1}
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              autoresize();
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
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder="Напишите сообщение… (Enter — отправить, Shift+Enter — перенос)"
            disabled={disabled}
            className={cn(
              "flex-1 resize-none bg-transparent outline-none",
              "text-sm leading-6 placeholder:text-muted-foreground/60",
              "min-h-[24px] max-h-[160px] px-2 py-1",
              "disabled:opacity-60",
              // Composer input is real prose, not UI label — opt out of UPPERCASE.
              "font-sans normal-case tracking-normal",
            )}
            aria-label="Сообщение"
          />
          {streaming ? (
            <button
              type="button"
              onClick={onAbort}
              className={cn(
                "shrink-0 size-8 rounded-md",
                "bg-destructive/10 text-destructive hover:bg-destructive/20",
                "flex items-center justify-center transition-opacity",
              )}
              aria-label="Остановить"
            >
              <Square size={14} aria-hidden />
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              disabled={
                disabled ||
                uploading ||
                failed ||
                (!value.trim() && ready.length === 0)
              }
              title={
                uploading
                  ? "Файлы ещё загружаются"
                  : failed
                    ? "Уберите или повторите неудачное вложение"
                    : "Отправить"
              }
              className={cn(
                "shrink-0 size-[44px] rounded-lg border border-border",
                "bg-primary/10 text-primary hover:bg-primary/20",
                "disabled:opacity-40 disabled:cursor-not-allowed",
                "flex items-center justify-center transition-opacity",
              )}
              aria-label="Отправить"
            >
              <Send size={18} aria-hidden />
            </button>
          )}
        </div>
        {/* Подпись фазы разработки владельцу продукта ни о чём не говорит;
            подсказка про Enter уже есть в placeholder поля ввода. */}
        {allowAttachments && attachments.length === 0 && (
          <p className="mt-2 text-center font-sans text-sm normal-case tracking-normal text-text-secondary">
            Можно прикрепить файл — перетащите его сюда или нажмите скрепку
          </p>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  BubbleChatPage (default export)                                    */
/* ------------------------------------------------------------------ */

export interface BubbleChatPageProps {
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
}

export default function BubbleChatPage({
  agentProfile,
  onStreamingChange,
  draft: draftFromOwner,
  onDraftConsumed,
}: BubbleChatPageProps = {}) {
  // Live SSE state from useChatStream. Sends POST to /api/chat/completions
  // and streams response chunks back into messages[]. Tool progress events
  // update the last assistant message's toolCalls[].
  const {
    messages,
    sessionId,
    isStreaming,
    error,
    send,
    retryPending,
    discardPending,
    abort,
    loadSession,
    reset,
  } = useChatStream({ profile: agentProfile });

  // Решение владельца по артефакту уходит ОБЫЧНЫМ сообщением в чат, а не в
  // отдельный журнал. Так агент видит его штатно — у него в регламенте уже
  // есть правило «подтверждение → найти пункт, обновить статус, вернуть
  // сводку», — и решение остаётся в истории разговора наравне со всем
  // остальным, переживая перезагрузку.
  const [prefill, setPrefill] = useState<string | null>(null);
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
    pollIntervalMs: 15_000,
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
    if (agentProfile) return;
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

  const handleSelect = useCallback(
    (id: string) => {
      void loadSession(id);
    },
    [loadSession],
  );

  const handleNewChat = useCallback(() => {
    reset();
  }, [reset]);

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
        />
        <BubbleChatComposer
          onSend={send}
          prefill={prefill}
          onPrefillConsumed={() => setPrefill(null)}
          streaming={isStreaming}
          onAbort={abort}
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
