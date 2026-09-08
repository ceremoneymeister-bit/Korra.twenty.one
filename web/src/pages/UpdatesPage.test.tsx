// @vitest-environment jsdom
/**
 * Экран «Обновления» глазами владельца.
 *
 * Проверяем не разметку, а обещания: видно, что стоит; видно, что доступно и
 * что изменится; кнопка отправляет просьбу и ничего не перезапускает; пауза
 * названа заранее; а обрыв связи во время обновления читается как шаг
 * обновления, а не как поломка.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PageHeaderContext } from '@/contexts/page-header-context';
import type { ReleaseNote, UpdatesState } from '@/lib/api';
import UpdatesPage from './UpdatesPage';

const calls = vi.hoisted(() => ({
  getUpdatesState: vi.fn(),
  requestUpdate: vi.fn(),
  cancelUpdateRequest: vi.fn(),
}));
vi.mock('@/lib/api', () => ({ api: calls }));

let root: Root;
let container: HTMLDivElement;
const header = { setTitle: vi.fn(), setAfterTitle: vi.fn(), setEnd: vi.fn() };

const STEPS = [
  { key: 'fetch', title: 'Получаем новую сборку', detail: 'Скачиваем и проверяем образ.' },
  { key: 'drain', title: 'Ждём завершения текущих дел', detail: 'Начатые доводим до конца.' },
  { key: 'backup', title: 'Делаем резервную копию', detail: 'Память и документы сохраняются.' },
  { key: 'switch', title: 'Переключаем на новую версию', detail: 'Панель недоступна примерно минуту.' },
  { key: 'check', title: 'Проверяем, что всё поднялось', detail: 'Профили и ответ модели.' },
];

const INSTALLED = {
  release_id: 'K21-2026.09.07',
  title: 'Файлы в чате',
  published_at: '07.09.2026',
  summary: 'Документы ходят между чатом и «Файлами».',
  version: '0.21.0',
  sections: [{ heading: 'Что нового', items: [{ title: 'Документы прикрепляются', detail: 'Из рабочей папки.' }] }],
};

const AVAILABLE = {
  release_id: 'K21-2026.09.08',
  title: 'Обновление одной кнопкой',
  published_at: '08.09.2026',
  summary: 'Появился раздел «Обновления».',
  pause: 'около минуты',
  // Право нажать кнопку кабинет выдаёт поимённо и присылает в карточке.
  self_service: true,
  sections: [{
    heading: 'Что нового',
    items: [
      { title: 'Раздел «Обновления»', detail: 'Видно, что стоит и что доступно.' },
      { title: 'Возврат прежней версии', detail: 'Если что-то пошло не так.' },
      { title: 'Русский язык в переписке', detail: 'Ответы бота по-русски.' },
      { title: 'Русская справка', detail: 'В терминале.' },
      { title: 'Пятый пункт', detail: 'Скрыт под кнопкой.' },
    ],
  }],
};

function state(extra: Partial<UpdatesState> = {}): UpdatesState {
  return {
    installed: INSTALLED,
    available: null,
    up_to_date: true,
    managed_externally: true,
    request: null,
    progress: null,
    steps: STEPS,
    checked_at: 1,
    ...extra,
  } as UpdatesState;
}

async function render() {
  await act(async () => root.render(
    <PageHeaderContext.Provider value={header}><UpdatesPage /></PageHeaderContext.Provider>,
  ));
  // Первый опрос состояния — микрозадача внутри эффекта.
  await act(async () => { await Promise.resolve(); });
}

function button(text: string) {
  return [...container.querySelectorAll('button')].find(item => item.textContent?.includes(text)) ?? null;
}

async function click(element: Element | null) {
  expect(element).not.toBeNull();
  await act(async () => element?.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true })));
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe('что у меня стоит', () => {
  it('называет установленный выпуск и говорит, что новых нет', async () => {
    calls.getUpdatesState.mockResolvedValue(state());
    await render();
    expect(container.textContent).toContain('У вас установлено');
    expect(container.textContent).toContain('Файлы в чате');
    expect(container.textContent).toContain('K21-2026.09.07');
    expect(container.textContent).toContain('Новых выпусков пока нет');
    expect(header.setTitle).toHaveBeenCalledWith('Обновления');
  });

  it('честно говорит про ручную установку, а не предлагает кнопку', async () => {
    calls.getUpdatesState.mockResolvedValue(state({ managed_externally: false }));
    await render();
    expect(container.textContent).toContain('обновляется вручную');
    expect(button('Обновить')).toBeNull();
  });

  it('объясняет словами, если состояние не пришло', async () => {
    calls.getUpdatesState.mockRejectedValue(new Error('сеть'));
    await render();
    expect(container.textContent).toContain('Не удалось узнать состояние обновлений');
  });
});

describe('доступный выпуск', () => {
  beforeEach(() => {
    calls.getUpdatesState.mockResolvedValue(state({ available: AVAILABLE, up_to_date: false }));
  });

  it('показывает, что изменится, и предупреждает о паузе до нажатия', async () => {
    await render();
    expect(container.textContent).toContain('Доступен новый выпуск');
    expect(container.textContent).toContain('Обновление одной кнопкой');
    expect(container.textContent).toContain('Раздел «Обновления»');
    expect(container.textContent).toContain('Панель будет недоступна около минуты');
    expect(container.textContent).toContain('Память, документы, ключи и расписания останутся на месте');
  });

  it('прячет длинный список под «Показать всё»', async () => {
    await render();
    expect(container.textContent).not.toContain('Пятый пункт');
    await click(button('Показать всё'));
    expect(container.textContent).toContain('Пятый пункт');
  });

  it('отправляет просьбу и перечитывает состояние', async () => {
    calls.requestUpdate.mockResolvedValue({ ok: true, release_id: AVAILABLE.release_id });
    await render();
    await click(button('Обновить'));
    expect(calls.requestUpdate).toHaveBeenCalledWith('K21-2026.09.08');
    expect(calls.getUpdatesState).toHaveBeenCalledTimes(2);
  });

  it('показывает отказ сервера словами и не теряет кнопку', async () => {
    calls.requestUpdate.mockRejectedValue(new Error('Выпуск изменился. Обновите страницу.'));
    await render();
    await click(button('Обновить'));
    expect(container.textContent).toContain('Выпуск изменился');
    expect(button('Обновить')).not.toBeNull();
  });
});

describe('обновление разрешено не всем', () => {
  it('без права показывает выпуск, но не кнопку', async () => {
    // Право нажимать выдаёт кабинет поимённо. Кнопка без права обещала бы
    // обновление, которого не будет: просьба легла бы на диск и осталась там.
    calls.getUpdatesState.mockResolvedValue(state({
      available: { ...AVAILABLE, self_service: false },
      up_to_date: false,
    }));
    await render();
    expect(container.textContent).toContain('Доступен новый выпуск');
    expect(container.textContent).toContain('Раздел «Обновления»');
    expect(container.textContent).toContain('Этот выпуск установит наш оператор');
    expect(button('Обновить')).toBeNull();
    expect(calls.requestUpdate).not.toHaveBeenCalled();
  });

  it('считает отсутствие поля отсутствием права', async () => {
    // Старый или чужой кабинет поля не пришлёт: молчание не выдаёт права.
    const withoutTheField: ReleaseNote = { ...AVAILABLE };
    delete withoutTheField.self_service;
    calls.getUpdatesState.mockResolvedValue(state({ available: withoutTheField, up_to_date: false }));
    await render();
    expect(button('Обновить')).toBeNull();
    expect(container.textContent).toContain('Обновит оператор');
  });

  it('отзыв права убирает кнопку у уже отправленной просьбы', async () => {
    calls.getUpdatesState.mockResolvedValue(state({
      available: { ...AVAILABLE, self_service: false },
      up_to_date: false,
      request: { release_id: AVAILABLE.release_id, requested_at: 1, stale: false },
    }));
    await render();
    expect(container.textContent).not.toContain('Запрос отправлен');
    expect(button('Отменить запрос')).toBeNull();
  });
});

describe('просьба отправлена', () => {
  it('успокаивает и разрешает отменить', async () => {
    calls.getUpdatesState.mockResolvedValue(state({
      available: AVAILABLE,
      up_to_date: false,
      request: { release_id: AVAILABLE.release_id, requested_at: 1, stale: false },
    }));
    calls.cancelUpdateRequest.mockResolvedValue({ ok: true });
    await render();
    expect(container.textContent).toContain('Запрос отправлен');
    expect(container.textContent).toContain('можно закрыть страницу');
    await click(button('Отменить запрос'));
    expect(calls.cancelUpdateRequest).toHaveBeenCalled();
  });
});

describe('ход обновления', () => {
  const running = {
    status: 'running' as const,
    step: 'backup',
    phase: 'backup',
    release_id: AVAILABLE.release_id,
    message: 'Обновляем. Это займёт несколько минут.',
    error: '',
    started_at: 1,
    updated_at: 2,
    final: false,
    stale: false,
    installed_target: false,
  };

  it('показывает шаги и отмечает пройденные', async () => {
    calls.getUpdatesState.mockResolvedValue(state({ available: AVAILABLE, up_to_date: false, progress: running }));
    await render();
    const steps = [...container.querySelectorAll('.upd-steps li')];
    expect(steps.map(item => item.getAttribute('data-state'))).toEqual([
      'done', 'done', 'active', 'pending', 'pending',
    ]);
    expect(container.textContent).toContain('Идёт обновление');
    // Пока идёт обновление, карточку «обновиться» не показываем — нажимать нечего.
    expect(button('Обновить')).toBeNull();
  });

  it('считает обрыв связи шагом обновления, а не поломкой', async () => {
    calls.getUpdatesState.mockResolvedValueOnce(state({ progress: running }));
    await render();
    calls.getUpdatesState.mockRejectedValue(new Error('Failed to fetch'));
    await act(async () => { await vi.advanceTimersByTimeAsync(4100); });
    expect(container.textContent).toContain('Панель перезапускается');
    expect(container.textContent).not.toContain('Не удалось узнать состояние');
  });

  it('перестаёт обещать «несколько минут», когда вестей давно нет', async () => {
    // Кабинет присылает ход раз в десять секунд; замолчать надолго он может,
    // только если остановлен или потерял связь. Держать в этот момент бодрое
    // «Обновляем» — врать владельцу тем увереннее, чем дольше он смотрит.
    calls.getUpdatesState.mockResolvedValue(state({ progress: { ...running, stale: true } }));
    await render();
    expect(container.textContent).toContain('Об обновлении давно нет вестей');
    expect(container.textContent).toContain('Ваши данные на месте');
    expect(container.textContent).not.toContain('Это займёт несколько минут');
    // И не притворяемся, что работа идёт прямо сейчас.
    expect(container.querySelector('.upd-badge[data-tone="working"]')).toBeNull();
  });

  it('объясняет неудачу и успокаивает про данные', async () => {
    calls.getUpdatesState.mockResolvedValue(state({
      progress: { ...running, status: 'failed', final: true, error: 'Модель не ответила после переключения' },
    }));
    await render();
    expect(container.textContent).toContain('Обновление не прошло');
    expect(container.textContent).toContain('прежняя версия работает как раньше');
    expect(container.textContent).toContain('Модель не ответила после переключения');
  });

  it('после успеха показывает установленный выпуск без карточки хода', async () => {
    calls.getUpdatesState.mockResolvedValue(state({
      progress: {
        ...running, status: 'succeeded', step: 'check', phase: 'complete',
        final: true, installed_target: true, release_id: INSTALLED.release_id,
      },
    }));
    await render();
    expect(container.querySelector('.upd-steps')).toBeNull();
    expect(container.textContent).toContain('У вас установлено');
    expect(container.textContent).toContain('Обновление прошло, всё на месте');
  });

  it('после отката говорит, что вернули прежнюю версию, и не показывает шаги', async () => {
    // Откат по смыслу возвращает НЕ целевой выпуск: installed_target здесь
    // всегда false, и шаги обновления после него уже ничего не объясняют.
    calls.getUpdatesState.mockResolvedValue(state({
      available: AVAILABLE,
      up_to_date: false,
      progress: {
        ...running, status: 'rolled_back', step: 'check', phase: 'rollback_complete',
        final: true, installed_target: false,
      },
    }));
    await render();
    expect(container.querySelector('.upd-steps')).toBeNull();
    expect(container.textContent).toContain('Вернули прежнюю версию');
    expect(container.textContent).toContain('Данные, память и доступы остались как были');
    // Выпуск снова доступен: после отката его можно поставить заново.
    expect(button('Обновить')).not.toBeNull();
  });
});
