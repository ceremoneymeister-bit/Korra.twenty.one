import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { FONT_CHOICES, getFontChoice } from "@/themes/fonts";

describe("dashboard font catalog", () => {
  it("contains only CSP-safe local or system choices", () => {
    expect(FONT_CHOICES.every((choice) => !choice.fontUrl)).toBe(true);
    expect(FONT_CHOICES.map((choice) => choice.id)).toEqual([
      "system-sans",
      "system-serif",
      "system-mono",
      "onest",
      "jetbrains-mono",
    ]);
  });

  it("ships Onest and its OFL notice with the product", () => {
    const font = fileURLToPath(new URL("../../public/fonts/Onest-Variable.woff2", import.meta.url));
    const license = fileURLToPath(new URL("../../public/fonts/Onest-OFL.txt", import.meta.url));
    expect(existsSync(font)).toBe(true);
    expect(readFileSync(font).byteLength).toBeGreaterThan(80_000);
    expect(readFileSync(license, "utf8")).toContain("SIL OPEN FONT LICENSE Version 1.1");
    expect(getFontChoice("onest")?.stack).toContain("Onest");
  });
});
