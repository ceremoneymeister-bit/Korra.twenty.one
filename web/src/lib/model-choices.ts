/**
 * Список моделей для мастера агента и редактора модели профиля.
 *
 * Одна функция на оба экрана, чтобы «готов / нет ключа» считалось одинаково:
 * выбор модели чужого провайдера без ключа — это агент, который молча не
 * отвечает (парная работа с Астрой, 05.09).
 *
 * 06.09: на контуре владельца список — 51 строка подряд с английскими именами
 * провайдеров («ChatGPT or Codex Subscription · gpt-5.6-sol — готов»), и то же
 * имя уезжало в русскую подсказку под полем. Имена провайдеров переводятся
 * здесь, а мастер показывает выбор в два шага — провайдер, потом модель
 * (`groupModelChoices`).
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

/** Провайдер со своими моделями — для выбора в два шага. */
export interface ModelProviderGroup {
  provider: string;
  providerName: string;
  ready: boolean;
  choices: ModelChoice[];
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
 * Русские имена провайдеров по их идентификаторам в движке.
 *
 * Движок отдаёт английские маркетинговые названия («ChatGPT or Codex
 * Subscription», «OpenCode Free»), а подпись стоит в русской фразе «Доступ
 * к … настроен». Свои провайдеры владельца (`custom:*`) названы им самим —
 * их не переводим. Неизвестный идентификатор остаётся с именем от движка.
 */
const PROVIDER_NAMES_RU: Record<string, string> = {
  "openai-codex": "Подписка ChatGPT / Codex",
  "openai-api": "OpenAI (API)",
  anthropic: "Anthropic (Claude)",
  "opencode-free": "OpenCode — бесплатные модели",
  "opencode-zen": "OpenCode Zen",
  "opencode-go": "OpenCode Go",
  moa: "Смесь моделей (MoA)",
  gemini: "Google AI Studio (Gemini)",
  vertex: "Google Vertex AI",
  openrouter: "OpenRouter",
  deepseek: "DeepSeek",
  xai: "xAI (Grok)",
  "xai-oauth": "xAI Grok — подписка",
  copilot: "GitHub Copilot",
  huggingface: "Hugging Face",
  "ollama-cloud": "Ollama Cloud",
  lmstudio: "LM Studio (локально)",
  alibaba: "Qwen Cloud (Alibaba)",
  "kimi-coding": "Kimi (Moonshot)",
  minimax: "MiniMax",
  bedrock: "AWS Bedrock",
  "azure-foundry": "Azure Foundry",
  custom: "Свой сервер моделей",
};

/** Имя провайдера для интерфейса. */
export function providerDisplayName(slug: string, rawName?: string | null): string {
  const key = slug.trim().toLowerCase();
  return PROVIDER_NAMES_RU[key] ?? rawName?.trim() ?? key;
}

export interface BuildModelChoicesOptions {
  /** Идентификаторы провайдеров, которых в этом списке не показывать
   *  (например, виртуальный агрегатор `moa` в мастере для предпринимателя). */
  hide?: Iterable<string>;
}

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
  options: BuildModelChoicesOptions = {},
): ModelChoice[] {
  const hidden = new Set(
    [...HIDDEN_PROVIDERS, ...(options.hide ?? [])].map((slug) =>
      slug.toLowerCase(),
    ),
  );
  const ready: ModelChoice[] = [];
  const withoutKey: ModelChoice[] = [];
  for (const provider of providers ?? []) {
    const slug = (provider?.slug ?? "").trim();
    if (!slug || hidden.has(slug.toLowerCase())) continue;
    const providerName = providerDisplayName(slug, provider.name) || slug;
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

/**
 * Те же строки, собранные по провайдерам, в том же порядке (с ключом —
 * первыми). Мастер показывает сначала провайдера, потом его модели: пять
 * строк вместо пятидесяти.
 */
export function groupModelChoices(choices: readonly ModelChoice[]): ModelProviderGroup[] {
  const groups: ModelProviderGroup[] = [];
  const byProvider = new Map<string, ModelProviderGroup>();
  for (const choice of choices) {
    let group = byProvider.get(choice.provider);
    if (!group) {
      group = {
        provider: choice.provider,
        providerName: choice.providerName,
        ready: choice.ready,
        choices: [],
      };
      byProvider.set(choice.provider, group);
      groups.push(group);
    }
    group.choices.push(choice);
  }
  return groups;
}
