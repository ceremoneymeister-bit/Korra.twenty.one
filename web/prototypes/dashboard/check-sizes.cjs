const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require(process.env.KORRA_PLAYWRIGHT || '/opt/korra-pult-dev/node_modules/playwright')
const base = process.env.KORRA_DEMO_URL || 'http://127.0.0.1:5187/'
const evidence = process.env.KORRA_DEMO_EVIDENCE || '/root/Korra21/_work/dashboard-visual-v1/qa-sizes'
const widgets = [
  ['recent-results', 'Артефакты'],
  ['metrics', 'Показатель'],
  ['agents', 'Агенты'],
  ['upcoming-tasks', 'Лента дня']
]
const report = { base, layouts: [], errors: [], networkViolations: [], checks: [] }
fs.mkdirSync(evidence, { recursive: true })

;(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.KORRA_CHROMIUM || '/root/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome',
    args: ['--no-sandbox']
  })
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
    page.on('pageerror', error => report.errors.push(error.message))
    await page.route('**/*', route => {
      const request = route.request(),
        url = new URL(request.url())
      if (['blob:', 'data:'].includes(url.protocol)) return route.continue()
      if (url.origin !== new URL(base).origin || url.pathname.includes('/api/') || request.method() !== 'GET') {
        report.networkViolations.push(request.method() + ' ' + url.href)
        return route.abort()
      }
      return route.continue()
    })
    await page.goto(base, { waitUntil: 'networkidle' })
    await page.locator('.dv-grid').waitFor()
    const configure = page.getByRole('button', { name: 'Настроить', exact: true })
    const open = async () => {
      await configure.click()
      await page.getByRole('dialog').waitFor()
    }
    const done = async () => {
      await page.getByRole('button', { name: 'Готово', exact: true }).click()
      await page.waitForTimeout(150)
    }
    const setSize = async (title, size) =>
      page
        .getByRole('group', { name: `Размер: ${title}`, exact: true })
        .getByRole('radio', { name: new RegExp(`^${size.toUpperCase()} —`) })
        .check()
    const inspect = async label => {
      const result = await page.evaluate(() => ({
        width: innerWidth,
        overflow:
          document.documentElement.scrollWidth > innerWidth ||
          document.querySelector('.dv-root').scrollWidth > document.querySelector('.dv-root').clientWidth + 1,
        cards: [...document.querySelectorAll('[data-size]')].map(el => {
          const rect = el.getBoundingClientRect()
          return {
            id: el.dataset.widget,
            size: el.dataset.size,
            width: Math.round(rect.width),
            height: Math.round(rect.height),
            contentOverflow: el.scrollWidth > el.clientWidth + 1
          }
        })
      }))
      assert.equal(result.overflow, false, `${label}: page overflow`)
      assert.deepEqual(
        result.cards.filter(card => card.contentOverflow),
        [],
        `${label}: card overflow`
      )
      report.layouts.push({ label, ...result })
      await page.screenshot({ path: path.join(evidence, `${result.width}-${label}.png`) })
    }
    for (const width of [320, 390, 768, 1024, 1440, 1920]) {
      await page.setViewportSize({ width, height: width < 761 ? 844 : 900 })
      for (const size of ['s', 'm', 'l']) {
        await open()
        for (const [, title] of widgets) await setSize(title, size)
        await done()
        for (const [id] of widgets)
          assert.equal(await page.locator(`[data-widget="${id}"]`).getAttribute('data-size'), size)
        assert.equal(await page.locator('[data-widget="recent-results"] .dv-artifact').count(), size === 's' ? 1 : 3)
        assert.equal(await page.locator('[data-widget="metrics"] .dv-metric-chart').count(), size === 's' ? 0 : 1)
        assert.equal(await page.locator('.dv-week-strip').count(), size === 'l' ? 1 : 0)
        await inspect(`all-${size}`)
        if (width === 390 && size === 's') {
          const team = page.getByRole('button', { name: 'Открыть команду', exact: true })
          const box = await team.boundingBox()
          assert.ok(box.width >= 44 && box.height >= 44, 'small team has a usable whole-button touch target')
          await team.click()
          assert.equal(
            await page.getByRole('dialog', { name: 'Твоя команда', exact: true }).locator('.dv-agent-row').count(),
            3
          )
          await page.getByRole('button', { name: 'Открыть агента Дизайнер', exact: true }).click()
          await page.getByRole('dialog', { name: 'Дизайнер', exact: true }).waitFor()
          await page.keyboard.press('Escape')
          report.checks.push('compact team opens every agent without tiny touch targets')
        }
      }
    }
    report.checks.push('18 size/viewport combinations have no page or card overflow; content adapts to S/M/L')

    await page.setViewportSize({ width: 1440, height: 900 })
    await open()
    await setSize('Артефакты', 'l')
    await setSize('Показатель', 's')
    await setSize('Агенты', 'm')
    await setSize('Лента дня', 's')
    await done()
    await inspect('mixed')
    await page.getByRole('link', { name: 'Агенты', exact: true }).first().click()
    await page.locator('.dv-root').waitFor({ state: 'detached' })
    await page.getByRole('link', { name: 'Дашборд', exact: true }).first().click()
    await page.locator('.dv-root').waitFor()
    assert.equal(await page.locator('[data-widget="metrics"]').getAttribute('data-size'), 's')
    await page.reload({ waitUntil: 'networkidle' })
    await page.locator('.dv-root').waitFor()
    assert.equal(await page.locator('[data-widget="recent-results"]').getAttribute('data-size'), 'l')
    assert.equal(await page.locator('[data-widget="metrics"]').getAttribute('data-size'), 's')
    await open()
    const metricGroup = page.getByRole('group', { name: 'Размер: Показатель', exact: true })
    await metricGroup.getByRole('radio', { name: /^S —/ }).focus()
    await page.keyboard.press('ArrowRight')
    assert.equal(await metricGroup.getByRole('radio', { name: /^M —/ }).isChecked(), true)
    await page.keyboard.press('ArrowRight')
    assert.equal(await metricGroup.getByRole('radio', { name: /^L —/ }).isChecked(), true)
    await page.keyboard.press('Escape')
    assert.equal(await configure.evaluate(el => el === document.activeElement), true)
    report.checks.push('sizes survive navigation/reload; native radio keyboard arrows work; closing restores focus')

    await page.goto(base + '?theme=dark', { waitUntil: 'networkidle' })
    await page.locator('.dv-root').waitFor()
    await page.setViewportSize({ width: 390, height: 844 })
    await inspect('mixed-dark')
    await open()
    await page.screenshot({ path: path.join(evidence, 'mobile-size-picker.png') })
    await done()
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await open()
    assert.equal(
      await page
        .locator('.dv-size-face')
        .first()
        .evaluate(el => getComputedStyle(el).transitionDuration),
      '0s'
    )
    await page.getByRole('button', { name: 'Сбросить', exact: true }).click()
    await done()
    assert.deepEqual(await page.locator('[data-size]').evaluateAll(els => els.map(el => el.dataset.size)), [
      'm',
      'm',
      'm',
      'm'
    ])
    report.checks.push('dark and reduced-motion sizes work; reset restores the accepted v1 composition')

    // Load a real legacy-shaped browser record, not a mock of the migration.
    await page.evaluate(() =>
      localStorage.setItem(
        'korra.dashboard.visual-v1.layout',
        JSON.stringify({
          order: ['agents', 'recent-results', 'metrics', 'attention', 'upcoming-tasks'],
          hidden: ['metrics']
        })
      )
    )
    await page.reload({ waitUntil: 'networkidle' })
    await page.locator('.dv-root').waitFor()
    assert.equal(await page.locator('[data-widget="metrics"]').count(), 0)
    assert.equal(await page.locator('[data-size]').first().getAttribute('data-widget'), 'agents')
    assert.equal(await page.locator('[data-widget="agents"]').getAttribute('data-size'), 'm')
    report.checks.push('legacy v1 storage retains visibility and order and receives default M sizes')
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
