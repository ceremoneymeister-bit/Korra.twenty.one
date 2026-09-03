import type { ToolEntry } from "@/components/ToolCall";

export type ChatRole = "user" | "assistant";

export interface AttachmentDisplay {
  key: string;
  name: string;
  kind: string;
  sizeLabel: string;
}

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  timestamp: number;
  toolCalls?: ToolEntry[];
  /** След размышления агента. Приходит ТОЛЬКО из истории сессии
   *  (`reasoning_content` в строке ответа) — живой поток
   *  `/api/chat/completions` размышление не передаёт: `_on_tool_start`
   *  в api_server отбрасывает всё, что начинается с `_`, включая
   *  `_thinking`. Пусто у моделей, которые reasoning не отдают. */
  reasoning?: string;
  /** Attachments as sent from this tab. History reloaded from the server has
   *  them inside the `[вложения]` block instead — the transcript renders both
   *  the same way, so a message looks identical before and after a reload. */
  attachments?: AttachmentDisplay[];
  /** Honest browser-to-agent delivery state for the durable owner outbox. */
  delivery?: "sending" | "failed" | "delivered";
  clientMessageId?: string;
}

export interface ChatSession {
  id: string;
  title: string;
  lastMessageAt: number;
}

/** OpenAI-compatible chat completion chunk */
export interface SSEChatChunkData {
  id: string;
  object: "chat.completion.chunk";
  model: string;
  choices: Array<{
    index: number;
    delta: { role?: ChatRole; content?: string };
    finish_reason: string | null;
  }>;
  usage?: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
}

/** Korra-specific tool progress event.
 *
 *  Что РЕАЛЬНО приходит по проводу (gateway/platforms/api_server.py,
 *  `_on_tool_start` / `_on_tool_complete` около строки 5216):
 *    старт    — {tool, emoji, label, toolCallId, status: "running"}
 *    финиш    — {tool, toolCallId, status: "completed"}
 *  `label` — это `build_tool_preview(name, args)`: главный аргумент вызова
 *  человеческим текстом (команда, путь, запрос). Другого описания вызова у
 *  браузера нет: ни аргументов, ни результата, ни ошибки сервер не шлёт.
 *  `status: "error"` объявлен в контракте, но api_server его не отправляет. */
export interface SSEToolProgressData {
  tool: string;
  toolCallId: string;
  status: "running" | "completed" | "error";
  emoji?: string;
  label?: string;
}

/** Строка истории сессии сверх того, что описано в `SessionMessage`.
 *
 *  Панельный маршрут `GET /api/sessions/{id}/messages`
 *  (hermes_cli/web_routers/sessions.py) отдаёт строку таблицы `messages`
 *  целиком, без проекции api_server — поэтому `reasoning_content` доезжает
 *  до браузера как есть. */
export interface SessionMessageReasoning {
  reasoning_content?: string | null;
  display_kind?: string | null;
}

/** Discriminated union for SSE parser output */
export type SSEEvent =
  | { type: "chunk"; data: SSEChatChunkData }
  | { type: "tool_progress"; data: SSEToolProgressData }
  | { type: "done" };
