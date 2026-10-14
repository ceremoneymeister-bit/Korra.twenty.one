// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from "vitest";
vi.mock("@/lib/api", () => ({ HERMES_BASE_PATH: "/c/demo" }));
beforeEach(() => { localStorage.clear(); vi.resetModules(); });
it("читает просмотренные выпуски после перезагрузки страницы", async () => {
  const first = await import("./release-notice"); first.markReleaseSeen("one"); vi.resetModules();
  const reloaded = await import("./release-notice"); expect(reloaded.$seenReleaseIds.get()).toEqual(["one"]);
});
it("игнорирует повреждённую запись и не переносит отметку соседней установки", async () => {
  localStorage.setItem("korra.seen-releases:/c/other", JSON.stringify(["one"]));
  localStorage.setItem("korra.seen-releases:/c/demo", "broken");
  const state = await import("./release-notice"); expect(state.$seenReleaseIds.get()).toEqual([]);
  state.markReleaseSeen(""); expect(state.$seenReleaseIds.get()).toEqual([]);
});
