// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("@/components/KorraLoader", () => ({ KorraLoader: () => null }));
import { AuxiliaryTasksModal, UseAsMenu } from "./ModelsPage";

let root: Root;
let container: HTMLDivElement;
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  container.style.overflow = "hidden";
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});
it("меню назначения выходит из обрезающей карточки и прокручивается в пределах экрана", async () => {
  await act(async () => root.render(<UseAsMenu provider="openai-codex" model="test" isMain mainAuxTask={null} onAssigned={vi.fn()} />));
  await act(async () => container.querySelector("button")!.click());
  const menu = document.querySelector<HTMLElement>('[role="menu"]')!;
  expect(menu.parentElement).toBe(document.body);
  expect(container.contains(menu)).toBe(false);
  expect(menu.style.position).toBe("fixed");
  expect(parseFloat(menu.style.maxHeight)).toBeLessThan(window.innerHeight);
  expect(menu.className).toContain("overflow-y-auto");
  expect(menu.textContent).toContain("Основная модель");
  expect(menu.textContent).toContain("Куратор");
  await act(async () => document.body.dispatchEvent(new MouseEvent("mousedown", { bubbles: true })));
  expect(document.querySelector('[role="menu"]')).toBeNull();
});

it("окно служебных задач открывается поверх страницы, а не внутри карточки", async () => {
  await act(async () => root.render(<AuxiliaryTasksModal aux={null} refreshKey={0} onSaved={vi.fn()} onClose={vi.fn()} />));
  const dialog = document.querySelector<HTMLElement>('[role="dialog"][aria-labelledby="aux-modal-title"]')!;
  expect(dialog.parentElement).toBe(document.body);
  expect(container.contains(dialog)).toBe(false);
  const auto = Array.from(dialog.querySelectorAll("div")).find((node) => node.textContent === "Автоматически — используется основная модель")!;
  expect(auto.className).not.toContain("font-mono");
});
