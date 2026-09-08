// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { IntakeAnalysisPanel } from "./IntakeAnalysisPanel";
import type { AnalysisJob, AnalysisPlan, AnalysisProposal, IntakeAnalysis } from "@/lib/calc-intake-analysis";

const mocks = vi.hoisted(() => ({ get: vi.fn(), plan: vi.fn(), action: vi.fn() }));
vi.mock("@/lib/calc-intake-analysis", () => ({ getIntakeAnalysis: mocks.get, planIntakeAnalysis: mocks.plan, actOnIntakeAnalysis: mocks.action }));

function plan(id = "handoff-a"): AnalysisPlan {
  return {
    plan_id: `plan-${id}`, handoff_id: id, snapshot_id: `snapshot-${id}`, answers_revision: 1,
    source_ids: ["src-a", "src-b"], service_files: 1, cached_documents: 1, documents_to_read: 1,
    known_pages: 2, pages_unknown: 1, can_start: true, blockers: [], intended_result: "Предварительный состав",
    limits: { max_files: 20, max_pages_per_pdf: 8, max_total_pdf_pages: 40, max_xlsx_cells: 20000 },
  };
}

function job(id = "handoff-a"): AnalysisJob {
  return {
    job_id: `job-${id}`, handoff_id: id, plan_id: `plan-${id}`, status: "running", stage: "inventory",
    summary: { sources_total: 2, sources_complete: 0, sources_failed: 0 }, rows: [], issues: [], cancellable: true, retryable: false,
  };
}

function proposal(name: string): AnalysisProposal {
  return {
    positions: [{ position_id: "p1", product_id: "product1", designation_fact_id: "f1", role: "make", quantity: { value: null, unit: null, basis: null, evidence_ids: [] } }],
    facts: [
      { fact_id: "f1", subject_id: "product1", field_key: "designation", raw_text: name, normalized_value: name, unit: null, evidence_ids: ["e1"], status: "extracted", unknown_reason: null },
      { fact_id: "f2", subject_id: "product1", field_key: "material", raw_text: "Сталь 20", normalized_value: "Сталь 20", unit: null, evidence_ids: ["e1"], status: "needs_review", unknown_reason: null },
    ],
    evidence: [{ evidence_id: "e1", locator: { kind: "pdf", page: 2 } }], relations: [],
    issues: [{ code: "quantity_unknown", question: "Сколько деталей требуется?", blocks: "calculation" }],
    quantity: { value: null, unit: null, basis: null, evidence_ids: [] },
  };
}

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
beforeEach(() => {
  mocks.get.mockReset().mockResolvedValue({ plan: null, job: null });
  mocks.plan.mockReset().mockResolvedValue(plan());
  mocks.action.mockReset().mockResolvedValue(job());
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
});
async function mount(id = "handoff-a", ready = true, revision = 1) {
  await act(async () => root.render(<IntakeAnalysisPanel handoffId={id} snapshotId={`snapshot-${id}`} answersRevision={revision} answersReady={ready} />));
}
function button(text: string) {
  return [...container.querySelectorAll("button")].find((node) => node.textContent === text)!;
}
async function click(text: string) {
  expect(button(text)).toBeDefined();
  await act(async () => button(text).click());
}

describe("analysis plan and durable job", () => {
  it("requires saved answers, prepares a bounded plan, and only starts after explicit action", async () => {
    await mount("handoff-a", false, 0);
    expect(button("Подготовить план").disabled).toBe(true);
    expect(mocks.plan).not.toHaveBeenCalled();
    await mount();
    await click("Подготовить план");
    expect(container.textContent).toContain("до 20 документов, до 8 страниц в одном PDF, до 40 страниц PDF на заказ");
    expect(container.textContent).toContain("После запуска сначала проверим объём");
    expect(mocks.action).not.toHaveBeenCalled();
    await click("Начать разбор");
    expect(mocks.action).toHaveBeenCalledWith("handoff-a", "start", expect.objectContaining({ plan_id: "plan-handoff-a", request_id: expect.any(String) }));
    expect(container.textContent).toContain("Чтение и проверка объёма документов");
  });

  it("blocks oversized plans and plans for changed answers", async () => {
    const oversized = plan();
    oversized.can_start = false;
    oversized.blockers = ["meaningful_files_limit"];
    mocks.get.mockResolvedValue({ plan: oversized, job: null });
    await mount();
    expect(button("Начать разбор").disabled).toBe(true);
    expect(container.textContent).toContain("Превышен лимит документов");
    mocks.get.mockResolvedValue({ plan: plan(), job: null });
    await mount("handoff-a", true, 2);
    expect(button("Начать разбор").disabled).toBe(true);
    expect(container.textContent).toContain("План относится к предыдущей версии");
    expect(mocks.action).not.toHaveBeenCalled();
  });

  it("replays unknown start outcomes with the same id and never starts during polling", async () => {
    mocks.get.mockResolvedValue({ plan: plan(), job: null });
    await mount();
    mocks.action.mockRejectedValueOnce(new Error("network failure"));
    await click("Начать разбор");
    expect(button("Подготовить план").disabled).toBe(true);
    await click("Повторить запрос запуска");
    expect(mocks.action.mock.calls[1]).toEqual(mocks.action.mock.calls[0]);
    expect(mocks.action).toHaveBeenCalledTimes(2);
  });

  it("refreshes 409 conflicts without silently restarting", async () => {
    mocks.get.mockResolvedValue({ plan: plan(), job: null });
    await mount();
    mocks.action.mockRejectedValueOnce(new Error("409: План устарел"));
    await click("Начать разбор");
    expect(button("Начать разбор").disabled).toBe(true);
    expect(container.textContent).toContain("подготовьте актуальный план");
    expect(mocks.action).toHaveBeenCalledTimes(1);
    await click("Подготовить план");
    expect(button("Начать разбор").disabled).toBe(false);
  });

  it("allows a newly prepared current plan while retaining the previous stale job for review", async () => {
    const oldJob = job();
    oldJob.status = "stale";
    oldJob.cancellable = false;
    const nextPlan = plan();
    nextPlan.plan_id = "plan-current";
    nextPlan.answers_revision = 2;
    mocks.get.mockResolvedValue({ plan: nextPlan, job: oldJob });
    await mount("handoff-a", true, 2);
    expect(button("Начать разбор").disabled).toBe(false);
    await act(async () => window.dispatchEvent(new Event("focus")));
    expect(button("Начать разбор").disabled).toBe(false);
    expect(mocks.action).not.toHaveBeenCalled();
  });

  it("restores progress and keeps same-named proposal ids separate by source", async () => {
    vi.useFakeTimers();
    const current = job();
    current.summary.sources_complete = 2;
    current.status = "completed";
    current.stage = "review";
    current.cancellable = false;
    current.rows = [
      { source_id: "src-a", relative_path: "Сборка.pdf", status: "complete", page_count: 2, proposal: proposal("Деталь А") },
      { source_id: "src-b", relative_path: "Исполнение.pdf", status: "complete", page_count: 2, proposal: proposal("Деталь Б") },
    ];
    mocks.get.mockResolvedValue({ plan: plan(), job: current });
    await mount();
    expect(container.querySelectorAll("table")).toHaveLength(2);
    expect(container.textContent).toContain("Деталь А");
    expect(container.textContent).toContain("Деталь Б");
    expect(container.textContent).toContain("Сталь 20");
    expect(container.textContent).toContain("стр. 2");
    expect(container.textContent).toContain("Сколько деталей требуется?");
    expect(container.textContent).toContain("ещё требуют сопоставления");
    expect(container.querySelector("progress")?.value).toBe(2);
    mocks.get.mockRejectedValueOnce(new Error("503: Сервис временно недоступен."));
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(container.querySelectorAll("table")).toHaveLength(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(mocks.action).not.toHaveBeenCalled();
  });

  it("uses server retry/cancel permissions and can replay a retry after its response was lost", async () => {
    const current = job();
    current.status = "partial";
    current.retryable = true;
    current.cancellable = false;
    mocks.get.mockResolvedValue({ plan: plan(), job: current });
    await mount();
    expect(button("Остановить разбор")).toBeUndefined();
    mocks.action.mockRejectedValueOnce(new Error("network failure"));
    await click("Повторить неудавшиеся документы");
    mocks.get.mockResolvedValue({ plan: plan(), job: job() });
    await act(async () => window.dispatchEvent(new Event("focus")));
    await click("Повторить запрос повтора");
    expect(mocks.action.mock.calls[1]).toEqual(mocks.action.mock.calls[0]);
  });

  it("shows the concrete source failure and keeps uncertain dispatch out of automatic retry", async () => {
    const current = job();
    current.status = "blocked";
    current.retryable = false;
    current.cancellable = false;
    current.issues = [{ source_id: "src-a", relative_path: "Деталь.pdf", code: "model_dispatch_unknown", next_action: "Проверьте состояние задания." }];
    mocks.get.mockResolvedValueOnce({ plan: plan(), job: current });
    await mount();
    expect(container.textContent).toContain("Деталь.pdf: Получение задания агентом пока не подтверждено");
    expect(button("Повторить неудавшиеся документы")).toBeUndefined();
    expect(mocks.action).not.toHaveBeenCalled();
  });

  it("ignores a late response from a previous handoff", async () => {
    let resolve!: (value: IntakeAnalysis) => void;
    mocks.get.mockReturnValueOnce(new Promise<IntakeAnalysis>((done) => { resolve = done; }));
    await mount();
    mocks.get.mockResolvedValueOnce({ plan: plan("handoff-b"), job: null });
    await mount("handoff-b");
    await act(async () => resolve({ plan: plan(), job: job() }));
    mocks.action.mockResolvedValueOnce(job("handoff-b"));
    await click("Начать разбор");
    expect(mocks.action.mock.calls[0][0]).toBe("handoff-b");
    expect(mocks.action.mock.calls[0][2].plan_id).toBe("plan-handoff-b");
  });
});
