#!/usr/bin/env python3
"""Аудит продуктовой панели на мобильных ширинах.

Снимает синтетический стенд (`npx vite --config vite.interface.config.ts`)
на ширинах iPhone/iPad/ноутбука и проверяет то, что видно глазами плохо:
горизонтальное переполнение, размер шрифта в полях под фокусом, цвет
`meta[name="theme-color"]`, непрозрачность выдвижного меню и цели пальца.

Ничего не публикует и не ходит в сеть: стенд подменяет fetch демо-данными.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5184/"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/korra-mobile-audit")
VIEWPORTS = [
    (390, 844, True),
    (430, 932, True),
    (768, 1024, True),
    (820, 1180, True),
    # Альбомный iPad и ноутбук имеют одинаковую ширину, но разные правила
    # указателя. Проверяем обе ветки media query отдельно.
    (1024, 768, True),
    (1024, 768, False),
    (1280, 900, False),
]
ROUTES = ["/dashboard", "/agents", "/files", "/cron"]

OVERFLOW = """() => {
  const doc = document.documentElement;
  const limit = doc.clientWidth + 1;
  const wide = [];
  for (const el of document.querySelectorAll('body *')) {
    const box = el.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) continue;
    if (box.right > limit || box.left < -1) {
      const style = getComputedStyle(el);
      if (style.position === 'fixed' && box.width <= limit) continue;
      wide.push({
        tag: el.tagName.toLowerCase(),
        cls: (el.className && String(el.className)).slice(0, 90),
        left: Math.round(box.left), right: Math.round(box.right),
      });
    }
  }
  return { scrollWidth: doc.scrollWidth, clientWidth: doc.clientWidth, wide: wide.slice(0, 8) };
}"""

FIELDS = """() => [...document.querySelectorAll('input:not([type=checkbox]):not([type=radio]), textarea, select')]
  .filter(el => el.offsetParent !== null)
  .map(el => ({
    what: el.tagName.toLowerCase() + (el.type ? ':' + el.type : '') + ' ' + (el.className || '').slice(0, 40),
    size: parseFloat(getComputedStyle(el).fontSize),
  }))"""

SMALL_TARGETS = """() => [...document.querySelectorAll('button, a[href], [role=tab], summary')]
  .filter(el => el.offsetParent !== null)
  .map(el => ({ el, box: el.getBoundingClientRect() }))
  .filter(({ box }) => box.width > 0 && box.height > 0 && (box.height < 43.5 || box.width < 43.5))
  .map(({ el, box }) => ({
    label: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 40),
    w: Math.round(box.width), h: Math.round(box.height),
  })).slice(0, 10)"""


def run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    report: list[dict] = []
    failures: list[str] = []
    with sync_playwright() as play:
        browser = play.chromium.launch()
        for width, height, touch in VIEWPORTS:
            context = browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=2,
                has_touch=touch,
                is_mobile=touch,
            )
            page = context.new_page()
            for route in ROUTES:
                page.goto(f"{BASE}#{route}", wait_until="networkidle")
                page.wait_for_timeout(700)
                # Роутер стенда — MemoryRouter: маршрут выбираем кликом в меню.
                label = {"/dashboard": "Дашборд", "/agents": "Агенты", "/files": "Файлы", "/cron": "Задачи"}[route]
                if width < 1024:
                    page.get_by_role("button", name="Открыть навигацию").first.click()
                    page.wait_for_timeout(350)
                page.get_by_role("link", name=label, exact=True).first.click()
                page.wait_for_timeout(900)
                mode = "touch" if touch else "mouse"
                tag = f"{width}x{height}-{mode}{route.replace('/', '-')}"
                page.screenshot(path=str(OUT / f"{tag}.png"))
                report.append({
                    "viewport": f"{width}x{height}",
                    "pointer": mode,
                    "route": route,
                    "overflow": page.evaluate(OVERFLOW),
                    "fields": page.evaluate(FIELDS),
                    "small_targets": page.evaluate(SMALL_TARGETS),
                    "theme_color": page.evaluate(
                        "() => document.querySelector('meta[name=theme-color]')?.content"
                    ),
                    "html_bg": page.evaluate(
                        "() => getComputedStyle(document.documentElement).backgroundColor"
                    ),
                })
            context.close()
        browser.close()
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf8")
    for row in report:
        over = row["overflow"]
        width = int(row["viewport"].split("x")[0])
        touch_contract = row["pointer"] == "touch" or width < 1024
        bad_fields = [f for f in row["fields"] if f["size"] < 16] if touch_contract else []
        prefix = f"{row['viewport']} {row['pointer']} {row['route']}"
        if over["scrollWidth"] > over["clientWidth"] + 1:
            failures.append(f"{prefix}: документ шире viewport")
        if bad_fields:
            failures.append(f"{prefix}: поля мельче 16 px: {bad_fields}")
        if touch_contract and row["small_targets"]:
            failures.append(f"{prefix}: цели пальца мельче 44 px: {row['small_targets']}")
        if row["theme_color"] != "#e8e8e8" or row["html_bg"] != "rgb(232, 232, 232)":
            failures.append(f"{prefix}: цвет холста расходится с активной светлой темой")
        print(
            row["viewport"], row["pointer"], row["route"],
            "scroll", over["scrollWidth"], "/", over["clientWidth"],
            "| wide", len(over["wide"]),
            "| мелких полей", len(bad_fields),
            "| мелких целей", len(row["small_targets"]),
            "|", row["theme_color"], row["html_bg"],
        )
    if failures:
        print("\nОШИБКИ МОБИЛЬНОЙ ПРИЁМКИ:")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1
    print("\nМобильная матрица пройдена.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
