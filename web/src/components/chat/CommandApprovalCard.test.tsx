// @vitest-environment jsdom

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CommandApprovalCard } from "./CommandApprovalCard";

let container: HTMLDivElement;
let root: Root;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

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

describe("CommandApprovalCard — что видно человеку", () => {
  it("показывает команду и причину, а варианты берёт от сервера", async () => {
    await render(
      <CommandApprovalCard
        command="rm -rf /opt/data/tmp"
        description="Рекурсивное удаление каталога"
        choices={["once", "deny"]}
        onDecide={vi.fn()}
      />,
    );

    expect(container.textContent).toContain("rm -rf /opt/data/tmp");
    expect(container.textContent).toContain("Рекурсивное удаление каталога");
    expect(container.textContent).toContain("Разрешить");
    expect(container.textContent).toContain("Отклонить");
    // Движок для этого запроса «навсегда» не предлагал — карточка не имеет
    // права придумать вариант, которого он не примет.
    expect(container.textContent).not.toContain("Разрешить всегда");
    expect(container.textContent).not.toContain("Разрешить до конца чата");
  });

  it("«всегда» честно говорит про постоянный allowlist и все каналы", async () => {
    // Вариант пишет правило в config.yaml контура: оно переживает перезапуск и
    // действует в мессенджерах и CLI, а не только здесь. Подсказка «снимает
    // вопрос в будущих чатах» это скрывала.
    await render(
      <CommandApprovalCard
        command="chmod 777 /tmp/x"
        choices={["once", "session", "always", "deny"]}
        onDecide={vi.fn()}
      />,
    );
    const hint = buttonByText("Разрешить всегда").textContent ?? "";
    expect(hint).toContain("постоянный allowlist контура");
    expect(hint).toContain("во всех каналах");
  });

  it("подзаголовок показывает описание по-русски, а не строку движка", async () => {
    await render(
      <CommandApprovalCard
        command="rm -rf /opt/data/tmp"
        description="recursive delete"
        choices={["once", "deny"]}
        onDecide={vi.fn()}
      />,
    );
    expect(container.textContent).toContain("Рекурсивное удаление");
    expect(container.textContent).not.toContain("recursive delete");
  });

  it("в интерфейсе нет слова hermes", async () => {
    await render(
      <CommandApprovalCard
        command="chmod 777 /etc"
        description="Смена прав на системный каталог"
        choices={["once", "session", "always", "deny"]}
        onDecide={vi.fn()}
      />,
    );
    expect(container.textContent?.toLowerCase()).not.toContain("hermes");
  });
});

describe("CommandApprovalCard — отправка решения", () => {
  it("разовое разрешение и отказ уходят по первому клику", async () => {
    const onDecide = vi.fn();
    await render(
      <CommandApprovalCard
        command="dd if=/dev/zero of=/tmp/x"
        choices={["once", "session", "always", "deny"]}
        onDecide={onDecide}
      />,
    );

    await click(buttonByText("Разрешить"));
    expect(onDecide).toHaveBeenNthCalledWith(1, "once");

    await click(buttonByText("Отклонить"));
    expect(onDecide).toHaveBeenNthCalledWith(2, "deny");

    await click(buttonByText("Разрешить до конца чата"));
    expect(onDecide).toHaveBeenNthCalledWith(3, "session");
  });

  it("«Разрешить всегда» просит подтверждения — это запись правила навсегда", async () => {
    const onDecide = vi.fn();
    await render(
      <CommandApprovalCard
        command="rm -rf /opt/data/tmp"
        choices={["once", "always", "deny"]}
        onDecide={onDecide}
      />,
    );

    await click(buttonByText("Разрешить всегда"));
    expect(onDecide).not.toHaveBeenCalled();
    expect(container.textContent).toContain("Разрешить всегда — точно?");

    await click(buttonByText("Разрешить всегда — точно?"));
    expect(onDecide).toHaveBeenCalledWith("always");
  });

  it("во время отправки кнопки заблокированы", async () => {
    const onDecide = vi.fn();
    await render(
      <CommandApprovalCard
        command="rm -rf /opt/data/tmp"
        choices={["once", "deny"]}
        sending
        onDecide={onDecide}
      />,
    );

    await click(buttonByText("Разрешить"));
    expect(onDecide).not.toHaveBeenCalled();
  });
});

describe("CommandApprovalCard — исход", () => {
  it("после решения вместо кнопок остаётся отметка", async () => {
    await render(
      <CommandApprovalCard
        command="rm -rf /opt/data/tmp"
        choices={["once", "deny"]}
        decision="always"
        note="Ход продолжится на сервере."
        onDecide={vi.fn()}
      />,
    );

    expect(container.textContent).toContain("Разрешено всегда");
    expect(container.textContent).toContain("Ход продолжится на сервере.");
    expect(container.querySelectorAll("button")).toHaveLength(0);
  });

  it("истёкший вопрос не предлагает кнопку, которая ничего не сделает", async () => {
    await render(
      <CommandApprovalCard
        command="rm -rf /opt/data/tmp"
        choices={["once", "deny"]}
        expired
        onDecide={vi.fn()}
      />,
    );

    expect(container.querySelectorAll("button")).toHaveLength(0);
    expect(container.textContent).toContain("Агент больше не ждёт ответа");
  });
});
