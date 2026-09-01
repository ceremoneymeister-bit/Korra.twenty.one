import { useCallback, useEffect, useReducer, useRef } from "react";
import type { ToolEntry } from "@/components/ToolCall";
import type {
  ChatMessage,
  SSEToolProgressData,
} from "@/lib/chat-types";
import { api, withBasePath, type SessionMessage } from "@/lib/api";
import { splitSSEBuffer } from "@/lib/sse-parser";
import { toDisplay, type UploadedAttachment } from "@/lib/chat-attachments";
import {
  clearChatOutbox,
  loadChatOutbox,
  saveChatOutbox,
  type ChatOutboxRecord,
} from "@/lib/chat-outbox";

// ---------------------------------------------------------------------------
// State & Actions
// ---------------------------------------------------------------------------

interface StreamState {
  messages: ChatMessage[];
  sessionId: string | null;
  isStreaming: boolean;
  error: string | null;
}

type StreamAction =
  | { type: "SEND_USER"; userMsg: ChatMessage; assistantMsg: ChatMessage }
  | { type: "RETRY_USER"; userMsg: ChatMessage; assistantMsg: ChatMessage }
  | { type: "RESTORE_PENDING"; sessionId: string; userMsg: ChatMessage; error: string }
  | { type: "MARK_DELIVERY"; messageId: string; delivery: "sending" | "failed" | "delivered" }
  | { type: "DISCARD_PENDING"; messageId: string }
  | { type: "SET_SESSION_ID"; sessionId: string | null }
  | { type: "LOAD_SESSION"; sessionId: string; messages: ChatMessage[] }
  | { type: "APPEND_DELTA"; content: string }
  | { type: "UPSERT_TOOL"; toolData: SSEToolProgressData }
  | { type: "FINALIZE" }
  | { type: "SET_ERROR"; error: string }
  | { type: "RESET" }
  | { type: "RESET_STREAMING" };

const initialState: StreamState = {
  messages: [],
  sessionId: null,
  isStreaming: false,
  error: null,
};

function mapStatus(
  status: SSEToolProgressData["status"]
): ToolEntry["status"] {
  switch (status) {
    case "completed":
      return "done";
    case "error":
      return "error";
    default:
      return "running";
  }
}

function reducer(state: StreamState, action: StreamAction): StreamState {
  switch (action.type) {
    case "SEND_USER": {
      return {
        ...state,
        messages: [...state.messages, action.userMsg, action.assistantMsg],
        isStreaming: true,
        error: null,
      };
    }

    case "RETRY_USER": {
      let found = false;
      const messages = state.messages
        .filter((message) => message.role !== "assistant" || message.content || message.toolCalls?.length)
        .map((message) => {
          if (message.id !== action.userMsg.id) return message;
          found = true;
          return action.userMsg;
        });
      if (!found) messages.push(action.userMsg);
      messages.push(action.assistantMsg);
      return { ...state, messages, isStreaming: true, error: null };
    }

    case "RESTORE_PENDING": {
      return {
        messages: [action.userMsg],
        sessionId: action.sessionId,
        isStreaming: false,
        error: action.error,
      };
    }

    case "MARK_DELIVERY": {
      return {
        ...state,
        messages: state.messages.map((message) =>
          message.clientMessageId === action.messageId
            ? { ...message, delivery: action.delivery }
            : message,
        ),
      };
    }

    case "DISCARD_PENDING": {
      return {
        ...state,
        messages: state.messages.filter((message) =>
          message.clientMessageId !== action.messageId &&
          (message.role !== "assistant" || Boolean(message.content) || Boolean(message.toolCalls?.length)),
        ),
        error: null,
      };
    }

    case "SET_SESSION_ID": {
      return { ...state, sessionId: action.sessionId };
    }

    case "LOAD_SESSION": {
      return {
        ...state,
        sessionId: action.sessionId,
        messages: action.messages,
        isStreaming: false,
        error: null,
      };
    }

    case "APPEND_DELTA": {
      if (state.messages.length === 0) return state;
      const msgs = [...state.messages];
      const last = { ...msgs[msgs.length - 1] };
      last.content = last.content + action.content;
      msgs[msgs.length - 1] = last;
      return { ...state, messages: msgs };
    }

    case "UPSERT_TOOL": {
      if (state.messages.length === 0) return state;
      const msgs = [...state.messages];
      const last = { ...msgs[msgs.length - 1] };
      const existing = last.toolCalls ?? [];
      const idx = existing.findIndex(
        (t) => t.id === action.toolData.toolCallId
      );
      const now = Date.now();
      if (idx === -1) {
        const newEntry: ToolEntry = {
          kind: "tool",
          id: action.toolData.toolCallId,
          tool_id: action.toolData.tool,
          name: action.toolData.tool,
          status: mapStatus(action.toolData.status),
          startedAt: now,
        };
        last.toolCalls = [...existing, newEntry];
      } else {
        const updated: ToolEntry = {
          ...existing[idx],
          status: mapStatus(action.toolData.status),
        };
        const newCalls = [...existing];
        newCalls[idx] = updated;
        last.toolCalls = newCalls;
      }
      msgs[msgs.length - 1] = last;
      return { ...state, messages: msgs };
    }

    case "FINALIZE": {
      return { ...state, isStreaming: false };
    }

    case "SET_ERROR": {
      return { ...state, isStreaming: false, error: action.error };
    }

    case "RESET": {
      return {
        messages: [],
        sessionId: null,
        isStreaming: false,
        error: null,
      };
    }

    case "RESET_STREAMING": {
      return { ...state, isStreaming: false };
    }

    default: {
      // Exhaustive check — TypeScript will error if a case is missing
      const _exhaustive: never = action;
      return _exhaustive;
    }
  }
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export interface UseChatStreamReturn {
  messages: ChatMessage[];
  sessionId: string | null;
  isStreaming: boolean;
  error: string | null;
  /** Возвращает false, если сообщение доставить не удалось. */
  send: (text: string, attachments?: UploadedAttachment[]) => Promise<boolean>;
  retryPending: () => Promise<boolean>;
  discardPending: () => void;
  loadSession: (sessionId: string) => Promise<void>;
  reset: () => void;
  abort: () => void;
}

function sessionMessageToChatMessage(
  sessionId: string,
  message: SessionMessage,
  index: number
): ChatMessage | null {
  if (message.role !== "user" && message.role !== "assistant") {
    return null;
  }

  return {
    id: `${sessionId}-${index}`,
    role: message.role,
    content: message.content ?? "",
    timestamp: message.timestamp ?? Date.now(),
  };
}

export interface UseChatStreamOptions {
  /** Профиль агента, которому адресован чат. Пусто/не задан — профиль самого
   *  процесса панели (прежнее поведение). Уезжает в `?profile=` на
   *  /api/chat/completions и в загрузку истории сессии. */
  profile?: string;
}

export function useChatStream(
  options?: UseChatStreamOptions,
): UseChatStreamReturn {
  const profile = options?.profile;
  const [state, dispatch] = useReducer(reducer, initialState);
  const abortControllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  // Synchronous mirror of state.isStreaming so concurrent send() calls
  // can guard against re-entry without waiting for a re-render. React
  // state updates are async — without this ref, two send() calls fired
  // in the same event loop tick would both see isStreaming=false and
  // race: a second AbortController would overwrite the first one,
  // orphaning the first reader, which would keep dispatching
  // APPEND_DELTA into the wrong (newly-created) assistant message.
  // Codex stop-gate review #7 caught this.
  const streamingRef = useRef(false);
  // Identifier of the stream currently allowed to mutate state. send()
  // sets this to the session UUID it created/uses, and the read loop
  // checks it before every dispatch. If loadSession()/reset() flips it
  // (to null or another id), the in-flight stream sees a mismatch on its
  // next iteration and bails out cleanly — preventing APPEND_DELTA from
  // landing in a freshly-loaded thread or post-reset empty state.
  // (Codex stop-gate review #10.)
  const activeStreamIdRef = useRef<string | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortControllerRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    const pending = loadChatOutbox();
    if (!pending) return;
    const restored: ChatOutboxRecord = {
      ...pending,
      status: "failed",
      error: "Доставка не подтверждена после перезагрузки",
    };
    saveChatOutbox(restored);
    dispatch({
      type: "RESTORE_PENDING",
      sessionId: restored.sessionId,
      userMsg: {
        id: `user-${restored.messageId}`,
        role: "user",
        content: restored.text,
        timestamp: restored.createdAt,
        attachments: toDisplay(restored.attachments),
        delivery: "failed",
        clientMessageId: restored.messageId,
      },
      error: "Сообщение сохранилось в черновиках. Проверьте доставку кнопкой «Повторить».",
    });
  }, []);

  const abort = useCallback(() => {
    abortControllerRef.current?.abort();
  }, []);

  const loadSession = useCallback(async (sessionId: string): Promise<void> => {
    // Invalidate any in-flight stream before mutating state. Triple guard:
    // (1) activeStreamIdRef flip — read loop checks this synchronously on
    //     every iteration and bails out before dispatching APPEND_DELTA,
    // (2) abort() — propagates AbortError so the catch block runs cleanup,
    // (3) streamingRef=false — releases the concurrent-send lock so the
    //     user can immediately send into the loaded thread.
    // (Codex stop-gate review #10.)
    activeStreamIdRef.current = null;
    abortControllerRef.current?.abort();
    streamingRef.current = false;

    try {
      const resp = await api.getSessionMessages(sessionId, profile || undefined);
      const chatMessages = resp.messages
        .map((message, index) =>
          sessionMessageToChatMessage(sessionId, message, index)
        )
        .filter((message): message is ChatMessage => message !== null);

      dispatch({ type: "LOAD_SESSION", sessionId, messages: chatMessages });

      // Legacy/compressed sessions return an empty (or system-only) message
      // list — without feedback the user sees a blank transcript and assumes
      // the resume= link is broken. Surface a soft warning that lets them
      // continue the thread from here. (Codex stop-gate review #13.)
      if (chatMessages.length === 0) {
        dispatch({
          type: "SET_ERROR",
          error:
            "История этой сессии недоступна (сжата или legacy). Можешь продолжить отсюда — следующее сообщение запишется в эту нить.",
        });
      }
    } catch (err) {
      const msg =
        err instanceof Error
          ? `Не удалось загрузить сессию: ${err.message}`
          : "Не удалось загрузить сессию";
      dispatch({ type: "SET_ERROR", error: msg });
    }
  }, [profile]);

  const reset = useCallback(() => {
    // Same triple-guard as loadSession — abort any active stream so its
    // residual APPEND_DELTAs don't leak into the new chat.
    activeStreamIdRef.current = null;
    abortControllerRef.current?.abort();
    streamingRef.current = false;

    dispatch({ type: "RESET" });
  }, []);

  const send = useCallback(
    async (
      text: string,
      attachments: UploadedAttachment[] = [],
      retryRecord?: ChatOutboxRecord,
    ): Promise<boolean> => {
      // Reject concurrent sends. The composer UI already swaps Send for
      // Stop while streaming, but Enter-key submits bypass the swap, and
      // programmatic callers (tests, plugins) can call send() directly.
      // This ref-based guard is the source of truth.
      if (streamingRef.current) return false;
      if (!retryRecord) {
        const pending = loadChatOutbox();
        if (pending) {
          dispatch({
            type: "RESTORE_PENDING",
            sessionId: pending.sessionId,
            userMsg: {
              id: `user-${pending.messageId}`,
              role: "user",
              content: pending.text,
              timestamp: pending.createdAt,
              attachments: toDisplay(pending.attachments),
              delivery: "failed",
              clientMessageId: pending.messageId,
            },
            error: "Сначала проверьте сохранённое сообщение: повторите его или уберите после проверки истории.",
          });
          return false;
        }
      }
      // Явный признак доставки: у send() много точек выхода, и без него
      // undefined читался бы вызывающим кодом как успех. Кнопка «Согласовать»
      // именно так и показывала «отправлено» при оборванной сети.
      let delivered = false;
      streamingRef.current = true;

      let sessionId = retryRecord?.sessionId ?? state.sessionId;
      if (sessionId === null) {
        sessionId = crypto.randomUUID();
        dispatch({ type: "SET_SESSION_ID", sessionId });
      } else if (retryRecord && state.sessionId !== sessionId) {
        dispatch({ type: "SET_SESSION_ID", sessionId });
      }
      // Tag this stream as the one currently allowed to mutate state.
      // The read loop below re-checks this ref on every iteration so a
      // mid-stream loadSession()/reset() can invalidate us synchronously.
      const localStreamId = sessionId;
      activeStreamIdRef.current = localStreamId;

      // Build user message
      const messageId = retryRecord?.messageId ?? crypto.randomUUID();
      const createdAt = retryRecord?.createdAt ?? Date.now();
      const outboxRecord: ChatOutboxRecord = {
        messageId,
        sessionId,
        text,
        attachments,
        createdAt,
        status: "sending",
      };
      if (!saveChatOutbox(outboxRecord)) {
        activeStreamIdRef.current = null;
        streamingRef.current = false;
        dispatch({
          type: "SET_ERROR",
          error: "Браузер не смог сохранить сообщение перед отправкой. Освободите место и повторите.",
        });
        return false;
      }

      const userMsg: ChatMessage = {
        id: `user-${messageId}`,
        role: "user",
        content: text,
        timestamp: createdAt,
        // Cards must show the moment the message is sent. History reloaded
        // from the server carries the same files inside the `[вложения]`
        // block, and the transcript renders both paths identically — so a
        // message looks the same before and after a page reload.
        ...(attachments.length > 0 ? { attachments: toDisplay(attachments) } : {}),
        delivery: "sending",
        clientMessageId: messageId,
      };

      const assistantMsg: ChatMessage = {
        id: "asst-" + crypto.randomUUID(),
        role: "assistant",
        content: "",
        timestamp: Date.now(),
      };

      dispatch({
        type: retryRecord ? "RETRY_USER" : "SEND_USER",
        userMsg,
        assistantMsg,
      });

      // Snapshot current history for the request body
      // We append the new user message so the server sees it
      const historyMessages = [
        ...state.messages
          .filter((message) => message.id !== userMsg.id && (message.content || message.role === "user"))
          .map((m) => ({ role: m.role, content: m.content })),
        { role: userMsg.role, content: userMsg.content },
      ];

      const controller = new AbortController();
      abortControllerRef.current = controller;

      const token =
        typeof window !== "undefined"
          ? (window.__HERMES_SESSION_TOKEN__ ?? "")
          : "";

      // Этот запрос идёт мимо fetchJSON (нужен сырой ReadableStream), поэтому
      // глобальная подстановка ?profile= до него не доходит — адресуем сами.
      const chatUrl = profile
        ? `/api/chat/completions?profile=${encodeURIComponent(profile)}`
        : "/api/chat/completions";

      try {
        const response = await fetch(withBasePath(chatUrl), {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            // canary 0.15 loopback auth expects Bearer <ephemeral session token>
            Authorization: `Bearer ${token}`,
            "X-Hermes-Session-Id": sessionId,
            "X-Korra-Client-Message-Id": messageId,
          },
          body: JSON.stringify({
            model: "korra-agent",
            messages: historyMessages,
            stream: true,
            // Server-side the paths become an `[вложения]` block on the last
            // user message, so the agent gets a path plus the tool that opens
            // it. Sending them as a separate field keeps the transcript we
            // render locally free of filesystem noise.
            ...(attachments.length > 0 ? { attachments } : {}),
          }),
          signal: controller.signal,
        });

        if (!response.ok) {
          const errText = await response.text().catch(() => response.statusText);
          // Don't touch streamingRef/isStreaming if a newer send() (or
          // loadSession/reset) already invalidated us — those paths
          // already manage the lock and dispatching here would unlock
          // the *next* stream mid-flight. (Codex review #11.)
          if (
            mountedRef.current &&
            activeStreamIdRef.current === localStreamId
          ) {
            activeStreamIdRef.current = null;
            streamingRef.current = false;
            const failedRecord: ChatOutboxRecord = {
              ...outboxRecord,
              status: "failed",
              error: errText.slice(0, 500),
            };
            saveChatOutbox(failedRecord);
            dispatch({ type: "MARK_DELIVERY", messageId, delivery: "failed" });
            dispatch({
              type: "SET_ERROR",
              error: response.status === 409
                ? "Доставка требует проверки. Откройте историю или нажмите «Повторить» с тем же сообщением."
                : "Сообщение не отправлено. Оно сохранено — можно повторить без дубликата.",
            });
          }
          return delivered;
        }

        const reader = response.body!.getReader();
        const decoder = new TextDecoder("utf-8", { fatal: false });
        let buffer = "";
        // The `done` SSE event tells us the upstream considers itself
        // finished. Don't dispatch FINALIZE inline — wait until the reader
        // loop has actually exited so we can release the ref-lock and
        // signal isStreaming=false atomically (Codex review #9). Setting
        // FINALIZE while still inside `await reader.read()` opens a
        // window where (a) UI re-renders with isStreaming=false, (b) ref
        // is false too, (c) user starts a new send, (d) the old reader
        // is still alive and would write APPEND_DELTA into the new
        // assistant message — corrupting state (review #7 in disguise).
        let sawDone = false;
        let sawTerminalError = false;
        // Хоть одно событие от агента = сообщение до него доехало. Признак
        // нужен отдельно от `sawDone`: поток может оборваться после начала
        // ответа, и это уже не «не отправлено».
        let sawAgentOutput = false;

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const { events, remainder } = splitSSEBuffer(buffer);
          buffer = remainder;

          if (!mountedRef.current) {
            reader.cancel();
            return delivered;
          }
          // If loadSession/reset invalidated our stream, abandon quietly.
          // Don't dispatch any of the events we just decoded — they belong
          // to a thread the user has already navigated away from.
          if (activeStreamIdRef.current !== localStreamId) {
            void reader.cancel();
            return delivered;
          }

          for (const event of events) {
            if (!mountedRef.current) break;
            if (activeStreamIdRef.current !== localStreamId) break;
            if (event.type === "chunk") {
              const choice = event.data.choices[0];
              const content = choice?.delta?.content ?? "";
              if (content) {
                sawAgentOutput = true;
                dispatch({ type: "APPEND_DELTA", content });
              }
              if (choice?.finish_reason === "error") {
                activeStreamIdRef.current = null;
                streamingRef.current = false;
                dispatch({
                  type: "SET_ERROR",
                  error: "Agent stream ended with an error",
                });
                sawTerminalError = true;
                void reader.cancel();
                break;
              }
            } else if (event.type === "tool_progress") {
              sawAgentOutput = true;
              dispatch({ type: "UPSERT_TOOL", toolData: event.data });
            } else if (event.type === "done") {
              // Just record that we saw [DONE]. The atomic finalize
              // happens after the reader actually terminates below.
              sawDone = true;
            }
          }

          if (sawTerminalError) {
            break;
          }

          if (sawDone) {
            // Tear down the reader explicitly so the next read() call
            // returns {done: true} promptly and we exit the loop. If we
            // didn't cancel, an unfinished upstream might keep us here
            // arbitrarily.
            void reader.cancel();
            break;
          }
        }

        // Reader is now terminal (server closed, or we cancelled after
        // [DONE], or the network ended the stream). Release the ref-lock
        // and dispatch FINALIZE in the SAME synchronous span so React
        // sees both changes in one commit (review #8) and there is no
        // residual reader work that could write into a new message
        // (review #9). Skip if this stream was already invalidated by
        // loadSession/reset (review #10) — those paths already cleared
        // state and would re-flip isStreaming spuriously.
        if (mountedRef.current && activeStreamIdRef.current === localStreamId) {
          activeStreamIdRef.current = null;
          streamingRef.current = false;
          dispatch({ type: "FINALIZE" });
          // Доставкой считаем только явное `[DONE]` или начавшийся ответ
          // агента. Прежде терминальное состояние читателя само по себе
          // означало успех — то есть оборванный на полпути поток докладывал
          // карточке «отправлено агенту», и владелец считал решение
          // принятым (находка ревью 20.08.2026).
          delivered = sawDone || sawAgentOutput;
          if (delivered) {
            clearChatOutbox(messageId);
            dispatch({ type: "MARK_DELIVERY", messageId, delivery: "delivered" });
          } else {
            saveChatOutbox({
              ...outboxRecord,
              status: "failed",
              error: "Ответ завершился без подтверждения доставки",
            });
            dispatch({ type: "MARK_DELIVERY", messageId, delivery: "failed" });
          }
        }
      } catch (err) {
        if (!mountedRef.current) return delivered;
        // If our stream was invalidated (loadSession/reset, or a newer
        // send() somehow), the AbortError lands here — but the locks
        // and state already belong to the *new* stream. Silent return.
        // (Codex review #11.)
        if (activeStreamIdRef.current !== localStreamId) return delivered;

        // Drop ref-lock before any dispatch that flips state.isStreaming so
        // the UI re-render and the lock release happen in the same React
        // commit boundary. (Codex review #8.)
        activeStreamIdRef.current = null;
        streamingRef.current = false;
        if (err instanceof Error && err.name === "AbortError") {
          saveChatOutbox({
            ...outboxRecord,
            status: "failed",
            error: "Отправка остановлена до подтверждения доставки",
          });
          dispatch({ type: "MARK_DELIVERY", messageId, delivery: "failed" });
          dispatch({ type: "RESET_STREAMING" });
        } else {
          saveChatOutbox({
            ...outboxRecord,
            status: "failed",
            error: "Соединение прервалось",
          });
          dispatch({ type: "MARK_DELIVERY", messageId, delivery: "failed" });
          dispatch({
            type: "SET_ERROR",
            error: "Связь прервалась. Сообщение сохранено; повтор будет проверен по тому же ID.",
          });
          dispatch({ type: "RESET_STREAMING" });
        }
      } finally {
        // Defensive cleanup — only unlock if this stream is still the
        // active one. If invalidated, the new stream owns the lock and
        // we must not touch it. (Codex review #11.)
        if (activeStreamIdRef.current === localStreamId) {
          activeStreamIdRef.current = null;
          streamingRef.current = false;
        }
      }
      return delivered;
    },
    [state.messages, state.sessionId, profile]
  );

  const retryPending = useCallback(async (): Promise<boolean> => {
    const pending = loadChatOutbox();
    if (!pending || streamingRef.current) return false;
    return await send(pending.text, pending.attachments, pending);
  }, [send]);

  const discardPending = useCallback(() => {
    if (streamingRef.current) return;
    const pending = loadChatOutbox();
    if (!pending) return;
    clearChatOutbox(pending.messageId);
    dispatch({ type: "DISCARD_PENDING", messageId: pending.messageId });
  }, []);

  return {
    messages: state.messages,
    sessionId: state.sessionId,
    isStreaming: state.isStreaming,
    error: state.error,
    send,
    retryPending,
    discardPending,
    loadSession,
    reset,
    abort,
  };
}
