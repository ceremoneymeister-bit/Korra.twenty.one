// @vitest-environment jsdom

/**
 * Ключ провайдера, на котором контур реально работает, обязан быть виден.
 *
 * Свой endpoint объявляют в `custom_providers` / `providers`, ключ называют в
 * `key_env`. Каталог движка о нём не знает, поэтому `/api/env` теперь берёт
 * имя провайдера из конфигурации, а страница — из ответа сервера. До этого
 * такой ключ падал в безымянную группу «Other» (а в клиентском режиме и вовсе
 * не показывался), и счётчик писал «настроено 0 из 15» при работающем агенте.
 */

import { describe, expect, it, vi } from "vitest";

import type { EnvVarInfo } from "@/lib/api";

// KorraLoader оборачивает `window.matchMedia` прямо при импорте модуля, а в
// jsdom его нет — ставим заглушку до того, как подтянется страница.
vi.hoisted(() => {
  if (typeof window !== "undefined" && !window.matchMedia) {
    window.matchMedia = ((query: string) => ({
      addEventListener: () => {},
      addListener: () => {},
      dispatchEvent: () => false,
      matches: false,
      media: query,
      onchange: null,
      removeEventListener: () => {},
      removeListener: () => {},
    })) as unknown as typeof window.matchMedia;
  }
});

vi.mock("thinking-orbs", () => ({
  ThinkingOrb: ({ state }: { state: string }) => (
    <span data-orb={state} data-testid="orb" />
  ),
}));

import { getProviderGroup } from "./EnvPage";

function info(over: Partial<EnvVarInfo> = {}): EnvVarInfo {
  return {
    is_set: false,
    redacted_value: null,
    description: "",
    url: null,
    category: "provider",
    is_password: true,
    tools: [],
    advanced: false,
    ...over,
  };
}

describe("getProviderGroup", () => {
  it("ключ своего провайдера попадает в карточку с его именем", () => {
    expect(
      getProviderGroup(
        "DARIO_API_KEY",
        info({ provider: "custom:dario", provider_label: "dario", is_set: true }),
      ),
    ).toBe("dario");
  });

  it("имя провайдера от сервера разбирает бывшую свалку «Other»", () => {
    expect(
      getProviderGroup(
        "KILOCODE_API_KEY",
        info({ provider: "kilocode", provider_label: "Kilo Code" }),
      ),
    ).toBe("Kilo Code");
  });

  it("привычные объединения по префиксу не разъезжаются", () => {
    expect(getProviderGroup("GOOGLE_API_KEY", info({ provider_label: "Google AI Studio" }))).toBe(
      "Gemini",
    );
    expect(getProviderGroup("GEMINI_API_KEY", info({ provider_label: "Google AI Studio" }))).toBe(
      "Gemini",
    );
  });

  it("безымянный ключ уходит в русскую группу, а не в «Other»", () => {
    expect(getProviderGroup("SOMETHING_API_KEY", info())).toBe("Прочие провайдеры");
  });
});
