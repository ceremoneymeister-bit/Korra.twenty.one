import { describe, expect, it } from "vitest";
import { ownerFacingError } from "./owner-facing-error";

import {
  composeSoul,
  descriptionFromRole,
  explainProbeFailure,
  isEngineDefaultSoul,
  profileIdProblem,
  ROLE_STARTERS,
  slugFromDisplayName,
  soulNamedAs,
  uniqueProfileId,
} from "./agent-wizard";

const ENGINE_DEFAULT_SOUL =
  "You are Korra. Be direct: match the length of your reply to the weight of " +
  "the ask — a one-line question gets a one-line answer, and finished work gets " +
  "a short report of what changed, what's verified, and what's left, never a " +
  "replay of the process. No filler (\"Great question,\" \"I'd be happy to\"), no " +
  "restating the request back, no re-summarizing what you already said, no " +
  "narrating tool calls the user can see. Plain claims over adjectives; when " +
  "unsure, say so plainly. Agree because it's right, not because the user said " +
  "it. Depth is earned — give it when the user asks for detail, teaches, or the " +
  "stakes demand it, not by default.";

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

describe("soulNamedAs", () => {
  it("узнаёт наш шаблон с этим именем, в том числе с переводами строк Windows", () => {
    const soul = composeSoul("Секретарь", "Ведёшь дела.");
    expect(soulNamedAs(soul, "Секретарь")).toBe(true);
    expect(soulNamedAs(soul.replace(/\n/g, "\r\n"), " Секретарь ")).toBe(true);
  });

  it("не считает именем случайное слово в чужих инструкциях", () => {
    expect(soulNamedAs("Ты — Секретарь, считаешь по прайсу.", "Секретарь")).toBe(false);
    expect(soulNamedAs(ENGINE_DEFAULT_SOUL, "Секретарь")).toBe(false);
    expect(soulNamedAs(composeSoul("Секретарь", ""), "Бухгалтер")).toBe(false);
    expect(soulNamedAs(composeSoul("Секретарь", ""), "")).toBe(false);
  });
});

describe("isEngineDefaultSoul", () => {
  it("узнаёт дефолт движка, пустоту и заготовку старых установщиков", () => {
    expect(isEngineDefaultSoul(ENGINE_DEFAULT_SOUL)).toBe(true);
    expect(isEngineDefaultSoul(`${ENGINE_DEFAULT_SOUL}\n`)).toBe(true);
    expect(isEngineDefaultSoul(ENGINE_DEFAULT_SOUL.replace("Korra", "Hermes Agent, built by Nous Research"))).toBe(true);
    expect(isEngineDefaultSoul("")).toBe(true);
    expect(isEngineDefaultSoul(null)).toBe(true);
    expect(
      isEngineDefaultSoul(
        "# Hermes Agent Persona\n\n<!--\nThis file defines the agent's personality and tone.\n-->",
      ),
    ).toBe(true);
  });

  it("любой абзац владельца делает текст настоящей ролью", () => {
    expect(isEngineDefaultSoul(`${ENGINE_DEFAULT_SOUL}\n\nТы — Секретарь.`)).toBe(false);
    expect(isEngineDefaultSoul(composeSoul("Секретарь", ""))).toBe(false);
    expect(
      isEngineDefaultSoul("# Hermes Agent Persona\n\n<!-- шаблон -->\n\nТы — бухгалтер."),
    ).toBe(false);
  });
});

describe("ROLE_STARTERS", () => {
  it("не обещают того, чего роль сама не даёт", () => {
    for (const starter of ROLE_STARTERS) {
      expect(starter.name.trim()).not.toBe("");
      expect(starter.role.trim().length).toBeGreaterThan(60);
      // Почта, календарь, «напомню сам», публикации — это интеграции и
      // расписание, а не текст роли (ревью Астры 05.09).
      expect(starter.role).not.toMatch(/почт|календар|напомню|напоминаешь|отправляешь сам|публикуешь сам/i);
    }
    expect(new Set(ROLE_STARTERS.map((starter) => starter.id)).size).toBe(ROLE_STARTERS.length);
  });
});

describe("explainProbeFailure", () => {
  it("401 подписки — это доступ, а не сломанный агент", () => {
    const advice = explainProbeFailure(
      "Агент не ответил на контрольное сообщение.",
      "HTTP 401: OAuth access token has expired. Re-authenticate to continue.",
    );
    expect(advice.kind).toBe("access");
    expect(advice.keys).toBe(true);
    expect(advice.advice).toContain("Сам агент сохранён");
    expect(advice.advice).toMatch(/подписк/);
  });

  it("503 с лимитом — подождать, а не идти в «Ключи»", () => {
    const advice = explainProbeFailure(
      "Сервис временно недоступен. Повторите через минуту.",
      "HTTP 503: all accounts are rate-limited or in auth cool-down",
    );
    // «auth cool-down» тоже про доступ к подписке — ключ здесь ни при чём,
    // но человеку важнее, что делать: ждать.
    expect(["busy", "access"]).toContain(advice.kind);
    expect(advice.advice).toContain("Сам агент сохранён");
  });

  it("неизвестный провайдер у профиля — настройки, а не ключ и не подписка", () => {
    const advice = explainProbeFailure(
      "Агент ответил ошибкой.",
      "Unknown provider 'custom:dario'. Check 'hermes model' for available providers, or run 'hermes doctor' to diagnose config issues.",
    );
    expect(advice.kind).toBe("config");
    expect(advice.advice).toContain("Сам агент сохранён");
    expect(advice.advice).toMatch(/выберите модель ещё раз/);
  });

  it("сеть, таймаут и неизвестное различаются", () => {
    expect(explainProbeFailure("Не удалось связаться с сервером. Проверьте интернет и повторите.").kind).toBe("network");
    expect(explainProbeFailure("Проверка не дождалась ответа.", "timeout 90s").kind).toBe("timeout");
    const unknown = explainProbeFailure("Агент промолчал: ответ пришёл пустым.", "");
    expect(unknown.kind).toBe("unknown");
    expect(unknown.keys).toBe(false);
  });
});


it.each([
  ["502: HTTP 401: OAuth access token has expired", "access", true],
  ["429: insufficient_quota", "busy", false],
  ["429: rate_limit_exceeded", "busy", false],
  ["503: overloaded_error", "busy", false],
  ["504: timeout", "timeout", false],
  ["Failed to fetch", "network", false],
] as const)("keeps recovery advice after translation: %s", (raw, kind, keys) => {
  const advice = explainProbeFailure(ownerFacingError(new Error(raw)));
  expect(advice).toMatchObject({ kind, keys });
  expect(advice.advice).toContain("Сам агент сохранён");
});
