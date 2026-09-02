import { afterEach, describe, expect, it } from "vitest";

import { isProductUiMode, productUiMode } from "./dashboard-flags";

function setUiMode(mode: string | undefined) {
  (globalThis as { window?: unknown }).window = { __KORRA_UI_MODE__: mode };
}

afterEach(() => {
  delete (globalThis as { window?: unknown }).window;
});

describe("productUiMode", () => {
  it("узнаёт основной флотовый интерфейс", () => {
    setUiMode("fleet");
    expect(productUiMode()).toBe("fleet");
    expect(isProductUiMode()).toBe(true);
  });

  it("любое другое значение означает административную панель", () => {
    for (const mode of ["admin", "", "operator", undefined]) {
      setUiMode(mode);
      expect(productUiMode()).toBeNull();
      expect(isProductUiMode()).toBe(false);
    }
  });
});
