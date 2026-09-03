import { useCallback, useEffect, useReducer, useRef } from "react";
import type { ToolEntry } from "@/components/ToolCall";
import type {
  ApprovalChoiceValue,
  ChatMessage,
  SessionMessageReasoning,
  SSEApprovalRequestData,
  SSEToolProgressData,
} from "@/lib/chat-types";
import {
  fetchPendingApprovals,
  normalizeApprovalRequest,
  sendApprovalDecision,
} from "@/lib/chat-approvals";
import { previewArguments, previewToolResult } from "@/components/chat/tool-labels";
import { api, withBasePath, type SessionMessage } from "@/lib/api";
import { splitSSEBuffer } from "@/lib/sse-parser";
import { toDisplay, type UploadedAttachment } from "@/lib/chat-attachments";
import { ownerFacingError } from "@/lib/owner-facing-error";
import {
  clearChatOutbox,
  loadChatOutbox,
  saveChatOutbox,
  type ChatOutboxRecord,
} from "@/lib/chat-outbox";

// ---------------------------------------------------------------------------
// State & Actions
// ---------------------------------------------------------------------------

/**
 * Запрос одобрения вместе с тем, что с ним уже сделали в этой вкладке.
 *
 * `pending`  — агент стоит и ждёт ответа;
 * `sending`  — ответ отправляется;
 * `settled`  — ответ принят движком, исход в `decision`;
 * `expired`  — отвечать некому: ход кончился или истёк таймаут ожидания.
 *
 * Отвеченные карточки не удаляются: решение по опасной команде остаётся
 * видимым в переписке, как и всё остальное в ней.
 */
export interface ChatApprovalEntry {
  request: SSEApprovalRequestData;
  status: "pending" | "sending" | "settled" | "expired";
  decision?: ApprovalChoiceValue;
  error?: string;
  /** Пояснение к исходу, когда ответ ушёл не в открытый поток. */
  note?: string;
}

/** Как часто перепроверять нерешённые вопросы, когда живого потока нет. */
const APPROVAL_POLL_MS = 5000;

interface StreamState {
  messages: ChatMessage[];
  sessionId: string | null;
  isStreaming: boolean;
  error: string | null;
  approvals: ChatApprovalEntry[];
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
  | { type: "APPROVAL_REQUESTED"; request: SSEApprovalRequestData }
  | { type: "APPROVAL_RESTORED"; requests: SSEApprovalRequestData[] }
  | { type: "APPROVAL_SENDING"; requestId: string }
  | { type: "APPROVAL_SETTLED"; requestId: string; decision: ApprovalChoiceValue }
  | { type: "APPROVAL_FAILED"; requestId: string; error: string; expired: boolean }
  | { type: "RESET" }
  | { type: "RESET_STREAMING" };

const initialState: StreamState = {
  messages: [],
  sessionId: null,
  isStreaming: false,
  error: null,
  approvals: [],
};

/** Ход кончился — незакрытые вопросы уже некому исполнять. Держать их
 *  «ждущими» значило бы предлагать кнопку, которая ничего не сделает. */
function expirePendingApprovals(
  approvals: ChatApprovalEntry[],
): ChatApprovalEntry[] {
  if (!approvals.some((entry) => entry.status === "pending" || entry.status === "sending")) {
    return approvals;
  }
  return approvals.map((entry) =>
    entry.status === "pending" || entry.status === "sending"
      ? { ...entry, status: "expired" as const }
      : entry,
  );
}

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
      // Та же сессия — сообщение добавляется к переписке, а не заменяет её
      // (владелец 03.09: «ошибка возникла и исчезла переписка»). Другая
      // открытая сессия — переписку не трогаем, о черновике скажет баннер.
      const alreadyShown = state.messages.some(
        (message) => message.clientMessageId === action.userMsg.clientMessageId,
      );
      if (state.messages.length > 0 && state.sessionId !== action.sessionId) {
        return { ...state, isStreaming: false, error: action.error };
      }
      return {
        ...state,
        messages: alreadyShown ? state.messages : [...state.messages, action.userMsg],
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
        // Карточки принадлежат конкретному ходу конкретного чата; в другой
        // переписке они относились бы к чужой команде.
        approvals: [],
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
      const status = mapStatus(action.toolData.status);
      if (idx === -1) {
        const newEntry: ToolEntry = {
          kind: "tool",
          id: action.toolData.toolCallId,
          tool_id: action.toolData.tool,
          name: action.toolData.tool,
          status,
          startedAt: now,
          // Единственное описание вызова, которое сервер вообще присылает:
          // `label` = build_tool_preview(имя, аргументы) — команда, путь или
          // запрос человеческим текстом. Раньше поле молча терялось, и в
          // ленте оставалось голое имя инструмента.
          ...(action.toolData.label ? { context: action.toolData.label } : {}),
        };
        last.toolCalls = [...existing, newEntry];
      } else {
        // Событие «completed» приходит без label — берём его из записи о
        // старте, иначе чип с аргументом гас в момент завершения вызова.
        const updated: ToolEntry = {
          ...existing[idx],
          status,
          ...(status === "done" || status === "error"
            ? { completedAt: now }
            : {}),
          ...(action.toolData.label ? { context: action.toolData.label } : {}),
        };
        const newCalls = [...existing];
        newCalls[idx] = updated;
        last.toolCalls = newCalls;
      }
      msgs[msgs.length - 1] = last;
      return { ...state, messages: msgs };
    }

    case "FINALIZE": {
      return {
        ...state,
        isStreaming: false,
        approvals: expirePendingApprovals(state.approvals),
      };
    }

    case "SET_ERROR": {
      return {
        ...state,
        isStreaming: false,
        error: action.error,
        approvals: expirePendingApprovals(state.approvals),
      };
    }

    case "APPROVAL_REQUESTED": {
      // Один и тот же запрос может прийти дважды (переподписка на поток) —
      // ключ здесь `request_id`, а не позиция в списке.
      if (
        state.approvals.some(
          (entry) => entry.request.request_id === action.request.request_id,
        )
      ) {
        return state;
      }
      return {
        ...state,
        approvals: [
          ...state.approvals,
          { request: action.request, status: "pending" },
        ],
      };
    }

    case "APPROVAL_RESTORED": {
      // Хозяин очереди — сервер, поэтому его список правит обе стороны:
      // ждущий у нас запрос, которого там нет, гасим; и наоборот — вопрос,
      // который мы поспешили похоронить (например, по кнопке «Стоп»: она
      // рвёт только показ, а ход на сервере продолжает ждать ответа),
      // возвращаем в работу. Принятое решение не трогаем никогда.
      const alive = new Set(action.requests.map((item) => item.request_id));
      const known = new Set(
        state.approvals.map((entry) => entry.request.request_id),
      );
      const kept = state.approvals.map((entry) => {
        if (entry.status === "settled") return entry;
        const stillWaiting = alive.has(entry.request.request_id);
        if (stillWaiting && entry.status === "expired") {
          return { ...entry, status: "pending" as const, error: undefined };
        }
        if (!stillWaiting && entry.status !== "expired") {
          return { ...entry, status: "expired" as const };
        }
        return entry;
      });
      const added = action.requests
        .filter((item) => !known.has(item.request_id))
        .map((item) => ({ request: item, status: "pending" as const }));
      if (added.length === 0 && kept.every((entry, i) => entry === state.approvals[i])) {
        return state;
      }
      return { ...state, approvals: [...kept, ...added] };
    }

    case "APPROVAL_SENDING": {
      return {
        ...state,
        approvals: state.approvals.map((entry) =>
          entry.request.request_id === action.requestId
            ? { ...entry, status: "sending", error: undefined }
            : entry,
        ),
      };
    }

    case "APPROVAL_SETTLED": {
      // Решение, отправленное вне живого потока (после перезагрузки страницы),
      // разблокирует ход на сервере, но ответ агента дописывается уже мимо этой
      // вкладки — честнее сказать это сразу, чем оставить человека ждать.
      const note = state.isStreaming
        ? undefined
        : "Ход продолжится на сервере — ответ появится в истории чата.";
      return {
        ...state,
        approvals: state.approvals.map((entry) =>
          entry.request.request_id === action.requestId
            ? {
                ...entry,
                status: "settled",
                decision: action.decision,
                error: undefined,
                ...(note ? { note } : {}),
              }
            : entry,
        ),
      };
    }

    case "APPROVAL_FAILED": {
      return {
        ...state,
        approvals: state.approvals.map((entry) =>
          entry.request.request_id === action.requestId
            ? {
                ...entry,
                status: action.expired ? "expired" : "pending",
                error: action.error,
              }
            : entry,
        ),
      };
    }

    case "RESET": {
      return {
        messages: [],
        sessionId: null,
        isStreaming: false,
        error: null,
        approvals: [],
      };
    }

    case "RESET_STREAMING": {
      return {
        ...state,
        isStreaming: false,
        approvals: expirePendingApprovals(state.approvals),
      };
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
  /** Вопросы агента по опасным командам: и живые, и уже отвеченные. */
  approvals: ChatApprovalEntry[];
  /** Возвращает false, если сообщение доставить не удалось. */
  send: (text: string, attachments?: UploadedAttachment[]) => Promise<boolean>;
  /** Отправить решение по одному вопросу. False — решение не ушло. */
  resolveApproval: (
    requestId: string,
    choice: ApprovalChoiceValue,
  ) => Promise<boolean>;
  retryPending: () => Promise<boolean>;
  discardPending: () => void;
  loadSession: (sessionId: string) => Promise<void>;
  reset: () => void;
  abort: () => void;
}

/** Строка истории со всем, что реально отдаёт панельный маршрут
 *  `GET /api/sessions/{id}/messages` (он возвращает строку таблицы целиком,
 *  без проекции api_server). */
type HistoryMessage = SessionMessage & SessionMessageReasoning;

/**
 * История сессии → лента чата.
 *
 * В базе один ход агента лежит цепочкой строк: ответ с `tool_calls`, затем
 * строки роли `tool` с результатами, затем снова ответ — и так до реплики
 * без вызовов. В живом потоке весь этот ход рисуется ОДНИМ пузырём: события
 * `tool.progress` копятся на последнем сообщении. Поэтому цепочку сворачиваем
 * в одно сообщение — иначе после перезагрузки та же переписка выглядела бы
 * иначе, чем минуту назад вживую.
 */
function sessionMessagesToChat(
  sessionId: string,
  messages: HistoryMessage[],
): ChatMessage[] {
  const result: ChatMessage[] = [];
  /** Незакрытый ход агента: в него дописываются вызовы и текст. */
  let turn: ChatMessage | null = null;

  const closeTurn = () => {
    if (!turn) return;
    // Ход без текста и без вызовов показывать нечего (служебные строки).
    if (turn.content || turn.toolCalls?.length || turn.reasoning) {
      result.push(turn);
    }
    turn = null;
  };

  messages.forEach((message, index) => {
    const timestamp = message.timestamp ?? Date.now();
    const content = typeof message.content === "string" ? message.content : "";

    if (message.role === "user") {
      closeTurn();
      result.push({
        id: `${sessionId}-${index}`,
        role: "user",
        content,
        timestamp,
      });
      return;
    }

    if (message.role === "tool") {
      // Результат вызова прикрепляем к его же строке в текущем ходе.
      if (!turn?.toolCalls || !message.tool_call_id) return;
      const summary = previewToolResult(content);
      if (!summary) return;
      turn.toolCalls = turn.toolCalls.map((entry) =>
        entry.id === message.tool_call_id ? { ...entry, summary } : entry,
      );
      return;
    }

    if (message.role !== "assistant") return;

    if (!turn) {
      turn = {
        id: `${sessionId}-${index}`,
        role: "assistant",
        content: "",
        timestamp,
      };
    }
    if (content) {
      turn.content = turn.content ? `${turn.content}\n\n${content}` : content;
    }
    const reasoning = message.reasoning_content?.trim();
    if (reasoning && !turn.reasoning) turn.reasoning = reasoning;

    const calls = message.tool_calls ?? [];
    if (calls.length > 0) {
      const entries: ToolEntry[] = calls.map((call) => {
        const preview = previewArguments(call.function.name, call.function.arguments);
        return {
          kind: "tool",
          id: call.id,
          tool_id: call.function.name,
          name: call.function.name,
          status: "done",
          // История не хранит длительность вызова. `0` — принятый в ToolCall
          // признак «времени нет», и он же не даёт нарисовать выдуманное «0ms».
          startedAt: 0,
          ...(preview ? { context: preview } : {}),
        };
      });
      turn.toolCalls = [...(turn.toolCalls ?? []), ...entries];
      return;
    }

    // Ответ без вызовов завершает ход.
    closeTurn();
  });

  closeTurn();
  return result;
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
    const pending = loadChatOutbox(profile ?? "");
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
  }, [profile]);

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
      const chatMessages = sessionMessagesToChat(
        sessionId,
        resp.messages as HistoryMessage[],
      );

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
      const msg = ownerFacingError(err, "Не удалось загрузить сессию.");
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
        // Блокирует только черновик ТОЙ ЖЕ сессии; черновик другого чата
        // этого профиля показывает баннер и не мешает писать здесь.
        const pending = state.sessionId
          ? loadChatOutbox(profile ?? "", state.sessionId)
          : null;
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
        profile: profile ?? "",
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
        let terminalErrorMessage = "";
        // Хоть одно событие от агента = сообщение до него доехало. Признак
        // нужен отдельно от `sawDone`: поток может оборваться после начала
        // ответа, и это уже не «не отправлено».
        let sawAgentOutput = false;
        // Подпись «Отправляется…» гасим на ПЕРВОМ событии потока, а не в конце
        // хода: на вопросе об одобрении ход стоит минутами, и всё это время
        // владелец видел бы «отправляется» под сообщением, которое агент давно
        // читает. Черновик в outbox при этом не трогаем — он снимается только
        // по фактическому концу хода, иначе оборванный поток остался бы без
        // страховки на повтор.
        const noteAgentOutput = () => {
          if (sawAgentOutput) return;
          sawAgentOutput = true;
          dispatch({ type: "MARK_DELIVERY", messageId, delivery: "delivered" });
        };

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
                noteAgentOutput();
                dispatch({ type: "APPEND_DELTA", content });
              }
              if (choice?.finish_reason === "error") {
                sawTerminalError = true;
                // Сервер кладёт текст ошибки в content этого же чанка
                // («HTTP 401: invalid x-api-key», «No credentials…») — показываем
                // его человеку, а не общую фразу.
                terminalErrorMessage =
                  content.trim() || "Ответ агента завершился с ошибкой";
                void reader.cancel();
                break;
              }
            } else if (event.type === "tool_progress") {
              noteAgentOutput();
              dispatch({ type: "UPSERT_TOOL", toolData: event.data });
            } else if (event.type === "approval_request") {
              // Ход агента с этого мгновения стоит и ждёт ответа человека.
              // Значит, сообщение до агента доехало — доставку признаём.
              const request = normalizeApprovalRequest(event.data);
              if (request) {
                noteAgentOutput();
                dispatch({ type: "APPROVAL_REQUESTED", request });
              }
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
          if (sawTerminalError) {
            dispatch({ type: "SET_ERROR", error: terminalErrorMessage });
          } else {
            dispatch({ type: "FINALIZE" });
          }
          // Доставкой считаем только явное `[DONE]` или начавшийся ответ
          // агента. Прежде терминальное состояние читателя само по себе
          // означало успех — то есть оборванный на полпути поток докладывал
          // карточке «отправлено агенту», и владелец считал решение
          // принятым (находка ревью 20.08.2026).
          delivered = !sawTerminalError && (sawDone || sawAgentOutput);
          if (delivered) {
            clearChatOutbox(messageId, profile ?? "");
            dispatch({ type: "MARK_DELIVERY", messageId, delivery: "delivered" });
          } else {
            saveChatOutbox({
              ...outboxRecord,
              status: "failed",
              error:
                terminalErrorMessage ||
                "Ответ завершился без подтверждения доставки",
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
    const pending = loadChatOutbox(profile ?? "");
    if (!pending || streamingRef.current) return false;
    // Черновик из другого чата: сначала открываем тот чат, потом повторяем —
    // иначе сообщение оказалось бы в чужой переписке.
    if (pending.sessionId !== state.sessionId) {
      try {
        await loadSession(pending.sessionId);
      } catch {
        // Сессия могла так и не появиться на сервере — send создаст её.
      }
    }
    return await send(pending.text, pending.attachments, pending);
  }, [send, loadSession, profile, state.sessionId]);

  const discardPending = useCallback(() => {
    if (streamingRef.current) return;
    const pending = loadChatOutbox(profile ?? "");
    if (!pending) return;
    clearChatOutbox(pending.messageId, profile ?? "");
    dispatch({ type: "DISCARD_PENDING", messageId: pending.messageId });
  }, [profile]);

  const resolveApproval = useCallback(
    async (
      requestId: string,
      choice: ApprovalChoiceValue,
    ): Promise<boolean> => {
      const sessionId = state.sessionId;
      if (!sessionId || !requestId) return false;
      dispatch({ type: "APPROVAL_SENDING", requestId });
      const result = await sendApprovalDecision({
        sessionId,
        requestId,
        choice,
        ...(profile ? { profile } : {}),
      });
      if (!mountedRef.current) return result.ok;
      if (result.ok) {
        dispatch({ type: "APPROVAL_SETTLED", requestId, decision: choice });
        return true;
      }
      dispatch({
        type: "APPROVAL_FAILED",
        requestId,
        error: result.error,
        expired: result.expired,
      });
      return false;
    },
    [state.sessionId, profile],
  );

  // Восстановление вопроса после перезагрузки страницы. Живой поток SSE живёт
  // только в открытой вкладке, а ход агента переживает F5 и продолжает стоять
  // на вопросе — поэтому при отсутствии потока спрашиваем сервер напрямую.
  // Пока вопрос висит, перепроверяем: у ожидания есть таймаут, и мёртвую
  // карточку честнее погасить, чем оставить кнопку, которая ничего не сделает.
  const hasWaitingApproval = state.approvals.some(
    (entry) => entry.status === "pending",
  );
  const { sessionId: currentSessionId, isStreaming } = state;
  useEffect(() => {
    if (!currentSessionId || isStreaming) return;
    let cancelled = false;
    const load = async () => {
      try {
        const requests = await fetchPendingApprovals(
          currentSessionId,
          profile || undefined,
        );
        if (cancelled || !mountedRef.current) return;
        dispatch({ type: "APPROVAL_RESTORED", requests });
      } catch {
        // Не нашлось — не повод пугать человека: карточка либо появится на
        // следующей проверке, либо её и правда нет.
      }
    };
    void load();
    if (!hasWaitingApproval) {
      return () => {
        cancelled = true;
      };
    }
    const timer = window.setInterval(() => void load(), APPROVAL_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [currentSessionId, isStreaming, hasWaitingApproval, profile]);

  return {
    messages: state.messages,
    sessionId: state.sessionId,
    isStreaming: state.isStreaming,
    error: state.error,
    approvals: state.approvals,
    send,
    resolveApproval,
    retryPending,
    discardPending,
    loadSession,
    reset,
    abort,
  };
}
