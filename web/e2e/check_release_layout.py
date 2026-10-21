"""A20B-01: real shell/navigation and decision card, local synthetic data only.

Run Vite on port 4183, then /usr/bin/python3 web/e2e/check_release_layout.py
--output <evidence directory>. No force clicks, backend or model calls.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--base-url", default="http://127.0.0.1:4183")
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
results = []

with sync_playwright() as p:
    for engine in ["chromium", "webkit"]:
        browser = getattr(p, engine).launch()
        for width, touch in [(390, False), (390, True), (1440, False)]:
            for seen in [False, True]:
                context = browser.new_context(viewport={"width": width, "height": 1000},
                                              has_touch=touch, reduced_motion="reduce")
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                params = {"cast": "nagrada", "states": "busy",
                          "start": "/agents?agent=bitrix&resume=bitrix-0"}
                if seen:
                    params["seen"] = "1"
                name = f"{engine}-{width}-{'touch' if touch else 'mouse'}-{'seen' if seen else 'banner'}"
                result = {"case": name, "errors": errors}
                page.goto(f"{args.base_url}/e2e/release-layout.html?{urlencode(params)}")
                card = page.get_by_role("group", name="Решение по внешней отправке")
                card.wait_for(timeout=60000)
                page.evaluate("document.fonts.ready")
                page.wait_for_timeout(400)
                result["geometry"] = card.evaluate("""card => {
                  const r = card.getBoundingClientRect(), parent = card.parentElement.getBoundingClientRect();
                  const rail = document.querySelector('.k-rail')?.getBoundingClientRect();
                  const notice = document.querySelector('.release-notice')?.getBoundingClientRect();
                  const header = document.querySelector('[data-agents-header]')?.getBoundingClientRect();
                  return {card: r.toJSON(), parent: parent.toJSON(),
                    rail: rail?.toJSON(), notice: notice?.toJSON(), header: header?.toJSON(),
                    typing: document.querySelector('.k-agents')?.dataset.typing,
                    cardFits: r.right <= parent.right + 1 && r.left >= parent.left - 1,
                    noticeFits: !notice || !header || notice.bottom <= header.top,
                    buttonsFit: [...card.querySelectorAll('button')].every(b => {
                      const br = b.getBoundingClientRect(); return br.left >= r.left && br.right <= r.right;
                    })};
                }""")
                page.screenshot(path=str(args.output / f"{name}.png"), full_page=True)
                if width == 390:
                    try:
                        page.get_by_role("button", name="Идут работы:").click(timeout=2000)
                        page.get_by_role("dialog", name="Идут работы").wait_for(timeout=2000)
                        result["activity_click"] = True
                    except Exception as error:
                        result["activity_click"] = False
                        result["click_error"] = str(error)[-1200:]
                result["ok"] = (not errors and result["geometry"]["cardFits"]
                                and result["geometry"]["buttonsFit"]
                                and result["geometry"]["noticeFits"]
                                and result.get("activity_click", True))
                results.append(result)
                print(name, "PASS" if result["ok"] else "FAIL", flush=True)
                context.close()
        browser.close()
(args.output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
raise SystemExit(0 if all(row["ok"] for row in results) else 1)
