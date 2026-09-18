import { clearChatAttachmentDraft } from "@/hooks/useChatAttachmentDraft";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { chatViewKey, readChatSelection, writeChatSelection, writeChatView } from "@/lib/chat-view-state";
import { $viewedChat, markChatViewed, chatRunHeaders, chatRunUrl, getChatRuns, isRunBusy, refreshChatRuns, type ChatRun } from "@/lib/chat-runs";
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
import { splitAttachments, toDisplay, type UploadedAttachment } from "@/lib/chat-attachments";
import { ownerFacingError } from "@/lib/owner-facing-error";
import {
  clearChatOutbox,
  isChatOutboxStorageKey,
  loadChatOutbox,
  loadChatOutboxRecords,
  saveChatOutbox,
  type ChatOutboxRecord,
} from "@/lib/chat-outbox";

// ---------------------------------------------------------------------------
// State & Actions
// ---------------------------------------------------------------------------

/**
 * Запрос одобрения вместе с тем, что с ним уже сделали в этой вкладке.
 *
 * `pending`  — команда ждёт в живом ходе или внешний эффект ждёт решения;
 * `sending`  — ответ отправляется;
 * `settled`  — ответ принят движком, исход в `decision`;
 * `expired`  — отвечать некому: ход команды кончился или истёк таймаут.
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
  | { type: "SEND_USER"; userMsg: ChatMessage; assistantMsg: ChatMessage; streaming?: boolean }
  | { type: "RETRY_USER"; userMsg: ChatMessage; assistantMsg: ChatMessage }
  | { type: "RESTORE_PENDING"; sessionId: string; userMsg: ChatMessage; error: string }
  | { type: "MARK_DELIVERY"; messageId: string; delivery: "sending" | "failed" | "delivered"; terminal?: boolean; error?: string }
  | { type: "DISCARD_PENDING"; messageId: string }
  | { type: "SET_SESSION_ID"; sessionId: string | null }
  | { type: "LOAD_SESSION"; sessionId: string; messages: ChatMessage[] }
  | {
      type: "SYNC_SESSION";
      sessionId: string;
      messages: ChatMessage[];
      streaming?: boolean;
      /** Хвост списка — ход, который сейчас будет переигран из журнала.
       *  Дописывать после него нечего: поток пишет в последнее сообщение. */
      replay?: boolean;
    }
  | { type: "APPEND_DELTA"; content: string }
  | { type: "UPSERT_TOOL"; toolData: SSEToolProgressData }
  | { type: "FINALIZE" }
  | { type: "SET_ERROR"; error: string }
  | { type: "STOP_FAILED"; error: string }
  | { type: "APPROVAL_REQUESTED"; request: SSEApprovalRequestData }
  | { type: "APPROVAL_RESTORED"; requests: SSEApprovalRequestData[] }
  | { type: "APPROVAL_SENDING"; requestId: string }
  | { type: "APPROVAL_SETTLED"; requestId: string; decision: ApprovalChoiceValue; note?: string }
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

/** Ход кончился — обычные command approvals уже некому исполнять. Durable
 * external effects не привязаны к модельному потоку и остаются pending. */
function expirePendingApprovals(
  approvals: ChatApprovalEntry[],
): ChatApprovalEntry[] {
  if (!approvals.some((entry) => entry.status === "pending" || entry.status === "sending")) {
    return approvals;
  }
  return approvals.map((entry) =>
    (entry.status === "pending" || entry.status === "sending") &&
    !entry.request.decision_kind
      ? { ...entry, status: "expired" as const }
      : entry,
  );
}

function effectOutcomeNote(request: SSEApprovalRequestData): string | undefined {
  switch (request.effect_status) {
    case "denied":
      return request.decision_kind === "payment"
        ? "Оплата отклонена."
        : "Сообщение не отправлено.";
    case "succeeded":
      return request.decision_kind === "payment"
        ? "Оплата выполнена один раз."
        : "Сообщение отправлено один раз.";
    case "failed":
      return "Действие не выполнено: точный payload или аккаунт изменился.";
    case "unknown":
      return "Исход не подтверждён. Korra не повторяет действие вслепую.";
    case "approved":
    case "executing":
      return "Точное действие разрешено и выполняется.";
    default:
      return undefined;
  }
}

function restoredApprovalEntry(request: SSEApprovalRequestData): ChatApprovalEntry {
  if (request.decision_kind && request.effect_status && request.effect_status !== "pending") {
    return {
      request,
      status: "settled",
      decision: request.effect_status === "denied" ? "deny" : "once",
      note: effectOutcomeNote(request),
    };
  }
  return { request, status: "pending" };
}

/** Текст, который человек видит в пузыре. Блок `[вложения]` адресован модели:
 *  живая копия сообщения его не содержит, а та же строка из истории — да. */
function visibleText(message: ChatMessage): string {
  return message.role === "user" ? splitAttachments(message.content).text : message.content;
}

/** Файлы сообщения ровно в том виде, в каком их берёт лента: свои из
 *  отправки, чужие — разобранные из истории. */
function attachmentKeys(message: ChatMessage): string {
  const items =
    message.attachments ??
    (message.role === "user" ? splitAttachments(message.content).attachments : []);
  return items.map((item) => item.key).join(" ");
}

function toolTrace(message: ChatMessage): string {
  return (message.toolCalls ?? [])
    .map((entry) => `${entry.id} | ${entry.status} | ${entry.context ?? ""} | ${entry.summary ?? ""}`)
    .join(" ");
}

/** Один и тот же пузырь переписки. Живая копия и строка истории отличаются
 *  служебными полями (id, отметка времени, блок вложений), но для человека
 *  это одно сообщение — и один и тот же элемент ленты. */
function sameChatTurn(previous: ChatMessage, next: ChatMessage): boolean {
  return (
    previous.role === next.role &&
    visibleText(previous) === visibleText(next) &&
    attachmentKeys(previous) === attachmentKeys(next)
  );
}

/** Показывать заново нечего: совпадают и трасса вызовов, и размышление. */
function sameRendered(previous: ChatMessage, next: ChatMessage): boolean {
  return (
    sameChatTurn(previous, next) &&
    (previous.reasoning ?? "") === (next.reasoning ?? "") &&
    toolTrace(previous) === toolTrace(next)
  );
}

/**
 * Свежий список сообщений поверх уже показанного.
 *
 * Возврат к чату не должен выглядеть как повторная прогрузка: неизменившиеся
 * пузыри остаются теми же объектами, а изменившиеся сохраняют свой `id`. Для
 * React это значит «тот же элемент», поэтому карточка вложения не монтируется
 * заново — не перепроверяет файл и не качает превью второй раз. Если ничего
 * не изменилось, возвращается прежний массив, и лента вообще не перерисуется.
 */
function mergeMessages(
  previous: ChatMessage[],
  next: ChatMessage[],
  keepUndelivered: boolean,
): ChatMessage[] {
  let changed = previous.length !== next.length;
  const merged = next.map((message, index) => {
    const shown = previous[index];
    if (!shown || !sameChatTurn(shown, message)) {
      changed = true;
      return message;
    }
    if (sameRendered(shown, message)) return shown;
    changed = true;
    return { ...message, id: shown.id };
  });
  // Сообщение, которое не дошло до сервера, в истории не появится. Убрать его
  // на возврате значило бы спрятать и текст владельца, и кнопку «Повторить».
  const undelivered = keepUndelivered
    ? previous.filter(
        (message) =>
          message.delivery === "failed" &&
          !merged.some((kept) => sameChatTurn(kept, message)),
      )
    : [];
  if (undelivered.length > 0) {
    changed = true;
    merged.push(...undelivered);
  }
  return changed ? merged : previous;
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
        isStreaming: action.streaming ?? true,
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
            ? {
                ...message,
                delivery: action.delivery,
                failureConfirmed: action.terminal ?? message.failureConfirmed,
                failureReason: action.delivery === "failed"
                  ? (action.error ?? message.failureReason)
                  : undefined,
              }
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

    case "SYNC_SESSION": {
      // Освежение уже открытого чата. Чужую переписку сюда не пускаем: если
      // за время проверки открыли другой чат, ведём себя как обычная загрузка.
      if (state.sessionId !== action.sessionId) {
        return {
          ...state,
          sessionId: action.sessionId,
          messages: action.messages,
          isStreaming: action.streaming ?? false,
          error: null,
          approvals: [],
        };
      }
      const messages = mergeMessages(state.messages, action.messages, action.replay !== true);
      const isStreaming = action.streaming ?? false;
      // Ничего не изменилось — нечего и перерисовывать. Живые карточки
      // одобрения остаются на месте: их хозяин — сервер, а не эта проверка.
      if (messages === state.messages && state.isStreaming === isStreaming && state.error === null) {
        return state;
      }
      return { ...state, messages, isStreaming, error: null };
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

    case "STOP_FAILED": return { ...state, error: action.error };

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
      const restored = new Map(
        action.requests.map((item) => [item.request_id, restoredApprovalEntry(item)]),
      );
      const alive = new Set(restored.keys());
      const known = new Set(
        state.approvals.map((entry) => entry.request.request_id),
      );
      const kept = state.approvals.map((entry) => {
        const serverEntry = restored.get(entry.request.request_id);
        if (serverEntry?.status === "settled") return serverEntry;
        if (entry.status === "settled") return entry;
        const stillWaiting = alive.has(entry.request.request_id);
        if (stillWaiting && entry.status === "expired") {
          return serverEntry ?? { ...entry, status: "pending" as const, error: undefined };
        }
        if (!stillWaiting && entry.status !== "expired") {
          return { ...entry, status: "expired" as const };
        }
        return entry;
      });
      const added = action.requests
        .filter((item) => !known.has(item.request_id))
        .map((item) => restored.get(item.request_id)!);
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
      const note = action.note ?? (state.isStreaming
        ? undefined
        : "Ход продолжится на сервере — ответ появится в истории чата.");
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

export interface PendingMessageTarget { sessionId: string; messageId: string }

export interface UseChatStreamReturn {
  isLoading: boolean;
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
  retryPending: (target?: PendingMessageTarget) => Promise<boolean>;
  discardPending: (target?: PendingMessageTarget) => void;
  loadSession: (sessionId: string, options?: LoadSessionOptions) => Promise<void>;
  reset: () => void;
  abort: () => void;
}

export interface LoadSessionOptions {
  /** Освежить уже открытый чат, не очищая ленту: новые сообщения и состояние
   *  хода проверяются в фоне, показанное остаётся на месте. Для первого
   *  открытия и перехода в другой чат это делать нельзя — там очистка
   *  обязательна, иначе на мгновение видна чужая переписка. */
  background?: boolean;
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
  active?: boolean;
  /** Профиль агента, которому адресован чат. Пусто/не задан — профиль самого
   *  процесса панели (прежнее поведение). Уезжает в `?profile=` на
   *  /api/chat/completions и в загрузку истории сессии. */
  profile?: string;
}

function outboxFailure(record: ChatOutboxRecord, failure?: ChatRun["failure"]): string {
  if (failure) {
    return ownerFacingError(
      JSON.stringify({ error: failure }),
      record.error || "Предыдущая попытка завершилась с ошибкой.",
    );
  }
  if (record.terminal) {
    return ownerFacingError(record.error, "Предыдущая попытка завершилась с ошибкой.");
  }
  return record.error || "Доставка не подтверждена. Проверьте историю перед повторной отправкой.";
}

function outboxMessage(record: ChatOutboxRecord, failure?: ChatRun["failure"]): ChatMessage {
  return {
    id: `user-${record.messageId}`,
    clientMessageId: record.messageId,
    role: "user",
    content: record.text,
    timestamp: record.createdAt,
    attachments: toDisplay(record.attachments),
    delivery: "failed",
    failureConfirmed: record.terminal || Boolean(failure),
    failureReason: outboxFailure(record, failure),
  };
}

export function useChatStream(
  options?: UseChatStreamOptions,
): UseChatStreamReturn {
  const profile = options?.profile;
  const active = options?.active !== false;
  const selectionKey = `${chatViewKey(profile)}:selected`;
  const [state, dispatch] = useReducer(reducer, initialState);
  const [isLoading, setIsLoading] = useState(false);
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
    const selection = readChatSelection(selectionKey);
    if (selection === null) return;
    for (const pending of loadChatOutboxRecords(profile ?? "", selection)) {
      const restored: ChatOutboxRecord = {
        ...pending,
        status: "failed",
        error: pending.error || "Доставка не подтверждена после перезагрузки",
      };
      saveChatOutbox(restored);
      const explanation = outboxFailure(restored);
      dispatch({
        type: "RESTORE_PENDING",
        sessionId: restored.sessionId,
        userMsg: outboxMessage(restored),
        error: restored.terminal
          ? explanation
          : "Сообщение сохранено в этом браузере. «Проверить и повторить» сначала сверит прежнюю отправку.",
      });
    }
  }, [profile, selectionKey]);

  const abort = useCallback(() => {
    const generation = activeStreamIdRef.current;
    const sessionId = state.sessionId;
    if (!sessionId) return;
    void (async () => {
      try {
        const run = (await getChatRuns(profile ?? "", sessionId))[0];
        if (!run) throw new Error("Ход пока отправляется. Попробуйте остановить ещё раз.");
        const response = await fetch(chatRunUrl(`/${encodeURIComponent(run.message_id)}/cancel`, profile ?? "", sessionId), {
          method: "POST", headers: chatRunHeaders(),
        });
        if (!response.ok) throw new Error("Не удалось остановить ход. Проверьте связь и повторите.");
        clearChatOutbox(run.message_id, profile ?? "");
        if (mountedRef.current && activeStreamIdRef.current === generation) {
          activeStreamIdRef.current = null;
          abortControllerRef.current?.abort();
          streamingRef.current = false;
          dispatch({ type: "SET_ERROR", error: "Ход остановлен. Можно написать новое сообщение." });
        }
        void refreshChatRuns();
      } catch (error) {
        if (mountedRef.current && activeStreamIdRef.current === generation) {
          // A failed Stop is not evidence that the agent stopped.
          dispatch({ type: "STOP_FAILED", error: ownerFacingError(error, "Не удалось остановить ход.") });
        }
      }
    })();
  }, [profile, state.sessionId]);

  const loadSession = useCallback(async (sessionId: string, options?: LoadSessionOptions): Promise<void> => {
    const background = options?.background === true;
    setIsLoading(true);
    writeChatSelection(selectionKey, sessionId);
    const generation = crypto.randomUUID();
    activeStreamIdRef.current = generation;
    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;
    streamingRef.current = true; // Loading also excludes a concurrent send.
    const current = () => mountedRef.current && activeStreamIdRef.current === generation;
    /** Первое открытие и переход в другой чат ленту очищают — показывать чужую
     *  историю нельзя. Освежение текущего чата её не трогает: новое доезжает
     *  поверх показанного, а неизменившееся остаётся тем же самым. */
    const show = (messages: ChatMessage[], replay?: { streaming: boolean }) =>
      dispatch(background
        ? { type: "SYNC_SESSION", sessionId, messages, ...(replay ? { streaming: replay.streaming, replay: true } : {}) }
        : { type: "LOAD_SESSION", sessionId, messages });
    // Пустая лента на время проверки — это и есть «всё загружается заново»:
    // пузыри монтируются с нуля, карточки вложений заново проверяют файл и
    // заново качают превью, а место чтения теряется.
    const pendingMessages = (runs: ChatRun[] = []) => {
      const runsById = new Map(runs.map((run) => [run.message_id, run]));
      return loadChatOutboxRecords(profile ?? "", sessionId).map((record) => {
        const run = runsById.get(record.messageId);
        const failure = run?.status === "failed" ? run.failure : undefined;
        const failureExplanation = failure ? outboxFailure(record, failure) : record.error;
        if (run?.status === "failed" && (
          !record.terminal || (failureExplanation && record.error !== failureExplanation)
        )) {
          const updated = {
            ...record,
            status: "failed" as const,
            terminal: true,
            error: failureExplanation || "Предыдущая попытка завершилась с ошибкой.",
          };
          saveChatOutbox(updated);
          return outboxMessage(updated, failure);
        }
        return outboxMessage(record, failure);
      });
    };
    const showWithPending = (messages: ChatMessage[], runs: ChatRun[] = []) => {
      const restored = [...messages];
      const runsById = new Map(runs.map((run) => [run.message_id, run]));
      for (const pending of pendingMessages(runs)) {
        const run = runsById.get(pending.clientMessageId ?? "");
        if (run?.status === "completed" && restored.length >= run.history_count + 2) {
          clearChatOutbox(run.message_id, profile ?? "");
          continue;
        }
        if (run && restored[run.history_count]?.role === "user" &&
            sameChatTurn(restored[run.history_count], pending)) {
          restored[run.history_count] = pending;
        } else if (!restored.some((message) => message.clientMessageId === pending.clientMessageId)) {
          restored.push(pending);
        }
      }
      show(restored);
    };
    if (!background) {
      dispatch({ type: "LOAD_SESSION", sessionId, messages: pendingMessages() });
    }
    try {
      // Read the run AFTER history: completion between these reads is replayed
      // from the same ledger, never from a stale history snapshot.
      const resp = await api.getSessionMessages(sessionId, profile || "default").catch(error => {
        if (error instanceof Error && /^404(?:\s|:)/.test(error.message)) return { messages: [] };
        throw error;
      });
      if (!current()) return;
      const chatMessages = sessionMessagesToChat(sessionId, resp.messages as HistoryMessage[]);
      let runs: ChatRun[];
      try {
        runs = await getChatRuns(profile ?? "", sessionId);
      } catch {
        if (!current()) return;
        showWithPending(chatMessages);
        dispatch({ type: "SET_ERROR", error: "История загружена. Не удалось проверить, работает ли агент; связь будет проверена при возврате." });
        return;
      }
      if (!current()) return;
      const run = runs[0];
      setIsLoading(false);
      const savedIntents = loadChatOutboxRecords(profile ?? "", sessionId);
      for (const item of runs) {
        if (item.status === "completed" && chatMessages.length >= item.history_count + 2) {
          clearChatOutbox(item.message_id, profile ?? "");
        }
      }
      const newerHistory = run?.status === "completed" && chatMessages.length > run.history_count + 2;
      if (!run || newerHistory || (!isRunBusy(run) && run.status !== "completed")) {
        showWithPending(chatMessages, runs);
        if (run?.status === "interrupted") dispatch({ type: "SET_ERROR", error: "Связь с ходом потеряна. Проверьте историю перед повторной отправкой." });
        if (run?.status === "failed") {
          const pending = loadChatOutboxRecords(profile ?? "", sessionId)
            .find((record) => record.messageId === run.message_id);
          const explanation = pending
            ? outboxFailure(pending, run.failure)
            : ownerFacingError(JSON.stringify({ error: run.failure }), "Предыдущая попытка завершилась с ошибкой.");
          dispatch({ type: "SET_ERROR", error: `${explanation} Если задача уже выполнялась частично, проверьте результат перед новой отправкой.` });
        }
        return;
      }
      // Ход закончен, и его ответ уже целиком лежит в истории. Перечитывать
      // журнал на каждом возврате незачем: replay нужен, когда снимок истории
      // мог отстать от журнала, а не чтобы заново нарисовать то же самое.
      const hasOtherIntent = savedIntents.some((record) => record.messageId !== run.message_id);
      if ((background || hasOtherIntent) && run.status === "completed" && chatMessages.length >= run.history_count + 2) {
        clearChatOutbox(run.message_id, profile ?? "");
        showWithPending(chatMessages, runs);
        return;
      }
      const pending = loadChatOutboxRecords(profile ?? "", sessionId)
        .find((record) => record.messageId === run.message_id);
      const userMsg: ChatMessage = {
        id: `user-${run.message_id}`, clientMessageId: run.message_id,
        role: "user", content: run.user_message.content,
        timestamp: run.updated_at * 1000, delivery: "delivered",
        ...(pending ? { attachments: toDisplay(pending.attachments) } : {}),
      };
      const assistantMsg: ChatMessage = {
        id: `asst-${run.message_id}`, role: "assistant", content: "", timestamp: Date.now(),
      };
      // The durable stream replays from byte zero. Remove this turn's saved
      // copy before replay, including tool messages, so it appears exactly once.
      if (background) {
        show([...chatMessages.slice(0, run.history_count), userMsg, assistantMsg], { streaming: isRunBusy(run) });
      } else {
        dispatch({ type: "LOAD_SESSION", sessionId, messages: chatMessages.slice(0, run.history_count) });
        dispatch({ type: "SEND_USER", streaming: isRunBusy(run), userMsg, assistantMsg });
      }
      const response = await fetch(chatRunUrl(`/${encodeURIComponent(run.message_id)}/stream`, profile ?? "", sessionId), {
        headers: chatRunHeaders(), signal: controller.signal, cache: "no-store",
      });
      if (!current()) return;
      if (!response.ok || !response.body) throw new Error("Не удалось восстановить ответ. Вернитесь в чат для повторного подключения.");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let done = false;
      while (!done) {
        const part = await reader.read();
        if (!current()) { void reader.cancel(); return; }
        if (part.done) break;
        const parsed = splitSSEBuffer(buffer + decoder.decode(part.value, { stream: true }));
        buffer = parsed.remainder;
        for (const event of parsed.events) {
          if (event.type === "chunk") {
            const choice = event.data.choices[0];
            if (choice?.delta?.content && choice.finish_reason !== "error") dispatch({ type: "APPEND_DELTA", content: choice.delta.content });
            if (choice?.finish_reason === "error") {
              void reader.cancel();
              const explanation = ownerFacingError(JSON.stringify({
                error: {
                  ...event.data.error,
                  message: choice.delta?.content || event.data.error?.message,
                },
              }), "Агент завершил ответ с ошибкой");
              if (pending?.messageId === run.message_id) {
                saveChatOutbox({ ...pending, terminal: true, status: "failed", error: explanation });
                dispatch({ type: "MARK_DELIVERY", messageId: pending.messageId, delivery: "failed", terminal: true, error: explanation });
              }
              throw new Error(explanation);
            }
          } else if (event.type === "tool_progress") dispatch({ type: "UPSERT_TOOL", toolData: event.data });
          else if (event.type === "approval_request") {
            const request = normalizeApprovalRequest(event.data);
            if (request) dispatch({ type: "APPROVAL_REQUESTED", request });
          } else if (event.type === "done") done = true;
        }
      }
      void reader.cancel();
      if (!done) throw new Error("Связь с ответом прервалась. Агент может продолжать работу; вернитесь в чат для подключения.");
      clearChatOutbox(run.message_id, profile ?? "");
      dispatch({ type: "FINALIZE" });
      for (const remaining of pendingMessages(runs).filter((message) => message.clientMessageId !== run.message_id)) {
        dispatch({ type: "RESTORE_PENDING", sessionId, userMsg: remaining, error: "Ответ получен. Сохранённое сообщение ещё требует проверки." });
      }
      void refreshChatRuns();
    } catch (err) {
      if (current()) {
        const pending = loadChatOutboxRecords(profile ?? "", sessionId)[0];
        if (pending) dispatch({ type: "MARK_DELIVERY", messageId: pending.messageId, delivery: "failed", error: outboxFailure(pending) });
        dispatch({ type: "SET_ERROR", error: `Не удалось обновить переписку. ${ownerFacingError(err, "Проверьте связь и откройте чат ещё раз. Работа агента могла продолжиться.")}` });
      }
    } finally {
      if (current()) {
        setIsLoading(false);
        streamingRef.current = false;
        activeStreamIdRef.current = null;
      }
    }
  }, [profile, selectionKey]);

  const reset = useCallback(() => {
    writeChatSelection(selectionKey, null);
    // Same triple-guard as loadSession — abort any active stream so its
    // residual APPEND_DELTAs don't leak into the new chat.
    activeStreamIdRef.current = null;
    abortControllerRef.current?.abort();
    streamingRef.current = false;

    setIsLoading(false);
    dispatch({ type: "RESET" });
  }, [selectionKey]);

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
      writeChatSelection(selectionKey, sessionId);
      // Tag this stream as the one currently allowed to mutate state.
      // The read loop below re-checks this ref on every iteration so a
      // mid-stream loadSession()/reset() can invalidate us synchronously.
      const localStreamId = crypto.randomUUID();
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

      writeChatView(chatViewKey(profile, state.sessionId), "");
      clearChatAttachmentDraft(chatViewKey(profile, state.sessionId));
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
          // Локальная копия с неподтверждённой доставкой остаётся в интерфейсе,
          // но не становится контекстом нового запроса модели.
          .filter((message) => message.id !== userMsg.id && message.delivery !== "failed" && (message.content || message.role === "user"))
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
            const explanation = response.status === 429 && /queue|capacity|concurrent|места заняты|очеред/i.test(errText)
              ? "Сейчас выполняется слишком много задач. Повторите, когда одна из задач завершится."
              : response.status === 409
              ? "Доставка требует проверки. Откройте историю или повторите это же сообщение."
              : ownerFacingError(`${response.status}: ${errText}`, "Не удалось подтвердить отправку.");
            const failedRecord: ChatOutboxRecord = {
              ...outboxRecord,
              status: "failed",
              error: explanation,
            };
            saveChatOutbox(failedRecord);
            dispatch({ type: "MARK_DELIVERY", messageId, delivery: "failed", error: explanation });
            dispatch({
              type: "SET_ERROR",
              error: `${explanation} Сообщение сохранено в этом браузере.`,
            });
          }
          return delivered;
        }

        // The durable proxy acknowledged the intent, including queue admission.
        // Keep the outbox until DONE, but do not call accepted work "sending".
        if (response.headers.get("X-Korra-Delivery-State")) {
          dispatch({ type: "MARK_DELIVERY", messageId, delivery: "delivered" });
        }
        void refreshChatRuns();
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
              if (content && choice?.finish_reason !== "error") {
                noteAgentOutput();
                dispatch({ type: "APPEND_DELTA", content });
              }
              if (choice?.finish_reason === "error") {
                sawTerminalError = true;
                // Сервер кладёт текст ошибки в content этого же чанка
                // («HTTP 401: invalid x-api-key», «No credentials…») — показываем
                // его человеку, а не общую фразу. Если content пуст, причина
                // приходит отдельным полем `error` финального чанка (так
                // выглядит отказ провайдера на свежем контуре без ключа).
                terminalErrorMessage = ownerFacingError(
                  JSON.stringify({
                    error: {
                      ...event.data.error,
                      message: content.trim() || event.data.error?.message?.trim(),
                    },
                  }),
                  "Ответ агента завершился с ошибкой",
                );
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
          if (!sawDone && !sawTerminalError) dispatch({ type: "SET_ERROR", error: "Связь с ответом прервалась. Агент может продолжать работу; вернитесь в чат для подключения." });
          if (delivered && sawDone) {
            clearChatOutbox(messageId, profile ?? "");
            dispatch({ type: "MARK_DELIVERY", messageId, delivery: "delivered" });
          } else {
            saveChatOutbox({
              ...outboxRecord,
              status: "failed",
              terminal: sawTerminalError,
              error:
                terminalErrorMessage ||
                "Ответ завершился без подтверждения доставки",
            });
            dispatch({ type: "MARK_DELIVERY", messageId, delivery: "failed", terminal: sawTerminalError, error: terminalErrorMessage || "Ответ завершился без подтверждения доставки" });
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
          dispatch({ type: "MARK_DELIVERY", messageId, delivery: "failed", error: "Отправка остановлена до подтверждения доставки" });
          dispatch({ type: "RESET_STREAMING" });
        } else {
          saveChatOutbox({
            ...outboxRecord,
            status: "failed",
            error: "Соединение прервалось",
          });
          dispatch({ type: "MARK_DELIVERY", messageId, delivery: "failed", error: "Соединение прервалось" });
          dispatch({
            type: "SET_ERROR",
            error: "Связь прервалась. Сообщение сохранено в этом браузере. Агент мог уже принять его — «Проверить и повторить» проверит прежнюю отправку.",
          });
          dispatch({ type: "RESET_STREAMING" });
        }
      } finally {
        void refreshChatRuns();
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
    [state.messages, state.sessionId, profile, selectionKey]
  );

  // Page and profile switches detach only the reader. A fresh visit resolves
  // the session again; a remembered browser flag never proves agent liveness.
  const sessionRef = useRef(state.sessionId);
  const selectionLoadRef = useRef<Promise<void> | null>(null);
  useEffect(() => { sessionRef.current = state.sessionId; }, [state.sessionId]);
  useEffect(() => {
    const selection = readChatSelection(selectionKey);
    const id = selection === undefined ? loadChatOutbox(profile ?? "")?.sessionId : selection;
    // The resume effect runs in this same commit, before RESET/LOAD_SESSION
    // is rendered. Never let it refresh the previous profile's conversation.
    sessionRef.current = id ?? null;
    if (id) {
      const pending = loadSession(id).finally(() => {
        if (selectionLoadRef.current === pending) selectionLoadRef.current = null;
      });
      selectionLoadRef.current = pending;
      void pending;
    } else reset();
  }, [loadSession, profile, selectionKey, reset]);
  useEffect(() => {
    if (!active) return;
    // Возврат к вкладке — не повод показывать чат заново. Проверяем в фоне:
    // новые сообщения и состояние хода доезжают поверх уже показанной ленты.
    const reconcile = async (forceHistory = false) => {
      if (document.hidden) return;
      void refreshChatRuns();
      const sessionId = sessionRef.current;
      if (!sessionId) return;
      // The profile/selection effect in this same commit already started the
      // authoritative load.  Do not abort it with a duplicate activation
      // load; this guard is only for the current in-flight promise and cannot
      // mask a reader that became stuck on an earlier visit.
      if (forceHistory && selectionLoadRef.current) {
        await selectionLoadRef.current;
        return;
      }
      if (forceHistory || !streamingRef.current) {
        await loadSession(sessionId, { background: true });
        return;
      }
      // A browser can kill a hidden SSE reader without running our catch/
      // finally path.  The ref then still says "streaming" forever.  Durable
      // server state is authoritative: once no run is busy, reconnect the
      // history even while that stale local lock remains set.
      try {
        const runs = await getChatRuns(profile ?? "", sessionId);
        if (!runs.some(isRunBusy)) await loadSession(sessionId, { background: true });
      } catch {
        // refreshChatRuns exposes reachability; keep the visible transcript.
      }
    };
    void reconcile(true);
    const resume = () => { void reconcile(false); };
    window.addEventListener("online", resume);
    window.addEventListener("focus", resume);
    document.addEventListener("visibilitychange", resume);
    const syncOutbox = (event: StorageEvent) => {
      if (isChatOutboxStorageKey(event.key)) void reconcile(false);
    };
    window.addEventListener("storage", syncOutbox);
    return () => {
      window.removeEventListener("online", resume);
      window.removeEventListener("focus", resume);
      document.removeEventListener("visibilitychange", resume);
      window.removeEventListener("storage", syncOutbox);
    };
  }, [active, loadSession, profile]);
  useEffect(() => {
    if (!active) return;
    markChatViewed(profile ?? "", state.sessionId);
    return () => {
      const viewed = $viewedChat.get();
      if (viewed?.profile === (profile ?? "") && viewed.sessionId === state.sessionId) $viewedChat.set(null);
    };
  }, [active, profile, state.sessionId]);

  const retryPending = useCallback(async (target?: PendingMessageTarget): Promise<boolean> => {
    const sessionId = target?.sessionId ?? state.sessionId;
    if (streamingRef.current) {
      dispatch({ type: "STOP_FAILED", error: "Дождитесь завершения текущего ответа или обновления переписки." });
      return false;
    }
    if (!sessionId) return false;
    const records = loadChatOutboxRecords(profile ?? "", sessionId);
    const pending = target
      ? records.find((record) => record.messageId === target.messageId)
      : records[0];
    if (!pending) return false;
    // Cross-chat banners navigate first. Never POST with the currently open
    // chat's history captured in send()'s closure.
    if (sessionId !== state.sessionId) {
      await loadSession(sessionId);
      return false;
    }
    return await send(pending.text, pending.attachments, pending);
  }, [send, loadSession, profile, state.sessionId]);

  const discardPending = useCallback((target?: PendingMessageTarget) => {
    if (streamingRef.current) {
      dispatch({ type: "STOP_FAILED", error: "Дождитесь завершения текущего ответа или обновления переписки. Сохранённое сообщение пока не убрано." });
      return;
    }
    const sessionId = target?.sessionId ?? state.sessionId;
    if (!sessionId) return;
    const records = loadChatOutboxRecords(profile ?? "", sessionId);
    const pending = target
      ? records.find((record) => record.messageId === target.messageId)
      : records[0];
    if (!pending) return;
    clearChatOutbox(pending.messageId, profile ?? "");
    dispatch({ type: "DISCARD_PENDING", messageId: pending.messageId });
  }, [profile, state.sessionId]);

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
        const request = state.approvals.find(
          (entry) => entry.request.request_id === requestId,
        )?.request;
        let note: string | undefined;
        if (request?.decision_kind === "outbound_message") {
          if (choice === "deny" || result.effectStatus === "denied") {
            note = "Сообщение не отправлено.";
          } else if (result.effectStatus === "succeeded") {
            note = "Сообщение отправлено один раз.";
          } else if (result.effectStatus === "unknown") {
            note = "Исход отправки не подтверждён. Korra не повторяет её вслепую.";
          } else if (result.effectStatus === "failed") {
            note = "Отправка не выполнена: точный черновик или аккаунт изменился.";
          } else {
            note = "Решение принято; состояние отправки сохранено на сервере.";
          }
        }
        dispatch({ type: "APPROVAL_SETTLED", requestId, decision: choice, ...(note ? { note } : {}) });
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
    [state.sessionId, state.approvals, profile],
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
    isLoading,
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
