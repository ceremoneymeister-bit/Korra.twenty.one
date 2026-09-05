// @vitest-environment jsdom

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Орбита рисует себя через requestAnimationFrame и canvas — в jsdom это шум,
// а проверяем мы не её. Заглушка сохраняет только факт присутствия.
vi.mock("thinking-orbs", () => ({
  ThinkingOrb: ({ state }: { state: string }) => (
    <span data-testid="orb" data-state={state} />
  ),
}));

import type { ToolEntry } from "@/components/ToolCall";
import { AgentTrace } from "./AgentTrace";

let container: HTMLDivElement;
let root: Root;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

async function render(ui: ReactNode) {
  await act(async () => root.render(ui));
}

function tool(overrides: Partial<ToolEntry> & { id: string; name: string }): ToolEntry {
  return {
    kind: "tool",
    tool_id: overrides.name,
    status: "done",
    startedAt: 0,
    ...overrides,
  };
}

function text(): string {
  return container.textContent ?? "";
}

function panel(): HTMLElement {
  const node = container.querySelector<HTMLElement>(".korra-trace__panel");
  if (!node) throw new Error("Блок хода работы не отрисован");
  return node;
}

function rowButtons(): HTMLButtonElement[] {
  return [...container.querySelectorAll<HTMLButtonElement>(".korra-trace__row button")];
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

describe("AgentTrace — сворачивание", () => {
  it("свёрнут после ответа и раскрыт, пока агент работает", async () => {
    const tools = [tool({ id: "1", name: "terminal", context: "ls -la" })];

    await render(<AgentTrace tools={tools} active startedAt={1_000} />);
    expect(panel().dataset.open).toBe("true");
    expect(panel().hasAttribute("inert")).toBe(false);
    expect(container.querySelector("[data-testid='orb']")).not.toBeNull();
    expect(text()).toContain("Думаю");

    await render(<AgentTrace tools={tools} active={false} startedAt={1_000} />);
    expect(panel().dataset.open).toBe("false");
    expect(panel().hasAttribute("inert")).toBe(true);
    expect(panel().getAttribute("aria-hidden")).toBe("true");
    expect(container.querySelector("[data-testid='orb']")).toBeNull();
  });

  it("открывается и закрывается по клику в заголовок", async () => {
    await render(
      <AgentTrace
        tools={[tool({ id: "1", name: "read_file" })]}
        active={false}
        startedAt={1_000}
      />,
    );
    const header = container.querySelector<HTMLButtonElement>("button[aria-expanded]");
    expect(header?.getAttribute("aria-expanded")).toBe("false");

    await click(header!);
    expect(header?.getAttribute("aria-expanded")).toBe("true");
    expect(panel().dataset.open).toBe("true");

    await click(header!);
    expect(header?.getAttribute("aria-expanded")).toBe("false");
  });

  it("ничего не рисует, когда показывать нечего", async () => {
    await render(<AgentTrace tools={[]} active={false} startedAt={1_000} />);
    expect(container.innerHTML).toBe("");
  });

  it("оставляет ошибку заметной, даже когда подробности свёрнуты", async () => {
    await render(<AgentTrace tools={[
      tool({ id: "1", name: "read_file", status: "error", error: "Файл не найден" }),
      tool({ id: "2", name: "terminal" }),
    ]} startedAt={0} />);
    const header = container.querySelector<HTMLButtonElement>("button[aria-expanded]");
    expect(header?.textContent).toContain("1 с ошибкой");
    expect(header?.getAttribute("aria-expanded")).toBe("false");
    await click(header!);
    await click(rowButtons()[0]);
    expect(text()).toContain("Файл не найден");
  });
});

describe("AgentTrace — подписи инструментов", () => {
  it("переводит известные инструменты и показывает аргумент в чипе", async () => {
    await render(
      <AgentTrace
        active={false}
        startedAt={1_000}
        tools={[
          tool({ id: "1", name: "terminal", context: "docker ps" }),
          tool({ id: "2", name: "read_file", context: "/opt/korra-21/README.md" }),
          tool({ id: "3", name: "web_search", context: "цены на металл" }),
        ]}
      />,
    );

    expect(text()).toContain("Команда в терминале");
    expect(text()).toContain("Чтение файла");
    expect(text()).toContain("Поиск в интернете");
    expect(text()).toContain("docker ps");
    expect(text()).toContain("/opt/korra-21/README.md");
  });

  it("незнакомый инструмент показывает своим именем, а не выдуманным", async () => {
    await render(
      <AgentTrace
        tools={[tool({ id: "1", name: "quantum_flux" })]}
        active={false}
        startedAt={1_000}
      />,
    );
    expect(text()).toContain("quantum_flux");
  });

  it("считает вызовы по-русски в заголовке", async () => {
    await render(
      <AgentTrace
        active={false}
        startedAt={1_000}
        tools={[
          tool({ id: "1", name: "terminal" }),
          tool({ id: "2", name: "terminal" }),
        ]}
      />,
    );
    expect(text()).toContain("2 вызова");
  });

  it("показывает длительность, когда метки времени реальные", async () => {
    await render(
      <AgentTrace
        active={false}
        startedAt={10_000}
        tools={[
          tool({ id: "1", name: "terminal", startedAt: 10_500, completedAt: 14_000 }),
        ]}
      />,
    );
    expect(text()).toContain("Думал 4 с");
  });

  it("не выдумывает длительность для истории без меток", async () => {
    await render(
      <AgentTrace
        tools={[tool({ id: "1", name: "terminal" })]}
        active={false}
        startedAt={0}
      />,
    );
    expect(text()).not.toContain("Думал");
    expect(text()).toContain("1 вызов");
  });
});

describe("AgentTrace — размышление", () => {
  it("отдельная строка, свёрнутая по умолчанию, раскрывается по клику", async () => {
    await render(
      <AgentTrace
        tools={[]}
        reasoning="Сначала проверю остатки, потом посчитаю раскрой."
        active={false}
        startedAt={1_000}
      />,
    );

    expect(text()).toContain("Размышление");
    expect(text()).not.toContain("Сначала проверю остатки");

    await click(rowButtons()[0]);
    expect(text()).toContain("Сначала проверю остатки, потом посчитаю раскрой.");
  });

  it("пустое размышление строку не создаёт", async () => {
    await render(
      <AgentTrace
        tools={[tool({ id: "1", name: "terminal" })]}
        reasoning="   "
        active={false}
        startedAt={1_000}
      />,
    );
    expect(text()).not.toContain("Размышление");
    expect(rowButtons()).toHaveLength(1);
  });

  it("строка вызова раскрывает полный аргумент, обрезанный в чипе", async () => {
    const long = "cd /opt/korra-21 && ./scripts/run_tests.sh --profile raschet-blank";
    await render(
      <AgentTrace
        tools={[tool({ id: "1", name: "terminal", context: long })]}
        active={false}
        startedAt={1_000}
      />,
    );

    await click(rowButtons()[0]);
    const detail = container.querySelector(".korra-trace__detail");
    expect(detail?.textContent).toBe(long);
  });
});
