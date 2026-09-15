"""Живая проверка только astra-ui за отдельным прокси с X-Forwarded-Prefix.

Панель 9140, прокси 9141, префикс /c/astra-test. Боевые адреса не настраиваются.
Данные проверки сохраняются только в одноразовом каталоге контейнера.
"""
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml
from playwright.sync_api import expect, sync_playwright

BASE = "http://127.0.0.1:9141/c/astra-test"
OUT = Path("/root/Antigravity/projects/Korra 21/ui pack/qa")


def api(page, path, method=None, body=None):
    return page.evaluate("""async ({path, method, body}) => {
      const options = method ? {method, headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)} : undefined;
      return window.__HERMES_PLUGIN_SDK__.fetchJSON(path, options);
    }""", {"path": path, "method": method, "body": body})


def shot(page, name):
    path = OUT / ("astra-ui-2-" + name + ".png")
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime("%H%M%S%f")
        path.rename(path.with_name(path.stem + "-previous-" + stamp + ".png"))
    page.screenshot(path=str(path))
    print("Снимок:", path)


def goto(page, path):
    page.goto(BASE + path)
    page.wait_for_function("Boolean(window.__HERMES_PLUGIN_SDK__?.router)")
    assert page.evaluate("window.__HERMES_BASE_PATH__") == "/c/astra-test"


def fits(page):
    page.wait_for_function("""() => [...document.querySelectorAll('[role=dialog]')].every(el => {
      const r=el.getBoundingClientRect(); return r.top >= 8 && r.left >= 8 && r.bottom <= innerHeight - 8 && r.right <= innerWidth - 8;
    })""")


def main():
    info = json.loads(subprocess.check_output(["docker", "inspect", "astra-ui"], text=True, encoding="utf-8"))[0]
    assert info["Name"] == "/astra-ui" and info["State"]["Running"]
    data = Path(next(m["Source"] for m in info["Mounts"] if m["Destination"] == "/opt/data"))
    assert str(data).startswith("/tmp/astra-ui-data.")
    assert "TELEGRAM_" not in (data / ".env").read_text(encoding="utf-8")
    config_text = (data / "config.yaml").read_text(encoding="utf-8")
    assert "telegram" not in config_text.lower()
    assert yaml.safe_load(config_text)["kanban"]["dispatch_in_gateway"] is False
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True,
                                      permissions=["clipboard-read", "clipboard-write"])
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        goto(page, "/achievements")
        page.wait_for_selector('[data-milestone="task"] a')
        page.evaluate("window.astraNavigationSentinel = 21")
        # Проверяем открытие полученного адреса в отдельной вкладке без общего состояния.
        separate = context.new_page()
        separate.goto(page.locator('[data-milestone="task"] a').evaluate("link => link.href"))
        expect(separate).to_have_url(BASE + "/kanban")
        expect(separate.get_by_role("button", name="Новое поручение", exact=True)).to_be_enabled()
        separate.close()
        page.locator('[data-milestone="task"] a').click()
        expect(page).to_have_url(BASE + "/kanban")
        assert page.evaluate("window.astraNavigationSentinel") == 21
        page.go_back(); expect(page).to_have_url(BASE + "/achievements")
        page.go_forward(); expect(page).to_have_url(BASE + "/kanban")
        assert page.evaluate("window.astraNavigationSentinel") == 21
        page.get_by_role("link", name="Задачи", exact=True).click()
        page.get_by_role("link", name="Открыть доску", exact=True).click()
        expect(page).to_have_url(BASE + "/kanban")
        assert page.evaluate("window.astraNavigationSentinel") == 21
        print("Польза и Задачи → доска: префикс сохранён, документ не перезагружен")

        page.get_by_role("button", name="Новая доска", exact=True).click()
        page.get_by_label("Название доски").fill("Закупки — проверка кабинета")
        page.get_by_label("Для каких задач").fill("Сравнение условий поставщиков")
        page.get_by_role("button", name="Создать доску", exact=True).click()
        expect(page.get_by_role("dialog")).to_have_count(0)
        expect(page.get_by_label("Доска", exact=True)).to_have_value(re.compile("^board-"))
        board = page.get_by_label("Доска", exact=True).input_value()
        assert parse_qs(urlparse(page.url).query)["board"] == [board]
        page.get_by_role("button", name="Новое поручение", exact=True).click()
        page.get_by_label("Что нужно сделать", exact=True).fill("Сравнить поставщиков для магазина")
        page.get_by_label("Задание и ожидаемый результат", exact=True).fill("Сравни предложения во вложениях. Подготовь таблицу и рекомендацию.")
        page.get_by_label("Кому поручить", exact=True).select_option("default")
        page.get_by_label("Исходные файлы", exact=True).set_input_files([
            {"name": "поставщик-А.txt", "mimeType": "text/plain", "buffer": "Цена 100, доставка 2 дня".encode()},
            {"name": "поставщик-Б.txt", "mimeType": "text/plain", "buffer": "Цена 90, доставка 10 дней".encode()},
        ])
        fits(page); shot(page, "create-with-files")
        # Сбой второй загрузки: первая остаётся на сервере, назначения ещё нет.
        uploads = []
        def upload_route(route):
            if route.request.method != "POST":
                route.continue_(); return
            uploads.append(route.request.url)
            if len(uploads) == 2:
                route.fulfill(status=503, content_type="application/json", body='{"detail":"Проверочный сбой загрузки"}')
            else:
                route.continue_()
        page.route("**/api/plugins/kanban/tasks/*/attachments?*", upload_route)
        page.get_by_role("button", name="Передать агенту", exact=True).click()
        expect(page.get_by_role("alert")).to_contain_text("Поручение сохранено без запуска")
        response = api(page, f"/api/plugins/kanban/board?board={board}")
        tasks = [task for col in response["columns"] for task in col["tasks"]]
        assert len(tasks) == 1 and tasks[0]["assignee"] is None
        task_id = tasks[0]["id"]
        endpoint = f"/api/plugins/kanban/tasks/{task_id}?board={board}"
        assert len(api(page, endpoint)["attachments"]) == 1
        page.get_by_role("button", name="Передать агенту", exact=True).click()
        expect(page.get_by_role("dialog").get_by_role("heading", name="Сравнить поставщиков для магазина")).to_be_visible()
        assert len(uploads) == 3
        page.unroute("**/api/plugins/kanban/tasks/*/attachments?*", upload_route)
        details = api(page, endpoint)
        assert details["task"]["assignee"] == "default" and len(details["attachments"]) == 2
        assert parse_qs(urlparse(page.url).query)["task"] == [task_id]
        page.get_by_role("button", name="Скопировать ссылку на поручение", exact=True).click()
        copied = page.evaluate("navigator.clipboard.readText()")
        assert copied == BASE + f"/kanban?board={board}&task={task_id}"
        page.reload()
        expect(page.get_by_role("button", name="поставщик-А.txt", exact=True)).to_be_visible()
        with page.expect_download() as download:
            page.get_by_role("button", name="поставщик-А.txt", exact=True).click()
        assert Path(download.value.path()).read_text(encoding="utf-8") == "Цена 100, доставка 2 дня"
        page.get_by_role("button", name="Закрыть окно", exact=True).click()
        card = page.locator(f'[data-task-id="{task_id}"]')
        card.drag_to(page.locator('[data-status="blocked"]'))
        page.get_by_label("Причина паузы", exact=True).fill("Нужно согласовать бюджет")
        page.get_by_role("button", name="Отмена", exact=True).click()
        assert api(page, endpoint)["task"]["status"] == "ready"
        card.drag_to(page.locator('[data-status="blocked"]'))
        page.get_by_label("Причина паузы", exact=True).fill("Нужно согласовать бюджет")
        page.get_by_role("button", name="Приостановить", exact=True).click()
        expect(page.locator(f'[data-status="blocked"] [data-task-id="{task_id}"]')).to_be_visible()
        card.click()
        expect(page.get_by_text("Что мешает продолжить", exact=True)).to_be_visible()
        page.get_by_role("button", name="Передать агенту", exact=True).click()
        page.get_by_label("Что нужно уточнить или исправить", exact=True).fill("Бюджет согласован")
        page.get_by_role("button", name="Передать агенту", exact=True).click()
        expect(page.get_by_label("Комментарий", exact=True)).to_be_visible()
        page.get_by_label("Комментарий", exact=True).fill("Проверка не должна стереть этот текст")
        result = "## Сравнение предложений\n\n| Поставщик | Цена | Срок |\n|---|---:|---|\n| А | 100 | 2 дня |\n| Б | 90 | 10 дней |\n\n**Рекомендация:** поставщик А — доставка быстрее."
        # Сохранённый ответ через настоящий API: модель для этого сценария не нужна.
        api(page, endpoint, "PATCH", {"status": "review", "summary": result, "assignee": ""})
        expect(page.get_by_role("button", name="Принять результат", exact=True)).to_be_visible(timeout=15000)
        expect(page.get_by_label("Комментарий", exact=True)).to_have_value("Проверка не должна стереть этот текст")
        expect(page.get_by_role("dialog").locator("section table").first).to_be_visible()
        fits(page); shot(page, "review-result")
        page.get_by_label("Комментарий", exact=True).fill("")
        page.get_by_role("button", name="Принять результат", exact=True).click()
        expect(page.get_by_label("Что получилось", exact=True)).to_have_value(result)
        page.get_by_role("button", name="Принять результат", exact=True).click()
        expect(page.get_by_role("button", name="Завершить поручение", exact=True)).to_have_count(0)
        assert api(page, endpoint)["task"]["result"] == result
        page.reload(); expect(page.get_by_role("dialog").locator("section table").first).to_be_visible()
        shot(page, "completed-result")
        page.get_by_role("button", name="Закрыть окно", exact=True).click()
        page.get_by_role("button", name="Результаты", exact=True).click()
        assert parse_qs(urlparse(page.url).query)["view"] == ["done"]
        expect(card).to_be_visible()
        # «Достижения» (прежняя «Польза от агентов») живут в свёрнутой группе.
        page.get_by_role("button", name="Служебное", exact=True).click()
        page.get_by_role("link", name="Достижения", exact=True).click()
        page.locator('[data-milestone="result"] a').click()
        expect(page.get_by_role("button", name="Результаты", exact=True)).to_have_attribute("aria-pressed", "true")
        expect(page.get_by_label("Доска", exact=True)).to_have_value(board)
        pending = api(page, f"/api/plugins/kanban/tasks?board={board}", "POST", {"title": "Согласовать объём закупки", "body": "Нужно решение руководителя"})["task"]["id"]
        api(page, f"/api/plugins/kanban/tasks/{pending}?board={board}", "PATCH", {"status": "blocked", "block_reason": "Уточните количество"})
        goto(page, "/achievements")
        page.locator(f'a[href="/c/astra-test/kanban?board={board}&view=attention"]').click()
        expect(page.get_by_label("Доска", exact=True)).to_have_value(board)
        expect(page.locator("[data-task-id]")).to_have_count(1)
        assert page.locator("[data-task-id]").get_attribute("data-task-id") == pending
        page.get_by_role("button", name="Сбросить фильтры", exact=True).click()
        print("Доска, файлы до назначения, повтор после 503, адрес карточки, обновление результата и приёмка: успешно")

        for theme in ("dark", "light"):
            page.get_by_role("button", name="Сменить тему", exact=True).click()
            page.get_by_role("radio", name="Тёмная" if theme == "dark" else "Светлая", exact=True).click()
            expected_surface = "#212121" if theme == "dark" else "#e0e0e0"
            page.wait_for_function("surface => getComputedStyle(document.documentElement).getPropertyValue('--neo-surface').trim().toLowerCase() === surface", arg=expected_surface)
            shot(page, "kanban-" + theme)
            goto(page, "/achievements"); page.wait_for_selector('[data-milestone="routine"]')
            shot(page, "benefits-" + theme)
            goto(page, "/kanban?board=" + board)

        goto(page, "/env")
        expect(page.get_by_role("button", name="Подключить сервис", exact=True)).to_be_visible()
        shot(page, "keys")
        page.get_by_role("button", name="Подключить аккаунт", exact=True).click()
        details = page.locator("details").filter(has_text="korra auth add qwen-oauth")
        details.locator("summary").click()
        details.get_by_role("button").click()
        assert page.evaluate("navigator.clipboard.readText()") == "korra auth add qwen-oauth"
        goto(page, "/config")
        page.get_by_placeholder(re.compile("Поиск")).fill("Часовой пояс")
        expect(page.locator("main").get_by_text("Часовой пояс", exact=True)).to_be_visible()
        shot(page, "settings-search")
        page.set_viewport_size({"width": 390, "height": 844})
        goto(page, f"/kanban?board={board}&task={task_id}")
        expect(page.get_by_role("dialog").locator("section table").first).to_be_visible()
        fits(page); shot(page, "result-mobile")
        page.get_by_role("button", name="Закрыть окно", exact=True).click()
        page.get_by_role("button", name="Новое поручение", exact=True).click()
        fits(page); shot(page, "create-mobile")
        page.get_by_role("button", name="Отмена", exact=True).click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        goto(page, "/achievements"); page.wait_for_selector('[data-milestone="task"]')
        shot(page, "benefits-mobile")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        goto(page, f"/kanban?board={board}&task={task_id}")
        page.get_by_role("button", name="В архив", exact=True).click()
        page.get_by_role("button", name="В архив", exact=True).click()
        expect(page.get_by_role("button", name="Изменить задание", exact=True)).to_be_visible()
        page.reload(); expect(page.get_by_role("dialog").locator("section table").first).to_be_visible()
        archived = api(page, endpoint)
        assert archived["task"]["status"] == "archived" and archived["task"]["result"] == result
        assert len(archived["attachments"]) == 2
        assert errors == [], errors
        print("Обе темы, телефон, копирование команды, русский поиск: успешно. Ошибок JavaScript: 0")
        browser.close()


if __name__ == "__main__":
    main()
