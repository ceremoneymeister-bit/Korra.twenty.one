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

/** Korra-specific tool progress event */
export interface SSEToolProgressData {
  tool: string;
  toolCallId: string;
  status: "running" | "completed" | "error";
  emoji?: string;
  label?: string;
}

/** Discriminated union for SSE parser output */
export type SSEEvent =
  | { type: "chunk"; data: SSEChatChunkData }
  | { type: "tool_progress"; data: SSEToolProgressData }
  | { type: "done" };
