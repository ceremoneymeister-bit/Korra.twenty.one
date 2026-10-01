"""K21-231: real agent screen routes attachments to the selected draft.

Start Vite on 127.0.0.1:4178; API and model responses are synthetic.
"""

import json
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright, expect

with sync_playwright() as p:
    for engine in ["chromium", "webkit"]:
        for target in ["recent", "new"]:
            browser = getattr(p, engine).launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            held = []
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))

            def route(r):
                u = urlparse(r.request.url)
                q = parse_qs(u.query)
                data = {}
                if u.hostname != "127.0.0.1":
                    return r.abort()
                if not u.path.startswith("/api/"):
                    return r.continue_()
                if u.path == "/api/profiles":
                    data = {
                        "profiles": [
                            {"name": "default", "is_default": True},
                            {"name": "designer", "display_name": "Дизайнер"},
                        ]
                    }
                elif "agent-tabs" in u.path:
                    data = {
                        "initialized": True,
                        "revision": 1,
                        "order": ["", "designer"],
                        "hidden": [],
                    }
                elif u.path == "/api/sessions":
                    data = {
                        "sessions": [
                            {
                                "id": "old-chat",
                                "title": "Старый разговор",
                                "source": "dashboard",
                                "message_count": 2,
                            }
                        ],
                        "total": 1,
                    }
                elif u.path.endswith("/messages"):
                    if "/recent-chat/" in u.path:
                        held.append(r)
                        return
                    data = {
                        "session_id": "old-chat",
                        "messages": [
                            {"id": 1, "role": "user", "content": "Старый вопрос"},
                            {"id": 2, "role": "assistant", "content": "Старый ответ"},
                        ],
                    }
                elif u.path == "/api/files/attachment":
                    data = {
                        "path": "/synthetic/brief.txt",
                        "name": "brief.txt",
                        "kind": "document",
                        "size": 5,
                        "reader": "text",
                    }
                elif "runs" in u.path:
                    data = {"runs": []}
                elif u.path.endswith("/approvals"):
                    data = {"approvals": []}
                r.fulfill(
                    status=200, content_type="application/json", body=json.dumps(data)
                )

            page.route("**/*", route)
            page.goto("http://127.0.0.1:4178/e2e/handoff.html")
            expect(page.get_by_role("textbox", name="Сообщение Корре")).to_be_enabled(
                timeout=60000
            )
            expect(
                page.locator("#agent-panel-designer").get_by_text(
                    "Старый вопрос", exact=True
                )
            ).to_be_visible(timeout=10000)
            box = page.get_by_role("textbox", name="Сообщение Корре")
            box.fill("Старый черновик")
            page.get_by_role(
                "button",
                name="Передать в недавний"
                if target == "recent"
                else "Передать в новый",
                exact=True,
            ).click()
            if target == "recent":
                page.wait_for_timeout(400)
                assert held
                assert not page.locator("[role=listitem][data-status=ready]").count()
                for r in held:
                    r.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps({
                            "session_id": "recent-chat",
                            "messages": [
                                {"id": 3, "role": "user", "content": "Недавний вопрос"},
                                {"id": 4, "role": "assistant", "content": "Ответ"},
                            ],
                        }),
                    )
            expect(page.locator("[role=listitem][data-status=ready]")).to_have_count(
                1, timeout=10000
            )
            values = page.evaluate(
                'Object.fromEntries(Object.entries(sessionStorage).filter(([k])=>k.endsWith(":files")))'
            )
            expected = "recent-chat" if target == "recent" else "new"
            assert len(values) == 1 and next(iter(values)).endswith(
                f":designer:{expected}:files"
            ), values
            assert errors == [], errors
            print(engine, target, "PASS", values.keys())
            browser.close()
