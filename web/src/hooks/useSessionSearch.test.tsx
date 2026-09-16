// @vitest-environment jsdom
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SessionSearchResponse, SessionSearchResult } from "@/lib/api";

const search = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ api: { searchSessions: search } }));
import { useSessionSearch } from "./useSessionSearch";

let root: Root, container: HTMLDivElement;
let latest: ReturnType<typeof useSessionSearch>;
function Probe({ profile = "designer", query = "", revision = "" }) {
  const value = useSessionSearch(profile, query, revision);
  useEffect(() => { latest = value; }, [value]);
  return null;
}
const result = (id: string): SessionSearchResult => ({ id, session_id: id, title: id } as SessionSearchResult);
async function render(query: string, profile = "designer", revision = "") {
  await act(async () => root.render(<Probe query={query} profile={profile} revision={revision} />));
}
async function tick() { await act(async () => { await vi.advanceTimersByTimeAsync(250); }); }
beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers(); search.mockReset();
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.useRealTimers(); });

describe("search in an agent's chat history", () => {
  it("debounces, uses an explicit profile and deduplicates message hits from older chats", async () => {
    search.mockResolvedValue({ results: [result("older-chat"), result("older-chat"), result("another")] });
    await render(""); await tick(); expect(search).not.toHaveBeenCalled();
    await render("в"); await render("весна"); await tick();
    expect(search).toHaveBeenCalledTimes(1);
    expect(search).toHaveBeenCalledWith("весна", { profile: "designer" });
    expect(latest.sessions.map(s => s.id)).toEqual(["older-chat", "another"]);
  });
  it("never shows a late result from a different profile", async () => {
    let resolve!: (value: SessionSearchResponse) => void;
    search.mockImplementationOnce(() => new Promise(r => { resolve = r; }));
    await render("договор"); await tick();
    search.mockResolvedValueOnce({ results:[result("lawyer-chat")] });
    await render("договор", "lawyer");
    expect(latest.sessions).toEqual([]);
    await tick();
    await act(async () => resolve({ results:[result("designer-chat")] }));
    expect(latest.sessions.map(s => s.id)).toEqual(["lawyer-chat"]);
  });
  it("distinguishes failure from no matches and supports retry and clearing", async () => {
    search.mockRejectedValueOnce(new Error("offline"));
    await render("план"); await tick();
    expect(latest.error).toBeTruthy(); expect(latest.loading).toBe(false);
    search.mockResolvedValueOnce({ results:[] });
    await act(async () => latest.refresh()); await tick();
    expect(latest.error).toBeNull(); expect(latest.sessions).toEqual([]);
    await render(""); expect(latest.loading).toBe(false); expect(latest.error).toBeNull();
  });
  it("refreshes changed titles without new requests for an unchanged list", async () => {
    search.mockResolvedValue({ results:[result("renamed")] });
    await render("план", "default", "before"); await tick();
    await render("план", "default", "before"); await tick();
    expect(search).toHaveBeenCalledTimes(1);
    await render("план", "default", "after"); await tick();
    expect(search).toHaveBeenCalledTimes(2);
  });
});
