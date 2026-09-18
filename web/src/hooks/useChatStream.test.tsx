// @vitest-environment jsdom

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, type SessionMessage } from "@/lib/api";
import { chatViewKey, writeChatSelection } from "@/lib/chat-view-state";
import { loadChatOutbox, loadChatOutboxRecords, saveChatOutbox } from "@/lib/chat-outbox";
import { useChatStream, type UseChatStreamReturn } from "./useChatStream";

vi.mock("@/lib/chat-runs", async importOriginal => ({
  ...await importOriginal<typeof import("@/lib/chat-runs")>(),
  getChatRuns: vi.fn(async () => []),
  refreshChatRuns: vi.fn(async () => {}),
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
let current: UseChatStreamReturn;
/** Каждое состояние, которое лента реально успела показать. Мигание видно
 *  только здесь: к концу загрузки сообщения снова на месте. */
let shown: UseChatStreamReturn[] = [];

function Probe({ onValue, profile, active = true }: { onValue: (value: UseChatStreamReturn) => void; profile?: string; active?: boolean }) {
  const value = useChatStream({ profile, active });
  useEffect(() => onValue(value), [onValue, value]);
  return null;
}

beforeEach(async () => {
  localStorage.clear();
  sessionStorage.clear();
  shown = [];
  let sequence = 0;
  vi.stubGlobal("crypto", {
    randomUUID: () => `12345678-1234-4234-8234-${String(++sequence).padStart(12, "0")}`,
  });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(<Probe onValue={(value) => { current = value; shown.push(value); }} />),
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

  it("показывает причину отказа из поля error финального чанка", async () => {
    // Свежий контур без ключа: движок кладёт причину в `error`, а delta
    // финального чанка остаётся пустой. Общая фраза «завершился с ошибкой»
    // здесь врёт — человеку нужно знать, что не введён ключ.
    const event = new TextEncoder().encode(
      'data: {"choices":[{"delta":{},"finish_reason":"error"}],' +
        '"error":{"message":"Провайдер ответа не настроен: добавьте ключ в разделе «Ключи».",' +
        '"type":"agent_error"}}\n\n',
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

    await act(async () => {
      await current.send("Привет! Кто ты?");
    });

    expect(current.error).toBe(
      "Провайдер ответа не настроен: добавьте ключ в разделе «Ключи».",
    );
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
    // Ход стоит на вопросе, но сообщение агент давно читает: подпись
    // «Отправляется…» под ним обязана погаснуть на первом же событии потока,
    // а не ждать конца хода, который на одобрении длится минутами.
    expect(
      current.messages.find((message) => message.role === "user")?.delivery,
    ).toBe("delivered");
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

  it("после F5 восстанавливает ожидающую отправку и два компактных исхода", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: RequestInfo | URL) => {
        if (String(url).includes("/api/chat/approvals")) {
          return Response.json({
            data: [
              {
                request_id: "effect_pending",
                decision_kind: "outbound_message",
                effect_status: "pending",
                source_session_id: "s-effects",
                command: "Кому: telegram:3\n\nТретий черновик",
                choices: ["once", "deny"],
              },
              {
                request_id: "effect_sent",
                decision_kind: "outbound_message",
                effect_status: "succeeded",
                source_session_id: "s-effects",
                command: "Кому: email:1\n\nПервый черновик",
                choices: ["once", "deny"],
              },
              {
                request_id: "effect_denied",
                decision_kind: "outbound_message",
                effect_status: "denied",
                source_session_id: "s-effects",
                command: "Кому: slack:2\n\nВторой черновик",
                choices: ["once", "deny"],
              },
            ],
          });
        }
        return Response.json({});
      }),
    );
    vi.spyOn(api, "getSessionMessages").mockResolvedValue({
      session_id: "s-effects",
      messages: [{ role: "user", content: "Подготовь отправки" }] as unknown as SessionMessage[],
    });

    await act(async () => current.loadSession("s-effects"));
    await settle();

    expect(current.approvals).toHaveLength(3);
    expect(current.approvals.map((entry) => [entry.request.request_id, entry.status, entry.decision])).toEqual([
      ["effect_pending", "pending", undefined],
      ["effect_sent", "settled", "once"],
      ["effect_denied", "settled", "deny"],
    ]);
    expect(current.approvals[1].note).toBe("Сообщение отправлено один раз.");
    expect(current.approvals[2].note).toBe("Сообщение не отправлено.");
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

describe("переключение сессий", () => {
  it("запоздавшая история не заменяет выбранный чат", async () => {
    let finishOld!: (value: { session_id: string; messages: SessionMessage[] }) => void;
    vi.spyOn(api, "getSessionMessages").mockImplementation(id => id === "old"
      ? new Promise(resolve => { finishOld = resolve; })
      : Promise.resolve({ session_id: id, messages: [{ role: "user", content: "Новый чат" }] as SessionMessage[] }));
    let old!: Promise<void>;
    await act(async () => { old = current.loadSession("old"); });
    await act(async () => { await current.loadSession("new"); });
    await act(async () => {
      finishOld({ session_id: "old", messages: [{ role: "user", content: "Старый чат" }] as SessionMessage[] });
      await old;
    });
    expect(current.sessionId).toBe("new");
    expect(current.messages[0].content).toBe("Новый чат");
  });
});

describe("восстановление серверного хода", () => {
  it("готовый ответ из журнала заменяет сохранённую копию без дублей", async () => {
    const { getChatRuns } = await import("@/lib/chat-runs");
    vi.mocked(getChatRuns).mockResolvedValueOnce([{
      message_id: "server-message-123456", session_id: "s-replay", profile: "",
      status: "completed", updated_at: 123, history_count: 2,
      user_message: { role: "user", content: "Повторный вопрос" },
    }]);
    vi.spyOn(api, "getSessionMessages").mockResolvedValue({ session_id: "s-replay", messages: [
      { role: "user", content: "Первый вопрос" }, { role: "assistant", content: "Первый ответ" },
      { role: "user", content: "Повторный вопрос" }, { role: "assistant", content: "Второй ответ" },
    ] as SessionMessage[] });
    const requests: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      requests.push(`${init?.method ?? "GET"} ${url}`);
      if (url.includes("/stream")) return sseResponse('data: {"choices":[{"delta":{"content":"Второй ответ"}}]}\n\n', "data: [DONE]\n\n");
      return new Response(JSON.stringify({ approvals: [] }), { headers: { "content-type": "application/json" } });
    }));
    await act(async () => { await current.loadSession("s-replay"); });
    expect(current.messages.map(message => message.content)).toEqual(["Первый вопрос", "Первый ответ", "Повторный вопрос", "Второй ответ"]);
    expect(current.isStreaming).toBe(false);
    expect(requests.some(request => request.startsWith("POST"))).toBe(false);
  });

  it("возврат подключается к активному потоку, а уход не отменяет ход", async () => {
    const { getChatRuns } = await import("@/lib/chat-runs");
    vi.mocked(getChatRuns).mockResolvedValueOnce([{
      message_id: "server-live-123456789", session_id: "s-live-replay", profile: "",
      status: "running", updated_at: 123, history_count: 0,
      user_message: { role: "user", content: "Жду ответ" },
    }]);
    vi.spyOn(api, "getSessionMessages").mockResolvedValue({ session_id: "s-live-replay", messages: [] });
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>({ start(value) { controller = value; }, cancel() { cancelled = true; } });
    const fetcher = vi.fn(async (url: string) => url.includes("/stream") ? new Response(stream) : new Response(JSON.stringify({ approvals: [] })));
    vi.stubGlobal("fetch", fetcher);
    let loading!: Promise<void>;
    await act(async () => { loading = current.loadSession("s-live-replay"); });
    expect(current.isStreaming).toBe(true);
    await act(async () => { controller.enqueue(new TextEncoder().encode('data: {"choices":[{"delta":{"content":"Начало"}}]}\n\n')); });
    expect(current.messages.at(-1)?.content).toBe("Начало");
    await act(async () => { current.reset(); });
    await act(async () => {
      controller.enqueue(new TextEncoder().encode('data: {"choices":[{"delta":{"content":"Конец"}}]}\n\ndata: [DONE]\n\n'));
      await loading;
    });
    expect(cancelled).toBe(true);
    expect(current.messages).toEqual([]);
    expect(fetcher.mock.calls.every(([url]) => !url.includes("/cancel"))).toBe(true);
  });
});


describe("возврат к уже открытому чату", () => {
  beforeEach(async () => {
    const { getChatRuns } = await import("@/lib/chat-runs");
    vi.mocked(getChatRuns).mockReset().mockResolvedValue([]);
  });

  /** История отвечает не мгновенно — как настоящая сеть. Без этого пустое
   *  состояние успевало бы схлопнуться в один коммит и мигание, которое
   *  видит человек, из теста бы исчезло. */
  function slowHistory(sessionId: string, messages: unknown[]) {
    return vi.spyOn(api, "getSessionMessages").mockImplementation(async () => {
      await new Promise(resolve => setTimeout(resolve, 1));
      return { session_id: sessionId, messages: messages as SessionMessage[] };
    });
  }

  /** Уход на соседнюю вкладку и возврат обратно. */
  async function returnToTab() {
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      await new Promise(resolve => setTimeout(resolve, 5));
    });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)); });
  }

  const PHOTO =
    "Вот файл\n\n[вложения]\n1. фото.png · png · 12 КБ · читать: read_file\n/opt/data/workspace/фото.png";

  it("не очищает ленту и не пересобирает пузыри", async () => {
    slowHistory("s-focus", [
      { role: "user", content: PHOTO },
      { role: "assistant", content: "Посмотрела.\nMEDIA:/opt/data/workspace/ответ.png" },
    ]);
    await act(async () => { await current.loadSession("s-focus"); });
    const before = current.messages;
    expect(before).toHaveLength(2);

    shown.length = 0;
    await returnToTab();
    await returnToTab();

    // Ни одного состояния с пустой лентой: человек не видит «прогрузку заново».
    expect(shown.map(value => value.messages.length).filter(length => length === 0)).toEqual([]);
    // Те же самые объекты сообщений — React не перемонтирует пузыри, карточки
    // вложений остаются на месте вместе с уже скачанными превью.
    expect(current.messages).toBe(before);
    expect(current.sessionId).toBe("s-focus");
  });

  it("не перечитывает журнал завершённого хода при каждом возврате", async () => {
    const { getChatRuns } = await import("@/lib/chat-runs");
    vi.mocked(getChatRuns).mockResolvedValue([{
      message_id: "run-done-0001", session_id: "s-done", profile: "",
      status: "completed", updated_at: 123, history_count: 2,
      user_message: { role: "user", content: "Повторный вопрос" },
    }]);
    slowHistory("s-done", [
      { role: "user", content: "Первый вопрос" }, { role: "assistant", content: "Первый ответ" },
      { role: "user", content: "Повторный вопрос" }, { role: "assistant", content: "Второй ответ" },
    ]);
    const requests: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      requests.push(String(url));
      if (String(url).includes("/stream")) {
        return sseResponse('data: {"choices":[{"delta":{"content":"Второй ответ"}}]}\n\n', "data: [DONE]\n\n");
      }
      return new Response(JSON.stringify({ data: [] }), { headers: { "content-type": "application/json" } });
    }));

    await act(async () => { await current.loadSession("s-done"); });
    // Первичная загрузка повторяет ход из журнала — это и есть durable replay.
    expect(requests.filter(url => url.includes("/stream"))).toHaveLength(1);
    const before = current.messages;

    requests.length = 0;
    await returnToTab();
    await returnToTab();
    await returnToTab();

    // Ход уже закончен и целиком лежит в истории: перечитывать нечего.
    expect(requests.filter(url => url.includes("/stream"))).toHaveLength(0);
    expect(current.messages.map(message => message.content)).toEqual([
      "Первый вопрос", "Первый ответ", "Повторный вопрос", "Второй ответ",
    ]);
    expect(current.messages[0]).toBe(before[0]);
    expect(current.isStreaming).toBe(false);
  });

  it("подключается к работающему ходу, не убирая прежнюю переписку", async () => {
    const { getChatRuns } = await import("@/lib/chat-runs");
    vi.mocked(getChatRuns).mockResolvedValue([{
      message_id: "run-live-0001", session_id: "s-live", profile: "",
      status: "running", updated_at: 123, history_count: 2,
      user_message: { role: "user", content: "Второй вопрос" },
    }]);
    slowHistory("s-live", [
      { role: "user", content: "Первый вопрос" }, { role: "assistant", content: "Первый ответ" },
    ]);
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (String(url).includes("/stream")) {
        return sseResponse('data: {"choices":[{"delta":{"content":"Ответ"}}]}\n\n', "data: [DONE]\n\n");
      }
      return new Response(JSON.stringify({ data: [] }), { headers: { "content-type": "application/json" } });
    }));

    await act(async () => { await current.loadSession("s-live"); });
    const before = current.messages;
    expect(before.map(message => message.content)).toEqual([
      "Первый вопрос", "Первый ответ", "Второй вопрос", "Ответ",
    ]);

    shown.length = 0;
    await returnToTab();

    expect(shown.map(value => value.messages.length).filter(length => length === 0)).toEqual([]);
    // Ответ остаётся ровно один — реплей не дописывает его во второй раз.
    expect(current.messages.map(message => message.content)).toEqual([
      "Первый вопрос", "Первый ответ", "Второй вопрос", "Ответ",
    ]);
    // Прежняя переписка та же самая, перерисовывается только текущий ход.
    expect(current.messages[0]).toBe(before[0]);
    expect(current.messages[1]).toBe(before[1]);
  });

  it("сохраняет сообщение без ответа, которого ещё нет в истории", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("не принято", { status: 500 })));
    slowHistory("12345678-1234-4234-8234-000000000001", []);
    await act(async () => { await current.send("Посчитай смету"); });
    const failed = current.messages.find(message => message.delivery === "failed");
    expect(failed?.content).toBe("Посчитай смету");

    slowHistory(current.sessionId!, []);
    await returnToTab();

    expect(current.messages.find(message => message.delivery === "failed")?.content)
      .toBe("Посчитай смету");
    expect(loadChatOutbox()).toMatchObject({ status: "failed" });
  });

  it("переход в другой чат не смешивает историю двух сессий", async () => {
    slowHistory("s-one", [{ role: "user", content: "Первая сессия" }]);
    await act(async () => { await current.loadSession("s-one"); });
    expect(current.messages[0].content).toBe("Первая сессия");

    // Возврат во вкладку и сразу выбор соседнего чата того же агента:
    // запоздавший ответ фонового освежения не должен показаться в новом чате.
    slowHistory("s-two", [{ role: "user", content: "Вторая сессия" }]);
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      await current.loadSession("s-two");
    });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });

    expect(current.sessionId).toBe("s-two");
    expect(current.messages.map(message => message.content)).toEqual(["Вторая сессия"]);
  });
});

it("после возврата отказ сервера не превращается в молчание", async () => {
  const { getChatRuns } = await import("@/lib/chat-runs");
  vi.mocked(getChatRuns).mockResolvedValueOnce([{
    message_id: "server-failed-123456", session_id: "failed-session", profile: "",
    status: "failed", updated_at: 123, history_count: 0,
    user_message: { role: "user", content: "Вопрос" },
  }]);
  vi.spyOn(api, "getSessionMessages").mockResolvedValue({ session_id: "failed-session", messages: [{ role: "user", content: "Вопрос" }] as SessionMessage[] });
  await act(async () => { await current.loadSession("failed-session"); });
  expect(current.error).toContain("Предыдущая попытка завершилась с ошибкой");
  expect(current.isStreaming).toBe(false);
  expect(current.messages[0].content).toBe("Вопрос");
});

describe("K21-105 selected conversation recovery", () => {
  async function reopenPage() {
    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(<Probe onValue={value => { current = value; shown.push(value); }} />));
  }

  it("сохраняет выбранный чат после закрытия вкладки и нового входа", async () => {
    vi.spyOn(api, "getSessionMessages").mockResolvedValue({
      session_id: "chosen", messages: [{ role: "user", content: "Моя выбранная беседа" }] as SessionMessage[],
    });
    await act(async () => { await current.loadSession("chosen"); });
    sessionStorage.clear(); // A new tab has no previous tab's sessionStorage.
    await reopenPage();
    expect(current.sessionId).toBe("chosen");
    expect(current.messages[0]?.content).toBe("Моя выбранная беседа");
  });

  it("Новый чат после F5 не подменяется старой недоставленной репликой", async () => {
    vi.spyOn(api, "getSessionMessages").mockResolvedValue({ session_id: "old", messages: [] });
    saveChatOutbox({
      messageId: "undelivered-message-123456", sessionId: "old", profile: "",
      text: "Не потерять этот черновик", attachments: [], createdAt: 123, status: "failed",
    });
    await act(async () => { await current.loadSession("old"); });
    await act(async () => current.reset());
    await reopenPage();
    expect(current.sessionId).toBeNull();
    expect(current.messages).toEqual([]);
    expect(loadChatOutbox("", "old")?.text).toBe("Не потерять этот черновик");
  });

  it("не создаёт новый чат, пока выбранная история ещё загружается", async () => {
    const history = vi.spyOn(api, "getSessionMessages").mockResolvedValue({ session_id: "chosen", messages: [] });
    await act(async () => { await current.loadSession("chosen"); });
    let finish!: (value: { session_id: string; messages: SessionMessage[] }) => void;
    history.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const post = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () => sseResponse(
      'data: {"choices":[{"delta":{"content":"Продолжаем"},"finish_reason":"stop"}]}\n\n',
      'data: [DONE]\n\n',
    ));
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) =>
      url.includes("/chat/completions") ? post(url, init) : Promise.resolve(new Response('{"approvals":[]}')),
    );
    await reopenPage();
    await act(async () => { expect(await current.send("Не отправлять до восстановления")).toBe(false); });
    expect(post).not.toHaveBeenCalled();
    await act(async () => finish({ session_id: "chosen", messages: [{ role: "user", content: "Ранее согласованный план" }] as SessionMessage[] }));
    await act(async () => { expect(await current.send("Продолжим план")).toBe(true); });
    expect(post).toHaveBeenCalledTimes(1);
    expect(new Headers((post.mock.calls[0] as unknown as [string, RequestInit])[1].headers).get("X-Hermes-Session-Id")).toBe("chosen");
    expect(current.sessionId).toBe("chosen");
  });

  it("новый профиль без истории не наследует выбранную сессию другого агента", async () => {
    vi.spyOn(api, "getSessionMessages").mockResolvedValue({
      session_id: "first-agent-chat", messages: [{ role: "user", content: "Первый агент" }] as SessionMessage[],
    });
    await act(async () => { await current.loadSession("first-agent-chat"); });
    await act(async () => root.render(<Probe profile="another-agent" onValue={value => { current = value; }} />));
    expect(current.sessionId).toBeNull();
    expect(current.messages).toEqual([]);
  });
});

describe("K21-117 activation reconciliation", () => {
  it("refreshes durable runs and history even when the old reader ref is stuck", async () => {
    const history = vi.spyOn(api, "getSessionMessages").mockResolvedValue({
      session_id: "chosen", messages: [{ role: "user", content: "План" }] as SessionMessage[],
    });
    await act(async () => { await current.loadSession("chosen"); });
    history.mockClear();

    // The POST never settles, reproducing a browser-suspended reader whose
    // catch/finally did not get a chance to clear streamingRef.
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => undefined)));
    await act(async () => { void current.send("Продолжить в фоне"); });
    expect(current.isStreaming).toBe(true);

    const onValue = (value: UseChatStreamReturn) => { current = value; };
    await act(async () => root.render(<Probe active={false} onValue={onValue} />));
    const { refreshChatRuns } = await import("@/lib/chat-runs");
    vi.mocked(refreshChatRuns).mockClear();
    await act(async () => root.render(<Probe active onValue={onValue} />));

    expect(refreshChatRuns).toHaveBeenCalled();
    expect(history).toHaveBeenCalledWith("chosen", "default");
  });
});

describe("message-specific recovery", () => {
  const pending = (sessionId: string, createdAt: number) => ({
    messageId: `pending-message-${sessionId}-123456`, sessionId, text: `Запрос ${sessionId}`,
    attachments: [], status: "failed" as const, createdAt,
  });
  it("retry and discard target the visible message, not an older sibling chat", async () => {
    const a = pending("chat-a", 1), b = pending("chat-b", 2);
    saveChatOutbox(a); saveChatOutbox(b);
    saveChatOutbox({ ...a, profile: "neighbor" });
    vi.spyOn(api, "getSessionMessages").mockResolvedValue({ session_id: "chat-b", messages: [] });
    await act(async () => { await current.loadSession("chat-b"); });
    const fetcher = vi.fn<typeof fetch>(async () => sseResponse('data: [DONE]\n\n'));
    vi.stubGlobal("fetch", fetcher);
    await act(async () => {
      await current.retryPending({ sessionId: "chat-b", messageId: "obsolete-message" });
      current.discardPending({ sessionId: "chat-b", messageId: "obsolete-message" });
    });
    expect(fetcher).not.toHaveBeenCalled();
    expect(loadChatOutbox("", "chat-b")?.messageId).toBe(b.messageId);
    await act(async () => { await current.retryPending(); });
    expect(fetcher).toHaveBeenCalledTimes(1);
    const init = fetcher.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["X-Hermes-Session-Id"]).toBe("chat-b");
    expect((init.headers as Record<string, string>)["X-Korra-Client-Message-Id"]).toBe(b.messageId);
    expect(loadChatOutbox("", "chat-a")?.messageId).toBe(a.messageId);
    saveChatOutbox(b);
    await act(async () => current.discardPending());
    expect(loadChatOutbox("", "chat-b")).toBeNull();
    expect(loadChatOutbox("", "chat-a")?.messageId).toBe(a.messageId);
    expect(loadChatOutbox("neighbor", "chat-a")?.messageId).toBe(a.messageId);
  });
  it("foreground history restore retains the local message and attachments", async () => {
    const record = { ...pending("restore", 1), attachments: Array.from({length:6}, (_, i) => ({path:`/workspace/test-${i}.txt`,name:`test-${i}.txt`,kind:"document",size:12,reader:"text"})) };
    saveChatOutbox(record);
    vi.spyOn(api, "getSessionMessages").mockResolvedValue({ session_id:"restore", messages:[] });
    await act(async () => { await current.loadSession("restore"); });
    expect(current.messages).toEqual([expect.objectContaining({content:record.text, clientMessageId:record.messageId, delivery:"failed", attachments:expect.arrayContaining([expect.objectContaining({name:"test-5.txt"})])})]);
  });
});


it("retains the next undelivered message when the ledger still describes the previous completed answer", async () => {
  const { getChatRuns } = await import("@/lib/chat-runs");
  vi.mocked(getChatRuns).mockResolvedValueOnce([{
    message_id:"old-completed-message",session_id:"same-chat",profile:"",status:"completed",updated_at:123,history_count:0,
    user_message:{role:"user",content:"Старый вопрос"},
  }]);
  saveChatOutbox({messageId:"new-pending-message-1234",sessionId:"same-chat",text:"Новый вопрос",attachments:[],createdAt:124,status:"failed"});
  vi.spyOn(api,"getSessionMessages").mockResolvedValue({session_id:"same-chat",messages:[{role:"user",content:"Старый вопрос"},{role:"assistant",content:"Старый ответ"}] as SessionMessage[]});
  const fetcher=vi.fn<typeof fetch>(async () => Response.json({approvals:[]}));vi.stubGlobal("fetch",fetcher);
  await act(async()=>{await current.loadSession("same-chat");});
  expect(current.messages.map(message=>message.content)).toEqual(["Старый вопрос","Старый ответ","Новый вопрос"]);
  expect(current.messages[2].delivery).toBe("failed");
  expect(fetcher.mock.calls.filter(([url]) => String(url).includes("/stream") || String(url).includes("/completions"))).toHaveLength(0);
  expect(loadChatOutbox("","same-chat")?.messageId).toBe("new-pending-message-1234");
});


it("keeps a confirmed model failure and its explanation after remount", async () => {
  const explanation = "Лимит использования модели исчерпан.";
  saveChatOutbox({ messageId: "quota-message-123456", sessionId: "quota-chat", text: "Запрос", attachments: [], createdAt: 124, status: "failed", terminal: true, error: explanation });
  writeChatSelection(`${chatViewKey()}:selected`, "quota-chat");
  vi.spyOn(api, "getSessionMessages").mockResolvedValue({ session_id: "quota-chat", messages: [] });
  await act(async () => root.unmount());
  root = createRoot(container);
  await act(async () => root.render(<Probe onValue={value => { current = value; }} />));
  expect(loadChatOutbox("", "quota-chat")).toMatchObject({ terminal: true, error: explanation });
  expect(current.messages).toEqual([expect.objectContaining({ failureConfirmed: true, content: "Запрос" })]);
});

describe("K21-115 multi-message outbox", () => {
  it("does not put old failed messages into a new request or overwrite either copy", async () => {
    const requests: Array<{ messages: Array<{ role: string; content: string }> }> = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      if (!String(url).includes("/chat/completions")) return Response.json({ data: [] });
      const body = JSON.parse(String(init?.body ?? "{}")) as { messages: Array<{ role: string; content: string }> };
      requests.push(body);
      return new Response("не принято", { status: 500 });
    }));

    await act(async () => { expect(await current.send("Первый сохранённый текст")).toBe(false); });
    await act(async () => { expect(await current.send("Второй сохранённый текст")).toBe(false); });

    const records = loadChatOutboxRecords("", current.sessionId!);
    expect(records.map((item) => item.text)).toEqual([
      "Первый сохранённый текст",
      "Второй сохранённый текст",
    ]);
    expect(requests).toHaveLength(2);
    expect(requests[1].messages.map((item) => item.content)).toEqual(["Второй сохранённый текст"]);
    expect(current.messages.filter((message) => message.delivery === "failed").map((message) => message.content))
      .toEqual(["Первый сохранённый текст", "Второй сохранённый текст"]);

    await act(async () => current.discardPending({
      sessionId: current.sessionId!,
      messageId: records[1].messageId,
    }));
    expect(loadChatOutboxRecords("", current.sessionId!).map((item) => item.text))
      .toEqual(["Первый сохранённый текст"]);
  });

  it("keeps a known reset across F5, allows new text, and retries one selected ID once", async () => {
    const completions = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
      void init;
      const call = completions.mock.calls.length;
      if (call === 1) {
        return sseResponse(
          'data: {"choices":[{"delta":{},"finish_reason":"error"}],"error":' +
            '{"message":"Модель временно не принимает запросы: достигнут лимит.",' +
            '"reason":"rate_limit","resets_at":"2026-09-19T10:30:00Z"}}\n\n',
          "data: [DONE]\n\n",
        );
      }
      return sseResponse(
        'data: {"choices":[{"delta":{"content":"Готово"},"finish_reason":"stop"}]}\n\n',
        "data: [DONE]\n\n",
      );
    });
    vi.stubGlobal("fetch", (url: string | URL | Request, init?: RequestInit) =>
      String(url).includes("/chat/completions")
        ? completions(url, init)
        : Promise.resolve(Response.json({ data: [] })),
    );

    await act(async () => { expect(await current.send("Повторить после сброса")).toBe(false); });
    const failed = loadChatOutboxRecords("", current.sessionId!)[0];
    expect(failed.error).toContain("2026");

    await act(async () => { expect(await current.send("Новый независимый текст")).toBe(true); });
    expect(loadChatOutboxRecords("", current.sessionId!).map((item) => item.messageId))
      .toEqual([failed.messageId]);
    const secondBody = JSON.parse(String((completions.mock.calls[1][1] as RequestInit).body)) as {
      messages: Array<{ content: string }>;
    };
    expect(secondBody.messages.map((item) => item.content)).not.toContain("Повторить после сброса");

    // Legacy 0.21.9 could leave the local copy without the terminal reason.
    // The durable run must restore the structured cause and reset time.
    saveChatOutbox({ ...failed, error: undefined });
    const { getChatRuns } = await import("@/lib/chat-runs");
    vi.mocked(getChatRuns).mockResolvedValue([{
      message_id: failed.messageId,
      session_id: current.sessionId!,
      profile: "",
      status: "failed",
      updated_at: 123,
      history_count: 0,
      user_message: { role: "user", content: failed.text },
      failure: {
        message: "Модель временно не принимает запросы: достигнут лимит.",
        reason: "rate_limit",
        resets_at: "2026-09-19T10:30:00Z",
      },
    }]);
    vi.spyOn(api, "getSessionMessages").mockResolvedValue({ session_id: current.sessionId!, messages: [] });
    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(<Probe onValue={value => { current = value; }} />));
    const restored = current.messages.find((message) => message.clientMessageId === failed.messageId);
    expect(restored).toMatchObject({
      content: "Повторить после сброса",
      delivery: "failed",
      failureConfirmed: true,
    });
    expect(restored?.failureReason).toContain("2026");
    expect(restored?.failureReason).not.toContain("пока неизвестно");

    await act(async () => {
      const results = await Promise.all([
        current.retryPending({ sessionId: current.sessionId!, messageId: failed.messageId }),
        current.retryPending({ sessionId: current.sessionId!, messageId: failed.messageId }),
      ]);
      expect(results.sort()).toEqual([false, true]);
    });
    expect(completions).toHaveBeenCalledTimes(3);
    expect((completions.mock.calls[2][1] as RequestInit).headers).toMatchObject({
      "X-Korra-Client-Message-Id": failed.messageId,
    });
    expect(loadChatOutboxRecords("", current.sessionId!)).toEqual([]);
  });
});
