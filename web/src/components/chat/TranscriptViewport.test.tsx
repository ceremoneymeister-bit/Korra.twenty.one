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
