// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { Markdown } from "../Markdown";

const host = document.createElement("div");
const root = createRoot(host);
afterEach(async () => { await act(async () => root.render(null)); delete window.__HERMES_SESSION_TOKEN__; vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("показывает имя, размер и защищённое скачивание, восстанавливается после перезагрузки", async () => {
  const path = "/opt/data/workspace/report.xlsx";
  vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({path, name: "report.xlsx", kind: "xlsx", size: 2048, reader: "xlsx"})))));
  const content = `Готово: [скачать](sandbox:${path})\nMEDIA:${path}`;
  for (let reload = 0; reload < 2; reload++) {
    await act(async () => { root.render(<Markdown content={content} />); });
    expect(host.textContent).toContain("XLSX · 2 КБ");
    const link = host.querySelector('a[download]');
    expect(link?.textContent).toContain("Скачать");
    expect(link?.getAttribute("href")).toContain("/api/files/download?");
    expect(link?.getAttribute("href")).toContain("chat=1");
    expect(host.querySelectorAll('a[download]')).toHaveLength(1);
    await act(async () => root.render(null));
  }
});

it("недоступный файл объясняется без рабочей кнопки скачивания", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("denied")));
  await act(async () => root.render(<Markdown content="MEDIA:/etc/secret.xlsx" />));
  expect(host.textContent).toContain("Вложение недоступно");
  expect(host.querySelector('a[download]')).toBeNull();
});

it("изображение получает превью через тот же проверяемый маршрут", async () => {
  const path = "/opt/data/workspace/image.png";
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({path, name: "image.png", kind: "png", size: 128, reader: "image"}))));
  await act(async () => root.render(<Markdown content={`MEDIA:${path}`} />));
  expect(host.querySelector("img")?.src).toContain("chat=1");
  expect(host.querySelector('a[download]')).not.toBeNull();
});

it("прямой fleet скачивает с заголовком авторизации, не помещая токен в ссылку", async () => {
  window.__HERMES_SESSION_TOKEN__ = "private-session";
  window.__KORRA_UI_MODE__ = "fleet";
  const path = "/opt/data/workspace/report.xlsx";
  const fetcher = vi.fn().mockImplementation((url: string) => Promise.resolve(url.includes("/attachment?")
    ? new Response(JSON.stringify({path, name: "report.xlsx", kind: "xlsx", size: 3, reader: "xlsx"}))
    : new Response("abc")));
  vi.stubGlobal("fetch", fetcher);
  vi.stubGlobal("URL", class extends URL { static createObjectURL() { return "blob:download"; } static revokeObjectURL() {} });
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  await act(async () => root.render(<Markdown content={`MEDIA:${path}`} />));
  await act(async () => { host.querySelector('a[download]')!.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true })); });
  expect(click).toHaveBeenCalledOnce();
  const [url, options] = fetcher.mock.calls.find(([url]) => url.includes("/download?"))!;
  expect(url).not.toContain("private-session");
  expect(options.headers.get("X-Hermes-Session-Token")).toBe("private-session");
  expect(host.querySelector('a[download]')?.getAttribute("href")).not.toContain("token=");
  delete window.__KORRA_UI_MODE__;
});
