// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("thinking-orbs", () => ({
  ThinkingOrb: ({ state }: { state: string }) => <span data-orb={state} />,
}));

import { ChatWorking, formatWaitSeconds } from "./ChatWorking";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | undefined;
let host: HTMLDivElement | undefined;
afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
});

async function render(ui: React.ReactNode) {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root!.render(ui));
}

describe("ChatWorking", () => {
  it("форматирует долгое ожидание минутами", () => {
    expect(formatWaitSeconds(45)).toBe("45 с");
    expect(formatWaitSeconds(125)).toBe("2 мин 5 с");
    expect(formatWaitSeconds(120)).toBe("2 мин");
    expect(formatWaitSeconds(3780)).toBe("1 ч 3 мин");
  });

  it("не объявляет тикающие секунды диктору", async () => {
    await render(<ChatWorking startedAt={Date.now() - 125_000} />);
    const status = host!.querySelector('[role="status"]')!;
    expect(status.getAttribute("aria-live")).toBe("polite");
    const timer = status.querySelector(".tabular-nums")!;
    expect(timer.textContent).toContain("2 мин 5 с");
    expect(timer.getAttribute("aria-hidden")).toBe("true");
  });

  it("на вопросе владельцу говорит «Ждёт вашего решения»", async () => {
    await render(<ChatWorking startedAt={Date.now()} state="awaiting" />);
    expect(host!.textContent).toContain("Ждёт вашего решения");
    expect(host!.textContent).not.toContain("Агент работает");
  });
});
