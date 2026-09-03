/** Keep arbitrary backend copy from leaking English prose into Korra 21. */
export function russianInterfaceText(
  value: unknown,
  fallback = "",
): string {
  const text = typeof value === "string" ? value.trim() : "";
  const hasRussian = /[А-Яа-яЁё]/.test(text);
  const hasEnglishPhrase = /\b[A-Za-z]{3,}\b(?:[\s,:;()\-–—]+\b[A-Za-z]{3,}\b)+/.test(text);
  return hasRussian && !hasEnglishPhrase ? text : fallback;
}

/** Короткая подпись (категория навыков, имя плагина: «MLOps», «Kanban») —
 *  не проза, её не подменяем: иначе 24 категории схлопывались в «Общие»
 *  (QA 03.09). Длинные английские фразы по-прежнему уходят в запасное слово. */
export function russianInterfaceLabel(value: unknown, fallback = ""): string {
  const text = typeof value === "string" ? value.trim() : "";
  if (!text) return fallback;
  const words = text.split(/\s+/).filter(Boolean);
  if (words.length <= 3 && text.length <= 32) return text;
  return russianInterfaceText(text, fallback);
}
