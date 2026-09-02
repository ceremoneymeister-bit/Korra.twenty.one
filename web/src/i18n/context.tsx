import { createContext, useContext, useCallback, useEffect, type ReactNode } from "react";
import type { Locale, Translations } from "./types";
import { ru } from "./ru";

// Public locale metadata follows the product contract: Korra 21 exposes only
// Russian even if an old plugin still imports the legacy switcher component.
export const LOCALE_META = {
  ru: { name: "Русский" },
} as const;

const PRODUCT_LOCALE: Locale = "ru";

interface I18nContextValue {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: Translations;
  tr: (key: string, values?: Record<string, string | number>) => string;
}

function interpolate(
  message: string,
  values?: Record<string, string | number>,
): string {
  const replacements = values ?? {};
  return message.replace(/\{([^{}]+)\}/g, (token, name: string) => {
    if (!Object.prototype.hasOwnProperty.call(replacements, name)) return token;
    return String(replacements[name]);
  });
}

const I18nContext = createContext<I18nContextValue>({
  locale: PRODUCT_LOCALE,
  setLocale: () => {},
  t: ru,
  tr: (key, values) => interpolate(ru.dashboard[key] ?? key, values),
});

export function I18nProvider({ children }: { children: ReactNode }) {
  // Korra 21 is a Russian-only product. Keep the setter as a no-op for the
  // stable plugin/i18n context contract, while preventing persisted legacy
  // choices or browser preferences from changing the visible language.
  const setLocale = useCallback((locale: Locale) => {
    void locale;
  }, []);

  useEffect(() => {
    if (typeof document === "undefined") return;
    document.documentElement.lang = PRODUCT_LOCALE;
    document.documentElement.dir = "ltr";
  }, []);

  const tr = useCallback(
    (key: string, values?: Record<string, string | number>) => {
      return interpolate(ru.dashboard[key] ?? key, values);
    },
    [],
  );

  const value: I18nContextValue = {
    locale: PRODUCT_LOCALE,
    setLocale,
    t: ru,
    tr,
  };

  return (
    <I18nContext.Provider value={value}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n() {
  return useContext(I18nContext);
}
