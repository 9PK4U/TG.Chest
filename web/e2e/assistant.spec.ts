import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.route('https://telegram.org/js/telegram-web-app.js', route => route.fulfill({ contentType: 'text/javascript', body: '' }))
})

test('sensitive intake preserves source and cannot fall back to ordinary AI', async ({ page }, info) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Вкинуть информацию', exact: true }).click()
  let dialog = page.getByRole('dialog')
  await dialog.getByRole('checkbox', { name: 'Можно передавать обычной LLM' }).uncheck()
  const text = `Чувствительная информация ${info.project.name}`
  await dialog.getByRole('textbox', { name: 'Информация для разбора' }).fill(text)
  const intake = page.waitForRequest(r => r.url().endsWith('/api/intake') && r.method() === 'POST')
  await dialog.getByRole('button', { name: 'Сохранить и разобрать' }).click()
  expect((await intake).postDataJSON().ai_policy).toBe('trusted')
  await expect(dialog).not.toBeVisible()
  await page.getByRole('button', { name: `Открыть ${text}`, exact: true }).click()
  dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('checkbox', { name: 'Можно передавать обычной LLM' })).not.toBeChecked()
  await dialog.getByRole('button', { name: 'Исходники и история' }).click()
  await expect(dialog.locator('.source-history pre')).toContainText(text)
  await dialog.getByRole('textbox', { name: 'Инструкция для ИИ' }).fill('Сократи')
  await dialog.getByRole('button', { name: 'Предложить правку' }).click()
  await expect(page.getByRole('status')).toContainText('не подключена')
  await expect(dialog.getByRole('textbox', { name: 'Содержимое', exact: true })).toHaveValue(text)
  await dialog.getByRole('button', { name: 'Закрыть', exact: true }).click()
  await page.getByRole('combobox', { name: 'Режим поиска', exact: true }).selectOption('local')
  const search = page.waitForRequest(r => r.url().endsWith('/api/search') && r.method() === 'POST')
  await page.getByRole('textbox', { name: 'Поиск по сохранённому' }).fill('Чувствительная')
  await page.getByRole('button', { name: 'Найти', exact: true }).click()
  expect((await search).postDataJSON().ai_policy).toBe('local')
  await expect(async () => {
    await page.getByRole('button', { name: 'Найти', exact: true }).click()
    await expect(page.getByRole('heading', { name: text, exact: true })).toBeVisible()
  }).toPass({ timeout: 15000 })
})

test('selected text uses Unicode character offsets and preview requires apply', async ({ page }, info) => {
  await page.goto('/')
  const title = `Правка ${info.project.name}`
  const body = '😀 Начало. Дата: 22 октября. Конец без изменений.'
  await page.getByRole('button', { name: 'Новая запись', exact: true }).click()
  let dialog = page.getByRole('dialog')
  await dialog.getByLabel('Название', { exact: true }).fill(title)
  await dialog.getByLabel('Содержимое').fill(body)
  await dialog.getByRole('button', { name: 'Сохранить', exact: true }).click()
  await page.getByRole('button', { name: `Открыть ${title}`, exact: true }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByLabel('Содержимое').evaluate((element: HTMLTextAreaElement) => {
    element.focus(); const start = element.value.indexOf('22 октября'); element.setSelectionRange(start, start + '22 октября'.length)
    document.dispatchEvent(new Event('selectionchange', { bubbles: true }))
  })
  await page.route('**/api/entries/*/assist', async route => {
    const data = route.request().postDataJSON()
    expect(data.start).toBe(Array.from(body.slice(0, body.indexOf('22 октября'))).length)
    expect(data.end - data.start).toBe('22 октября'.length)
    await route.fulfill({ json: { id: 'preview-check', status: 'preview', selected: '22 октября', replacement: '25 октября', policy: 'external', mode: 'edit', created_at: 1, result_ids: [], error: '' } })
  })
  await dialog.getByRole('textbox', { name: 'Инструкция для ИИ' }).fill('Перенеси на 25 октября')
  await dialog.getByRole('button', { name: 'Предложить правку' }).click()
  await expect(dialog.locator('.diff-after')).toContainText('25 октября')
  await expect(dialog.getByRole('textbox', { name: 'Содержимое', exact: true })).toHaveValue(body)
  await expect(dialog.getByRole('button', { name: 'Применить только эту правку' })).toBeVisible()
})
