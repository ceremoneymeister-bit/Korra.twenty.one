// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.resetModules();
  delete window.__HERMES_BASE_PATH__;
  delete window.__HERMES_SESSION_TOKEN__;
});

describe("workspace download authorization", () => {
  it("streams through the cabinet cookie even when HTML includes an engine token", async () => {
    vi.resetModules();
    window.__HERMES_BASE_PATH__ = "/c/probe";
    window.__HERMES_SESSION_TOKEN__ = "test-only-token";
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    let href = "";
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      href = this.getAttribute("href") ?? "";
    });
    const { downloadWorkspaceFile } = await import("./chat-attachments");
    await downloadWorkspaceFile("/workspace/large.bin", "large.bin");
    expect(fetch).not.toHaveBeenCalled(); // No multi-GB buffering in JS.
    expect(href).toBe("/c/probe/api/files/download?path=%2Fworkspace%2Flarge.bin");
    expect(href).not.toContain("test-only-token");
  });

  it("keeps direct-panel credentials in headers, never in a download URL", async () => {
    vi.resetModules();
    window.__HERMES_SESSION_TOKEN__ = "test-only-token";
    const fetch = vi.fn().mockResolvedValue(new Response(new Blob(["test"])));
    vi.stubGlobal("fetch", fetch);
    vi.stubGlobal("URL", class extends URL {
      static createObjectURL = vi.fn().mockReturnValue("blob:test-download");
    });
    vi.useFakeTimers();
    let href = "";
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      href = this.getAttribute("href") ?? "";
    });
    const { downloadWorkspaceFile } = await import("./chat-attachments");
    await downloadWorkspaceFile("/workspace/a.bin", "a.bin");
    expect(fetch).toHaveBeenCalledOnce();
    expect(new Headers(fetch.mock.calls[0][1].headers).get("X-Hermes-Session-Token")).toBe("test-only-token");
    expect(href).toBe("blob:test-download");
  });
});
