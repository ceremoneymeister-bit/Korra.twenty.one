import { describe, expect, it } from 'vitest';
import { CLIENT_CABINET_HIDDEN_PATHS, CLIENT_SETTINGS_PATHS, SERVICE_PATHS } from '@/lib/product-nav';
import { HELP_ARTICLES, HELP_GROUPS, QUICK_HELP } from './articles';
import { forClientCabinet, helpPath, searchHelp } from './catalog';

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

describe('Помощь клиентского кабинета (K21-312)', () => {
  const client = forClientCabinet(HELP_ARTICLES, CLIENT_CABINET_HIDDEN_PATHS);
  const hidden = (to: string) => CLIENT_CABINET_HIDDEN_PATHS.includes(to.split(/[?#]/)[0]);

  it('не ведёт в разделы, которых нет в клиентском меню', () => {
    const ids = new Set(client.map(article => article.id));
    for (const article of client) {
      const links = [article.action, ...article.sections.flatMap(section => section.links ?? []),
        ...article.problems.flatMap(problem => problem.link ? [problem.link] : [])];
      for (const item of links) {
        expect(hidden(item.to), `${article.id}: ${item.to}`).toBe(false);
        if (item.to.startsWith('/help/')) expect(ids.has(item.to.split(/[?#]/)[0].split('/')[2]), `${article.id}: ${item.to}`).toBe(true);
      }
      for (const id of article.related) expect(ids.has(id), `${article.id}: ${id}`).toBe(true);
    }
    expect(client.map(article => article.id)).not.toContain('channels');
  });

  it('Telegram для клиента подключает команда Korra, а не раздел «Каналы»', () => {
    const telegram = client.find(article => article.id === 'telegram')!;
    expect(telegram).toBeDefined();
    const text = JSON.stringify(telegram);
    expect(text).not.toContain('Каналы');
    expect(text).not.toContain('Служебное');
    expect(text).toContain('команда Korra');
    const owner = HELP_ARTICLES.find(article => article.id === 'telegram')!;
    expect(owner.action.to).toBe('/channels');
  });

  it('оставляет рабочие ссылки на оставшиеся инструкции и шаги', () => {
    for (const article of client) {
      for (const item of article.sections.flatMap(section => section.links ?? [])) {
        if (!item.to.startsWith('/help/')) continue;
        const url = new URL(item.to, 'https://panel.test');
        const target = client.find(candidate => candidate.id === url.pathname.split('/')[2])!;
        if (url.hash) expect(target.sections.some(section => section.id === url.hash.slice(1)), item.to).toBe(true);
      }
    }
  });
});
