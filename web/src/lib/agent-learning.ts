/**
 * Чистые помощники панели «Обучение и настройки» агента.
 *
 * Вынесены из компонента, чтобы правила проверялись тестами, а файл
 * компонента экспортировал только компонент (быстрое обновление в dev).
 */

import type { ProfileMaterialInfo } from "@/lib/api";

/** Что принимаем как файл материала — то же, что агент читает штатными
 *  средствами (решение с Астрой 06.09). */
export const MATERIAL_FILE_EXTENSIONS = [
  ".txt",
  ".md",
  ".csv",
  ".json",
  ".pdf",
  ".docx",
  ".xlsx",
] as const;

export const MATERIAL_FILE_LIMIT_BYTES = 10 * 1024 * 1024;

/**
 * Вопрос для проверки материала.
 *
 * Без прямой просьбы прочитать навык агент при старом каталоге навыков может
 * ответить из общих знаний — и проверка ничего не докажет (Астра, 06.09).
 */
export function materialProbePrompt(
  material: Pick<ProfileMaterialInfo, "name">,
  question = "",
): string {
  return `Прочитай навык «${material.name}» и его источники, затем ответь: ${question}`;
}

/** Причина, по которой файл не годится, или `null`. Проверяем до отправки,
 *  чтобы человек увидел её сразу, а не после загрузки 10 МБ. */
export function materialFileProblem(file: Pick<File, "name" | "size">): string | null {
  const lower = file.name.toLowerCase();
  if (!MATERIAL_FILE_EXTENSIONS.some((extension) => lower.endsWith(extension))) {
    const allowed = MATERIAL_FILE_EXTENSIONS.map((extension) =>
      extension.slice(1).toUpperCase(),
    ).join(", ");
    return `Этот формат здесь не поддерживается. Подходят ${allowed}.`;
  }
  if (file.size === 0) return "Файл пуст. Выберите файл с содержимым.";
  if (file.size > MATERIAL_FILE_LIMIT_BYTES) {
    return "Файл больше 10 МБ — разбейте его или сохраните только нужную часть.";
  }
  return null;
}

/** Разряды через неразрывный пробел: «2 200», без зависимости от ICU среды. */
export function formatChars(value: number): string {
  return String(Math.max(0, Math.round(value))).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}

/** Explicit owner-initiated learning turn. The current corrected work is
 * already complete; this bounded turn may only distil reusable guidance. */
export function correctionLearningPrompt(
  source: string,
  approved: string,
  note = "",
): string {
  const ownerNote = note.trim()
    ? `\nПояснение владельца:\n${note.trim()}\n`
    : "";
  return [
    "Это уже исправленный и утверждённый владельцем результат. Не переделывай текущую работу и не подменяй её обучением.",
    "Сравни исходный и утверждённый варианты. Отдели разовую правку от предпочтения владельца и повторяемого способа.",
    "Если есть переносимое правило, обнови одну подходящую методику через skill_manage и обязательно приложи learning receipt:",
    "scope, ограниченную область applies_to, точное правило rule из записанного skill, исходный и утверждённый примеры, private_markers (стороны, реквизиты, суммы и особые условия) и 1–8 наблюдаемых rubric-пунктов для другого примера.",
    "Не переноси private_markers в правило. Если это разовая правка или данных недостаточно, не меняй skill и честно скажи об этом. До отдельной проверки называй результат только «сохранено, ещё не проверено».",
    ownerNote,
    `Исходный результат:\n${source.trim()}`,
    `\nУтверждённый результат:\n${approved.trim()}`,
  ].join("\n\n");
}

/** A deferred example deliberately omits the learned rule. Selection and
 * transfer must come from the profile's actual skill in a fresh chat. */
export function deferredLearningPrompt(example: string): string {
  return [
    "Выполни это как новый самостоятельный пример, используя актуальные навыки профиля.",
    "Не проси повторить сохранённое правило и не оценивай собственный ответ — верни только готовый результат.",
    example.trim(),
  ].join("\n\n");
}
