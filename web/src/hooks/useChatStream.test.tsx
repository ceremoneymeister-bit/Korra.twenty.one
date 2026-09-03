// @vitest-environment jsdom

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, type SessionMessage } from "@/lib/api";
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
  vi.restoreAllMocks();
});

/** Собрать SSE-поток из готовых блоков — ровно в том виде, в каком его
 *  отдаёт api_server и дословно пробрасывает панельный прокси. */
function sseResponse(...blocks: string[]) {
  const bytes = new TextEncoder().encode(blocks.join(""));
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(bytes);
        controller.close();
      },
    }),
    { status: 200, headers: { "content-type": "text/event-stream" } },
  );
}

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

describe("useChatStream — вызовы инструментов из живого потока", () => {
  it("сохраняет превью аргумента из label и не теряет его на завершении", async () => {
    // Ровно то, что шлёт api_server: на старте — label с превью главного
    // аргумента, на финише — только id и статус, без label.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse(
          "event: hermes.tool.progress\n" +
            'data: {"tool":"terminal","emoji":"⚡","label":"docker ps -a",' +
            '"toolCallId":"call-1","status":"running"}\n\n',
          "event: hermes.tool.progress\n" +
            'data: {"tool":"terminal","toolCallId":"call-1","status":"completed"}\n\n',
          'data: {"choices":[{"delta":{"content":"Готово"},"finish_reason":null}]}\n\n',
          "data: [DONE]\n\n",
        ),
      ),
    );

    await act(async () => {
      await current.send("Покажи контейнеры");
    });

    const answer = current.messages[current.messages.length - 1];
    expect(answer.role).toBe("assistant");
    expect(answer.content).toBe("Готово");
    expect(answer.toolCalls).toHaveLength(1);
    expect(answer.toolCalls?.[0]).toMatchObject({
      name: "terminal",
      status: "done",
      context: "docker ps -a",
    });
    expect(answer.toolCalls?.[0].completedAt).toBeGreaterThan(0);
  });
});

describe("useChatStream — история сессии", () => {
  it("сворачивает цепочку ход/инструмент в один ответ с трассой и размышлением", async () => {
    const history = [
      { role: "user", content: "Посчитай раскрой" },
      {
        role: "assistant",
        content: "Смотрю остатки.",
        reasoning_content: "Сначала проверю склад, потом посчитаю.",
        tool_calls: [
          {
            id: "call-1",
            function: { name: "terminal", arguments: '{"command":"ls /opt/data"}' },
          },
        ],
      },
      {
        role: "tool",
        tool_call_id: "call-1",
        tool_name: "terminal",
        content: '{"output":"blank.csv\\nsheets.csv"}',
      },
      { role: "assistant", content: "Готово: два файла." },
    ] as unknown as SessionMessage[];

    vi.spyOn(api, "getSessionMessages").mockResolvedValue({
      session_id: "s-1",
      messages: history,
    });

    await act(async () => {
      await current.loadSession("s-1");
    });

    expect(current.messages).toHaveLength(2);
    const [question, answer] = current.messages;
    expect(question.role).toBe("user");
    // Текст обоих ответов агента живёт в одном пузыре — как в живом потоке.
    expect(answer.content).toBe("Смотрю остатки.\n\nГотово: два файла.");
    expect(answer.reasoning).toBe("Сначала проверю склад, потом посчитаю.");
    expect(answer.toolCalls).toHaveLength(1);
    expect(answer.toolCalls?.[0]).toMatchObject({
      name: "terminal",
      status: "done",
      context: "ls /opt/data",
      summary: "blank.csv\nsheets.csv",
    });
  });

  it("не приписывает длительность историческим вызовам", async () => {
    vi.spyOn(api, "getSessionMessages").mockResolvedValue({
      session_id: "s-2",
      messages: [
        { role: "user", content: "?" },
        {
          role: "assistant",
          content: "",
          tool_calls: [
            { id: "c", function: { name: "read_file", arguments: '{"path":"/tmp/a"}' } },
          ],
        },
        { role: "assistant", content: "Прочитал." },
      ] as unknown as SessionMessage[],
    });

    await act(async () => {
      await current.loadSession("s-2");
    });

    const answer = current.messages[current.messages.length - 1];
    expect(answer.toolCalls?.[0].startedAt).toBe(0);
    expect(answer.toolCalls?.[0].completedAt).toBeUndefined();
  });
});

describe("useChatStream — одобрение опасных команд", () => {
  /** Ровно тот блок, который пишет api_server, когда ход агента упёрся в
   *  политику одобрения и встал ждать ответа человека. */
  const APPROVAL_BLOCK =
    "event: hermes.approval.request\n" +
    'data: {"event":"approval.request","request_id":"req-1","session_id":"s-live",' +
    '"command":"rm -rf /opt/data/tmp","description":"Рекурсивное удаление",' +
    '"pattern_key":"rm_rf","choices":["once","session","always","deny"]}\n\n';

  /** Поток, который не закрывается сам: ход агента стоит на вопросе. */
  function openStream() {
    const encoder = new TextEncoder();
    let push!: (chunk: string) => void;
    let close!: () => void;
    const body = new ReadableStream({
      start(controller) {
        push = (chunk) => controller.enqueue(encoder.encode(chunk));
        close = () => controller.close();
      },
    });
    return { body, push: (chunk: string) => push(chunk), close: () => close() };
  }

  function stubFetch(
    stream: ReadableStream,
    approvalResponse: () => Response,
    calls: Array<{ url: string; method: string; body: string }>,
  ) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
        const href = String(url);
        const method = (init?.method ?? "GET").toUpperCase();
        calls.push({ url: href, method, body: String(init?.body ?? "") });
        if (href.includes("/api/chat/approval") && method === "POST") {
          return approvalResponse();
        }
        if (href.includes("/api/chat/approvals")) {
          return new Response(JSON.stringify({ data: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          });
        }
        return new Response(stream, {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        });
      }),
    );
  }

  /** Дать читателю потока провернуть очередь микрозадач. */
  async function settle() {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  it("живой запрос становится карточкой, а решение уходит своим маршрутом", async () => {
    const stream = openStream();
    const calls: Array<{ url: string; method: string; body: string }> = [];
    stubFetch(
      stream.body,
      () =>
        new Response(JSON.stringify({ resolved: 1 }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      calls,
    );

    let sending!: Promise<boolean>;
    await act(async () => {
      sending = current.send("Почисти временные файлы");
    });
    await settle();

    await act(async () => {
      stream.push(APPROVAL_BLOCK);
    });
    await settle();

    expect(current.approvals).toHaveLength(1);
    expect(current.approvals[0]).toMatchObject({
      status: "pending",
      request: {
        request_id: "req-1",
        command: "rm -rf /opt/data/tmp",
        description: "Рекурсивное удаление",
      },
    });
    // Варианты не выдумываются в браузере: они приехали от движка.
    expect(current.approvals[0].request.choices).toEqual([
      "once",
      "session",
      "always",
      "deny",
    ]);

    let ok = false;
    await act(async () => {
      ok = await current.resolveApproval("req-1", "once");
    });

    expect(ok).toBe(true);
    const decision = calls.find(
      (call) => call.method === "POST" && call.url.includes("/api/chat/approval"),
    );
    expect(decision).toBeDefined();
    expect(JSON.parse(decision!.body)).toEqual({
      session_id: current.sessionId,
      request_id: "req-1",
      choice: "once",
    });
    expect(current.approvals[0]).toMatchObject({
      status: "settled",
      decision: "once",
    });

    // Ход продолжился и закончился штатно — карточка остаётся в переписке.
    await act(async () => {
      stream.push('data: {"choices":[{"delta":{"content":"Удалил."},"finish_reason":null}]}\n\n');
      stream.push("data: [DONE]\n\n");
      stream.close();
      await sending;
    });
    await settle();

    expect(current.approvals[0].status).toBe("settled");
    expect(current.messages[current.messages.length - 1].content).toBe("Удалил.");
  });

  it("опоздавшее решение гасит карточку и говорит, почему", async () => {
    const stream = openStream();
    const calls: Array<{ url: string; method: string; body: string }> = [];
    stubFetch(
      stream.body,
      () =>
        new Response(
          JSON.stringify({ error: { code: "approval_not_pending" } }),
          { status: 409, headers: { "content-type": "application/json" } },
        ),
      calls,
    );

    await act(async () => {
      void current.send("Почисти временные файлы");
    });
    await settle();
    await act(async () => {
      stream.push(APPROVAL_BLOCK);
    });
    await settle();

    let ok = true;
    await act(async () => {
      ok = await current.resolveApproval("req-1", "deny");
    });

    expect(ok).toBe(false);
    expect(current.approvals[0]).toMatchObject({ status: "expired" });
    expect(current.approvals[0].error).toContain("больше не ждёт ответа");

    await act(async () => {
      stream.close();
    });
    await settle();
  });

  it("после перезагрузки страницы нерешённый вопрос восстанавливается опросом", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: RequestInfo | URL) => {
        if (String(url).includes("/api/chat/approvals")) {
          return new Response(
            JSON.stringify({
              object: "list",
              session_id: "s-restored",
              data: [
                {
                  request_id: "req-restored",
                  command: "shutdown -h now",
                  description: "Выключение машины",
                  choices: ["once", "deny"],
                },
              ],
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          );
        }
        return new Response("{}", { status: 200 });
      }),
    );

    vi.spyOn(api, "getSessionMessages").mockResolvedValue({
      session_id: "s-restored",
      messages: [{ role: "user", content: "Выключи стенд" }] as unknown as SessionMessage[],
    });

    await act(async () => {
      await current.loadSession("s-restored");
    });
    await settle();

    expect(current.approvals).toHaveLength(1);
    expect(current.approvals[0]).toMatchObject({
      status: "pending",
      request: { request_id: "req-restored", command: "shutdown -h now" },
    });
    // Живого потока нет — про исход надо сказать честно.
    expect(current.approvals[0].request.choices).toEqual(["once", "deny"]);
  });
});

describe("useChatStream — хозяин очереди вопросов остаётся на сервере", () => {
  it("вопрос, живой на сервере, возвращается в работу после конца потока", async () => {
    const live = {
      request_id: "req-live",
      command: "rm -rf /opt/data/tmp",
      description: "Рекурсивное удаление",
      choices: ["once", "deny"],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: RequestInfo | URL) => {
        if (String(url).includes("/api/chat/approvals")) {
          return new Response(JSON.stringify({ data: [live] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          });
        }
        const bytes = new TextEncoder().encode(
          "event: hermes.approval.request\n" +
            `data: ${JSON.stringify(live)}\n\n` +
            "data: [DONE]\n\n",
        );
        return new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(bytes);
              controller.close();
            },
          }),
          { status: 200, headers: { "content-type": "text/event-stream" } },
        );
      }),
    );

    // Поток кончился, а ответа на вопрос не было: сам по себе конец потока —
    // повод погасить карточку, потому что обычно вместе с ходом снимается и
    // слушатель одобрений.
    await act(async () => {
      await current.send("Почисти временные файлы");
    });
    expect(current.approvals).toHaveLength(1);

    // Но правду знает сервер: он всё ещё числит вопрос нерешённым, и опрос
    // возвращает карточку в работу вместо мёртвой кнопки.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(current.approvals[0].status).toBe("pending");
    expect(current.approvals[0].request.request_id).toBe("req-live");
  });
});
