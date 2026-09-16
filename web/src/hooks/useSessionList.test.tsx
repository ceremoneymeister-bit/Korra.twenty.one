// @vitest-environment jsdom
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, type SessionInfo } from "@/lib/api";
import { useSessionList } from "./useSessionList";

let root: Root, container: HTMLDivElement;
let current: ReturnType<typeof useSessionList>;
function Probe({ profile }: { profile: string }) {
  const value = useSessionList({ profile });
  useEffect(() => { current = value; }, [value]);
  return null;
}
const response = (id: string) => ({
  sessions: [{ id, last_active: 1 } as SessionInfo], total: 1, offset: 0, limit: 50,
});
const render = async (profile: string) => {
  await act(async () => root.render(<Probe profile={profile} />));
};
beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

describe("K21-105 history refresh", () => {
  it("не заменяет свежий список запоздалым ответом другого профиля", async () => {
    let finishOld!: (value: ReturnType<typeof response>) => void;
    vi.spyOn(api, "getSessions")
      .mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }))
      .mockResolvedValueOnce(response("lawyer-chat"));
    await render("designer");
    await render("lawyer");
    await act(async () => finishOld(response("designer-chat")));
    expect(current.sessions.map(row => row.id)).toEqual(["lawyer-chat"]);
  });

  it("не показывает прежний профиль во время загрузки нового", async () => {
    let finish!: (value: ReturnType<typeof response>) => void;
    vi.spyOn(api, "getSessions").mockResolvedValueOnce(response("designer-chat"))
      .mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    await render("designer");
    await render("lawyer");
    expect(current.sessions).toEqual([]);
    expect(current.loading).toBe(true);
    await act(async () => finish(response("lawyer-chat")));
    expect(current.sessions.map(row => row.id)).toEqual(["lawyer-chat"]);
  });

  it("polling старого запроса не откатывает список после нового ответа", async () => {
    const get = vi.spyOn(api, "getSessions").mockResolvedValueOnce(response("initial"));
    await render("designer");
    let finishOld!: (value: ReturnType<typeof response>) => void;
    get.mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }))
      .mockResolvedValueOnce(response("newest"));
    let old!: Promise<void>;
    await act(async () => { old = current.refresh(); });
    await act(async () => current.refresh());
    await act(async () => { finishOld(response("stale")); await old; });
    expect(current.sessions.map(row => row.id)).toEqual(["newest"]);
  });
});
