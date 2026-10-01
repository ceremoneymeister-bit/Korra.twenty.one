"""K21-220/217 regression. Start Vite on loopback :4178, then run this script.

Real components and browser picker; only synthetic API responses, no model.
"""

import json
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import expect, sync_playwright


def check(playwright, engine, width):
    browser = getattr(playwright, engine).launch()
    page = browser.new_page(viewport={"width": width, "height": 900})
    held, manifests, uploads, errors = [], {}, [], []
    background = False
    page.on("pageerror", lambda error: errors.append(str(error)))
    rows = [
        {
            "id": i,
            "role": "user" if i % 2 else "assistant",
            "content": "Да" if i % 2 else "Готово",
        }
        for i in range(1, 31)
    ]

    def route(r):
        nonlocal background
        request = r.request
        url = urlparse(request.url)
        if url.hostname != "127.0.0.1":
            return r.abort()
        if not url.path.startswith("/api/"):
            return r.continue_()
        data = {}
        if url.path.endswith("/messages"):
            if background:
                held.append(r)
                return
            data = {"session_id": "fixture", "messages": rows}
        elif url.path == "/api/sessions":
            data = {
                "sessions": [
                    {"id": "fixture", "source": "dashboard", "message_count": 30}
                ],
                "total": 1,
            }
        elif "runs" in url.path:
            data = {"runs": []}
        elif url.path.endswith("/approvals"):
            data = {"approvals": []}
        elif url.path == "/api/uploads" and request.method == "POST":
            manifest = request.post_data_json
            manifests[manifest["upload_id"]] = manifest
            uploads.append(manifest)
            data = {
                "upload_id": manifest["upload_id"],
                "published": False,
                "received": [],
                "already_present": [],
            }
        elif url.path.startswith("/api/uploads/"):
            parts = url.path.split("/")
            manifest = manifests[parts[3]]
            if parts[-1] == "complete":
                data = {
                    "published": True,
                    "folder": None,
                    "files": [
                        {
                            "index": i,
                            "name": f["path"],
                            "path": "/synthetic/" + f["path"],
                            "kind": "heic",
                            "reader": "image",
                            "size": f["size"],
                        }
                        for i, f in enumerate(manifest["files"])
                    ],
                }
            elif request.method == "PUT":
                data = {
                    "index": int(parts[-1]),
                    "bytes": manifest["files"][int(parts[-1])]["size"],
                    "complete": True,
                }
            else:
                data = {
                    "upload_id": parts[3],
                    "published": False,
                    "received": [],
                    "already_present": [],
                }
        r.fulfill(status=200, content_type="application/json", body=json.dumps(data))

    page.route("**/*", route)
    page.goto("http://127.0.0.1:4178/e2e/chat.html")
    expect(page.locator("#count")).to_have_text("30", timeout=60000)
    expect(page.locator("#loading")).to_have_text("false")
    box = page.get_by_role("textbox", name="Сообщение Корре")
    box.fill("Черновик сохраняется")
    for name in ["first.HEIC", "second.HEIC", "first.HEIC"]:
        page.get_by_role("button", name="Прикрепить файл", exact=True).click()
        with page.expect_file_chooser() as chooser:
            page.get_by_role("menuitem", name="Загрузить с устройства").click()
        background = True
        # Reproduce returning from native picker BEFORE its change event.
        page.evaluate("window.dispatchEvent(new Event('focus'))")
        page.wait_for_function(
            "document.querySelector('#loading').textContent === 'false'"
        )
        page.wait_for_timeout(150)
        assert held, "focus did not start background history"
        assert not box.is_disabled()
        chooser.value.set_files({
            "name": name,
            "mimeType": "",
            "buffer": Path(__file__).with_name("synthetic.HEIC").read_bytes(),
        })
        expect(page.locator('[role="listitem"][data-status="ready"]')).to_have_count(
            1, timeout=10000
        )
        expect(box).to_have_value("Черновик сохраняется")
        assert page.locator('input[type="file"]').get_attribute("accept") is None
        # Returning history must preserve the selected attachment.
        background = False
        for r in held:
            r.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"session_id": "fixture", "messages": rows}),
            )
        held.clear()
        expect(page.locator('[role="listitem"][data-status="ready"]')).to_have_count(1)
        page.get_by_role("button", name=f"Убрать {name}", exact=True).click()
        expect(page.locator('[role="listitem"]')).to_have_count(0)
    assert len(uploads) == 3 and not errors, (uploads, errors)
    page.goto("about:blank")
    browser.close()
    return {
        "browser": engine,
        "width": width,
        "uploads": len(uploads),
        "pageerrors": errors,
        "result": "PASS",
    }


with sync_playwright() as p:
    print(
        json.dumps(
            [
                check(p, engine, width)
                for engine in ["chromium", "webkit"]
                for width in [390, 1440]
            ],
            ensure_ascii=False,
            indent=2,
        )
    )
