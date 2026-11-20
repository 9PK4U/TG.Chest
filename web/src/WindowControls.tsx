import { useEffect, useRef, useState } from 'react'
import { Maximize2, Minimize2 } from 'lucide-react'
import { isDesktopTelegram, requestTelegramFullscreen } from './fullscreen'
import type { TelegramApp } from './types'

export default function WindowControls({ notify }: { notify: (message: string) => void }) {
  const [desktop, setDesktop] = useState(false)
  const [fullscreen, setFullscreen] = useState(false)
  const requestedByUser = useRef(false)

  useEffect(() => {
    let detach = () => {}
    function attach() {
      detach()
      const app = window.Telegram?.WebApp
      setDesktop(isDesktopTelegram(app))
      setFullscreen(!!app?.isFullscreen)
      if (!app) return
      const changed = () => { requestedByUser.current = false; setFullscreen(!!app.isFullscreen) }
      const failed = (data?: { error?: string }) => {
        setFullscreen(!!app.isFullscreen)
        if (requestedByUser.current && data?.error !== 'ALREADY_FULLSCREEN') {
          notify('Эта версия Telegram не поддерживает полный экран. Можно развернуть окно вручную.')
        }
        requestedByUser.current = false
      }
      app.onEvent?.('fullscreenChanged', changed)
      app.onEvent?.('fullscreenFailed', failed)
      detach = () => { app.offEvent?.('fullscreenChanged', changed); app.offEvent?.('fullscreenFailed', failed) }
    }
    attach()
    const script = document.getElementById('telegram-sdk')
    script?.addEventListener('load', attach)
    return () => { script?.removeEventListener('load', attach); detach() }
  }, [notify])

  if (!desktop) return null
  function toggle() {
    const app: TelegramApp | undefined = window.Telegram?.WebApp
    if (!app) return
    if (app.isFullscreen) {
      try { app.exitFullscreen?.() } catch { notify('Не удалось выйти из полного экрана. Используй меню Telegram.') }
    } else {
      requestedByUser.current = true
      if (!requestTelegramFullscreen(app)) {
        requestedByUser.current = false
        notify('Эта версия Telegram не поддерживает полный экран. Можно развернуть окно вручную.')
      }
    }
  }
  return <button className="window-control" onClick={toggle} title={fullscreen ? 'Выйти из полного экрана' : 'Развернуть на весь экран'}
    aria-label={fullscreen ? 'Выйти из полного экрана' : 'Развернуть на весь экран'}>
    {fullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}<span>{fullscreen ? 'Свернуть' : 'Развернуть'}</span>
  </button>
}
