// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardWidgetBoundary } from "./DashboardWidgetBoundary";

let container: HTMLDivElement;
let root: Root;

function BrokenWidget(): never {
  throw new Error("widget failed");
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe("DashboardWidgetBoundary", () => {
  it("оставляет соседнее содержимое на экране при сбое одного виджета", async () => {
    // React сообщает пойманную ошибку в console.error — в этом тесте она
    // ожидаема, а пользователь получает локальное состояние карточки.
    vi.spyOn(console, "error").mockImplementation(() => undefined);

    await act(async () =>
      root.render(
        <div>
          <DashboardWidgetBoundary title="Агенты" widgetId="agents">
            <BrokenWidget />
          </DashboardWidgetBoundary>
          <p>Соседняя карточка работает</p>
        </div>,
      ),
    );

    expect(container.textContent).toContain("Не удалось показать карточку");
    expect(container.textContent).toContain("«Агенты» временно недоступна");
    expect(container.textContent).toContain("Соседняя карточка работает");
    expect(
      container.querySelector('[data-widget-error="agents"]'),
    ).not.toBeNull();
  });
});
