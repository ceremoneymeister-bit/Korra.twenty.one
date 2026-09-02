// @vitest-environment jsdom

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { loadChatOutbox } from "@/lib/chat-outbox";
import { useChatStream, type UseChatStreamReturn } from "./useChatStream";

let container: HTMLDivElement;
let root: Root;
let current: UseChatStreamReturn;

function Probe({ onValue }: { onValue: (value: UseChatStreamReturn) => void }) {
  const value = useChatStream();
  useEffect(() => onValue(value), [onValue, value]);
  return null;
}

beforeEach(async () => {
  localStorage.clear();
  let sequence = 0;
  vi.stubGlobal("crypto", {
    randomUUID: () => `12345678-1234-4234-8234-${String(++sequence).padStart(12, "0")}`,
  });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(<Probe onValue={(value) => { current = value; }} />),
  );
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("useChatStream durable errors", () => {
  it("marks the outbox retryable after a terminal SSE error", async () => {
    const event = new TextEncoder().encode(
      'data: {"choices":[{"delta":{},"finish_reason":"error"}]}\n\n',
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(event);
              controller.close();
            },
          }),
          { status: 200, headers: { "content-type": "text/event-stream" } },
        ),
      ),
    );

    let delivered = true;
    await act(async () => {
      delivered = await current.send("Проверить доставку");
    });

    expect(delivered).toBe(false);
    expect(current.isStreaming).toBe(false);
    expect(current.error).toBe("Ответ агента завершился с ошибкой");
    expect(current.messages.find((message) => message.role === "user")?.delivery)
      .toBe("failed");
    expect(loadChatOutbox()).toMatchObject({
      status: "failed",
      text: "Проверить доставку",
    });
  });
});
