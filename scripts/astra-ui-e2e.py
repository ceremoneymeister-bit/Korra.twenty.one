"""Проверка только одноразовой панели astra-ui: реальные запросы и сохранение данных.
Запуск: python3 scripts/astra-ui-e2e.py. Боевые адреса намеренно не настраиваются.
"""
import json
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

BASE = "http://127.0.0.1:9140"
OUT = Path("/root/Antigravity/projects/Korra 21/ui pack/qa")


def api(page, path, method=None, body=None):
    return page.evaluate("""async ({path, method, body}) => {
      const options = method ? {method, headers: {'Content-Type': 'application/json'},
        ...(body === null ? {} : {body: JSON.stringify(body)})} : undefined;
      return window.__HERMES_PLUGIN_SDK__.fetchJSON(path, options);
    }""", {"path": path, "method": method, "body": body})


def shot(page, name):
    path = OUT / ("astra-" + name + ".png")
    if path.exists():
        # Предыдущий снимок оставляем рядом, перед перезаписью сохраняем копию.
        path.replace(path.with_name(path.stem + "-previous.png"))
    page.screenshot(path=str(path), full_page=False)
    print("Снимок:", path)


def goto(page, path):
    page.goto(BASE + path)
    page.wait_for_function("Boolean(window.__HERMES_PLUGIN_SDK__)")


def dialog_fits(page):
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    # Ждём окончание штатного появления, затем проверяем геометрию настоящего окна.
    page.wait_for_function("""() => [...document.querySelectorAll('[role=dialog]')].every(el => {
      const r=el.getBoundingClientRect(); return r.top >= 8 && r.left >= 8 && r.bottom <= innerHeight - 8 && r.right <= innerWidth - 8;
    })""")


def main():
    info = json.loads(subprocess.check_output(["docker", "inspect", "astra-ui"], text=True, encoding="utf-8"))[0]
    assert info["Name"] == "/astra-ui" and info["State"]["Running"]
    data = Path(next(m["Source"] for m in info["Mounts"] if m["Destination"] == "/opt/data"))
    assert str(data).startswith("/tmp/astra-ui-data.")
    assert "TELEGRAM_" not in (data / ".env").read_text(encoding="utf-8")
    import yaml
    config = yaml.safe_load((data / "config.yaml").read_text(encoding="utf-8"))
    assert "telegram" not in config
    assert config["kanban"]["dispatch_in_gateway"] is False
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True, permissions=["clipboard-read", "clipboard-write"])
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        goto(page, "/kanban")
        expect(page.get_by_role("button", name="Новое поручение", exact=True)).to_be_enabled()
        # Новая доска через интерфейс: русское название, без обязательного технического имени.
        page.get_by_role("button", name="Новая доска", exact=True).click()
        page.get_by_label("Название доски").fill("Проверка поручений")
        page.get_by_label("Для каких задач").fill("Изолированная проверка интерфейса")
        page.get_by_role("button", name="Создать доску", exact=True).click()
        expect(page.get_by_role("dialog")).to_have_count(0)
        import re
        expect(page.get_by_label("Доска", exact=True)).to_have_value(re.compile(r"^board-"))
        board = page.get_by_label("Доска", exact=True).input_value()
        assert board.startswith("board-")
        shot(page, "kanban-empty")
        page.get_by_role("button", name="Новое поручение", exact=True).click()
        expect(page.get_by_role("button", name="Передать агенту", exact=True)).to_be_disabled()
        page.get_by_label("Что нужно сделать", exact=True).fill("Сравнить предложения поставщиков")
        page.get_by_label("Задание и ожидаемый результат", exact=True).fill("Сравнить цену и сроки. Итог — таблица и рекомендация для закупки.")
        page.get_by_label("Кому поручить", exact=True).select_option("default")
        dialog_fits(page)
        shot(page, "kanban-create")
        page.get_by_role("button", name="Передать агенту", exact=True).click()
        expect(page.get_by_role("dialog")).to_have_count(0)
        card = page.locator('[data-status="ready"] [data-task-id]').first
        expect(card).to_be_visible()
        task_id = card.get_attribute("data-task-id")
        endpoint = f"/api/plugins/kanban/tasks/{task_id}?board={board}"
        task = api(page, endpoint)["task"]
        assert task["status"] == "ready" and task["assignee"] == "default"
        assert "рекомендация" in task["body"]
        # Настоящее перетаскивание мышью. Отмена обязана оставить статус на сервере.
        card.drag_to(page.locator('[data-status="done"]'))
        expect(page.get_by_role("dialog")).to_be_visible()
        expect(page.get_by_role("button", name="Принять результат", exact=True)).to_be_disabled()
        page.get_by_role("button", name="Отмена", exact=True).click()
        assert api(page, endpoint)["task"]["status"] == "ready"
        card.click()
        page.get_by_label("Комментарий", exact=True).fill("Нужна доставка не дольше двух дней.")
        page.get_by_role("button", name="Добавить комментарий", exact=True).click()
        expect(page.get_by_text("Нужна доставка не дольше двух дней.", exact=True)).to_be_visible()
        expect(page.get_by_label("Прикрепить файл", exact=True)).to_be_enabled()
        page.get_by_label("Прикрепить файл", exact=True).set_input_files({"name": "условия.txt", "mimeType": "text/plain", "buffer": "Поставщик А: 100 рублей, 2 дня".encode()})
        file_button = page.get_by_role("button", name="условия.txt", exact=True)
        expect(file_button).to_be_visible()
        with page.expect_download() as download:
            file_button.click()
        assert Path(download.value.path()).read_text(encoding="utf-8") == "Поставщик А: 100 рублей, 2 дня"
        page.get_by_role("button", name="Приостановить", exact=True).click()
        page.get_by_label("Причина паузы", exact=True).fill("Уточнить бюджет у руководителя")
        page.get_by_role("button", name="Приостановить", exact=True).click()
        expect(page.get_by_role("dialog")).to_have_count(0)
        assert api(page, endpoint)["task"]["status"] == "blocked"
        page.locator('[data-status="blocked"] [data-task-id]').first.click()
        expect(page.get_by_role("dialog").get_by_text("Уточнить бюджет у руководителя", exact=True).first).to_be_visible()
        page.get_by_role("button", name="Передать агенту", exact=True).click()
        page.get_by_label("Что нужно уточнить или исправить", exact=True).fill("Бюджет согласован, можно продолжать.")
        page.get_by_role("button", name="Передать агенту", exact=True).click()
        expect(page.get_by_role("dialog")).to_have_count(0)
        assert api(page, endpoint)["task"]["status"] == "ready"
        # Проверка результата через настоящий серверный переход review.
        api(page, endpoint, "PATCH", {"status": "review", "summary": "Предложения сопоставлены. Выбран поставщик А.", "assignee": ""})
        page.get_by_role("button", name="Обновить", exact=True).click()
        review = page.locator('[data-status="review"] [data-task-id]').first
        expect(review).to_be_visible(); review.click()
        expect(page.get_by_text("Предложения сопоставлены. Выбран поставщик А.", exact=True).first).to_be_visible()
        dialog_fits(page)
        shot(page, "kanban-result")
        page.get_by_role("button", name="Принять результат", exact=True).click()
        page.get_by_label("Что получилось", exact=True).fill("Выбран поставщик А: доставка за два дня, бюджет соблюдён.")
        page.get_by_role("button", name="Принять результат", exact=True).click()
        expect(page.get_by_role("dialog")).to_have_count(0)
        assert api(page, endpoint)["task"]["status"] == "done"
        assert "бюджет соблюдён" in api(page, endpoint)["task"]["result"]
        page.reload(); expect(page.locator('[data-status="done"] [data-task-id]')).to_have_count(1)
        shot(page, "kanban-dark")
        page.get_by_label("Найти задачу", exact=True).fill("несуществующая задача")
        expect(page.get_by_text("По этим фильтрам ничего не найдено. Измените запрос или сбросьте фильтры.")).to_be_visible()
        page.get_by_role("button", name="Сбросить фильтры", exact=True).click()
        page.get_by_label("Доска", exact=True).select_option("default")
        expect(page.locator('[data-task-id="' + task_id + '"]')).to_have_count(0)
        page.get_by_label("Доска", exact=True).select_option(board)
        expect(page.locator('[data-task-id="' + task_id + '"]')).to_be_visible()
        # Архив сохраняет данные и исключается из основного списка.
        archived_task = api(page, f"/api/plugins/kanban/tasks?board={board}", "POST", {
            "title": "Поручение для архива", "body": "Проверка сохранности карточки", "assignee": "default",
        })["task"]
        page.get_by_role("button", name="Обновить", exact=True).click()
        archive_card = page.locator('[data-task-id="' + archived_task["id"] + '"]')
        expect(archive_card).to_be_visible(); archive_card.click()
        page.get_by_role("button", name="В архив", exact=True).click()
        page.get_by_role("button", name="В архив", exact=True).click()
        expect(page.get_by_role("dialog")).to_have_count(0)
        expect(archive_card).to_have_count(0)
        assert api(page, f"/api/plugins/kanban/tasks/{archived_task['id']}?board={board}")["task"]["status"] == "archived"
        page.get_by_label("Показать архив", exact=True).check()
        expect(archive_card).to_be_visible()
        page.get_by_label("Показать архив", exact=True).uncheck()
        goto(page, "/achievements")
        expect(page.locator('[data-milestone="result"] .is-complete')).to_be_visible()
        assert "Hermes" not in page.locator("main").inner_text()
        shot(page, "benefits-dark")
        goto(page, "/env")
        expect(page.get_by_text("korra auth add openai-codex", exact=True)).to_be_visible()
        assert "hermes auth" not in page.locator("main").inner_text()
        page.get_by_text("korra auth add openai-codex", exact=True).locator("..").get_by_role("button").click()
        assert page.evaluate("navigator.clipboard.readText()") == "korra auth add openai-codex"
        shot(page, "keys-dark")
        goto(page, "/config")
        expect(page.get_by_text("Защита от зацикливания", exact=True)).to_be_visible()
        shot(page, "config-dark")
        # Обрыв одного источника не превращается в бесконечную загрузку настроек.
        page.route("**/api/config?*", lambda route: route.fulfill(status=503, content_type="application/json", body='{"detail":"temporary failure"}'))
        page.route("**/api/config", lambda route: route.fulfill(status=503, content_type="application/json", body='{"detail":"temporary failure"}'))
        page.reload()
        expect(page.get_by_role("button", name="Повторить загрузку", exact=True)).to_be_visible()
        shot(page, "config-error")
        page.unroute_all()
        goto(page, "/missing-astra-section")
        expect(page.get_by_text("Такого раздела нет", exact=True)).to_be_visible()
        shot(page, "missing-section")
        # Обе темы и мобильная ширина; прокручивается доска, а не вся страница.
        goto(page, "/achievements")
        expect(page.get_by_role("button", name="Обновить данные", exact=True)).to_be_enabled()
        page.get_by_role("button", name="Сменить тему", exact=True).click()
        page.get_by_role("radio", name="Светлая", exact=True).click()
        expect(page.locator(".korra-benefits")).to_be_visible()
        shot(page, "benefits-light")
        goto(page, "/kanban")
        expect(page.get_by_role("button", name="Новое поручение", exact=True)).to_be_enabled()
        shot(page, "kanban-light")
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(300)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        shot(page, "kanban-mobile")
        page.get_by_role("button", name="Новое поручение", exact=True).click()
        dialog_fits(page)
        shot(page, "kanban-mobile-form")
        page.get_by_role("button", name="Отмена", exact=True).click()
        goto(page, "/achievements")
        expect(page.get_by_role("button", name="Обновить данные", exact=True)).to_be_enabled()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        shot(page, "benefits-mobile")
        print("Ошибки JavaScript:", errors)
        assert not errors
        print("ПРОЙДЕНО: создание доски и поручения, исполнитель и результат, отмена перетаскивания, комментарий, загрузка и скачивание файла, пауза и продолжение, проверка результата, сохранение после перезагрузки, поиск, изоляция досок, архив, показатели пользы, ключи, конфигурация и ошибки, обе темы, мобильная ширина и геометрия модальных окон.")
        browser.close()


if __name__ == "__main__":
    main()
