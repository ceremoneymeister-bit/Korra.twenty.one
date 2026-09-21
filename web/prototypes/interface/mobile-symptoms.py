#!/usr/bin/env python3
"""Проверка шести симптомов мобильной панели на синтетическом стенде.

Каждый пункт — наблюдение в браузере, а не рассуждение: прозрачность
выдвижного меню, прокрутка полосы вкладок пальцем, панель истории чатов над
шапкой, шрифт поля под фокусом, цвет системного хрома после смены темы и
габариты уведомления о готовом ответе.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5184/"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/korra-mobile-symptoms")

# Демо-стенд отдаёт пустой список работ; подменяем один готовый ответ, чтобы
# увидеть настоящее уведомление, а не его вёрстку в отрыве от страницы.
INJECT_RUNS = """() => {
  const original = window.fetch;
  window.fetch = async (input, init) => {
    const url = String(typeof input === 'string' ? input : input.url || input);
    if (url.includes('/api/chat/runs')) {
      const runs = ['designer', 'lawyer', 'mentor'].map((profile, i) => ({
        message_id: 'demo-run-' + i, session_id: profile + '-0', profile,
        status: 'completed', updated_at: 2 + i, history_count: 1, unread: true,
        event_revision: String(2 + i), user_message: { role: 'user', content: 'Демонстрация' },
      }));
      return new Response(JSON.stringify({ runs }), { headers: { 'Content-Type': 'application/json' } });
    }
    return original(input, init);
  };
}"""


def surface(page, selector: str) -> dict:
    return page.evaluate(
        r"""(sel) => {
          const el = document.querySelector(sel);
          if (!el) return { missing: true };
          const style = getComputedStyle(el);
          const rgba = style.backgroundColor.match(/[\d.]+/g)?.map(Number) || [];
          return { background: style.backgroundColor, opacity: Number(style.opacity),
            alpha: rgba.length === 4 ? rgba[3] : 1 };
        }""",
        selector,
    )


def run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    with sync_playwright() as play:
        browser = play.chromium.launch()
        context = browser.new_context(
            viewport={"width": 430, "height": 932}, device_scale_factor=2,
            has_touch=True, is_mobile=True,
        )
        page = context.new_page()
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_timeout(1200)
        # Обёртку ставим ПОСЛЕ стенда: demo-api сам присваивает window.fetch.
        page.evaluate(INJECT_RUNS)

        print("5. Выдвижное меню")
        page.get_by_role("button", name="Открыть навигацию").first.click()
        page.wait_for_timeout(500)
        drawer_surface = surface(page, "#app-sidebar")
        header_surface = surface(page, "header")
        print("   фон aside:", drawer_surface)
        print("   фон шапки:", header_surface)
        check(not drawer_surface.get("missing") and drawer_surface["alpha"] == 1 and drawer_surface["opacity"] == 1,
              "выдвижное меню осталось прозрачным")
        check(not header_surface.get("missing") and header_surface["alpha"] == 1 and header_surface["opacity"] == 1,
              "мобильная шапка осталась прозрачной")
        page.screenshot(path=str(OUT / "05-drawer.png"))
        page.get_by_role("link", name="Агенты", exact=True).first.click()
        page.wait_for_timeout(1400)

        print("2. Полоса вкладок агентов")
        strip = page.locator(".korra-agent-tabs__scroller")
        box = strip.bounding_box()
        before = strip.evaluate("el => el.scrollLeft")
        overflow = strip.evaluate("el => el.scrollWidth - el.clientWidth")
        page.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.move(box["x"] + box["width"] - 20, box["y"] + box["height"] / 2)
        # Реальный палец: pointerType=touch через CDP-совместимый жест.
        order_before = page.evaluate("() => [...document.querySelectorAll('[role=tab]')].map(t => t.textContent.trim())")
        page.evaluate(
            """(sel) => {
              const el = document.querySelector(sel);
              const wrap = el.querySelector('[data-agent-tab-profile]');
              const rect = wrap.getBoundingClientRect();
              const send = (type, x) => {
                const ev = new PointerEvent(type, { bubbles: true, cancelable: true,
                  pointerId: 1, pointerType: 'touch', isPrimary: true, button: 0,
                  clientX: x, clientY: rect.top + rect.height / 2 });
                wrap.dispatchEvent(ev);
                return ev;
              };
              send('pointerdown', rect.left + 40);
              window.__moveDefaultPrevented = send('pointermove', rect.left - 60).defaultPrevented;
              send('pointerup', rect.left - 60);
            }""",
            ".korra-agent-tabs__scroller",
        )
        prevented = page.evaluate("() => window.__moveDefaultPrevented")
        order = page.evaluate("() => [...document.querySelectorAll('[role=tab]')].map(t => t.textContent.trim())")
        strip.evaluate("el => el.scrollBy({ left: 160 })")
        page.wait_for_timeout(300)
        after = strip.evaluate("el => el.scrollLeft")
        wrap_touch = page.evaluate(
            "() => getComputedStyle(document.querySelector('[data-agent-tab-profile]')).touchAction"
        )
        print(f"   запас прокрутки {overflow}px, scrollLeft {before} → {after}, touch-action={wrap_touch}")
        print(f"   pointermove отменён браузеру: {prevented}; порядок вкладок: {order[:3]}")
        check(overflow > 0 and after > before, "полоса вкладок не имеет рабочей горизонтальной прокрутки")
        check(not prevented and order == order_before, "палец всё ещё запускает перестановку вкладок")
        check(wrap_touch not in ("pan-y", "none"), f"touch-action запрещает горизонтальный жест: {wrap_touch}")
        page.screenshot(path=str(OUT / "02-tabs.png"))

        print("1. Уведомление о готовом ответе")
        toast = page.locator("[data-run-toast]")
        toast.wait_for(timeout=15000)
        tbox = toast.bounding_box()
        tabs_box = page.locator(".korra-agent-tabs").bounding_box()
        covers = not (tbox["y"] + tbox["height"] <= tabs_box["y"] or tbox["y"] >= tabs_box["y"] + tabs_box["height"])
        print(f"   габариты {round(tbox['width'])}×{round(tbox['height'])} в точке "
              f"({round(tbox['x'])},{round(tbox['y'])}); перекрывает полосу вкладок: {covers}")
        print("   строк в уведомлении:", page.evaluate(
            "() => [...document.querySelectorAll('[data-run-toast] li')].filter(li => li.offsetParent).length"))
        header_overlap = page.evaluate("""() => {
          const toast = document.querySelector('[data-run-toast]').getBoundingClientRect();
          return [...document.querySelectorAll('header button, header a[href]')]
            .filter(el => el.offsetParent !== null)
            .some(el => { const r = el.getBoundingClientRect(); return toast.left < r.right && toast.right > r.left && toast.top < r.bottom && toast.bottom > r.top; });
        }""")
        check(not covers, "уведомление перекрывает полосу вкладок агентов")
        check(not header_overlap, "уведомление перекрывает управление в шапке")
        check(page.evaluate("() => [...document.querySelectorAll('[data-run-toast] li')].filter(li => li.offsetParent).length") == 1,
              "мобильное уведомление занимает больше одной строки ответа")
        page.screenshot(path=str(OUT / "01-toast.png"))
        page.locator("[data-run-toast] button").click()
        page.wait_for_timeout(300)

        print("3. История чатов")
        page.get_by_role("button", name="Открыть историю чатов").first.click()
        page.wait_for_timeout(600)
        page.screenshot(path=str(OUT / "03-history.png"))
        close = page.locator("[data-chat-history-close]")
        cbox = close.bounding_box()
        top_element = page.evaluate(
            """() => {
              const el = document.querySelector('[data-chat-history-close]');
              const r = el.getBoundingClientRect();
              const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
              return el.contains(hit) ? 'сам крестик' : hit.tagName + '.' + String(hit.className).slice(0, 40);
            }"""
        )
        print(f"   крестик {round(cbox['width'])}×{round(cbox['height'])}; в точке нажатия: {top_element}")
        check(cbox["width"] >= 44 and cbox["height"] >= 44 and top_element == "сам крестик",
              "крестик истории чатов меньше 44 px или перекрыт другим слоем")
        page.locator("[role=dialog][aria-modal=true] .korra-chat-history__item").nth(1).click()
        page.wait_for_timeout(500)
        history_still_open = page.locator("[role=dialog][aria-modal=true]").count() > 0
        print("   после выбора чата панель открыта:", history_still_open)
        check(not history_still_open, "история чатов не закрылась после выбора чата")

        print("4. Фокус в поле ввода")
        page.evaluate("() => [...document.querySelectorAll('textarea.korra-chat-composer__textarea')].find(el => el.offsetParent !== null).focus()")
        page.wait_for_timeout(300)
        print("   шрифт композера:", page.evaluate(
            "() => getComputedStyle(document.activeElement).fontSize"))
        sizes = page.evaluate(
            """() => [...document.querySelectorAll('input:not([type=checkbox]):not([type=radio]), textarea')]
                 .filter(el => el.offsetParent !== null)
                 .map(el => parseFloat(getComputedStyle(el).fontSize))"""
        )
        print("   все видимые поля:", sorted(set(sizes)))
        check(bool(sizes) and min(sizes) >= 16, f"видимое поле мельче 16 px: {sorted(set(sizes))}")
        print("   высота документа:", page.evaluate("() => [document.documentElement.scrollHeight, innerHeight]"))
        page.screenshot(path=str(OUT / "04-composer.png"))

        print("6. Цвет системного хрома")
        meta = "() => document.querySelector('meta[name=theme-color]').content"
        zones = """() => ({
          html: getComputedStyle(document.documentElement).backgroundColor,
          body: getComputedStyle(document.body).backgroundColor,
        })"""
        light_meta, light_zones = page.evaluate(meta), page.evaluate(zones)
        print("   светлая:", light_meta, light_zones)
        # Переключатель темы живёт в выдвижном меню — открываем его.
        # С 0.21.12 это капсула из двух icon-only зон, а не выпадающий список.
        page.get_by_role("button", name="Открыть навигацию").first.click()
        page.wait_for_timeout(500)
        page.get_by_role("button", name="Тёмная тема", exact=True).first.click()
        page.wait_for_timeout(900)
        dark_meta, dark_zones = page.evaluate(meta), page.evaluate(zones)
        print("   тёмная: ", dark_meta, dark_zones)
        check(light_meta == "#e8e8e8" and set(light_zones.values()) == {"rgb(232, 232, 232)"},
              "системный хром и холст светлой темы расходятся")
        check(dark_meta == "#212121" and set(dark_zones.values()) == {"rgb(33, 33, 33)"},
              "системный хром и холст тёмной темы расходятся после переключения")
        page.screenshot(path=str(OUT / "06-dark.png"))
        context.close()
        browser.close()
    if failures:
        print("\nОШИБКИ СЦЕНАРНОЙ ПРИЁМКИ:")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1
    print("\nВсе шесть мобильных симптомов закрыты.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
