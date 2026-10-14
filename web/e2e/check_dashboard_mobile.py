"""Real dashboard CSS geometry in Chromium/WebKit; synthetic preview only.

Serve web with Vite or vite preview on port 4183; pass --base-url to override.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--shots', type=Path, default=Path('/tmp/korra-dashboard-mobile'))
parser.add_argument('--baseline', action='store_true')
parser.add_argument('--base-url', default='http://127.0.0.1:4183')
args = parser.parse_args()
args.shots.mkdir(parents=True, exist_ok=True)
results = []

GEOMETRY = """() => {
  const clipped = [];
  const widgets = ['codex-quota', 'calendar'];
  const cards = {};
  for (const widget of widgets) {
    const card = document.querySelector(`[data-widget="${widget}"]`);
    const bounds = card.getBoundingClientRect();
    cards[widget] = {height: bounds.height, width: bounds.width};
    const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
    for (let node; (node = walker.nextNode());) {
      if (!node.textContent.trim() || node.parentElement.closest('.sr-only')) continue;
      const range = document.createRange(); range.selectNodeContents(node);
      for (const rect of range.getClientRects()) {
        for (let ancestor = node.parentElement; ancestor && card.contains(ancestor); ancestor = ancestor.parentElement) {
          const style = getComputedStyle(ancestor), clip = ancestor.getBoundingClientRect();
          if (['hidden', 'clip', 'auto', 'scroll'].includes(style.overflowY) &&
              (rect.top < clip.top - 1 || rect.bottom > clip.bottom + 1)) {
            clipped.push({widget, text: node.textContent.trim(), ancestor: ancestor.className,
              top: rect.top, bottom: rect.bottom, clipTop: clip.top, clipBottom: clip.bottom});
            break;
          }
          if (['hidden', 'clip', 'auto', 'scroll'].includes(style.overflowX) &&
              (rect.left < clip.left - 1 || rect.right > clip.right + 1)) {
            clipped.push({widget, text: node.textContent.trim(), ancestor: ancestor.className,
              left: rect.left, right: rect.right, clipLeft: clip.left, clipRight: clip.right});
            break;
          }
        }
      }
    }
  }
  const quota = document.querySelector('.kdw-quota--m');
  return {viewport: innerWidth, width: document.documentElement.scrollWidth, cards, clipped,
    quota: {clientHeight: quota.clientHeight, scrollHeight: quota.scrollHeight}};
}"""

with sync_playwright() as p:
    for name in ['chromium', 'webkit']:
        browser = getattr(p, name).launch()
        for width in [320, 390, 1440]:
            context = browser.new_context(viewport={'width': width, 'height': 900},
                                          locale='ru-RU', timezone_id='Europe/Moscow',
                                          reduced_motion='reduce', service_workers='block')
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.clock.install(time=datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc))
            page.goto(args.base_url.rstrip('/') + '/e2e/dashboard-mobile.html?start=/dashboard')
            expect(page.locator('.kdw-limit-say')).to_contain_text('Тратится в', timeout=60000)
            expect(page.locator('[data-calendar-next]')).to_contain_text('Созвон с поставщиком')
            page.evaluate('document.fonts.ready')
            # Allow resize observers and the product's entry animation to settle.
            page.wait_for_timeout(500)
            geometry = page.evaluate(GEOMETRY)
            result = {'browser': name, **geometry, 'pageerrors': errors}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            page.screenshot(path=str(args.shots / f'{name}-{width}-dashboard.png'), full_page=True)
            if not args.baseline:
                assert not errors, errors
                assert geometry['width'] <= width, 'horizontal page overflow'
                assert not geometry['clipped'], 'dashboard card clips text'
                assert geometry['quota']['scrollHeight'] <= geometry['quota']['clientHeight'] + 1
            context.close()
        browser.close()
(args.shots / 'geometry.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
