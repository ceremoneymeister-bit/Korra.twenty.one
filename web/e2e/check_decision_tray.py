"""«Ваши ответы»: на узком экране имя читается, кнопки и «×» без горизонтальной прокрутки.

Serve web with Vite on port 4183; pass --base-url to override. Synthetic rows, no backend.
"""
import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--shots', type=Path, default=Path('/tmp/korra-decision-tray'))
parser.add_argument('--base-url', default='http://127.0.0.1:4183')
args = parser.parse_args()
args.shots.mkdir(parents=True, exist_ok=True)

GEOMETRY = """() => {
  const tray = document.querySelector('section[aria-label]').getBoundingClientRect();
  const rows = [...document.querySelectorAll('section li')].map(li => {
    const name = li.querySelector('span[title]');
    const parts = [...li.querySelectorAll('button[role=radio], button[aria-label^="Убрать"]')];
    return {
      nameVisible: name.clientWidth, nameClipped: name.scrollWidth > name.clientWidth,
      outside: parts.filter(b => { const r = b.getBoundingClientRect(); return r.left < tray.left - 1 || r.right > tray.right + 1; }).length,
      twoLines: li.querySelector('[role=radiogroup]').getBoundingClientRect().top >= name.getBoundingClientRect().bottom,
    };
  });
  return {viewport: innerWidth, scrollWidth: document.documentElement.scrollWidth, rows};
}"""

failed = False
with sync_playwright() as p:
    for engine in ['chromium', 'webkit']:
        browser = getattr(p, engine).launch()
        for width in [320, 390, 768, 1440]:
            page = browser.new_page(viewport={'width': width, 'height': 800})
            page.goto(f'{args.base_url}/e2e/decision-tray.html')
            page.wait_for_selector('section li')
            g = page.evaluate(GEOMETRY)
            page.screenshot(path=str(args.shots / f'{engine}-{width}.png'))
            ok = g['scrollWidth'] <= g['viewport'] and all(r['outside'] == 0 for r in g['rows'])
            if width >= 390:
                ok = ok and all(r['nameVisible'] >= 110 for r in g['rows'])  # ~15 символов при text-sm
            failed |= not ok
            print(engine, width, 'OK' if ok else 'FAIL', g)
            page.close()
        browser.close()
raise SystemExit(1 if failed else 0)
