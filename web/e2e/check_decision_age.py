"""K21-398: real chat hook/transcript/decision center, synthetic HTTP only.

Start Vite on 127.0.0.1:4183, then /usr/bin/python3 web/e2e/check_decision_age.py
--output <evidence directory>. No backend or messenger is contacted.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

expect.set_options(timeout=15_000)


def check(playwright, engine, width, output):
    browser = getattr(playwright, engine).launch()
    page = browser.new_page(viewport={"width": width, "height": 1000}, timezone_id="UTC")
    page.clock.set_fixed_time(datetime(2026, 10, 19, 5, 30, tzinfo=timezone.utc))
    page.add_init_script("window.__KORRA_OWNER_TIMEZONE__='Asia/Novosibirsk'")
    errors, posts = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    row = {
        "request_id": "k21b-age", "decision_kind": "outbound_message",
        "effect_status": "pending", "source_session_id": "fixture",
        "command": "Кому: k21b-Локальная проверка · отправка отключена\n\n"
                   "Тестовый текст: проверьте актуальность инструкции перед отправкой.",
        "description": "Проверьте адресата, текст и вложения перед внешней отправкой.",
        "choices": ["once", "deny"],
        "timestamp": datetime(2026, 10, 3, 11, 49, tzinfo=timezone.utc).timestamp(),
        "updated_at": datetime(2026, 10, 19, 5, 29, tzinfo=timezone.utc).timestamp(),
    }

    def route(r):
        url = urlparse(r.request.url)
        if url.hostname != "127.0.0.1":
            return r.abort()
        if not url.path.startswith("/api/"):
            return r.continue_()
        data = {}
        if url.path == "/api/chat/approval" and r.request.method == "POST":
            posts.append(r.request.post_data_json)
            data = {"effect_status": "denied"}
        elif url.path == "/api/sessions/fixture" and r.request.method == "PATCH":
            data = {"id": "fixture"}  # existing chat view metadata, local fixture only
        elif r.request.method != "GET":
            raise AssertionError(f"Unexpected write: {r.request.method} {url.path}")
        elif url.path.endswith("/messages"):
            data = {"session_id": "fixture", "messages": [
                {"id": 1, "role": "user", "content": "Подготовь инструкцию"},
                {"id": 2, "role": "assistant", "content": "Черновик готов. Проверьте его перед отправкой."},
            ]}
        elif url.path == "/api/sessions":
            data = {"sessions": [{"id": "fixture", "source": "dashboard", "message_count": 2}], "total": 1}
        elif url.path in {"/api/chat/approvals", "/api/chat/decisions"}:
            data = {"data": [row]}
        elif "runs" in url.path:
            data = {"runs": []}
        r.fulfill(status=200, content_type="application/json", body=json.dumps(data))

    page.route("**/*", route)
    page.goto("http://127.0.0.1:4183/e2e/chat.html?decisions")
    expect(page.locator("#loading")).to_have_text("false", timeout=60000)
    card = page.get_by_role("group", name="Решение по внешней отправке")
    expect(card).to_have_count(1, timeout=60000)
    expect(card.locator("time")).to_have_text("03.10.2026 в 18:49")
    expect(card.get_by_role("note")).to_contain_text("мог устареть")
    expect(card.get_by_role("button", name="Не отправлять")).to_be_enabled()
    card.scroll_into_view_if_needed()
    expect(card.get_by_role("note")).to_be_visible()
    page.screenshot(path=str(output / f"{engine}-{width}-chat-old.png"), full_page=True)

    page.get_by_role("button", name="Решения").click()
    center = page.get_by_role("region", name="Центр решений")
    expect(center.locator("time")).to_have_text("03.10.2026 в 18:49")
    expect(center.get_by_role("note")).to_contain_text("мог устареть")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(output / f"{engine}-{width}-center-old.png"), full_page=True)
    center.get_by_role("button", name="Не отправлять").click()
    page.wait_for_function("document.body.textContent.includes('Не отправлено')")
    assert posts == [{"session_id": "fixture", "request_id": "k21b-age", "choice": "deny"}]

    row["timestamp"] = datetime(2026, 10, 19, 5, 16, tzinfo=timezone.utc).timestamp()
    page.reload()
    expect(page.locator("#loading")).to_have_text("false")
    expect(card).to_have_count(1)
    expect(card.locator("time")).to_have_text("сегодня в 12:16")
    expect(card.get_by_role("note")).to_have_count(0)
    page.screenshot(path=str(output / f"{engine}-{width}-chat-fresh.png"), full_page=True)
    assert not errors, errors
    browser.close()
    print(f"PASS {engine} {width}: old/fresh, timezone, center/reload, one-click deny, no overflow")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        for engine in ("chromium", "webkit"):
            for width in (390, 1440):
                check(p, engine, width, args.output)
