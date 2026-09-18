// @vitest-environment jsdom

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, type StatusResponse } from "@/lib/api";
import { useSidebarStatus, type SidebarStatus } from "./useSidebarStatus";

let root: Root;
let container: HTMLDivElement;
let current: SidebarStatus;

const status = (active: number) => ({
  active_sessions: active,
  gateway_running: true,
  gateway_state: "running",
}) as unknown as StatusResponse;

function Probe({ profile }: { profile: string | null }) {
  const value = useSidebarStatus(profile);
  useEffect(() => { current = value; }, [value]);
  return null;
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe("K21-117 sidebar freshness", () => {
  it("polls the selected agent and ignores a late response from the previous tab", async () => {
    let finishOld!: (value: StatusResponse) => void;
    vi.spyOn(api, "getProfileStatus")
      .mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }))
      .mockResolvedValueOnce(status(2));

    await act(async () => root.render(<Probe profile="designer" />));
    await act(async () => root.render(<Probe profile="lawyer" />));
    expect(api.getProfileStatus).toHaveBeenLastCalledWith("lawyer");
    expect(current.status?.active_sessions).toBe(2);

    await act(async () => finishOld(status(99)));
    expect(current.status?.active_sessions).toBe(2);
  });

  it("refreshes immediately on focus and visibility restoration", async () => {
    const get = vi.spyOn(api, "getProfileStatus").mockResolvedValue(status(1));
    await act(async () => root.render(<Probe profile="lawyer" />));
    expect(get).toHaveBeenCalledTimes(1);

    await act(async () => window.dispatchEvent(new Event("focus")));
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    expect(get).toHaveBeenCalledTimes(3);
  });

  it("keeps the owner panel status outside /agents", async () => {
    const panel = vi.spyOn(api, "getPanelStatus").mockResolvedValue(status(0));
    const profile = vi.spyOn(api, "getProfileStatus").mockResolvedValue(status(0));
    await act(async () => root.render(<Probe profile={null} />));
    expect(panel).toHaveBeenCalledOnce();
    expect(profile).not.toHaveBeenCalled();
  });
});
