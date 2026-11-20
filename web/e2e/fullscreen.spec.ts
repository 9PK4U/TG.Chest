import { expect, test, type Page } from '@playwright/test'

async function telegram(page: Page, platform = 'tdesktop', unsupported = false) {
  await page.route('https://telegram.org/js/telegram-web-app.js', route => route.fulfill({ contentType: 'text/javascript', body: '' }))
  await page.addInitScript(({ platform, unsupported }) => {
    const handlers = new Map<string, Set<(event?: { error?: string }) => void>>()
    const stats = { requests: 0, exits: 0, expansions: 0 }
    const emit = (name: string, data?: { error?: string }) => handlers.get(name)?.forEach(fn => fn(data))
    const app = {
      initData: '', platform, isFullscreen: false,
      ready() {}, expand() { stats.expansions++ }, setHeaderColor() {}, setBackgroundColor() {}, isVersionAtLeast: () => true,
      requestFullscreen() {
        stats.requests++
        if (unsupported) emit('fullscreenFailed', { error: 'UNSUPPORTED' })
        else { app.isFullscreen = true; emit('fullscreenChanged') }
      },
      exitFullscreen() { stats.exits++; app.isFullscreen = false; emit('fullscreenChanged') },
      onEvent(event: string, callback: (data?: { error?: string }) => void) {
        if (!handlers.has(event)) handlers.set(event, new Set())
        handlers.get(event)!.add(callback)
      },
      offEvent(event: string, callback: (data?: { error?: string }) => void) { handlers.get(event)?.delete(callback) },
    }
    window.Telegram = { WebApp: app }
    Object.assign(window, { fullscreenStats: stats, leaveFullscreenFromTelegram: () => app.exitFullscreen() })
  }, { platform, unsupported })
}

test('desktop opens in the default window and supports manual fullscreen', async ({ page }) => {
  await telegram(page)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Все записи.' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Развернуть на весь экран' })).toBeVisible()
  expect(await page.evaluate(() => (window as any).fullscreenStats)).toEqual({ requests: 0, exits: 0, expansions: 0 })
  await page.getByRole('button', { name: 'Развернуть на весь экран' }).click()
  await page.getByRole('button', { name: 'Выйти из полного экрана' }).click()
  await page.getByRole('button', { name: 'Развернуть на весь экран' }).click()
  await expect(page.getByRole('button', { name: 'Выйти из полного экрана' })).toBeVisible()
  expect(await page.evaluate(() => (window as any).fullscreenStats.requests)).toBe(2)
  await page.evaluate(() => (window as any).leaveFullscreenFromTelegram())
  await expect(page.getByRole('button', { name: 'Развернуть на весь экран' })).toBeVisible()
  expect(await page.evaluate(() => (window as any).fullscreenStats.requests)).toBe(2)
})

test('unsupported fullscreen leaves the app usable and explains a manual request', async ({ page }) => {
  await telegram(page, 'tdesktop', true)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Все записи.' })).toBeVisible()
  await expect(page.getByRole('status')).not.toBeVisible()
  await page.getByRole('button', { name: 'Развернуть на весь экран' }).click()
  await expect(page.getByRole('status')).toContainText('не поддерживает полный экран')
  await page.getByRole('button', { name: 'Новая запись', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Новая запись' })).toBeVisible()
})

test('mobile does not request desktop fullscreen', async ({ page }) => {
  await telegram(page, 'android')
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Все записи.' })).toBeVisible()
  expect(await page.evaluate(() => (window as any).fullscreenStats)).toEqual({ requests: 0, exits: 0, expansions: 0 })
  await expect(page.getByRole('button', { name: 'Развернуть на весь экран' })).not.toBeVisible()
})
