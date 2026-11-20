import type { TelegramApp } from './types'

export function isDesktopTelegram(app?: TelegramApp): boolean {
  return !!app && ['tdesktop', 'macos', 'unigram', 'web', 'weba', 'webk'].includes(app.platform || '')
}

export function requestTelegramFullscreen(app: TelegramApp): boolean {
  try {
    if (app.isFullscreen) return true
    if (!app.requestFullscreen || (app.isVersionAtLeast && !app.isVersionAtLeast('8.0'))) return false
    app.requestFullscreen()
    return true
  } catch {
    return false
  }
}
