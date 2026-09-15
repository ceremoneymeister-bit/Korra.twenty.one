// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { Markdown } from "../Markdown";

const host = document.createElement("div");
// Просмотр изображения возвращает фокус на карточку — для этого дерево должно
// быть в документе, иначе focus() в jsdom не делает ничего.
document.body.append(host);
const root = createRoot(host);
afterEach(async () => { await act(async () => root.render(null)); delete window.__HERMES_SESSION_TOKEN__; vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function opener(): HTMLButtonElement | null {
  return host.querySelector<HTMLButtonElement>('button[aria-label^="Открыть изображение крупно"]');
}

function viewer(): HTMLElement | null {
  return document.body.querySelector<HTMLElement>('[role="dialog"]');
}

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

it("результат вне рабочей папки получает причину сервера и продолжение, а не общий текст", async () => {
  const detail = "Файл вне рабочей папки. Попросите агента сохранить его в workspace.";
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail }), { status: 403 })));
  await act(async () => root.render(<Markdown content="MEDIA:/opt/data/profiles/designer/report.pdf" />));
  expect(host.textContent).toContain("Вложение недоступно. Файл вне рабочей папки. Попросите агента");
  expect(host.querySelector('a[download]')).toBeNull();
  expect(host.querySelector('a[href*="/files?"]')).toBeNull();
});

it("«Показать в папке» ведёт в папку файла и подсвечивает именно его", async () => {
  const path = "/opt/data/workspace/Отчёты/сентябрь.xlsx";
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ path, name: "сентябрь.xlsx", kind: "xlsx", size: 10, reader: "xlsx" }))));
  await act(async () => root.render(<Markdown content={`MEDIA:${path}`} />));
  const link = [...host.querySelectorAll("a")].find(item => item.textContent?.includes("Показать в папке"))!;
  const url = new URL(link.getAttribute("href")!, "https://local.test");
  expect(url.pathname).toBe("/files");
  expect(url.searchParams.get("path")).toBe("/opt/data/workspace/Отчёты");
  expect(url.searchParams.get("highlight")).toBe("сентябрь.xlsx");
});

it("изображение получает превью через тот же проверяемый маршрут", async () => {
  const path = "/opt/data/workspace/image.png";
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({path, name: "image.png", kind: "png", size: 128, reader: "image"}))));
  await act(async () => root.render(<Markdown content={`MEDIA:${path}`} />));
  expect(host.querySelector("img")?.src).toContain("chat=1");
  expect(host.querySelector('a[download]')).not.toBeNull();
});

it("нажатие на картинку открывает просмотр поверх чата, Escape возвращает к карточке", async () => {
  const path = "/opt/data/workspace/кот.png";
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({path, name: "кот.png", kind: "png", size: 2048, reader: "image"}))));
  await act(async () => root.render(<Markdown content={`MEDIA:${path}`} />));
  const open = opener()!;
  open.focus();
  await act(async () => { open.click(); });
  const view = viewer()!;
  expect(view.getAttribute("aria-label")).toContain("кот.png");
  // Просмотр показывает то же, что уже получила карточка: второго маршрута нет.
  expect(view.querySelector("img")!.getAttribute("src")).toBe(host.querySelector("img")!.getAttribute("src"));
  expect(view.contains(document.activeElement)).toBe(true);
  await act(async () => { document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
  expect(viewer()).toBeNull();
  expect(document.activeElement).toBe(open);
  // Карточка осталась на месте вместе со своим «Скачать».
  expect(host.querySelector('a[download]')).not.toBeNull();
});

it("картинка больше 25 МБ остаётся карточкой со скачиванием, без превью и просмотра", async () => {
  const path = "/opt/data/workspace/огромная.png";
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({path, name: "огромная.png", kind: "png", size: 26 * 1024 * 1024, reader: "image"}))));
  await act(async () => root.render(<Markdown content={`MEDIA:${path}`} />));
  expect(host.querySelector("img")).toBeNull();
  expect(opener()).toBeNull();
  expect(host.querySelector('a[download]')).not.toBeNull();
});

it("недоступное вложение не предлагает просмотр", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("denied")));
  await act(async () => root.render(<Markdown content="MEDIA:/etc/secret.png" />));
  expect(opener()).toBeNull();
  expect(viewer()).toBeNull();
});

it("«Скачать» из просмотра идёт тем же авторизованным маршрутом, что и карточка", async () => {
  window.__HERMES_SESSION_TOKEN__ = "private-session";
  window.__KORRA_UI_MODE__ = "fleet";
  const path = "/opt/data/workspace/кот.png";
  const fetcher = vi.fn().mockImplementation((url: string) => Promise.resolve(url.includes("/attachment?")
    ? new Response(JSON.stringify({path, name: "кот.png", kind: "png", size: 2048, reader: "image"}))
    : new Response("bytes")));
  vi.stubGlobal("fetch", fetcher);
  vi.stubGlobal("URL", class extends URL { static createObjectURL() { return "blob:preview"; } static revokeObjectURL() {} });
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  await act(async () => root.render(<Markdown content={`MEDIA:${path}`} />));
  await act(async () => { opener()!.click(); });
  const view = viewer()!;
  expect(view.querySelector("img")!.getAttribute("src")).toBe("blob:preview");
  const link = view.querySelector<HTMLAnchorElement>('a[download]')!;
  expect(link.getAttribute("href")).not.toContain("token=");
  await act(async () => { link.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true })); });
  expect(click).toHaveBeenCalledOnce();
  const [url, options] = fetcher.mock.calls.find(([url]) => url.includes("/download?") && !url.includes("inline="))!;
  expect(url).not.toContain("private-session");
  expect(options.headers.get("X-Hermes-Session-Token")).toBe("private-session");
  delete window.__KORRA_UI_MODE__;
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
