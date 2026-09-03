import { describe, expect, it } from "vitest";

import { parseSSEBlock } from "./sse-parser";

describe("parseSSEBlock", () => {
  it("accepts the upstream Hermes tool-progress event", () => {
    expect(
      parseSSEBlock(
        'event: hermes.tool.progress\ndata: {"tool_call_id":"call-1","status":"running"}',
      ),
    ).toEqual({
      type: "tool_progress",
      data: { tool_call_id: "call-1", status: "running" },
    });
  });

  it("keeps the Korra alias compatible", () => {
    expect(
      parseSSEBlock(
        'event: korra.tool.progress\ndata: {"tool_call_id":"call-2","status":"done"}',
      ),
    ).toMatchObject({ type: "tool_progress" });
  });
});

describe("parseSSEBlock — запрос одобрения команды", () => {
  it("разбирает событие, которым ход агента просит решение", () => {
    expect(
      parseSSEBlock(
        "event: hermes.approval.request\n" +
          'data: {"request_id":"req-1","command":"rm -rf /tmp/x",' +
          '"description":"Рекурсивное удаление","choices":["once","deny"]}',
      ),
    ).toEqual({
      type: "approval_request",
      data: {
        request_id: "req-1",
        command: "rm -rf /tmp/x",
        description: "Рекурсивное удаление",
        choices: ["once", "deny"],
      },
    });
  });

  it("запрос без request_id пропускает: такое решение некуда отправить", () => {
    expect(
      parseSSEBlock(
        'event: hermes.approval.request\ndata: {"command":"rm -rf /tmp/x"}',
      ),
    ).toBeNull();
  });
});
