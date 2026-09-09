// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ThemeSwitcher } from './ThemeSwitcher';
const state = vi.hoisted(() => ({ narrow: true, setEvening: vi.fn(async () => true) }));
vi.mock('@nous-research/ui/hooks/use-below-breakpoint', () => ({ useBelowBreakpoint: () => state.narrow }));
vi.mock('@/i18n', () => ({ useI18n: () => ({ t: { common: { close: 'Закрыть' }, theme: {} } }) }));
vi.mock('@/themes', () => ({ THEME_DEFAULT_FONT_ID: 'theme', useTheme: () => ({
  themeName: 'light', availableThemes: [{ name: 'light', label: 'Светлая' }, { name: 'dark', label: 'Тёмная' }],
  setTheme: vi.fn(), fontId: 'theme', fontChoices: [], setFont: vi.fn(), saveState: 'error', saveError: 'Ошибка сохранения',
  retryTheme: vi.fn(), preference: { evening: { disabled: true } }, setEvening: state.setEvening,
}) }));
let root: Root, host: HTMLDivElement;
beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks(); Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true }); Object.defineProperty(window, 'matchMedia', { configurable: true, value: () => ({ matches: true }) }); host = document.createElement('div'); document.body.append(host); root = createRoot(host); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.useRealTimers(); });
it.each([true, false])('keeps explicit evening reset inside the active menu (mobile=%s)', async (mobile) => {
  state.narrow = mobile;
  await act(async () => root.render(<ThemeSwitcher dropUp />));
  await act(async () => (host.querySelector('[aria-haspopup=dialog]') as HTMLButtonElement).click());
  await act(async () => vi.advanceTimersByTimeAsync(100));
  const dialog = document.querySelector('[role=dialog]')!;
  const reset = [...dialog.querySelectorAll('button')].find(button => button.textContent?.includes('Разрешить вечернее предложение'));
  expect(reset).toBeDefined();
  expect(dialog.querySelector('[role=alert]')?.textContent).toBe('Ошибка сохранения');
  await act(async () => reset!.click());
  expect(state.setEvening).toHaveBeenCalledWith('enable');
});
