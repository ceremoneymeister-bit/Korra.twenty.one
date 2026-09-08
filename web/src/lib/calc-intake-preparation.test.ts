// @vitest-environment jsdom

import { afterEach, expect, it, vi } from "vitest";
import { getIntakePreparation, saveIntakeAnswers } from "./calc-intake-preparation";

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.__HERMES_SESSION_TOKEN__;
});

it("uses the authenticated handoff endpoint and sends the bound answer revision and request id", async () => {
  window.__HERMES_SESSION_TOKEN__ = "test-session";
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ handoff_id: "intake-a" }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  await getIntakePreparation("intake/a");
  expect(fetchMock.mock.calls[0][0]).toBe("/api/calc/intake-handoffs/intake%2Fa/preparation");
  expect(fetchMock.mock.calls[0][1].headers.get("X-Hermes-Session-Token")).toBe("test-session");
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ handoff_id: "intake-a" }), { status: 200 }));
  const request = {
    snapshot_id: "snapshot-a", expected_revision: 3, request_id: "request-a",
    answers: { scope: "whole" as const, scope_note: "", more_documents: "unknown" as const, quantity_source: "in_documents" as const, notes: "Заказ целиком" },
  };
  await saveIntakeAnswers("intake/a", request);
  const [url, init] = fetchMock.mock.calls[1];
  expect(url).toBe("/api/calc/intake-handoffs/intake%2Fa/preparation/answers");
  expect(init.method).toBe("POST");
  expect(init.credentials).toBe("include");
  expect(init.headers.get("Content-Type")).toBe("application/json");
  expect(init.headers.get("X-Hermes-Session-Token")).toBe("test-session");
  expect(JSON.parse(init.body)).toEqual(request);
});

it("preserves the HTTP conflict status for the form's explicit conflict recovery", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: "conflict" }), { status: 409 })));
  await expect(getIntakePreparation("intake-a")).rejects.toThrow(/^409:/);
});
