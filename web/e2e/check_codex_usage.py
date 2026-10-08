"""K21-321. Run with Vite at 127.0.0.1:4181; all data are synthetic."""
import json
import time
from urllib.parse import urlparse
from playwright.sync_api import expect, sync_playwright


def check(engine):
    now = time.time()
    windows = [{"key": key, "window_minutes": minutes, "resets_at": now + minutes * 30,
                "remaining_percent": 64, "used_percent": 36, "level": "normal",
                "renewed": False, "forecast": None, "label": "неделя"}
               for key, minutes in [("primary", 300), ("secondary", 10080)]]
    agents = [{"profile": f"a{i}", "name": "ОченьДлинноеИмяАгентаБезПробелов" * 4 if i == 0 else f"Агент {i}",
               "status": "ok", "calls": 1000 - i, "output_tokens": (10-i)*100,
               "share_percent": 25-i, "tracked_since": now - 86400*14}
              for i in range(7)]
    usage = {"status": "ok", "period": {"starts_at": now - 86400*3, "ends_at": now,
             "window_minutes": 10080, "resets_at": now + 86400*4},
             "measure": "output_tokens", "agents": agents,
             "total": {"calls": 100, "output_tokens": 10000}, "calculated_at": now,
             "unreadable": [], "incomplete": []}
    data = {"version": 1, "generated_at": now, "timezone": "Europe/Moscow",
            "quota": {"available": True, "status": "ok", "windows": windows,
                      "level": "normal", "captured_at": now, "plan_type": "pro",
                      "reset_credits": {"available": 2, "applicable": 0},
                      "usage_by_agent": usage}}
    browser = engine.launch()
    page = browser.new_page(viewport={"width": 390, "height": 844})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    def route(r):
        url = urlparse(r.request.url)
        if url.hostname != "127.0.0.1":
            return r.abort()
        if url.path == "/api/dashboard/state":
            return r.fulfill(content_type="application/json", body=json.dumps(data))
        if url.path.startswith("/api/"):
            return r.fulfill(content_type="application/json", body="{}")
        r.continue_()
    page.route("**/*", route)
    page.goto("http://127.0.0.1:4181/e2e/codex-usage.html")
    expect(page.locator(".kdw-usage-list li")).to_have_count(5, timeout=60000)
    expect(page.get_by_text("ещё 2 агента")).to_be_visible()
    page.evaluate("document.fonts.ready")
    bounds = page.evaluate("""() => {
      const tile = document.querySelector('.korra-dashboard__tile').getBoundingClientRect();
      const footer = document.querySelector('.kdw-limit-foot').getBoundingClientRect();
      return {width: document.documentElement.scrollWidth, viewport: innerWidth,
        bottom: footer.bottom, tileBottom: tile.bottom,
        rows: [...document.querySelectorAll('.kdw-usage-list li')].map(n => n.getBoundingClientRect().right)};
    }""")
    print(engine.name, bounds)
    assert bounds["width"] <= bounds["viewport"]
    assert max(bounds["rows"]) <= 390
    assert bounds["bottom"] <= bounds["tileBottom"], "card clips its content"
    usage["agents"] = []
    usage["total"] = {"calls": 0, "output_tokens": 0}
    page.reload()
    expect(page.get_by_text("На этой неделе агенты ещё не обращались к Codex")).to_be_visible()
    assert not errors, errors
    browser.close()


with sync_playwright() as p:
    check(p.chromium)
    check(p.webkit)
