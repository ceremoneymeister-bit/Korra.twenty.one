/** Подписи каталога отделены от идентификаторов API: поиск и установка сохраняют совместимость. */
export function skillSourceLabel(id: string): string {
  const labels: Record<string, string> = {
    "hermes-index": "Каталог навыков Korra", official: "Встроенные навыки",
    "well-known": "Сайты навыков", url: "По ссылке", github: "GitHub",
    "skills-sh": "Skills.sh", clawhub: "ClawHub", lobehub: "LobeHub", "browse-sh": "Browse.sh",
  };
  return labels[id] ?? id;
}
