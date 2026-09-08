// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { actOnIntakeAnalysis, getIntakeAnalysis, planIntakeAnalysis } from "./calc-intake-analysis";

afterEach(() => vi.unstubAllGlobals());

it("keeps plan creation separate from explicit version-bound start and cancel", async () => {
  const fetchMock = vi.fn().mockImplementation(async () => new Response("{}", { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  await getIntakeAnalysis("intake/a");
  await planIntakeAnalysis("intake/a");
  await actOnIntakeAnalysis("intake/a", "start", { plan_id: "plan-1", request_id: "request-1" });
  await actOnIntakeAnalysis("intake/a", "cancel", { job_id: "job-1" });
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    "/api/calc/intake-handoffs/intake%2Fa/analysis",
    "/api/calc/intake-handoffs/intake%2Fa/analysis/plan",
    "/api/calc/intake-handoffs/intake%2Fa/analysis/start",
    "/api/calc/intake-handoffs/intake%2Fa/analysis/cancel",
  ]);
  expect(fetchMock.mock.calls[1][1].method).toBe("POST");
  expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({ plan_id: "plan-1", request_id: "request-1" });
  expect(JSON.parse(fetchMock.mock.calls[3][1].body)).toEqual({ job_id: "job-1" });
});
