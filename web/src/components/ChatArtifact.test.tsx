// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ChatArtifactList } from "./ChatArtifact";
import { splitArtifacts } from "@/lib/chat-artifacts";

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
