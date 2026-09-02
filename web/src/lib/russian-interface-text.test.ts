import { describe, expect, it } from "vitest";

import { russianInterfaceText } from "./russian-interface-text";

describe("russianInterfaceText", () => {
  it("keeps Russian backend copy", () => {
    expect(russianInterfaceText("  Ошибка подключения к API  ", "Сбой"))
      .toBe("Ошибка подключения к API");
  });

  it("replaces English and empty backend copy", () => {
    expect(russianInterfaceText("Connection failed", "Сбой подключения"))
      .toBe("Сбой подключения");
    expect(russianInterfaceText(null, "Описание недоступно"))
      .toBe("Описание недоступно");
  });

  it("rejects mixed Russian and English prose", () => {
    expect(russianInterfaceText("Ошибка: Connection failed", "Сбой подключения"))
      .toBe("Сбой подключения");
    expect(russianInterfaceText("Ошибка API", "Сбой"))
      .toBe("Ошибка API");
  });
});
