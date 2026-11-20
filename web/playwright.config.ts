import { defineConfig } from '@playwright/test'
import path from 'node:path'
export default defineConfig({
  testDir: './e2e', timeout: 30000, workers: 1, reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:8001', screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  webServer: { command: `"${path.resolve(process.platform === 'win32' ? '../.venv/Scripts/python.exe' : '../.venv/bin/python')}" "${path.resolve('../scripts/qa_server.py')}"`, url: 'http://127.0.0.1:8001/api/health', reuseExistingServer: false, timeout: 30000 },
  projects: [{ name: 'desktop', use: { viewport: { width: 1440, height: 1000 } } }, { name: 'mobile', use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } }],
})
