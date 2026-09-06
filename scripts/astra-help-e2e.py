"""Проверка помощи только в одноразовом astra-help, в корне и за префиксом кабинета.

Запуск: python3 scripts/astra-help-e2e.py
Нужны установленный Playwright с Chromium и aiohttp. Прокси занимает случайный
локальный порт и закрывается при выходе. Боевые адреса не настраиваются.
"""
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import threading
from urllib.parse import urlparse

from aiohttp import ClientSession, web
from playwright.sync_api import expect, sync_playwright

DIRECT = 'http://127.0.0.1:9144'
PREFIX = '/c/uchebny'
OUT = Path('/root/Antigravity/projects/Korra 21/ui pack/qa')


@contextmanager
def cabinet_proxy():
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    state = {}

    async def handler(request):
        if not request.path.startswith(PREFIX + '/'):
            return web.Response(status=404)
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in ('host', 'connection', 'content-length')}
        headers['X-Forwarded-Prefix'] = PREFIX
        async with ClientSession() as session:
            async with session.request(request.method, DIRECT + request.raw_path[len(PREFIX):],
                                       headers=headers, data=await request.read()) as response:
                result = web.StreamResponse(status=response.status, headers={
                    k: v for k, v in response.headers.items()
                    if k.lower() not in ('transfer-encoding', 'content-encoding', 'content-length', 'connection')})
                await result.prepare(request)
                try:
                    async for chunk in response.content.iter_any():
                        await result.write(chunk)
                except (ConnectionResetError, asyncio.CancelledError):
                    pass
                return result

    async def start():
        app = web.Application()
        app.router.add_route('*', '/{path:.*}', handler)
        runner = web.AppRunner(app, shutdown_timeout=1)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        state.update(runner=runner, port=site._server.sockets[0].getsockname()[1])
        ready.set()

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start())
        loop.run_forever()
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(10), 'Не удалось запустить проверочный прокси'
    try:
        yield f'http://127.0.0.1:{state["port"]}{PREFIX}'
    finally:
        asyncio.run_coroutine_threadsafe(state['runner'].cleanup(), loop).result(timeout=10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=10)


def guard():
    import yaml
    info = json.loads(subprocess.check_output(['docker', 'inspect', 'astra-help']))[0]
    assert info['Name'] == '/astra-help' and info['State']['Running']
    data = Path(next(m['Source'] for m in info['Mounts'] if m['Destination'] == '/opt/data'))
    assert str(data).startswith('/tmp/astra-help-data.')
    assert 'TELEGRAM_' not in (data / '.env').read_text()
    config = yaml.safe_load((data / 'config.yaml').read_text())
    assert 'telegram' not in config and config['kanban']['dispatch_in_gateway'] is False


def shot(page, name):
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / f'help-{name}.png'))


def goto(page, base, path):
    response = page.goto(base + path, wait_until='networkidle')
    assert response and response.ok
    expect(page.locator('#help-heading')).to_be_visible()
    expect(page.get_by_role('banner').get_by_role('heading')).to_have_text('Помощь')


def fits(page):
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Переполнение страницы'
    assert page.locator('.korra-help').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1'), 'Переполнение помощи'
    assert 'Onest' in page.locator('.korra-help').evaluate('(el) => getComputedStyle(el).fontFamily')


def main():
    guard()
    with cabinet_proxy() as proxy, sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        checks = 0
        assets = set()
        for base in [DIRECT, proxy]:
            prefix = '' if base == DIRECT else PREFIX
            goto(page, base, '/help')
            assert page.evaluate('window.__HERMES_BASE_PATH__') == prefix
            paths = page.locator('.help-catalog-item').evaluate_all('(items) => items.map(el => el.getAttribute("href"))')
            assert len(paths) == len(set(paths)) and paths
            for path in paths:
                goto(page, base, path[len(prefix):])
                fits(page)
                assert page.locator('.help-steps li').count() > 0
                for item in page.locator('.korra-help a').evaluate_all('(items) => items.map(el => el.getAttribute("href"))'):
                    assert item.startswith(prefix + '/'), item
                for item in page.locator('.help-on-page a').evaluate_all('(items) => items.map(el => el.hash.slice(1))'):
                    assert page.locator('#' + item).count() == 1, item
                for details in page.locator('.help-illustration').all():
                    details.locator('summary').click()
                    image = details.locator('figure > img')
                    image.scroll_into_view_if_needed()
                    expect(image).to_be_visible()
                    image.evaluate('(el) => el.decode()')
                    dims = image.evaluate('(el) => [el.naturalWidth, el.naturalHeight, el.width, el.height]')
                    assert dims[0] > 0 and dims[1] > 0
                    src = image.get_attribute('src')
                    assert src.startswith(prefix + '/help/') and src.endswith('.webp')
                    assert int(image.get_attribute('width')) == dims[0]
                    assert int(image.get_attribute('height')) == dims[1]
                    # Повторяется только обрыв соединения; HTTP-ошибки остаются ошибками проверки.
                    response = context.request.get(urlparse(base)._replace(path=src, query='', fragment='').geturl(), max_retries=2)
                    assert response.ok and response.headers['content-type'].startswith('image/webp')
                    assets.add(src)
                checks += 1
            print(f'Открыты все страницы: {len(paths)}; база: {prefix or "корень"}', flush=True)

        goto(page, proxy, '/help')
        page.evaluate('window.helpNavigationMarker = 21')
        page.locator('.help-quick').filter(has_text='Дать агенту поручение').click()
        expect(page).to_have_url(proxy + '/help/kanban#create')
        expect(page.locator('#create')).to_be_focused()
        top = page.locator('#create').bounding_box()['y']
        assert page.locator('main').bounding_box()['y'] <= top < 150
        page.locator('.help-article-header a').click()
        expect(page).to_have_url(proxy + '/kanban')
        expect(page.get_by_role('button', name='Новое поручение', exact=True)).to_be_visible()
        assert page.evaluate('window.helpNavigationMarker') == 21
        page.go_back()
        expect(page.locator('#help-heading')).to_contain_text('Поручить работу')
        page.locator('.help-breadcrumbs a').click()
        search = page.get_by_label('Найти инструкцию', exact=True)
        search.fill('  ГОЛОС  ')
        expect(page.locator('.help-catalog-item').filter(has_text='Чат')).to_have_count(1)
        page.locator('.help-catalog-item').filter(has_text='Чат').click()
        page.go_back()
        expect(search).to_have_value('  ГОЛОС  ')
        search.fill('Несуществующаяинструкция')
        expect(page.locator('.help-search [role="status"]')).to_contain_text('ничего не найдено')
        page.get_by_role('button', name='Очистить', exact=True).click()
        expect(search).to_have_value('')
        print('Роутер, якорь, переход к доске, возврат и поиск: успешно', flush=True)

        for theme, label in [('light', 'Светлая'), ('dark', 'Тёмная')]:
            goto(page, proxy, '/help')
            page.get_by_role('button', name='Сменить тему', exact=True).click()
            page.get_by_role('radio', name=label, exact=True).click()
            page.wait_for_timeout(250)
            fits(page)
            for asset in assets:
                filename = asset.rsplit('/', 1)[1].replace('-light.webp', '-' + theme + '.webp').replace('-dark.webp', '-' + theme + '.webp')
                image_response = context.request.get(proxy + '/help/' + filename, max_retries=2)
                assert image_response.ok and image_response.headers['content-type'].startswith('image/webp')
            assert page.locator('.help-welcome').bounding_box()['y'] >= page.locator('main').bounding_box()['y'] - 1
            shot(page, 'index-' + theme)
            goto(page, proxy, '/help/kanban')
            shot(page, 'kanban-' + theme)
            goto(page, proxy, '/help/agents#create')
            illustration = page.locator('.help-illustration').first
            illustration.locator('summary').click()
            illustration.locator('figure > img').scroll_into_view_if_needed()
            illustration.locator('figure > img').evaluate('(el) => el.decode()')
            assert illustration.locator('figure > img').get_attribute('src').endswith('-' + theme + '.webp')
            shot(page, 'illustration-' + theme)
            illustration.get_by_role('button', name='Увеличить снимок').click()
            expect(page.get_by_role('dialog', name='Снимок интерфейса')).to_be_visible()
            page.keyboard.press('Escape')
            expect(page.get_by_role('dialog', name='Снимок интерфейса')).to_have_count(0)
            expect(illustration.get_by_role('button', name='Увеличить снимок')).to_be_focused()
            for width in [390, 320]:
                page.set_viewport_size({'width': width, 'height': 844})
                for path in ['/help', '/help/kanban', '/help/chat#voice', '/help/telegram']:
                    goto(page, proxy, path)
                    fits(page)
                    if width == 390:
                        shot(page, path.split('#')[0].replace('/help', 'mobile').replace('/', '-') + '-' + theme)
                if width == 390:
                    page.get_by_label('Другие инструкции').select_option('files')
                    expect(page).to_have_url(proxy + '/help/files')
                    page.locator('.help-illustration summary').click()
                    page.get_by_role('button', name='Увеличить снимок').click()
                    viewer = page.get_by_role('dialog', name='Снимок интерфейса')
                    expect(viewer).to_be_visible()
                    assert viewer.bounding_box()['width'] <= width
                    assert viewer.locator('img').evaluate('(el) => el.clientWidth === el.naturalWidth')
                    page.get_by_role('button', name='Закрыть снимок').click()
                    page.locator('.help-breadcrumbs a').click()
                    expect(page.locator('#help-search')).to_be_visible()
            page.set_viewport_size({'width': 1440, 'height': 1000})
        goto(page, proxy, '/help/missing')
        expect(page.locator('#help-heading')).to_have_text('Такой инструкции пока нет')
        page.get_by_role('link', name='Оглавление и поиск', exact=True).click()
        expect(page.locator('#help-search')).to_be_visible()
        assert not errors, errors
        print(f'Успешно: {checks} прямых открытий; адресов картинок: {len(assets)}; светлая/тёмная темы; ширины 1440, 390, 320; ошибок JavaScript: 0', flush=True)
        browser.close()


if __name__ == '__main__':
    main()
