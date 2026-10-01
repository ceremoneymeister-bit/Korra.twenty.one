"""K21-207: real hook + transcript restore a live answer outside the last page.

Start Vite on 127.0.0.1:4178. Synthetic HTTP, no model or live user data.
"""

import json
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright


def check(playwright, engine):
    browser = getattr(playwright, engine).launch()
    page = browser.new_page(viewport={"width": 390, "height": 844})
    reads, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script("""if (!sessionStorage.getItem('e2e:scroll')) {
      sessionStorage.setItem('e2e:scroll', JSON.stringify({id:'asst-live',key:'assistant:c41',offset:40}));
    }""")

    def route(r):
        url = urlparse(r.request.url)
        if url.hostname != "127.0.0.1":
            return r.abort()
        if not url.path.startswith("/api/"):
            return r.continue_()
        data = {}
        if url.path.endswith("/messages"):
            before = int(parse_qs(url.query).get("before_id", [101])[0])
            first = max(1, before - 30)
            reads.append(before)
            rows = [
                {
                    "id": i,
                    "role": "user" if i % 2 else "assistant",
                    "content": f"Реплика {i}. " + ("Текст ответа. " * 60),
                    **(
                        {"display_metadata": {"client_message_id": f"c{i}"}}
                        if i % 2
                        else {}
                    ),
                }
                for i in range(first, before)
            ]
            data = {
                "session_id": "fixture",
                "messages": rows,
                "pagination": {"before_id": first, "has_more": first > 1},
            }
        elif url.path == "/api/sessions":
            data = {
                "sessions": [
                    {"id": "fixture", "source": "dashboard", "message_count": 100}
                ],
                "total": 1,
            }
        elif "runs" in url.path:
            data = {"runs": []}
        elif url.path.endswith("/approvals"):
            data = {"approvals": []}
        r.fulfill(status=200, content_type="application/json", body=json.dumps(data))

    page.route("**/*", route)
    page.goto("http://127.0.0.1:4178/e2e/chat.html")
    for reload in range(2):
        if reload:
            page.reload()
        expect(page.locator('[data-chat-key="assistant:c41"]')).to_have_count(
            1, timeout=60000
        )
        page.wait_for_function(
            """() => {
          const view=document.querySelector('[aria-label="Переписка"]');
          const answer=document.querySelector('[data-chat-key="assistant:c41"]');
          return answer && Math.abs(answer.getBoundingClientRect().top-view.getBoundingClientRect().top+40)<2;
        }""",
            timeout=10000,
        )
    assert reads.count(71) == 2 and not errors, (reads, errors)
    browser.close()
    return {
        "browser": engine,
        "history_reads": reads,
        "restored_offset": 40,
        "F5": "PASS",
        "pageerrors": errors,
    }


with sync_playwright() as p:
    print(
        json.dumps(
            [check(p, engine) for engine in ["chromium", "webkit"]],
            ensure_ascii=False,
            indent=2,
        )
    )
