import type { SSEChatChunkData, SSEEvent, SSEToolProgressData } from "./chat-types";

/**
 * Parse one SSE block (the text between two `\n\n` boundaries).
 *
 * Recognised forms:
 *   - `data: [DONE]`                              → { type: "done" }
 *   - `data: {...}`                               → { type: "chunk", data: ... }
 *   - `event: korra.tool.progress\ndata: {...}`   → { type: "tool_progress", data: ... }
 *
 * Returns null for empty blocks, malformed JSON, or unknown event types.
 * Unknown event types are silently ignored to remain forward-compatible.
 */
export function parseSSEBlock(block: string): SSEEvent | null {
  if (!block.trim()) return null;

  const lines = block.split("\n");

  // Collect field values
  let eventType: string | undefined;
  let dataLine: string | undefined;

  for (const line of lines) {
    if (line.startsWith("event:")) {
      eventType = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLine = line.slice("data:".length).trim();
    }
  }

  // No data field at all → skip
  if (dataLine === undefined) return null;

  // [DONE] sentinel (case-sensitive per spec)
  if (dataLine === "[DONE]") {
    return { type: "done" };
  }

  // Route by event type
  if (eventType === "korra.tool.progress") {
    try {
      const parsed = JSON.parse(dataLine) as SSEToolProgressData;
      return { type: "tool_progress", data: parsed };
    } catch {
      console.warn("[sse-parser] Malformed JSON in korra.tool.progress block:", dataLine);
      return null;
    }
  }

  // Default (no event type or unrecognised event type):
  // treat as chat.completion.chunk only when there is no explicit eventType
  // or the eventType is one we know about.
  if (eventType !== undefined) {
    // Unknown non-tool event type — skip, future-proof
    return null;
  }

  // Plain `data:` line — OpenAI chat chunk
  try {
    const parsed = JSON.parse(dataLine) as SSEChatChunkData;
    return { type: "chunk", data: parsed };
  } catch {
    console.warn("[sse-parser] Malformed JSON in SSE data block:", dataLine);
    return null;
  }
}

/**
 * Split a streaming accumulation buffer by `\n\n`, parse each complete block,
 * and return the parsed events plus the trailing incomplete fragment (remainder)
 * which should be prepended to the next read chunk.
 *
 * Example:
 *   buffer = 'data: {"a":1}\n\ndata: {"b":2}\n\ndata: {"c'
 *   → events: [chunk{a:1}, chunk{b:2}]
 *   → remainder: 'data: {"c'
 */
export function splitSSEBuffer(buffer: string): {
  events: SSEEvent[];
  remainder: string;
} {
  if (!buffer) return { events: [], remainder: "" };

  const parts = buffer.split("\n\n");

  // The last element is either empty (if buffer ended with \n\n) or
  // an incomplete block that we must keep for the next round.
  const remainder = parts.pop() ?? "";

  const events: SSEEvent[] = [];
  for (const part of parts) {
    const event = parseSSEBlock(part);
    if (event !== null) {
      events.push(event);
    }
  }

  return { events, remainder };
}
