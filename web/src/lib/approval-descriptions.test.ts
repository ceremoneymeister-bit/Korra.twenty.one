import { describe, expect, it } from "vitest";

import { translateApprovalDescription } from "./approval-descriptions";

describe("translateApprovalDescription", () => {
  it("переводит штатные описания паттернов движка", () => {
    expect(translateApprovalDescription("recursive delete")).toBe(
      "Рекурсивное удаление",
    );
    expect(translateApprovalDescription("delete in root path")).toBe(
      "Удаление в корневом каталоге",
    );
    expect(translateApprovalDescription("pipe remote content to shell")).toBe(
      "Скачать из сети и сразу выполнить",
    );
    expect(translateApprovalDescription("format filesystem")).toBe(
      "Форматирование файловой системы",
    );
    expect(translateApprovalDescription("write to block device")).toBe(
      "Запись напрямую на диск",
    );
  });

  it("убирает прежнее имя форка из подписи", () => {
    // Две строки `DANGEROUS_PATTERNS` называют движок его прежним именем.
    // В интерфейсе Korra его быть не должно.
    const updated = translateApprovalDescription(
      "hermes update (restarts gateway, kills running agents)",
    );
    expect(updated.toLowerCase()).not.toContain("hermes");
    expect(updated).toContain("Обновление Корры");

    const restart = translateApprovalDescription(
      "stop/restart hermes gateway (kills running agents)",
    );
    expect(restart.toLowerCase()).not.toContain("hermes");
  });

  it("разбирает склейку нескольких предупреждений через «; »", () => {
    expect(
      translateApprovalDescription("recursive delete; world/other-writable permissions"),
    ).toBe("Рекурсивное удаление; права на запись всем подряд");
  });

  it("незнакомое описание оставляет как есть", () => {
    // Находки tirith собираются на лету; выдумывать им перевод — врать про то,
    // что команда делает.
    const raw = "tirith: homograph URL in argument";
    expect(translateApprovalDescription(raw)).toBe(raw);
    expect(
      translateApprovalDescription("recursive delete; tirith: something new"),
    ).toBe("Рекурсивное удаление; tirith: something new");
  });

  it("пустое описание остаётся пустым", () => {
    expect(translateApprovalDescription(undefined)).toBe("");
    expect(translateApprovalDescription("   ")).toBe("");
  });
});
