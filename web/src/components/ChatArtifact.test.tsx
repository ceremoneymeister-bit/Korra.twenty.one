// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatArtifactList } from "./ChatArtifact";
import { asksForDecision, splitArtifacts } from "@/lib/chat-artifacts";

const host = document.createElement("div");
document.body.append(host);
const root = createRoot(host);
beforeEach(() => { vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ path: "/opt/data/workspace/план.png", name: "план.png", kind: "png", size: 1024, reader: "image" })))); });
afterEach(async () => { await act(async () => root.render(null)); vi.unstubAllGlobals(); });

function opener(): HTMLButtonElement | null {
  return host.querySelector<HTMLButtonElement>('button[aria-label^="Открыть изображение крупно"]');
}

function viewer(): HTMLElement | null {
  return document.body.querySelector<HTMLElement>('[role="dialog"]');
}

async function render(content: string) {
  const { artifacts } = splitArtifacts(content);
  await act(async () => root.render(<ChatArtifactList items={artifacts} />));
}

it("результат агента открывается тем же просмотром, что и вложение владельца", async () => {
  await render("Готово.\n[артефакт] /opt/data/workspace/план.png");
  const open = opener()!;
  open.focus();
  await act(async () => { open.click(); });
  const view = viewer()!;
  expect(view.getAttribute("aria-label")).toContain("план.png");
  // Признаки общего viewer: масштаб 100 % и явное закрытие с доступным именем.
  expect(view.querySelector('[aria-label="Показать в исходном размере — 100 %"]')).not.toBeNull();
  expect(view.querySelector('[aria-label="Закрыть просмотр"]')).not.toBeNull();
  expect(view.querySelector('a[download]')!.getAttribute("href")).toContain("/api/files/download?");
  await act(async () => { document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
  expect(viewer()).toBeNull();
  expect(document.activeElement).toBe(open);
  // Скачивание из исходной карточки никуда не делось.
  expect(host.querySelector('a[download]')).not.toBeNull();
});

it("документ-артефакт остаётся карточкой без просмотра изображения", async () => {
  await render("[артефакт] /opt/data/workspace/смета.xlsx");
  expect(opener()).toBeNull();
  expect(host.querySelector("img")).toBeNull();
  expect(host.querySelector('a[download]')).not.toBeNull();
});

describe("решение по результату только по явной просьбе (UX 17.10)", () => {
  const twoFiles = "[артефакт] /opt/data/workspace/смета.xlsx\n[артефакт] /opt/data/workspace/договор.docx";

  it("справка без просьбы — только «открыть/скачать», без «Согласовать»", async () => {
    const { artifacts } = splitArtifacts(`Вот выгрузка, как просили.\n${twoFiles}`);
    await act(async () => root.render(<ChatArtifactList items={artifacts} askDecision={false} onDecision={vi.fn()} />));
    expect(host.querySelectorAll("a[download]")).toHaveLength(2);
    expect(host.textContent).not.toContain("Что делаем");
    expect(host.querySelector('[aria-label="Решение по артефакту"]')).toBeNull();
  });

  it("просьба согласовать — один общий блок на сообщение и одно решение по всем файлам", async () => {
    const { text, artifacts } = splitArtifacts(`Согласуйте, пожалуйста, оба документа.\n${twoFiles}`);
    expect(asksForDecision(text)).toBe(true);
    const onDecision = vi.fn(async () => true);
    await act(async () => root.render(<ChatArtifactList items={artifacts} askDecision onDecision={onDecision} />));
    expect(host.querySelectorAll('[aria-label="Решение по артефакту"]')).toHaveLength(1);
    expect(host.textContent).toContain("Что делаем с файлами: «смета.xlsx», «договор.docx»?");
    const button = (label: string) => [...host.querySelectorAll("button")].find((node) => node.textContent?.includes(label))!;
    await act(async () => { button("Согласовать").click(); });
    await act(async () => { button("Продолжить").click(); });
    expect(onDecision).toHaveBeenCalledWith("approve", expect.objectContaining({
      path: "/opt/data/workspace/смета.xlsx\n/opt/data/workspace/договор.docx",
      name: "смета.xlsx», «договор.docx",
    }));
  });

  it("решение, принятое по всем файлам, восстанавливается отметкой", async () => {
    const { artifacts } = splitArtifacts(twoFiles);
    const decided = new Map([["/opt/data/workspace/смета.xlsx", "approve" as const], ["/opt/data/workspace/договор.docx", "approve" as const]]);
    await act(async () => root.render(<ChatArtifactList items={artifacts} askDecision onDecision={vi.fn()} decided={decided} />));
    expect(host.textContent).toContain("Согласовано");
    expect(host.textContent).not.toContain("Что делаем");
  });
});

describe("asksForDecision", () => {
  it.each([
    "Согласуйте смету, пожалуйста.",
    "Отправляю на согласование.",
    "Утвердите договор?",
    "Подтвердите отправку клиенту.",
    "Нужно ваше решение по счёту.",
  ])("видит просьбу: %s", (text) => expect(asksForDecision(text)).toBe(true));

  it.each([
    "Готово, вот выгрузка.",
    "Сделал отчёт за неделю.",
    "Согласованный ранее вариант лежит в папке.",
  ])("не выдумывает просьбу: %s", (text) => expect(asksForDecision(text)).toBe(false));
});
