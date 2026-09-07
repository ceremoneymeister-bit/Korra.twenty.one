"""Playwright regression against an isolated panel and delayed provider.

python scripts/qa/parallel_sessions_browser.py --base http://127.0.0.1:9146 --artifacts <directory>
Requires lawyer/accountant profiles and gateway.api_server.max_concurrent_runs: 2.
Run parallel_sessions_provider.py on 8677 and point their custom model at it.
"""
import argparse
import asyncio
import json
import time
from pathlib import Path
from playwright.async_api import async_playwright, expect


async def main(base, artifacts):
    artifacts.mkdir(parents=True, exist_ok=True)
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(base + "/agents")
        await page.locator("#agent-tab-lawyer").wait_for()
        token = await page.evaluate("window.__HERMES_SESSION_TOKEN__")
        headers = {"Authorization": f"Bearer {token}"}
        suffix = str(int(time.time()))

        async def snapshot(name):
            await page.screenshot(path=str(artifacts / f"sessions-{name}.png"))

        async def runs():
            response = await page.request.get(base + "/api/chat/runs", headers=headers)
            assert response.ok, await response.text()
            return (await response.json())["runs"]

        async def wait_status(sid, expected, timeout=45):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                found = next((r for r in await runs() if r["session_id"] == sid), None)
                if found and found["status"] in expected:
                    return found
                await asyncio.sleep(.25)
            raise AssertionError(f"Session {sid}: expected {expected}, got {found}")

        async def open_chat(profile, sid):
            # BrowserRouter history navigation: no reload, exactly like a Link.
            url = f"/agents?agent={profile or 'default'}&resume={sid}"
            await page.evaluate("url => { history.pushState({}, '', url); dispatchEvent(new PopStateEvent('popstate')); }", url)
            panel = page.locator(f"#agent-panel-{profile}")
            await expect(panel).to_be_visible()
            await asyncio.sleep(.35)
            return panel

        async def send(profile, label, extra=""):
            await page.locator(f"#agent-tab-{profile}").click()
            panel = page.locator(f"#agent-panel-{profile}")
            await panel.get_by_role("button", name="Новый чат", exact=True).click()
            marker = f"SESSION_TEST_{label}_{suffix}"
            await panel.locator("textarea").fill(marker + extra)
            async with page.expect_request(lambda req: "/api/chat/completions" in req.url and req.method == "POST") as info:
                await panel.locator("textarea").press("Enter")
            request = await info.value
            sid = request.headers["x-hermes-session-id"]
            await wait_status(sid, {"running", "queued"})
            return sid, marker

        async def assert_reply(profile, sid, marker):
            panel = await open_chat(profile, sid)
            transcript = panel.get_by_label("Переписка", exact=True)
            await expect(transcript.get_by_text(f"Готово: {marker}. Ответ сохранён.", exact=True)).to_be_visible(timeout=45000)
            assert await transcript.get_by_text(marker, exact=True).count() == 1
            assert await transcript.get_by_text(f"Готово: {marker}. Ответ сохранён.", exact=True).count() == 1
            await expect(panel.get_by_role("button", name="Остановить генерацию")).to_have_count(0, timeout=45000)
            return panel

        a, ma = await send("lawyer", "TWO_AGENTS_A")
        b, mb = await send("accountant", "TWO_AGENTS_B")
        await page.locator("#agent-tab-lawyer").click()
        assert (await wait_status(a, {"running", "completed"}))["status"] == "running"
        await expect(page.locator("#agent-panel-lawyer").get_by_role("button", name="Остановить генерацию")).to_be_visible()
        await snapshot("two-agents-streaming")
        directory_requested = asyncio.Event()
        async def delayed_directory(route):
            response = await route.fetch()
            directory_requested.set()
            await asyncio.sleep(1)
            await route.fulfill(response=response)
        await page.route("**/api/files**", delayed_directory)
        await page.get_by_role("link", name="Файлы", exact=True).click()
        await asyncio.wait_for(directory_requested.wait(), timeout=10)
        await page.get_by_role("link", name="Агенты", exact=True).click()
        await asyncio.sleep(1.25)
        await page.unroute("**/api/files**", delayed_directory)
        assert page.url.endswith("/agents"), "A late file response stole the chat route"
        await expect(page.locator("#agent-panel-lawyer")).to_be_visible()
        await wait_status(b, {"completed"})
        await expect(page.get_by_role("link", name="Бухгалтер ответил · Открыть чат")).to_be_visible(timeout=10000)
        await snapshot("background-reply")
        await assert_reply("lawyer", a, ma)
        await assert_reply("accountant", b, mb)
        results.append("two agents; section switch during stream; background notification; completed return")

        a, ma = await send("lawyer", "SAME_AGENT_A")
        b, mb = await send("lawyer", "SAME_AGENT_B")
        panel = await open_chat("lawyer", a)
        await expect(panel.get_by_role("button", name="Остановить генерацию")).to_be_visible()
        await snapshot("same-agent-return")
        await wait_status(a, {"completed"})
        await wait_status(b, {"completed"})
        await assert_reply("lawyer", a, ma)
        await assert_reply("lawyer", b, mb)
        results.append("same agent, simultaneous sessions; active reattachment; completed replay without duplicates")

        panel = await open_chat("lawyer", a)
        await panel.locator("textarea").fill("Черновик для первого дела")
        panel = await open_chat("lawyer", b)
        await expect(panel.locator("textarea")).to_have_value("")
        await panel.locator("textarea").fill("Черновик для второго дела")
        panel = await open_chat("lawyer", a)
        await expect(panel.locator("textarea")).to_have_value("Черновик для первого дела")
        panel = await open_chat("lawyer", b)
        await expect(panel.locator("textarea")).to_have_value("Черновик для второго дела")
        results.append("independent unsent drafts per session")
        panel = await open_chat("lawyer", a)
        uploads = []
        page.on("request", lambda request: uploads.append(request.url) if "/api/chat/upload" in request.url and request.method == "POST" else None)
        await panel.locator('input[type="file"]').set_input_files({"name": "Черновик договора.txt", "mimeType": "text/plain", "buffer": "Условия договора".encode()})
        chip = panel.locator('.korra-chat-attachment-chip[data-status="ready"]')
        await expect(chip).to_have_count(1)
        await open_chat("lawyer", b)
        await expect(chip).to_have_count(0)
        await open_chat("lawyer", a)
        await expect(chip).to_have_count(1)
        await page.reload()
        await expect(chip).to_have_count(1)
        assert len(uploads) == 1, "A restored draft uploaded the file again"
        results.append("prepared file stays in its own draft across switches and reload, without reupload")

        r, mr = await send("lawyer", "RELOAD")
        await page.reload()
        await expect(page.locator("#agent-panel-lawyer")).to_be_visible()
        await assert_reply("lawyer", r, mr)
        await snapshot("reload-recovered")
        results.append("full reload reattaches and retains selected profile/session")

        a, ma = await send("lawyer", "CAP_A")
        b, mb = await send("accountant", "CAP_B")
        c, mc = await send("", "CAP_QUEUE")
        await wait_status(c, {"queued"})
        await expect(page.get_by_text("Все места заняты. Сообщение в очереди", exact=False)).to_be_visible(timeout=5000)
        await snapshot("queue")
        await page.locator("#agent-panel-").get_by_role("button", name="Остановить генерацию").click()
        await wait_status(c, {"failed"})
        c, mc = await send("", "CAP_QUEUE_RETRY")
        await wait_status(c, {"completed"}, timeout=60)
        await assert_reply("", c, mc)
        await assert_reply("lawyer", a, ma)
        await assert_reply("accountant", b, mb)
        results.append("global concurrency limit; explicit queued cancellation; next queued turn starts automatically")

        long_id, long_marker = await send("lawyer", "SCROLL", "\n" + "\n".join(f"Пункт договора {i}: проверить условия и сохранить комментарий." for i in range(80)))
        await wait_status(long_id, {"completed"})
        panel = page.locator("#agent-panel-lawyer")
        viewport = panel.get_by_label("Переписка", exact=True)
        await expect(viewport.get_by_text(f"Готово: {long_marker}. Ответ сохранён.", exact=True)).to_be_visible()
        await viewport.evaluate("el => { el.scrollTop = 120; el.dispatchEvent(new Event('scroll')); }")
        await open_chat("lawyer", a)
        await open_chat("lawyer", long_id)
        await asyncio.sleep(1)
        assert abs(await viewport.evaluate("el => el.scrollTop") - 120) < 3
        await snapshot("scroll-restored")
        results.append("reading position survives a session switch and replay")
        assert not errors, errors
        provider = await page.request.get("http://127.0.0.1:8677/status")
        calls = [call for call in (await provider.json())["calls"] if call["marker"].endswith(suffix)]
        # The engine also asks its auxiliary client for a session title.
        # Count agent invocations separately from that deliberate LLM call.
        agent_calls = [call for call in calls if call["tool_count"] > 0]
        markers = [call["marker"] for call in agent_calls]
        assert len(markers) == len(set(markers)), "Reattachment invoked the model twice"
        assert f"SESSION_TEST_CAP_QUEUE_{suffix}" not in markers, "Cancelled queue reached the provider"
        starts = {call["marker"]: call["time"] for call in agent_calls}
        assert abs(starts[f"SESSION_TEST_TWO_AGENTS_A_{suffix}"] - starts[f"SESSION_TEST_TWO_AGENTS_B_{suffix}"]) < 10
        results.append("provider receipts: simultaneous starts, no duplicate calls, cancelled queue never executes")
        (artifacts / "sessions-browser-results.json").write_text(json.dumps({"passed": results, "page_errors": errors, "provider_calls": calls}, ensure_ascii=False, indent=2), encoding="utf-8")
        await browser.close()
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:9146")
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.base, args.artifacts))
