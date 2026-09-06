// @vitest-environment jsdom
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

let root: Root | undefined;
afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  document.body.innerHTML = "";
  sessionStorage.clear();
  vi.restoreAllMocks();
});

describe("Общий роутер панели и плагинов", () => {
  it.each(["", "/c/owner-21"])("ссылки пользы и слота задач сохраняют кабинет %s и состояние панели", async (prefix) => {
    vi.resetModules();
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    window.__HERMES_BASE_PATH__ = prefix;
    window.history.replaceState({}, "", `${prefix}/achievements`);
    const { exposePluginSDK, getPluginComponent } = await import("./registry");
    const { PluginSlot } = await import("./slots");
    const { BrowserRouter, Routes, Route } = await import("react-router");
    const { HERMES_BASE_PATH } = await import("@/lib/api");
    exposePluginSDK();
    const sdk = window.__HERMES_PLUGIN_SDK__!;
    vi.spyOn(sdk, "fetchJSON").mockResolvedValue({ boards: [] });
    vi.spyOn(sdk.api, "getCronJobs").mockResolvedValue([]);
    vi.spyOn(sdk.api, "getProfiles").mockResolvedValue({ profiles: [] });
    // Исполняем поставляемые плагины: тест ловит и голые href, и перепутанный слот.
    // @ts-expect-error Плагин поставляется как браузерный JavaScript.
    await import("../../../plugins/hermes-achievements/dashboard/dist/index.js");
    // @ts-expect-error Плагин поставляется как браузерный JavaScript.
    await import("../../../plugins/kanban/dashboard/dist/index.js");
    const Benefits = getPluginComponent("hermes-achievements")!;
    const { Link, useNavigate, useLocation, useHref } = sdk.router;
    let mounts = 0;
    function Shell() {
      const [draft, setDraft] = React.useState("");
      React.useEffect(() => { mounts++; }, []);
      const navigate = useNavigate();
      const location = useLocation();
      return <>
        <input aria-label="Несохранённая заметка" value={draft} onChange={e => setDraft(e.target.value)} />
        <Link to="/cron">Задачи</Link>
        <button onClick={() => void navigate("/achievements")}>Сводка</button>
        <output>{location.pathname}</output>
        <span data-href>{useHref("/kanban?board=sales#result")}</span>
        <Routes>
          <Route path="/achievements" element={<Benefits />} />
          <Route path="/cron" element={<PluginSlot name="cron:top" />} />
          <Route path="/kanban" element={<p>Открыта доска</p>} />
        </Routes>
      </>;
    }
    const host = document.createElement("div"); document.body.append(host); root = createRoot(host);
    await act(async () => root!.render(<BrowserRouter basename={HERMES_BASE_PATH || undefined}><Shell /></BrowserRouter>));
    expect(host.querySelector("[data-href]")!.textContent).toBe(`${prefix}/kanban?board=sales#result`);
    const input = host.querySelector("input")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "Моя заметка");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const click = async (link: HTMLAnchorElement) => {
      expect(link.getAttribute("href")).toBe(`${prefix}/kanban`);
      await act(async () => link.click());
      expect(decodeURI(window.location.pathname)).toBe(`${prefix}/kanban`);
      expect(host.querySelector("output")!.textContent).toBe("/kanban");
      expect(host.querySelector("input")!.value).toBe("Моя заметка");
      expect(mounts).toBe(1);
    };
    await click(host.querySelector(".kb-benefit-next a")!);
    await act(async () => (host.querySelector('a[href$="/cron"]') as HTMLAnchorElement).click());
    await click(Array.from(host.querySelectorAll("a")).find(a => a.textContent === "Открыть доску")!);
    await act(async () => (host.querySelector("button") as HTMLButtonElement).click());
    expect(decodeURI(window.location.pathname)).toBe(`${prefix}/achievements`);
  // Импортируем настоящий SDK со всеми компонентами: параллельная сборка
  // соседних тестов может занять больше стандартных пяти секунд.
  }, 20000);

  it("не переносит список плагинов между кабинетами одного домена", async () => {
    vi.resetModules(); window.__HERMES_BASE_PATH__ = "/c/first";
    const first = await import("./usePlugins");
    first.cacheManifests([]);
    vi.resetModules(); window.__HERMES_BASE_PATH__ = "/c/second";
    const second = await import("./usePlugins");
    expect(second.getCachedManifests()).toBeNull();
    expect(first.getCachedManifests()).toEqual([]);
  });
});
