// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import UiKitPage from "./UiKitPage";

vi.mock("@/themes", () => ({
  useTheme: () => ({
    setTheme: vi.fn(),
    themeName: "light",
  }),
}));

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      addEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
      matches: false,
      media: query,
      onchange: null,
      removeEventListener: vi.fn(),
    })),
  );
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe("Korra UI kit acceptance route", () => {
  it("renders every documented control state and keeps enabled controls keyboard-focusable", async () => {
    await act(async () => root.render(<UiKitPage />));

    expect(container.querySelector('[data-testid="korra-ui-kit"]')).not.toBeNull();
    for (const state of ["hover", "pressed", "focus", "error", "disabled"]) {
      expect(
        container.querySelectorAll(`[data-demo-state="${state}"]`).length,
      ).toBeGreaterThanOrEqual(5);
    }

    const enabled = [
      ...container.querySelectorAll<HTMLElement>(
        "button:not(:disabled), input:not(:disabled), textarea:not(:disabled)",
      ),
    ];
    expect(enabled.length).toBeGreaterThan(24);
    for (const control of enabled.slice(0, 24)) {
      control.focus();
      expect(document.activeElement).toBe(control);
      expect(control.tabIndex).toBeGreaterThanOrEqual(0);
    }
  });
});
