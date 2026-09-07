// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { Markdown } from "../Markdown";

const host = document.createElement("div");
const root = createRoot(host);
afterEach(async () => { await act(async () => root.render(null)); vi.unstubAllGlobals(); });

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
  expect(host.textContent).toContain("Файл недоступен");
  expect(host.querySelector('a[download]')).toBeNull();
});

it("изображение получает превью через тот же проверяемый маршрут", async () => {
  const path = "/opt/data/workspace/image.png";
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({path, name: "image.png", kind: "png", size: 128, reader: "image"}))));
  await act(async () => root.render(<Markdown content={`MEDIA:${path}`} />));
  expect(host.querySelector("img")?.src).toContain("chat=1");
  expect(host.querySelector('a[download]')).not.toBeNull();
});
