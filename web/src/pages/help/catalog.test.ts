import { describe, expect, it } from 'vitest';
import { CLIENT_SETTINGS_PATHS, SERVICE_PATHS } from '@/lib/product-nav';
import { HELP_ARTICLES, HELP_GROUPS, QUICK_HELP } from './articles';
import { helpPath, searchHelp } from './catalog';

function checkHelpLink(to: string) {
  const url = new URL(to, 'https://panel.test');
  expect(url.origin).toBe('https://panel.test');
  const id = url.pathname.split('/')[2];
  if (!id) return;
  const article = HELP_ARTICLES.find(item => item.id === id);
  expect(article, `Нет инструкции для ${to}`).toBeDefined();
  if (url.hash) expect(article?.sections.some(section => section.id === url.hash.slice(1)), `Нет шага для ${to}`).toBe(true);
}

describe('Оглавление помощи', () => {
  it('каждый видимый раздел панели имеет инструкцию с переходом к нему', () => {
    const routes = ['/agents', '/files', '/sessions', '/cron', '/kanban', '/achievements', ...CLIENT_SETTINGS_PATHS, ...SERVICE_PATHS];
    const explained = new Set(HELP_ARTICLES.map(article => article.action.to));
    for (const path of routes.filter(path => path !== '/help')) expect(explained.has(path), path).toBe(true);
  });

  it('все перекрёстные ссылки и быстрые переходы ведут на существующую инструкцию и шаг', () => {
    for (const item of QUICK_HELP) checkHelpLink(item.to);
    for (const article of HELP_ARTICLES) {
      for (const id of article.related) checkHelpLink(helpPath(id));
      const links = [...article.sections.flatMap(section => section.links ?? []), ...article.problems.flatMap(problem => problem.link ? [problem.link] : [])];
      for (const item of links) checkHelpLink(item.to);
    }
  });

  it('страницы и шаги имеют уникальные адреса и не теряются в группах', () => {
    expect(new Set(HELP_ARTICLES.map(article => article.id)).size).toBe(HELP_ARTICLES.length);
    for (const article of HELP_ARTICLES) {
      expect(HELP_GROUPS).toContain(article.group);
      expect(article.id).toMatch(/^[a-z]+$/);
      expect(new Set(article.sections.map(section => section.id)).size).toBe(article.sections.length);
      for (const section of article.sections) {
        expect(section.id).toMatch(/^[a-z-]+$/);
        expect(section.steps.length).toBeGreaterThan(0);
        for (const item of section.steps) {
          expect(item.action.trim()).not.toBe('');
          expect(item.result.trim()).not.toBe('');
        }
      }
    }
  });

  it.each([
    ['  ГОЛОС  ', 'chat'], ['телеграм', 'telegram'], ['ЕЖЕНЕДЕЛЬНО', 'tasks'],
    ['не отвечает', 'troubleshooting'], ['Как мне восстановить файл?', 'files'], ['Что делать, если агент молчит?', 'troubleshooting'], ['роль агента', 'agents'], ['исходники', 'files'], ['доработку', 'kanban'],
  ])('поиск «%s» находит нужный рабочий сценарий', (query, id) => {
    expect(searchHelp(HELP_ARTICLES, query).map(article => article.id)).toContain(id);
  });

  it('поиск учитывает несколько слов, ё/е, текст шагов и пустой запрос', () => {
    expect(searchHelp(HELP_ARTICLES, '  ')).toEqual(HELP_ARTICLES);
    expect(searchHelp(HELP_ARTICLES, 'тёмную')).toEqual(searchHelp(HELP_ARTICLES, 'темную'));
    expect(searchHelp(HELP_ARTICLES, 'доступ истечь').map(article => article.id)).toContain('keys');
    expect(searchHelp(HELP_ARTICLES, 'несуществующееслово')).toEqual([]);
  });
});
