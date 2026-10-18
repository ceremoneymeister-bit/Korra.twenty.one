import { describe, expect, it } from "vitest";

import { chatAgentPhrases } from "./chat-agent-phrases";

describe("chatAgentPhrases", () => {
  it("склоняет только имя по умолчанию", () => {
    expect(chatAgentPhrases().placeholder).toBe("Напишите Корре…");
    expect(chatAgentPhrases("Корра").region).toBe("Разговор с Коррой");
  });

  it("называет переименованного агента в кавычках без склонения", () => {
    const phrases = chatAgentPhrases("  Секретарь ");
    expect(phrases.placeholder).toBe("Напишите агенту «Секретарь»…");
    expect(phrases.fieldLabel).toBe("Сообщение агенту «Секретарь»");
    expect(phrases.region).toBe("Разговор с агентом «Секретарь»");
    expect(Object.values(phrases).join(" ")).not.toMatch(/Корр/);
  });
});
