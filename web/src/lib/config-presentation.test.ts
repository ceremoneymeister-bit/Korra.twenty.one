import { configFieldMatches, configFieldLabel } from "./config-presentation";
import { describe, expect, it } from "vitest";
import { configCategoryName, parseToolsets, presentToolsets } from "./config-presentation";

describe("Понятная конфигурация", () => {
  it("сохраняет технические идентификаторы после редактирования русского представления", () => {
    const values = ["hermes-cli", "browser", "custom_plugin"];
    expect(presentToolsets(values)).not.toContain("hermes");
    expect(parseToolsets(presentToolsets(values))).toEqual(values);
    expect(parseToolsets(" browser, , custom_plugin ")).toEqual(["browser", "custom_plugin"]);
  });
  it("показывает понятное имя вместо имён переменных", () => {
    expect(configCategoryName("tool_loop_guardrails")).toBe("Защита от зацикливания");
    expect(configCategoryName("agent", "Агент")).toBe("Агент");
    expect(configCategoryName("future_private_key")).not.toContain("_");
  });
});


describe("Поиск настроек по видимому названию", () => {
  it("находит параметр по русской подписи и сохраняет поиск по техническому ключу", () => {
    const schema = { title: "Timezone", category: "general" };
    expect(configFieldMatches("timezone", schema, configFieldLabel("timezone", schema.title))).toBe(true);
    expect(configFieldMatches("timezone", schema, "timezone")).toBe(true);
    expect(configFieldMatches("timezone", schema, "поставщики")).toBe(false);
  });
  it("находит расширение по его собственной русской подписи и названию раздела", () => {
    const schema = { title: "Автоматическая проверка результата", category: "kanban" };
    expect(configFieldMatches("extension.review", schema, "проверка результата")).toBe(true);
    expect(configFieldMatches("extension.review", schema, "Доска задач")).toBe(true);
  });
});
