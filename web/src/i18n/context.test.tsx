// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { I18nProvider, useI18n } from "./context";

let container: HTMLDivElement;
let root: Root;

function LocaleProbe() {
  const { locale, setLocale, t, tr } = useI18n();
  return (
    <button type="button" onClick={() => setLocale("en")}>
      {locale}|{t.common.save}|{tr("Logged in as {name}", { name: "abc$&xyz" })}
    </button>
  );
}

function InterpolationProbe() {
  const { tr } = useI18n();
  return <span>{tr("{name}: {error}", { name: "literal {error}", error: "boom" })}</span>;
}

function RussianChromeProbe() {
  const { tr } = useI18n();
  return (
    <span>
      {tr("Copy")}|{tr("Copied!")}|{tr("This will reset {count} fields to their default values.", { count: 3 })}
    </span>
  );
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("hermes-locale", "en");
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  localStorage.clear();
});

describe("I18nProvider", () => {
  it("always exposes Russian and ignores legacy language choices", async () => {
    await act(async () => {
      root.render(
        <I18nProvider>
          <LocaleProbe />
        </I18nProvider>,
      );
    });

    const probe = container.querySelector("button") as HTMLButtonElement;
    expect(probe.textContent).toBe("ru|Сохранить|Выполнен вход: abc$&xyz");
    expect(document.documentElement.lang).toBe("ru");
    expect(document.documentElement.dir).toBe("ltr");

    await act(async () => probe.click());
    expect(probe.textContent).toBe("ru|Сохранить|Выполнен вход: abc$&xyz");
  });

  it("does not reinterpret placeholders inside user values", async () => {
    await act(async () => {
      root.render(
        <I18nProvider>
          <InterpolationProbe />
        </I18nProvider>,
      );
    });

    expect(container.textContent).toBe("literal {error}: boom");
  });

  it("keeps shared dialog and copy controls in Russian", async () => {
    await act(async () => {
      root.render(
        <I18nProvider>
          <RussianChromeProbe />
        </I18nProvider>,
      );
    });

    expect(container.textContent).toBe(
      "Копировать|Скопировано!|Для 3 полей будут восстановлены значения по умолчанию.",
    );
  });
});
