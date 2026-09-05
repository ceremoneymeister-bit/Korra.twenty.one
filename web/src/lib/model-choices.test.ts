import { describe, expect, it } from "vitest";

import { buildModelChoices, choiceKey, modelKey } from "./model-choices";

const PROVIDERS = [
  {
    name: "Anthropic",
    slug: "anthropic",
    models: ["claude-opus-4-5"],
    authenticated: false,
  },
  { name: "Nous", slug: "nous", models: ["hermes-4-70b"], authenticated: true },
  {
    name: "Dario",
    slug: "custom:dario",
    models: ["claude-opus-4-5", "claude-sonnet-4-5"],
    authenticated: true,
  },
];

describe("buildModelChoices", () => {
  it("ставит провайдеров с ключами первыми и метит остальных", () => {
    const choices = buildModelChoices(PROVIDERS);

    expect(choices.map((c) => c.label)).toEqual([
      "Dario · claude-opus-4-5 — готов",
      "Dario · claude-sonnet-4-5 — готов",
      "Anthropic · claude-opus-4-5 — нет ключа",
    ]);
    expect(choices.every((c) => c.provider !== "nous")).toBe(true);
    expect(choices[0].ready).toBe(true);
    expect(choices[2].ready).toBe(false);
  });

  it("считает провайдера готовым, пока движок не сказал обратного", () => {
    const choices = buildModelChoices([
      { name: "Свой", slug: "custom:local", models: ["qwen"] },
    ]);

    expect(choices).toHaveLength(1);
    expect(choices[0].ready).toBe(true);
  });

  it("не падает на пустом ответе движка", () => {
    expect(buildModelChoices(undefined)).toEqual([]);
    expect(buildModelChoices([{ name: "", slug: "" }])).toEqual([]);
  });
});

describe("modelKey", () => {
  it("склеивает провайдера и модель, а без одного из них пуст", () => {
    expect(modelKey("custom:dario", "claude-sonnet-5")).toBe(
      "custom:dario\u0000claude-sonnet-5",
    );
    expect(modelKey(null, "claude-sonnet-5")).toBe("");
    expect(modelKey("custom:dario", null)).toBe("");
    expect(choiceKey(buildModelChoices(PROVIDERS)[0])).toBe(
      modelKey("custom:dario", "claude-opus-4-5"),
    );
  });
});
