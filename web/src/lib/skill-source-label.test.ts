import { expect, it } from "vitest";
import { skillSourceLabel } from "./skill-source-label";

it("сохраняет различимость источников без старого бренда в подписи", () => {
  const sources = ["hermes-index", "official", "github", "user-catalog"];
  const labels = sources.map(skillSourceLabel);
  expect(new Set(labels).size).toBe(sources.length);
  expect(labels.join(" ")).not.toContain("hermes");
  expect(skillSourceLabel("user-catalog")).toBe("user-catalog");
});
