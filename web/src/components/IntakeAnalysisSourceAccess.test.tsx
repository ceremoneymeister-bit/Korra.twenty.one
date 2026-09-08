// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { IntakeAnalysisSourceAccess } from "./IntakeAnalysisSourceAccess";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

it("fetches the exact original and page with authentication, exposes only blob URLs and releases the preview", async () => {
  vi.useFakeTimers();
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response("pdf", { headers: { "Content-Type": "application/pdf" } }))
    .mockResolvedValueOnce(new Response("png", { headers: { "Content-Type": "image/png" } }));
  vi.stubGlobal("fetch", fetchMock);
  const createObjectURL = vi.fn().mockReturnValueOnce("blob:original").mockReturnValueOnce("blob:page");
  const revokeObjectURL = vi.fn();
  vi.stubGlobal("URL", class extends URL {
    static createObjectURL = createObjectURL;
    static revokeObjectURL = revokeObjectURL;
  });
  const anchor = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  window.__HERMES_SESSION_TOKEN__ = "test-secret";
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  async function click(text: string) {
    const button = [...document.querySelectorAll("button")].find((item) => item.textContent === text);
    expect(button).toBeDefined();
    await act(async () => button!.click());
  }
  try {
    await act(async () => root.render(<IntakeAnalysisSourceAccess source={{ source_id: "src/a", relative_path: "А/деталь.pdf", status: "complete", page_count: 2, inspect_job_id: "inspect-a", render_jobs: { "2": "render-a" }, proposal: null }} />));
    expect(fetchMock).not.toHaveBeenCalled();
    await click("Скачать исходник");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/calc/document-jobs/inspect-a/sources/src%2Fa/download");
    expect(fetchMock.mock.calls[0][1].credentials).toBe("include");
    expect(fetchMock.mock.calls[0][1].headers.get("X-Hermes-Session-Token")).toBe("test-secret");
    expect((anchor.mock.instances[0] as HTMLAnchorElement).href).toBe("blob:original");
    expect((anchor.mock.instances[0] as HTMLAnchorElement).download).toBe("деталь.pdf");
    await click("Страница 2");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/calc/document-jobs/render-a/sources/src%2Fa/image");
    expect(fetchMock.mock.calls[1][1].headers.get("X-Hermes-Session-Token")).toBe("test-secret");
    expect(document.querySelector('[role="dialog"] img')?.getAttribute("src")).toBe("blob:page");
    expect(document.body.innerHTML).not.toContain("test-secret");
    expect(document.querySelector('a[href^="/api/"]')).toBeNull();
    await click("Закрыть страницу");
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:page");
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:original");
  } finally {
    await act(async () => root.unmount());
    container.remove();
    delete window.__HERMES_SESSION_TOKEN__;
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  }
});
