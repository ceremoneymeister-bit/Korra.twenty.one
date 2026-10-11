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
  /** Вариант для клиентского кабинета, где нет части разделов (K21-312). */
  client?: Partial<Pick<HelpArticle, 'summary' | 'action' | 'sections' | 'problems'>>;
}

export const step = (title: string, action: string, result: string): HelpStep => ({ title, action, result });
export const link = (label: string, to: string): HelpLink => ({ label, to });
export const helpPath = (id: string) => `/help/${id}`;

const pathOf = (to: string) => to.split(/[?#]/)[0];

/**
 * Помощь для клиентского кабинета: применяет `client`-варианты и убирает
 * инструкции и ссылки на разделы, которых в его меню нет, чтобы подсказка не
 * вела в «Такого раздела нет».
 */
export function forClientCabinet(articles: HelpArticle[], hiddenPaths: readonly string[]): HelpArticle[] {
  const adapted = articles.map(({ client, ...article }) => ({ ...article, ...client }));
  const kept = adapted.filter(article => !hiddenPaths.includes(pathOf(article.action.to)));
  const keptIds = new Set(kept.map(article => article.id));
  const reachable = (item: HelpLink) => {
    const path = pathOf(item.to);
    if (hiddenPaths.includes(path)) return false;
    return !path.startsWith('/help/') || keptIds.has(path.split('/')[2]);
  };
  return kept.map(article => ({
    ...article,
    related: article.related.filter(id => keptIds.has(id)),
    sections: article.sections.map(section => ({ ...section, links: section.links?.filter(reachable) })),
    problems: article.problems.map(({ link: target, ...problem }) => target && !reachable(target) ? problem : { ...problem, link: target }),
  }));
}

/** Поиск по смысловым подсказкам и всему тексту, включая шаги и частые ошибки. */
export function searchHelp(articles: HelpArticle[], query: string): HelpArticle[] {
  const normalize = (value: string) => value.toLocaleLowerCase('ru').replaceAll('ё', 'е');
  const filler = new Set(['как', 'мне', 'что', 'если', 'где', 'в', 'на', 'и', 'к', 'по', 'для', 'из', 'с', 'со', 'у', 'а', 'это', 'делать']);
  const words = normalize(query).split(/[^\p{L}\p{N}]+/u).filter(word => word && !filler.has(word));
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
