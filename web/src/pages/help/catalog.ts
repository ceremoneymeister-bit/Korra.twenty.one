export interface HelpStep {
  title: string;
  action: string;
  result: string;
}

export interface HelpLink {
  label: string;
  to: string;
}

export interface HelpSection {
  id: string;
  title: string;
  intro?: string;
  steps: HelpStep[];
  note?: string;
  example?: string;
  links?: HelpLink[];
  image?: HelpImage;
}

export interface HelpImage {
  name: string;
  alt: string;
  caption: string;
  width: number;
  height: number;
}

export interface HelpArticle {
  id: string;
  label: string;
  title: string;
  summary: string;
  group: 'Начало и работа' | 'Настройки и связь' | 'Служебные разделы';
  keywords: string;
  action: HelpLink;
  sections: HelpSection[];
  problems: { question: string; answer: string; link?: HelpLink }[];
  related: string[];
}

export const step = (title: string, action: string, result: string): HelpStep => ({ title, action, result });
export const link = (label: string, to: string): HelpLink => ({ label, to });
export const helpPath = (id: string) => `/help/${id}`;

/** Поиск по смысловым подсказкам и всему тексту, включая шаги и частые ошибки. */
export function searchHelp(articles: HelpArticle[], query: string): HelpArticle[] {
  const normalize = (value: string) => value.toLocaleLowerCase('ru').replaceAll('ё', 'е');
  const words = normalize(query).trim().split(/\s+/).filter(Boolean);
  return articles.filter(article => {
    const text = normalize([
      article.label, article.title, article.summary, article.keywords,
      ...article.sections.flatMap(section => [section.title, section.intro, section.note, section.example,
        ...section.steps.flatMap(item => [item.title, item.action, item.result])]),
      ...article.problems.flatMap(problem => [problem.question, problem.answer]),
    ].join(' '));
    return words.every(word => text.includes(word));
  });
}
