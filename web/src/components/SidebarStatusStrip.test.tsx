// @vitest-environment jsdom

/**
 * Предупреждение об обрыве не должно пропадать из-за старого ответа.
 *
 * До правки `useSidebarStatus` глотал ошибку опроса, а полоса продолжала
 * показывать последний удачный ответ: при полном обрыве связи чат писал «не
 * удалось связаться с сервером», а подвал — «Корра на связи», зелёным.
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { StatusResponse } from "@/lib/api";

const flags = vi.hoisted(() => ({ productMode: true }));

vi.mock("@/lib/dashboard-flags", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/dashboard-flags")>(
      "@/lib/dashboard-flags",
    );
  return { ...actual, isProductUiMode: () => flags.productMode };
});

import { SidebarStatusStrip } from "./SidebarStatusStrip";

let container: HTMLDivElement;
let root: Root;

const RUNNING = {
  active_sessions: 2,
  gateway_running: true,
  gateway_state: "running",
} as unknown as StatusResponse;

beforeEach(() => {
  flags.productMode = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(node: React.ReactNode) {
  act(() => root.render(node));
  return container.textContent ?? "";
}

describe("SidebarStatusStrip", () => {
  it("при удачном опросе оставляет подвал без сообщения", () => {
    const text = render(
      <SidebarStatusStrip reachable={true} status={RUNNING} />,
    );
    expect(text).toBe("");
    expect(container.children).toHaveLength(0);
  });

  it("до первого ответа не сообщает о неисправности", () => {
    render(<SidebarStatusStrip reachable={null} status={null} />);
    expect(container.children).toHaveLength(0);
  });

  it.each([
    ["starting", "Корра запускается"],
    ["startup_failed", "Не удалось запустить Корру"],
    ["stopped", "Корра остановлена"],
  ])("показывает состояние %s, требующее внимания", (gateway_state, message) => {
    render(<SidebarStatusStrip reachable={true} status={{ ...RUNNING, gateway_state }} />);
    expect(container.querySelector('[role="status"]')?.textContent).toContain(message);
  });

  it("убирает предупреждение после восстановления связи", () => {
    render(<SidebarStatusStrip reachable={false} status={RUNNING} />);
    expect(container.textContent).toContain("Нет связи");
    render(<SidebarStatusStrip reachable={true} status={RUNNING} />);
    expect(container.children).toHaveLength(0);
  });

  it("при обрыве не показывает прошлый удачный статус", () => {
    const text = render(
      <SidebarStatusStrip reachable={false} status={RUNNING} />,
    );
    expect(text).not.toContain("Корра на связи");
    expect(text).toContain("Нет связи");
  });

  it("в режиме инженера обрыв тоже назван, а не подменён шлюзом", () => {
    flags.productMode = false;
    const text = render(
      <SidebarStatusStrip reachable={false} status={RUNNING} />,
    );
    expect(text).toContain("Панель недоступна");
  });
});
