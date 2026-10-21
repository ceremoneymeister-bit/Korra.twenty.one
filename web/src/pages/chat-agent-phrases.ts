/** Имя главного агента, если владелец не переименовал профиль `default`. */
export const DEFAULT_AGENT_NAME = "Корра";

export interface ChatAgentPhrases {
  /** Пустой экран нового чата. */
  empty: string;
  /** Подсказка в поле ввода. */
  placeholder: string;
  /** Невидимая подпись поля ввода для экранного диктора. */
  fieldLabel: string;
  /** Строка активности, пока агент отвечает. */
  responding: string;
  /** Подпись области разговора. */
  region: string;
}

/**
 * Фразы чата с именем агента. Склонять произвольное имя нельзя, поэтому
 * «Корре/Коррой» остаются только для имени по умолчанию, а переименованный
 * главный агент и агенты вкладок называются в кавычках через «агент».
 * Раньше «Напишите Корре…» показывалось и после переименования (UX 17.10).
 */
export function chatAgentPhrases(name?: string | null): ChatAgentPhrases {
  const label = name?.trim();
  if (!label || label === DEFAULT_AGENT_NAME) {
    return {
      empty: "Напишите Корре своими словами — она на связи.",
      placeholder: "Напишите Корре…",
      fieldLabel: "Сообщение Корре",
      responding: "Корра отвечает",
      region: "Разговор с Коррой",
    };
  }
  return {
    empty: `Это чат с агентом «${label}» — напишите ему.`,
    placeholder: `Напишите агенту «${label}»…`,
    fieldLabel: `Сообщение агенту «${label}»`,
    responding: `«${label}» отвечает`,
    region: `Разговор с агентом «${label}»`,
  };
}
