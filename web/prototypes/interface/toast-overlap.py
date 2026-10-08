"""K21-295: уведомление «Новый ответ» не перекрывает кнопки экрана.

Запуск при поднятом стенде (`vite --config vite.interface.config.ts`, порт 5184):
    python3 prototypes/interface/toast-overlap.py
Печатает по каждому размеру экрана, какие видимые кнопки пересекаются с
уведомлением; ожидается пустой список и `reachable: true` у «Все агенты».
"""
import json
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5184/?cast=nagrada&states=busy&start="
CASES = [
    ("desktop1280", (1280, 800), False, "%2Fagents%3Fagent%3Ddesigner"),
    ("desktop1024", (1024, 800), False, "%2Fagents%3Fagent%3Ddesigner"),
]
PROBE = """() => {
  const t = document.querySelector('[data-run-toast]');
  const tb = t && t.getBoundingClientRect();
  const over = [];
  let all = null;
  if (tb) for (const el of document.querySelectorAll('button, a[href], summary, [role=tab]')) {
    if (!el.offsetParent || t.contains(el)) continue;
    const r = el.getBoundingClientRect();
    const hit = r.width && tb.left < r.right && tb.right > r.left && tb.top < r.bottom && tb.bottom > r.top;
    const label = (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 40);
    if (hit) over.push(label);
    if (el.hasAttribute('data-agent-list-trigger')) {
      const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      all = { label, reachable: el.contains(top) };
    }
  }
  return { toast: tb && [tb.left, tb.top, tb.right, tb.bottom].map(Math.round), overlapped: over, listTrigger: all };
}"""


def main() -> int:
    bad = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, size, mobile, start in CASES:
            ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, is_mobile=mobile, has_touch=mobile)
            page = ctx.new_page()
            page.goto(BASE + start)
            page.wait_for_timeout(6000)
            info = page.evaluate(PROBE)
            print(name, json.dumps(info, ensure_ascii=False))
            if info["overlapped"] or (info["listTrigger"] and not info["listTrigger"]["reachable"]):
                bad += 1
            ctx.close()
        browser.close()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
