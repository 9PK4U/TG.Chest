let auth = ''
export async function initializeAuth() {
  const prepareTelegram = () => {
    const tg = window.Telegram?.WebApp
    tg?.ready()
    tg?.setHeaderColor?.('#151619')
    tg?.setBackgroundColor?.('#151619')
  }
  prepareTelegram()
  document.getElementById('telegram-sdk')?.addEventListener('load', prepareTelegram, { once: true })
  // Telegram passes signed initData in the URL fragment too. Auth must not wait for a slow SDK CDN.
  const initData = window.Telegram?.WebApp?.initData || new URLSearchParams(location.hash.slice(1)).get('tgWebAppData')
  if (initData) auth = `tma ${initData}`
  else {
    const config = await fetch('/api/config').then(r => r.json())
    if (config.dev_auth) auth = 'Dev local'
  }
}
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  headers.set('Authorization', auth)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  const res = await fetch(`/api${path}`, { ...options, headers })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(typeof body.detail === 'string' ? body.detail : `Ошибка ${res.status}. Проверьте введённые данные.`)
  }
  return res.json()
}
export async function download(id: string, filename: string) {
  const res = await fetch(`/api/files/${id}`, { headers: { Authorization: auth } })
  if (!res.ok) throw new Error('Не удалось скачать файл. Попробуйте позже.')
  const url = URL.createObjectURL(await res.blob())
  const a = document.createElement('a'); a.href = url; a.download = filename; a.click()
  setTimeout(() => URL.revokeObjectURL(url), 60000)
}
