// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { IntakePreparationPanel } from "./IntakePreparationPanel";
import type { IntakePreparation } from "@/lib/calc-intake-preparation";

const mocks = vi.hoisted(() => ({ get: vi.fn(), save: vi.fn() }));
vi.mock("@/lib/calc-intake-preparation", () => ({ getIntakePreparation: mocks.get, saveIntakeAnswers: mocks.save }));
vi.mock("./IntakeAnalysisPanel", () => ({
  IntakeAnalysisPanel: ({ answersReady, answersRevision }: { answersReady: boolean; answersRevision: number }) => <div data-analysis-ready={answersReady} data-analysis-revision={answersRevision} />,
}));

function preparation(id = "handoff-a", revision = 0): IntakePreparation {
  return {
    handoff_id: id, order_id: `order-${id}`, snapshot_id: `snapshot-${id}`,
    document_set_revision: 1, editable: true,
    summary: { files_total: 4, engineering_documents: 3, service_files: 1, cached_engineering_documents: 2, documents_without_observation: 1, unsupported_documents: 0, service_file_names: ["Thumbs.db"] },
    initial_answers: {
      revision,
      answers: { scope: "whole", scope_note: "", more_documents: "unknown", quantity_source: "unknown", notes: "" },
      receipt: null,
    },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

beforeEach(() => {
  mocks.get.mockReset().mockResolvedValue(preparation());
  mocks.save.mockReset();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
});

async function mount(id = "handoff-a") {
  await act(async () => root.render(<IntakePreparationPanel handoffId={id} />));
}
async function select(index: number, value: string) {
  const field = container.querySelectorAll("select")[index];
  await act(async () => { field.value = value; field.dispatchEvent(new Event("change", { bubbles: true })); });
}
async function submit() {
  await act(async () => { container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
}
async function clickText(text: string) {
  const button = [...container.querySelectorAll("button")].find((item) => item.textContent === text);
  expect(button).toBeDefined();
  await act(async () => button!.click());
}

describe("intake preparation", () => {
  it("allows analysis only for saved answers and locks it again while editing", async () => {
    await mount();
    expect(container.querySelector('[data-analysis-ready]')?.getAttribute("data-analysis-ready")).toBe("false");
    mocks.save.mockResolvedValueOnce(preparation("handoff-a", 1));
    await submit();
    expect(container.querySelector('[data-analysis-ready]')?.getAttribute("data-analysis-ready")).toBe("true");
    expect(container.querySelector('[data-analysis-revision]')?.getAttribute("data-analysis-revision")).toBe("1");
    await select(1, "yes");
    expect(container.querySelector('[data-analysis-ready]')?.getAttribute("data-analysis-ready")).toBe("false");
  });
  it("restores saved scope and separates service files from documents without starting work", async () => {
    await mount();
    expect(container.textContent).toContain("Документы для разбора: 3");
    expect(container.textContent).toContain("Служебные файлы: 1");
    expect(container.textContent).toContain("Thumbs.db");
    expect(container.querySelector("select")!.value).toBe("whole");
    expect(mocks.save).not.toHaveBeenCalled();
    expect(container.querySelector('[type="submit"]')!.textContent).toBe("Сохранить ответы");
  });

  it("keeps a draft across polling and transient errors", async () => {
    vi.useFakeTimers();
    await mount();
    await select(1, "yes");
    mocks.get.mockRejectedValueOnce(new Error("503: Сервис временно недоступен."));
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(container.querySelectorAll("select")[1].value).toBe("yes");
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(container.querySelectorAll("select")[1].value).toBe("yes");
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it("explains pending classification and partial or failed reading while retaining the file caveat", async () => {
    const data = preparation();
    data.summary.classification_pending = true;
    data.summary.partial_documents = 1;
    data.summary.failed_documents = 2;
    data.summary.classification_issues = [{
      source_id: "source-a", relative_path: "34219/Thumbs.db", reason: "service_name_content_conflict",
      message: "Содержимое файла требует отдельной проверки.",
    }];
    mocks.get.mockResolvedValueOnce(data);
    await mount();
    expect(container.textContent).toContain("До проверки кандидаты остаются в документах для разбора");
    expect(container.textContent).toContain("34219/Thumbs.db: Содержимое файла требует отдельной проверки.");
    expect(container.textContent).toContain("Прочитаны частично: 1");
    expect(container.textContent).toContain("Ошибки чтения: 2");
    expect(container.querySelectorAll("select")[1].querySelector('[value="no"]')?.textContent).toBe("Дополнений не ожидается");
  });

  it("retries an unknown save outcome with the same request id and binds its revision", async () => {
    await mount();
    await select(1, "yes");
    mocks.save.mockRejectedValueOnce(new Error("network failure"));
    await submit();
    const request = mocks.save.mock.calls[0][1];
    const saved = preparation("handoff-a", 1);
    saved.initial_answers.answers.more_documents = "yes";
    mocks.save.mockResolvedValueOnce(saved);
    await submit();
    expect(mocks.save.mock.calls[1]).toEqual(["handoff-a", request]);
    expect(request).toMatchObject({ snapshot_id: "snapshot-handoff-a", expected_revision: 0, answers: { more_documents: "yes" } });
    expect(request.request_id).toBeTruthy();
    expect(container.textContent).toContain("Ответы сохранены для этого комплекта.");
  });

  it("creates a new request id after editing a failed payload", async () => {
    await mount();
    mocks.save.mockRejectedValue(new Error("network failure"));
    await submit();
    await select(1, "yes");
    await submit();
    expect(mocks.save.mock.calls[0][1].request_id).not.toBe(mocks.save.mock.calls[1][1].request_id);
  });

  it("preserves the local draft on 409 and requires explicit adoption of the current revision", async () => {
    await mount();
    await select(1, "yes");
    const current = preparation("handoff-a", 2);
    current.initial_answers.answers.more_documents = "no";
    mocks.get.mockResolvedValue(current);
    mocks.save.mockRejectedValueOnce(new Error("409: Данные уже изменились."));
    await submit();
    expect(container.querySelectorAll("select")[1].value).toBe("yes");
    expect(container.querySelector<HTMLFieldSetElement>("fieldset")!.disabled).toBe(true);
    expect(container.textContent).toContain("Ваш черновик сохранён на экране");
    await clickText("Загрузить сохранённые ответы");
    expect(container.querySelectorAll("select")[1].value).toBe("no");
    mocks.save.mockResolvedValue(current);
    await submit();
    expect(mocks.save.mock.calls[1][1].expected_revision).toBe(2);
  });

  it("ignores late GET and save responses after changing the handoff", async () => {
    await mount();
    const oldSave = deferred<IntakePreparation>();
    mocks.save.mockReturnValueOnce(oldSave.promise);
    await submit();
    const nextGet = deferred<IntakePreparation>();
    mocks.get.mockReturnValueOnce(nextGet.promise);
    await mount("handoff-b");
    await act(async () => oldSave.resolve(preparation("handoff-a", 1)));
    expect(container.textContent).not.toContain("Ответы сохранены");
    await act(async () => nextGet.resolve(preparation("handoff-b")));
    await submit();
    expect(mocks.save.mock.calls[1][0]).toBe("handoff-b");
    expect(mocks.save.mock.calls[1][1].snapshot_id).toBe("snapshot-handoff-b");
  });

  it("ignores a GET that completes for a previously selected handoff", async () => {
    const oldGet = deferred<IntakePreparation>();
    mocks.get.mockReturnValueOnce(oldGet.promise);
    await mount();
    mocks.get.mockResolvedValueOnce(preparation("handoff-b"));
    await mount("handoff-b");
    const oldData = preparation();
    oldData.initial_answers.answers.scope = "selected";
    await act(async () => oldGet.resolve(oldData));
    expect(container.querySelector("select")!.value).toBe("whole");
    mocks.save.mockResolvedValueOnce(preparation("handoff-b", 1));
    await submit();
    expect(mocks.save.mock.calls[0][0]).toBe("handoff-b");
  });

  it("does not let a GET started before save replace the saved receipt", async () => {
    await mount();
    const oldGet = deferred<IntakePreparation>();
    mocks.get.mockReturnValueOnce(oldGet.promise);
    await act(async () => window.dispatchEvent(new Event("focus")));
    const saved = preparation("handoff-a", 1);
    saved.initial_answers.answers.more_documents = "yes";
    mocks.save.mockResolvedValueOnce(saved);
    await submit();
    await act(async () => oldGet.resolve(preparation()));
    expect(container.querySelectorAll("select")[1].value).toBe("yes");
    expect(container.textContent).toContain("Ответы сохранены");
  });

  it("requires positions for selected scope and prevents stale handoff mutation", async () => {
    await mount();
    await select(0, "selected");
    await submit();
    expect(mocks.save).not.toHaveBeenCalled();
    expect(container.textContent).toContain("Укажите, какие позиции");
    expect(container.querySelector("textarea")!.maxLength).toBe(2000);
    const stale = preparation("handoff-b");
    stale.editable = false;
    mocks.get.mockResolvedValue(stale);
    await mount("handoff-b");
    await submit();
    expect(mocks.save).not.toHaveBeenCalled();
  });
});
