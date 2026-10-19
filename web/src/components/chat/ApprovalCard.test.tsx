// @vitest-environment jsdom

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatArtifact } from "@/lib/chat-artifacts";
import { ChatArtifactView } from "@/components/ChatArtifact";

let container: HTMLDivElement;
let root: Root;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const ITEM: ChatArtifact = {
  path: "/opt/data/out/Пакет недели.pptx",
  name: "Пакет недели.pptx",
  kind: "pptx",
  isImage: false,
};

async function render(ui: ReactNode) {
  await act(async () => root.render(ui));
}

function buttonByText(label: string): HTMLButtonElement {
  const node = [...container.querySelectorAll("button")].find((element) =>
    element.textContent?.includes(label),
  );
  if (!node) throw new Error(`Кнопка «${label}» не найдена`);
  return node;
}

async function click(node: HTMLElement) {
  await act(async () => {
    node.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("ApprovalCard — протокол решений не изменился", () => {
  it("выбор варианта отправляет решение только по «Продолжить»", async () => {
    const onDecision = vi.fn(async () => true);
    await render(<ChatArtifactView item={ITEM} onDecision={onDecision} />);

    expect(container.textContent).toContain("Что делаем с «Пакет недели.pptx»?");

    // Один клик по варианту ничего не отправляет: «Согласовать» — необратимая
    // отправка в чат, промах мышью не должен её запускать.
    await click(buttonByText("Согласовать"));
    expect(onDecision).not.toHaveBeenCalled();

    await click(buttonByText("Продолжить"));
    expect(onDecision).toHaveBeenCalledWith("approve", ITEM);
    expect(container.textContent).toContain("Согласовано");
  });

  it("«Отложить» уходит тем же обработчиком", async () => {
    const onDecision = vi.fn(async () => true);
    await render(<ChatArtifactView item={ITEM} onDecision={onDecision} />);

    await click(buttonByText("Отложить"));
    await click(buttonByText("Продолжить"));

    expect(onDecision).toHaveBeenCalledWith("defer", ITEM);
    expect(container.textContent).toContain("Отложено");
  });

  it("«Изменить» ничего не помечает решённым — это начало разговора", async () => {
    const onDecision = vi.fn(async () => undefined);
    await render(<ChatArtifactView item={ITEM} onDecision={onDecision} />);

    await click(buttonByText("Изменить"));
    await click(buttonByText("Продолжить"));

    expect(onDecision).toHaveBeenCalledWith("change", ITEM);
    expect(container.textContent).not.toContain("Согласовано");
    expect(container.textContent).toContain("Что делаем с");
  });

  it("недоставленное решение не выдаёт себя за отправленное", async () => {
    const onDecision = vi.fn(async () => false);
    await render(<ChatArtifactView item={ITEM} onDecision={onDecision} />);

    await click(buttonByText("Согласовать"));
    await click(buttonByText("Продолжить"));

    expect(container.textContent).toContain("Решение не отправилось");
    // «Согласовано» — отметка о принятом решении; «Согласовать» — вариант
    // выбора. Первого быть не должно, карточка остаётся на месте.
    expect(container.textContent).not.toContain("Согласовано");
    expect(container.textContent).toContain("Что делаем с");
  });

  it("«Пропустить» прячет карточку, но оставляет путь к решению", async () => {
    const onDecision = vi.fn(async () => true);
    await render(<ChatArtifactView item={ITEM} onDecision={onDecision} />);

    await click(buttonByText("Пропустить"));
    expect(onDecision).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain("Что делаем с");

    await click(buttonByText("Решить по"));
    expect(container.textContent).toContain("Что делаем с");
  });

  it("уже принятое решение из истории показывается отметкой, а не вопросом", async () => {
    await render(
      <ChatArtifactView
        item={ITEM}
        onDecision={vi.fn()}
        decided={new Map([[ITEM.path, "approve" as const]])}
      />,
    );

    expect(container.textContent).toContain("Согласовано");
    expect(container.textContent).not.toContain("Что делаем с");
  });
});
