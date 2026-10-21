import { describe, expect, it } from "vitest";
import { formatDecisions, parseDecisions } from "@/lib/artifact-decisions";

describe("решения по документам одним сообщением", () => {
  it("пакет читается обратно, с путём и комментарием к «Изменить»", () => {
    const list = [
      { kind: "approve" as const, name: "КП 1024.docx", path: "/w/1024/КП 1024.docx" },
      { kind: "change" as const, name: "Калькуляция.xlsx", path: "/w/1024/Калькуляция.xlsx", note: "срок 30 дней\nи без доставки" },
      { kind: "defer" as const, name: "Отчёт.html", path: "/w/1024/Отчёт.html" },
    ];
    const text = formatDecisions(list);
    expect(text.split("\n")[0]).toBe("Решения по документам:");
    expect(parseDecisions(text)).toEqual([
      { kind: "approve", name: "КП 1024.docx", path: "/w/1024/КП 1024.docx", note: undefined },
      { kind: "change", name: "Калькуляция.xlsx", path: "/w/1024/Калькуляция.xlsx", note: "срок 30 дней и без доставки" },
      { kind: "defer", name: "Отчёт.html", path: "/w/1024/Отчёт.html", note: undefined },
    ]);
  });
  it("прежнее одиночное сообщение тоже читается", () => {
    expect(parseDecisions("Согласовано: «КП.docx»\nФайл: /w/КП.docx")).toEqual([
      { kind: "approve", name: "КП.docx", path: "/w/КП.docx", note: undefined },
    ]);
    expect(parseDecisions("Отложено: «Старое.pptx»")).toEqual([
      { kind: "defer", name: "Старое.pptx", path: "Старое.pptx", note: undefined },
    ]);
  });
  it("обычный текст — не решение", () => {
    expect(parseDecisions("Согласовано, но поменяй срок")).toEqual([]);
  });
});

describe("решение — только сообщение целиком", () => {
  it("цитата строки решения внутри обычного текста не помечает документ решённым", () => {
    expect(parseDecisions("Вчера я написал так:\nСогласовано: «КП.docx»\nФайл: /w/КП.docx\nно передумал")).toEqual([]);
    expect(parseDecisions("Напомни, почему было Отложено: «Старое.pptx»")).toEqual([]);
    expect(parseDecisions("Коллега пишет:\n1. Согласовано: «КП.docx»\nФайл: /w/КП.docx")).toEqual([]);
  });
  it("пакет читается только если сообщение начинается с «Решения по документам:»", () => {
    const body = "1. Согласовано: «КП.docx»\nФайл: /w/КП.docx";
    expect(parseDecisions(`Решения по документам:\n${body}`)).toHaveLength(1);
    expect(parseDecisions(`Привет!\nРешения по документам:\n${body}`)).toEqual([]);
  });
});
