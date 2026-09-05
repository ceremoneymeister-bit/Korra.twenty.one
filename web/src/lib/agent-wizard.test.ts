import { describe, expect, it } from "vitest";

import {
  composeSoul,
  descriptionFromRole,
  profileIdProblem,
  slugFromDisplayName,
  uniqueProfileId,
} from "./agent-wizard";

describe("slugFromDisplayName", () => {
  it("транслитерирует русское имя в идентификатор движка", () => {
    expect(slugFromDisplayName("Секретарь")).toBe("sekretar");
    expect(slugFromDisplayName("Учитель китайского")).toBe("uchitel-kitayskogo");
    expect(slugFromDisplayName("Щедрый ёж")).toBe("shchedryy-yozh");
  });

  it("оставляет латиницу и цифры, остальное превращает в один дефис", () => {
    expect(slugFromDisplayName("  SMM-менеджер №2 ")).toBe("smm-menedzher-2");
    expect(slugFromDisplayName("Coder!!!")).toBe("coder");
    expect(slugFromDisplayName("---")).toBe("");
    expect(slugFromDisplayName("🙂")).toBe("");
  });

  it("укорачивает до предела движка без хвостового дефиса", () => {
    const long = slugFromDisplayName("а".repeat(70) + " б");
    expect(long.length).toBeLessThanOrEqual(64);
    expect(long.endsWith("-")).toBe(false);
  });
});

describe("uniqueProfileId", () => {
  it("возвращает имя как есть, если оно свободно", () => {
    expect(uniqueProfileId("sekretar", ["default"])).toBe("sekretar");
  });

  it("добавляет числовой хвост занятому и зарезервированному имени", () => {
    expect(uniqueProfileId("sekretar", ["Sekretar", "sekretar-2"])).toBe(
      "sekretar-3",
    );
    expect(uniqueProfileId("profile", [])).toBe("profile-2");
    expect(uniqueProfileId("", [])).toBe("");
  });

  it("не выходит за 64 знака вместе с хвостом", () => {
    const base = "a".repeat(64);
    const next = uniqueProfileId(base, [base]);
    expect(next.length).toBeLessThanOrEqual(64);
    expect(next.endsWith("-2")).toBe(true);
  });
});

describe("profileIdProblem", () => {
  it("молчит на пустом и правильном имени", () => {
    expect(profileIdProblem("", [])).toBeNull();
    expect(profileIdProblem("sekretar_2", ["default"])).toBeNull();
  });

  it("объясняет грамматику, резерв и занятость", () => {
    expect(profileIdProblem("Секретарь", [])).toMatch(/латинские/);
    expect(profileIdProblem("-abc", [])).toMatch(/первый знак/);
    expect(profileIdProblem("default", [])).toMatch(/занято системой/);
    expect(profileIdProblem("sekretar", ["sekretar"])).toMatch(/уже есть/);
  });
});

describe("descriptionFromRole", () => {
  it("берёт первое предложение", () => {
    expect(
      descriptionFromRole(
        "Ведёт календарь и почту владельца. Напоминает о встречах.\nСледит за делами.",
      ),
    ).toBe("Ведёт календарь и почту владельца.");
  });

  it("режет длинное предложение по слову и ставит многоточие", () => {
    const long = "Помогает " + "очень ".repeat(60) + "долго";
    const description = descriptionFromRole(long);
    expect(description.length).toBeLessThanOrEqual(160);
    expect(description.endsWith("…")).toBe(true);
    expect(description).not.toMatch(/\s…$/);
  });

  it("пусто без текста", () => {
    expect(descriptionFromRole("   \n ")).toBe("");
  });
});

describe("composeSoul", () => {
  it("собирает имя, роль владельца и русские правила общения", () => {
    const soul = composeSoul(" Секретарь ", "Ведёшь календарь.\nОтвечаешь на письма.");
    expect(soul).toBe(
      "# Секретарь\n\n" +
        "Ты — Секретарь, агент в системе Korra.\n\n" +
        "Ведёшь календарь.\nОтвечаешь на письма.\n\n" +
        "## Как общаться\n\n" +
        "Отвечай по-русски и по существу: короткий вопрос — короткий ответ, " +
        "законченная работа — краткий отчёт (что сделано, что проверено, что " +
        "осталось). Без воды и повторов. Соглашайся потому, что это верно, а не " +
        "потому, что так сказал собеседник; если не уверен — скажи прямо.\n",
    );
  });

  it("без роли оставляет имя и правила, а не английский дефолт", () => {
    const soul = composeSoul("Бухгалтер", "");
    expect(soul).toContain("Ты — Бухгалтер, агент в системе Korra.");
    expect(soul).not.toMatch(/You are Korra/);
    expect(soul.split("\n\n")).toHaveLength(4);
  });
});
