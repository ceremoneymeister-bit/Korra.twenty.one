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
