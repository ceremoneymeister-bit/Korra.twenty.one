// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PageHeaderContext } from '@/contexts/page-header-context';
import { HELP_ARTICLES } from './help/articles';
import ClientHelpPage from './ClientHelpPage';

const appearance = vi.hoisted(() => ({ themeName: 'light' }));
vi.mock('@/themes', () => ({ useTheme: () => appearance }));
let root: Root;
let container: HTMLDivElement;
const header = { setTitle: vi.fn(), setAfterTitle: vi.fn(), setEnd: vi.fn() };
const scroll = vi.fn();
const base = '/c/uchebny';

function BackButton() {
  const navigate = useNavigate();
  return <button onClick={() => void navigate(-1)}>Назад в браузере</button>;
}

async function render(path: string) {
  await act(async () => root.render(<MemoryRouter basename={base} initialEntries={[base + path]}>
    <PageHeaderContext.Provider value={header}>
      <BackButton />
      <Routes>
        <Route path="/help" element={<ClientHelpPage />} />
        <Route path="/help/:article" element={<ClientHelpPage />} />
        <Route path="/kanban" element={<p>Доска открыта</p>} />
      </Routes>
    </PageHeaderContext.Provider>
  </MemoryRouter>));
}
async function click(element: Element | null) {
  expect(element).not.toBeNull();
  await act(async () => element?.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true })));
}

beforeEach(() => {
  appearance.themeName = 'light';
  vi.clearAllMocks();
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: scroll });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe('Страницы помощи в кабинете', () => {
  it.each(HELP_ARTICLES)('прямой адрес $id открывает инструкцию и её оглавление', async article => {
    await render(`/help/${article.id}`);
    expect(container.querySelector('#help-heading')?.textContent).toBe(article.title);
    expect(header.setTitle).toHaveBeenCalledWith('Помощь');
    const toc = container.querySelector('nav[aria-label="На этой странице"]');
    for (const section of article.sections) {
      expect(toc?.querySelector(`a[href="${base}/help/${article.id}#${section.id}"]`)).not.toBeNull();
      expect(container.querySelector(`#${section.id}`)).not.toBeNull();
    }
    for (const anchor of container.querySelectorAll<HTMLAnchorElement>('a')) expect(anchor.getAttribute('href')).toMatch(/^\/c\/uchebny\//);
    for (const image of container.querySelectorAll<HTMLImageElement>('img')) expect(image.getAttribute('src')).toMatch(/^\/c\/uchebny\/help\/.+-light\.webp$/);
  });

  it('переходы между оглавлением, шагом, доской и назад сохраняют кабинет', async () => {
    await render('/help');
    await click(container.querySelector(`a[href="${base}/help/kanban#create"]`));
    expect(document.activeElement?.id).toBe('create');
    await click(container.querySelector('.help-article-header a'));
    expect(container.textContent).toContain('Доска открыта');
    await click(container.querySelector('button'));
    expect(container.querySelector('#help-heading')?.textContent).toContain('Поручить работу');
    await click(container.querySelector(`a[href="${base}/help"]`));
    expect(container.querySelector('input[type="search"]')).not.toBeNull();
  });

  it('поисковый запрос живёт в адресе, пустой результат можно сбросить', async () => {
    await render('/help?q=несуществующееслово');
    expect(container.querySelector('[role="status"]')?.textContent).toContain('ничего не найдено');
    await click(container.querySelector('.help-search-field button'));
    expect(container.querySelectorAll('.help-catalog-item').length).toBe(HELP_ARTICLES.length);
  });

  it('тёмная тема получает свои изображения с тем же префиксом', async () => {
    appearance.themeName = 'dark';
    await render('/help/agents');
    for (const image of container.querySelectorAll('img')) expect(image.getAttribute('src')).toMatch(/^\/c\/uchebny\/help\/.+-dark\.webp$/);
  });

  it('неизвестная инструкция и повреждённый якорь не ломают помощь', async () => {
    await render('/help/missing#%E0%A4%A');
    expect(container.textContent).toContain('Такой инструкции пока нет');
    await click(container.querySelector(`a[href="${base}/help"]`));
    expect(container.textContent).toContain('Что вы хотите сделать?');
  });
});
