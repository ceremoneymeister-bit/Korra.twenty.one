/**
 * Список моделей для мастера агента и редактора модели профиля.
 *
 * Одна функция на оба экрана, чтобы «готов / нет ключа» считалось одинаково:
 * выбор модели чужого провайдера без ключа — это агент, который молча не
 * отвечает (парная работа с Астрой, 05.09).
 */

import type { ModelOptionProvider } from "@/lib/api";

/** Одна строка списка моделей: провайдер, модель и готовность его ключа. */
export interface ModelChoice {
  provider: string;
  providerName: string;
  model: string;
  /** Подпись в списке — её же показывает свёрнутый Select. */
  label: string;
  /** Ключ провайдера уже настроен: агент сможет ответить сразу. */
  ready: boolean;
}

/** Значение опции: провайдер и модель неразделимы, иначе выбор модели чужого
 *  провайдера молча уводит профиль на неработающий ключ. */
export const modelKey = (provider: string | null, model: string | null) =>
  provider && model ? `${provider}\u0000${model}` : "";

export const choiceKey = (choice: ModelChoice) =>
  modelKey(choice.provider, choice.model);

/** Nous в Korra 21 не предлагаем: контуры работают на своих провайдерах. */
const HIDDEN_PROVIDERS = new Set(["nous"]);

export const READY_MARK = "готов";
export const NO_KEY_MARK = "нет ключа";

/**
 * Список моделей для мастера: сначала провайдеры с рабочими ключами.
 *
 * Признак готовности даёт сам движок — `authenticated` в строке провайдера
 * (`_apply_picker_hints`, korra_cli/inventory.py): `false` стоит у строк-
 * заготовок без ключа и у настроенного провайдера, потерявшего доступ.
 * Ровно так же его читает CronPage. Выбрать провайдера без ключа мастер даёт
 * — но подпись и подсказка под списком говорят, что агент промолчит, пока
 * ключ не появится в «Ключах».
 */
export function buildModelChoices(
  providers: ModelOptionProvider[] | undefined,
): ModelChoice[] {
  const ready: ModelChoice[] = [];
  const withoutKey: ModelChoice[] = [];
  for (const provider of providers ?? []) {
    const slug = (provider?.slug ?? "").trim();
    if (!slug || HIDDEN_PROVIDERS.has(slug.toLowerCase())) continue;
    const providerName = provider.name?.trim() || slug;
    const isReady = provider.authenticated !== false;
    const bucket = isReady ? ready : withoutKey;
    for (const model of provider.models ?? []) {
      if (!model) continue;
      bucket.push({
        provider: slug,
        providerName,
        model,
        label: `${providerName} · ${model} — ${isReady ? READY_MARK : NO_KEY_MARK}`,
        ready: isReady,
      });
    }
  }
  return [...ready, ...withoutKey];
}
