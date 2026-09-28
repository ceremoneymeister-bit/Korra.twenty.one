// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TranscriptViewport } from "./TranscriptViewport";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: Root;
let height: number;
let resized: () => void;
const disconnect = vi.fn();

beforeEach(() => {
  height = 1000;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  vi.stubGlobal("ResizeObserver", class {
    constructor(callback: () => void) { resized = callback; }
    observe() {}
    disconnect = disconnect;
  });
  vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockImplementation(() => height);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(400);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function render(text: string, followKey = "question", awaitingApproval = false) {
  await act(async () => root.render(
    <TranscriptViewport followKey={followKey} awaitingApproval={awaitingApproval}>{text}</TranscriptViewport>,
  ));
  return host.querySelector<HTMLDivElement>('[aria-label="Переписка"]')!;
}

async function scroll(viewport: HTMLDivElement, top: number) {
  await act(async () => {
    viewport.scrollTop = top;
    viewport.dispatchEvent(new Event("scroll"));
  });
}

it("следует за потоком, но сохраняет место чтения после прокрутки вверх", async () => {
  const viewport = await render("Начало");
  expect(viewport.scrollTop).toBe(1000);
  height = 1200;
  await render("Продолжение");
  expect(viewport.scrollTop).toBe(1200);
  await scroll(viewport, 100);
  height = 1500;
  await render("Новый текст ниже");
  expect(viewport.scrollTop).toBe(100);
  expect(host.querySelector("button")?.textContent).toContain("К последнему ответу");
  await act(async () => host.querySelector("button")!.click());
  expect(viewport.scrollTop).toBe(1500);
  expect(host.querySelector("button")).toBeNull();
});

it("возобновляет следование после ручного возврата к концу или нового вопроса", async () => {
  const viewport = await render("Ответ");
  await scroll(viewport, 100);
  await scroll(viewport, 600);
  height = 1200;
  await render("Конец ответа");
  expect(viewport.scrollTop).toBe(1200);
  await scroll(viewport, 100);
  await render("Другой вопрос", "next-question");
  expect(viewport.scrollTop).toBe(1200);
});

it("учитывает загрузку вложений, не отрывая человека от чтения", async () => {
  const viewport = await render("Вложение");
  height = 1600;
  await act(async () => resized());
  expect(viewport.scrollTop).toBe(1600);
  await scroll(viewport, 100);
  height = 1900;
  await act(async () => resized());
  expect(viewport.scrollTop).toBe(100);
});

it("показывает ожидающий вопрос агента в кнопке возврата", async () => {
  const viewport = await render("Длинный ответ");
  await scroll(viewport, 100);
  await render("Нужно решение", "question", true);
  expect(viewport.scrollTop).toBe(100);
  expect(host.querySelector("button")?.textContent).toContain("Корра ждёт вашего решения");
});

async function renderPaged(text: string, anchorKey: string, older: { hasOlder: boolean; loading: boolean; failed: boolean; load: () => void }) {
  await act(async () => root.render(
    <TranscriptViewport anchorKey={anchorKey} older={older}>{text}</TranscriptViewport>,
  ));
  return host.querySelector<HTMLDivElement>('[aria-label="Переписка"]')!;
}

it("догружает начало, когда человек листает вверх, и держит место чтения", async () => {
  const load = vi.fn();
  const idle = { hasOlder: true, loading: false, failed: false, load };
  const viewport = await renderPaged("Последние 30", "m30", idle);
  expect(viewport.scrollTop).toBe(1000);
  // Прилипание к концу — прокрутка вниз, страницу она не просит.
  await scroll(viewport, 1000);
  expect(load).not.toHaveBeenCalled();
  await scroll(viewport, 600);
  expect(load).not.toHaveBeenCalled();
  await scroll(viewport, 200);
  expect(load).toHaveBeenCalledTimes(1);

  await renderPaged("Последние 30", "m30", { ...idle, loading: true });
  expect(host.querySelector('[role="status"]')?.textContent).toContain("Загружаем более ранние сообщения");
  await scroll(viewport, 150);
  expect(load).toHaveBeenCalledTimes(1);

  // Над читаемым местом встали 800 px ранних сообщений.
  height = 1800;
  await renderPaged("Ранние 30 и последние 30", "m0", { ...idle, hasOlder: false });
  expect(viewport.scrollTop).toBe(950);
  expect(host.querySelector("button")?.textContent).not.toContain("более ранние");
});

it("кнопка догружает начало, если листать нечего, и предлагает повтор после сбоя", async () => {
  const load = vi.fn();
  await renderPaged("Короткая переписка", "m1", { hasOlder: true, loading: false, failed: false, load });
  const more = [...host.querySelectorAll("button")].find(button => button.textContent?.includes("Показать более ранние сообщения"))!;
  await act(async () => more.click());
  expect(load).toHaveBeenCalledTimes(1);
  await renderPaged("Короткая переписка", "m1", { hasOlder: true, loading: false, failed: true, load });
  expect(host.textContent).toContain("Не удалось загрузить более ранние сообщения — повторить");
});

/** Сообщения с позициями в координатах ленты; прямоугольник считается от прокрутки. */
function placeMessages(viewport: HTMLDivElement, tops: Record<string, number>, height = 100) {
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const id = this.getAttribute("data-chat-message");
    if (this === viewport || !id || !(id in tops)) return { top: 0, bottom: 400, left: 0, right: 0, width: 0, height: 400, x: 0, y: 0, toJSON() {} } as DOMRect;
    const top = tops[id]! - viewport.scrollTop;
    return { top, bottom: top + height, left: 0, right: 0, width: 0, height, x: 0, y: top, toJSON() {} } as DOMRect;
  });
}

async function renderStored(ids: string[], older: { hasOlder: boolean; loading: boolean; failed: boolean; load: () => void }) {
  await act(async () => root.render(
    <TranscriptViewport storageKey="chat:scroll" anchorKey={ids[0]} older={older}>
      {ids.map(id => <div key={id} data-chat-message={id}>{id}</div>)}
    </TranscriptViewport>,
  ));
  return host.querySelector<HTMLDivElement>('[aria-label="Переписка"]')!;
}

it("после F5 возвращает к тому же сообщению, догрузив его страницу (ревью Astra R5)", async () => {
  sessionStorage.setItem("chat:scroll", JSON.stringify({ id: "c-h36", offset: 10 }));
  const load = vi.fn();
  const idle = { hasOlder: true, loading: false, failed: false, load };
  const viewport = await renderStored(["c-h61", "c-h62"], idle);
  expect(load).toHaveBeenCalledTimes(1);
  placeMessages(viewport, { "c-h31": 0, "c-h36": 500, "c-h61": 900, "c-h62": 1000 });
  await renderStored(["c-h31", "c-h36", "c-h61", "c-h62"], { ...idle, hasOlder: false });
  expect(viewport.scrollTop).toBe(510);
  expect(load).toHaveBeenCalledTimes(1);
  sessionStorage.clear();
});

it("прежнее место в пикселях не восстанавливается — оно указывало бы на другое сообщение", async () => {
  sessionStorage.setItem("chat:scroll", "1200");
  const load = vi.fn();
  const viewport = await renderStored(["c-h61", "c-h62"], { hasOlder: true, loading: false, failed: false, load });
  expect(viewport.scrollTop).toBe(1000);
  expect(load).not.toHaveBeenCalled();
  sessionStorage.clear();
});

it("запоминает сообщение и смещение внутри него, а не пиксели от начала", async () => {
  const load = vi.fn();
  const viewport = await renderStored(["c-h61", "c-h62", "c-h63"], { hasOlder: false, loading: false, failed: false, load });
  placeMessages(viewport, { "c-h61": 0, "c-h62": 300, "c-h63": 600 });
  await scroll(viewport, 350);
  expect(JSON.parse(sessionStorage.getItem("chat:scroll") ?? "null")).toEqual({ id: "c-h62", offset: 50 });
  sessionStorage.clear();
});
