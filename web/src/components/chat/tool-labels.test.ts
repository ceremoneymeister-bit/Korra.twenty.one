import { describe, expect, it } from "vitest";
import { toolMeta } from "./tool-labels";

describe("toolMeta", () => {
  it("называет навыки одним словом «навык», как меню и сайт (K21-311)", () => {
    expect(toolMeta("skill_view").label).toBe("Чтение навыка");
    expect(toolMeta("skills_list").label).toBe("Список навыков");
    expect(toolMeta("skill_manage").label).toBe("Правка навыка");
    for (const name of ["skill_view", "skills_list", "skill_manage"]) {
      expect(toolMeta(name).label).not.toMatch(/скилл/i);
    }
  });
});
