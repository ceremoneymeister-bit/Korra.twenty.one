// Browser acceptance of the built static demo. No production API access.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require(process.env.KORRA_PLAYWRIGHT || '/opt/korra-pult-dev/node_modules/playwright')
const base = process.env.KORRA_DEMO_URL || 'http://127.0.0.1:5187/'
const evidence = process.env.KORRA_DEMO_EVIDENCE || '/root/Korra21/_work/dashboard-visual-v1/qa'
fs.mkdirSync(evidence, { recursive: true })
const report = { base, layouts: [], checks: [], errors: [], networkViolations: [] }
const pause = page => page.waitForTimeout(600)
async function shot(page, name) {
  await pause(page)
  await page.screenshot({ path: path.join(evidence, name + '.png') })
}

;(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.KORRA_CHROMIUM || '/root/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome',
    headless: true,
    args: ['--no-sandbox']
  })
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
    await context.route('**/*', route => {
      const request = route.request(),
        url = new URL(request.url())
      if (['blob:', 'data:'].includes(url.protocol)) return route.continue()
      if (url.origin !== new URL(base).origin || url.pathname.includes('/api/') || request.method() !== 'GET') {
        report.networkViolations.push(request.method() + ' ' + url.href)
        return route.abort()
      }
      return route.continue()
    })
    const page = await context.newPage()
    page.on('pageerror', error => report.errors.push(error.message))
    await page.goto(base, { waitUntil: 'networkidle' })
    await page.locator('.dv-grid').waitFor()
    await page.evaluate(() => document.fonts.ready)

    // Inspect all three authored states across both navigation breakpoints.
    for (const width of [320, 390, 768, 1024, 1440, 1920]) {
      await page.setViewportSize({ width, height: width < 761 ? 844 : 900 })
      for (const label of ['Первый вход', 'Рабочий день', 'Нужно решение']) {
        await page.getByRole('button', { name: label, exact: true }).click()
        await shot(
          page,
          `${width}-${label === 'Первый вход' ? 'first' : label === 'Рабочий день' ? 'day' : 'attention'}`
        )
        const layout = await page.evaluate(() => ({
          overflow: document.documentElement.scrollWidth > innerWidth,
          mainOverflow:
            document.querySelector('.dv-root').scrollWidth > document.querySelector('.dv-root').clientWidth + 1,
          firstCardY: document.querySelector('[data-widget],.dv-first-card')?.getBoundingClientRect().y,
          activeScenario: document.querySelector('.dv-demo-tabs [aria-pressed=true]').textContent
        }))
        assert.equal(layout.overflow, false, `document overflow at ${width}/${label}`)
        assert.equal(layout.mainOverflow, false, `dashboard overflow at ${width}/${label}`)
        assert.equal(layout.activeScenario, label)
        report.layouts.push({ width, ...layout })
      }
    }

    await page.setViewportSize({ width: 1440, height: 900 })
    await page.getByRole('button', { name: 'Рабочий день', exact: true }).click()
    await pause(page) // Measure the settled layout, not its entrance transform.
    const configure = page.getByRole('button', { name: 'Настроить', exact: true })
    const widgetY = await page.locator('[data-widget="recent-results"]').evaluate(el => el.getBoundingClientRect().y)
    await configure.click()
    await page.getByRole('dialog', { name: 'Твой дашборд' }).waitFor()
    assert.equal(
      await page.locator('[data-widget="recent-results"]').evaluate(el => el.getBoundingClientRect().y),
      widgetY,
      'drawer does not push dashboard'
    )
    await page.getByRole('switch', { name: 'Показывать: Показатель', exact: true }).click()
    assert.equal(await page.locator('[data-widget="metrics"]').count(), 0)
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute('role')), 'switch')
    await page.getByRole('button', { name: 'Выше: Агенты', exact: true }).click()
    await shot(page, 'desktop-catalog')
    for (let i = 0; i < 22; i++) {
      await page.keyboard.press('Tab')
      assert.equal(
        await page.evaluate(() => !!document.activeElement.closest('dialog')),
        true,
        'dialog traps keyboard focus'
      )
    }
    await page.keyboard.press('Escape')
    assert.equal(await configure.evaluate(el => el === document.activeElement), true, 'closing restores trigger focus')
    const order = await page.locator('[data-widget]').evaluateAll(els => els.map(el => el.dataset.widget))
    await page.getByRole('link', { name: 'Агенты', exact: true }).first().click()
    await page.locator('.dv-root').waitFor({ state: 'detached' })
    await page.getByRole('link', { name: 'Дашборд', exact: true }).first().click()
    await page.locator('.dv-root').waitFor()
    assert.equal(await page.locator('[data-widget="metrics"]').count(), 0, 'removal survives route navigation')
    assert.deepEqual(await page.locator('[data-widget]').evaluateAll(els => els.map(el => el.dataset.widget)), order)
    await page.reload({ waitUntil: 'networkidle' })
    await page.locator('.dv-root').waitFor()
    assert.equal(await page.locator('[data-widget="metrics"]').count(), 0, 'removal survives reload')
    assert.deepEqual(
      await page.locator('[data-widget]').evaluateAll(els => els.map(el => el.dataset.widget)),
      order,
      'order survives reload'
    )
    report.checks.push(
      'drawer does not reflow content; focus trap/return; visibility and order survive navigation and reload'
    )

    await configure.click()
    await page.getByRole('button', { name: 'Сбросить', exact: true }).click()
    await page.getByRole('button', { name: 'Готово', exact: true }).click()
    await page.getByRole('button', { name: 'Период: неделя. Переключить', exact: true }).click()
    assert.match(await page.locator('.dv-metric-value').innerText(), /1 142 800/)
    await page.getByRole('button', { name: 'Период: месяц. Переключить', exact: true }).click()
    assert.match(await page.locator('.dv-metric-value').innerText(), /284 500/)
    const artifact = page.getByRole('button', { name: 'Открыть артефакт «Стратегия роста»', exact: true })
    await artifact.click()
    await page.getByRole('dialog', { name: 'Стратегия роста', exact: true }).waitFor()
    await shot(page, 'artifact-viewer')
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: 'Скачать текст .md', exact: true }).click()
    ])
    assert.match(download.suggestedFilename(), /Стратегия роста.*демо\.md/)
    const downloaded = fs.readFileSync(await download.path(), 'utf8')
    assert.match(downloaded, /Вымышленные данные/)
    assert.match(downloaded, /Один продукт/)
    await page.keyboard.press('Escape')
    assert.equal(await artifact.evaluate(el => el === document.activeElement), true)
    await page.getByRole('button', { name: 'Все', exact: true }).click()
    assert.equal(await page.getByRole('dialog').locator('.dv-artifact').count(), 3)
    await page.keyboard.press('Escape')
    report.checks.push(
      'metric period changes; artifact opens; meaningful demo Markdown downloads; gallery exposes all artifacts'
    )

    await page.getByRole('button', { name: 'Нужно решение', exact: true }).click()
    await page.getByRole('button', { name: 'Посмотреть', exact: true }).click()
    await shot(page, 'approval')
    await page.getByRole('button', { name: 'Подтвердить в макете', exact: true }).click()
    assert.equal(await page.locator('[data-widget="attention"]').count(), 0)
    assert.match(await page.locator('.dv-greeting p').innerText(), /Решение принято/)
    assert.equal(
      await page.evaluate(() => document.activeElement.tagName),
      'H2',
      'focus remains meaningful after trigger disappears'
    )
    report.checks.push('approval is local-only; acknowledgement shown; focus survives removed action')

    await page.goto(base + '?theme=dark', { waitUntil: 'networkidle' })
    await page.locator('.dv-root').waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.style.colorScheme), 'dark')
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 })
      await shot(page, `${width}-dark`)
      await configure.click()
      await shot(page, `${width}-dark-catalog`)
      await page.keyboard.press('Escape')
    }
    await page.emulateMedia({ reducedMotion: 'reduce' })
    assert.equal(
      await page
        .locator('.dv-activity i')
        .first()
        .evaluate(el => getComputedStyle(el).animationName),
      'none'
    )
    report.checks.push('dark theme uses product tokens; reduced motion disables activity animation')

    await page.goto(base + '?scene=first', { waitUntil: 'networkidle' })
    await page.locator('.dv-first-card').waitFor()
    await page.getByRole('button', { name: 'Начать с Коррой', exact: true }).click()
    await page.getByRole('dialog', { name: 'Корра', exact: true }).waitFor()
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: 'Посмотреть пример', exact: true }).click()
    await page.locator('.dv-grid').waitFor()
    await configure.click()
    for (const item of await page.getByRole('switch').all())
      if ((await item.getAttribute('aria-checked')) === 'true') await item.click()
    await page.getByRole('button', { name: 'Готово', exact: true }).click()
    await page.locator('.dv-empty').waitFor()
    await page.getByRole('button', { name: 'Выбрать виджеты', exact: true }).click()
    await page.getByRole('button', { name: 'Сбросить', exact: true }).click()
    await page.getByRole('button', { name: 'Готово', exact: true }).click()
    await page.locator('[data-widget="metrics"]').waitFor()
    report.checks.push('first visit has working next steps; empty personalization has recovery')
    assert.deepEqual(report.errors, [])
    assert.deepEqual(report.networkViolations, [])
    fs.writeFileSync(path.join(evidence, 'report.json'), JSON.stringify(report, null, 2) + '\n')
    console.log(JSON.stringify(report, null, 2))
  } finally {
    await browser.close()
  }
})().catch(error => {
  console.error(error)
  console.error(JSON.stringify(report, null, 2))
  process.exitCode = 1
})
