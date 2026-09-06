import { describe, expect, it } from "vitest";

import {
  buildModelChoices,
  choiceKey,
  groupModelChoices,
  modelKey,
  providerDisplayName,
} from "./model-choices";

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
      "Anthropic (Claude) · claude-opus-4-5 — нет ключа",
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

  it("переводит имена провайдеров движка и прячет то, о чём попросили", () => {
    const choices = buildModelChoices(
      [
        {
          name: "ChatGPT or Codex Subscription",
          slug: "openai-codex",
          models: ["gpt-5.6-sol"],
          authenticated: true,
        },
        { name: "Mixture of Agents", slug: "moa", models: ["default"], authenticated: true },
        { name: "OpenCode Free", slug: "opencode-free", models: ["mimo-v2.5-free"] },
      ],
      { hide: ["moa"] },
    );
    expect(choices.map((c) => c.label)).toEqual([
      "Подписка ChatGPT / Codex · gpt-5.6-sol — готов",
      "OpenCode — бесплатные модели · mimo-v2.5-free — готов",
    ]);
  });
});

describe("providerDisplayName", () => {
  it("свои провайдеры владельца оставляет с его именем, неизвестные — с именем движка", () => {
    expect(providerDisplayName("custom:dario", "dario")).toBe("dario");
    expect(providerDisplayName("some-new", "Some New Cloud")).toBe("Some New Cloud");
    expect(providerDisplayName("anthropic", "Anthropic")).toBe("Anthropic (Claude)");
    expect(providerDisplayName("weird", null)).toBe("weird");
  });
});

describe("groupModelChoices", () => {
  it("собирает модели по провайдерам, сохраняя порядок «с ключом — первые»", () => {
    const groups = groupModelChoices(buildModelChoices(PROVIDERS));
    expect(groups.map((g) => [g.providerName, g.ready, g.choices.length])).toEqual([
      ["Dario", true, 2],
      ["Anthropic (Claude)", false, 1],
    ]);
    expect(groups[0].choices.map((c) => c.model)).toEqual([
      "claude-opus-4-5",
      "claude-sonnet-4-5",
    ]);
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
