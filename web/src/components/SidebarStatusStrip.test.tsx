// @vitest-environment jsdom

/**
 * Единственный постоянный индикатор состояния не имеет права врать.
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
  it("при удачном опросе говорит, что Корра на связи", () => {
    const text = render(
      <SidebarStatusStrip reachable={true} status={RUNNING} />,
    );
    expect(text).toContain("Корра на связи");
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
