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
