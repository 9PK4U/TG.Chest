import { useEffect, useState } from 'react'
import { Check, LoaderCircle, RefreshCw, Settings2, Sparkles } from 'lucide-react'
import { api } from './api'
import type { Me } from './types'

type Policy = 'external' | 'trusted'
type Config = { enabled: boolean; base_url: string; proxy_url: string; bot_proxy_url: string; chat_model: string;
  organize_model: string; embedding_model: string; rerank_model: string; temperature: number; answer_max_tokens: number;
  organize_max_tokens: number; timeout_seconds: number; top_k: number; send_embedding_dimensions: boolean;
  api_key_set: boolean; revision: number; configured: boolean; embedding_dimensions: number; reindex_queued?: number }
type TestResult = { ok: boolean; message?: string; latency_ms?: number; models?: string[] }

export default function AISettings({ updateMe, toast }: { updateMe: (me: Me) => void; toast: (text: string) => void }) {
  const [configs, setConfigs] = useState<Record<Policy, Config> | null>(null), [policy, setPolicy] = useState<Policy>('external'), [expanded, setExpanded] = useState(false), [loading, setLoading] = useState(false)
  const [generation, setGeneration] = useState(0)
  async function load() {
    setLoading(true)
    try { setConfigs(await api('/ai-settings')); setGeneration(value => value + 1) } catch (e) { toast((e as Error).message) } finally { setLoading(false) }
  }
  useEffect(() => { if (expanded && !configs) void load() }, [expanded])
  return <section className="ai-settings"><button type="button" className="button full-width" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}><Settings2 size={17} />Настроить модели и подключения</button>
    {expanded && <><p className="form-hint">Настройки применяются к API, боту и обработчику без перезапуска и входят в бекап данных. Обычный и доверенный контуры независимы.</p><div className="ai-tabs" role="tablist" aria-label="Контур моделей"><button type="button" role="tab" aria-selected={policy === 'external'} onClick={() => setPolicy('external')}>Обычная LLM</button><button type="button" role="tab" aria-selected={policy === 'trusted'} onClick={() => setPolicy('trusted')}>Доверенная LLM</button></div>
      {loading && <LoaderCircle className="spin" size={18} />}
      {configs && <ProviderForm key={`${policy}-${configs[policy].revision}-${generation}`} policy={policy} initial={configs[policy]} toast={toast} changed={async next => { setConfigs({ ...configs, [policy]: next }); updateMe(await api<Me>('/me')) }} />}
      <button type="button" className="text-button" disabled={loading} onClick={load}><RefreshCw size={14} />Перечитать настройки</button></>}
  </section>
}

function ProviderForm({ policy, initial, changed, toast }: { policy: Policy; initial: Config; changed: (config: Config) => Promise<void>; toast: (s: string) => void }) {
  const [form, setForm] = useState(initial), [key, setKey] = useState(''), [clearKey, setClearKey] = useState(false), [busy, setBusy] = useState(false), [result, setResult] = useState<TestResult | null>(null), [models, setModels] = useState<string[]>([])
  const set = <K extends keyof Config>(name: K, value: Config[K]) => { setForm(previous => ({ ...previous, [name]: value })); setResult(null) }
  function payload() {
    const { api_key_set: _key, configured: _configured, embedding_dimensions: _dimensions, reindex_queued: _queued, ...values } = form
    return { ...values, api_key: key || null, clear_api_key: clearKey }
  }
  async function save(e: React.FormEvent) {
    e.preventDefault(); setBusy(true)
    try { const next = await api<Config>(`/ai-settings/${policy}`, { method: 'PUT', body: JSON.stringify(payload()) }); setKey(''); setClearKey(false); await changed(next); toast(next.reindex_queued ? `Сохранено. Переиндексация: ${next.reindex_queued} записей.` : 'Настройки моделей применены без перезапуска') }
    catch (e) { toast((e as Error).message) } finally { setBusy(false) }
  }
  async function test(task: string) {
    setBusy(true); setResult(null)
    try {
      const value = await api<TestResult>(`/ai-settings/${policy}/test`, { method: 'POST', body: JSON.stringify({ task, draft: payload() }) })
      setResult(value)
      if (value.models) {
        setModels(value.models)
        if (value.models.length === 1 && !form.chat_model && !form.organize_model) {
          setForm(previous => ({ ...previous, chat_model: value.models![0] }))
          setResult({ ...value, message: `Найдена одна модель: ${value.models[0]}. Подставлена для ответов и разбора — сохраните настройки.` })
        }
      }
    }
    catch (e) { toast((e as Error).message) } finally { setBusy(false) }
  }
  async function reset() {
    setBusy(true)
    try { const next = await api<Config>(`/ai-settings/${policy}/reset`, { method: 'POST', body: JSON.stringify({ revision: initial.revision }) }); setForm(next); setKey(''); setClearKey(false); await changed(next); toast('Возвращены исходные настройки из .env') }
    catch (e) { toast((e as Error).message) } finally { setBusy(false) }
  }
  async function resume() {
    setBusy(true)
    try { const value = await api<{ queued: number }>(`/ai-settings/${policy}/resume`, { method: 'POST' }); toast(`Ожидающие разборы в очереди: ${value.queued}`) }
    catch (e) { toast((e as Error).message) } finally { setBusy(false) }
  }
  const listId = `models-${policy}`
  return <form className="provider-form" onSubmit={save}><fieldset disabled={busy}>
    <label className="check-label"><input type="checkbox" checked={form.enabled} onChange={e => set('enabled', e.target.checked)} />Включить этот контур</label>
    <p className="form-hint">{policy === 'external' ? 'Для записей, которым разрешена обычная модель. Можно подключить OpenRouter или другой OpenAI-совместимый API.' : 'Только твой доверенный сервер или локальная модель с OpenAI-совместимым API. Перехода к обычной модели при сбое нет.'}</p>
    <label>Адрес API<input value={form.base_url} placeholder={policy === 'external' ? 'https://openrouter.ai/api/v1' : 'http://host.docker.internal:11434/v1'} onChange={e => set('base_url', e.target.value)} maxLength={2000} /></label>
    <label>API-ключ<input type="password" autoComplete="new-password" value={key} onChange={e => { setKey(e.target.value); setClearKey(false); setResult(null) }} maxLength={4096} placeholder={initial.api_key_set ? 'Ключ задан. Пустое поле сохранит его.' : 'Не задан; для локальной модели может не требоваться'} /></label>
    {initial.api_key_set && <label className="check-label"><input type="checkbox" checked={clearKey} onChange={e => { setClearKey(e.target.checked); if (e.target.checked) setKey('') }} />Удалить сохранённый ключ</label>}
    <p className="form-hint">Ключ не возвращается из API и не показывается повторно. В БД хранится зашифрованным.</p>
    {form.enabled && form.base_url && !form.chat_model && !form.organize_model && <p className="model-test-error" role="status">Адрес задан, но модель ещё не выбрана. Загрузите список и выберите модель ответов или разбора.</p>}
    <div className="assistant-actions"><button type="button" className="button subtle" onClick={() => test('models')}>Загрузить список моделей</button>{models.length > 0 && <small>{models.length} моделей в подсказках</small>}</div>
    <datalist id={listId}>{models.map(model => <option key={model} value={model} />)}</datalist>
    {models.length > 0 && <label>Выбрать модель из списка<select aria-label="Выбрать модель из списка" value={models.includes(form.chat_model) ? form.chat_model : ''} onChange={e => { if (e.target.value) set('chat_model', e.target.value) }}><option value="">Выберите модель ответов</option>{models.map(model => <option key={model} value={model}>{model}</option>)}</select></label>}
    <label>Модель ответов<input list={listId} value={form.chat_model} maxLength={200} onChange={e => set('chat_model', e.target.value)} placeholder="ID модели или пусто, чтобы отключить ответы" /></label>
    <label>Модель разбора и правок<input list={listId} value={form.organize_model} maxLength={200} onChange={e => set('organize_model', e.target.value)} placeholder="Пусто — использовать модель ответов" /></label>
    <label>Модель эмбеддингов<input list={listId} value={form.embedding_model} maxLength={200} onChange={e => set('embedding_model', e.target.value)} placeholder="Пусто — только поиск по словам" /></label>
    <label>Модель реранкера<input list={listId} value={form.rerank_model} maxLength={200} onChange={e => set('rerank_model', e.target.value)} placeholder="Необязательно; нужен endpoint /rerank" /></label>
    <p className="form-hint">Для эмбеддингов и реранкера можно вписать ID вручную: каталог провайдера может показывать только чат-модели. Новый эмбеддер должен выдавать {form.embedding_dimensions} чисел; при его смене записи этого контура переиндексируются.</p>
    <details className="provider-advanced"><summary>Прокси, генерация и поиск</summary><label>Прокси API и обработчика<input value={form.proxy_url} onChange={e => set('proxy_url', e.target.value)} placeholder="http://metacubexd:7890 или пусто" /></label><label>Прокси Telegram-бота<input value={form.bot_proxy_url} onChange={e => set('bot_proxy_url', e.target.value)} placeholder="http://127.0.0.1:7890 или пусто" /></label>
      <p className="form-hint">Это прокси запросов к моделям, не подключения к Telegram. API/обработчик работают в Docker, бот — в сети хоста. localhost внутри контейнера не равен серверу.</p>
      <div className="form-row"><label>Температура<input type="number" step="0.05" min="0" max="2" required value={form.temperature} onChange={e => set('temperature', Number(e.target.value))} /></label><label>Таймаут, сек<input type="number" min="5" max="300" required value={form.timeout_seconds} onChange={e => set('timeout_seconds', Number(e.target.value))} /></label></div>
      <div className="form-row"><label>Токены ответа<input type="number" min="128" max="32000" required value={form.answer_max_tokens} onChange={e => set('answer_max_tokens', Number(e.target.value))} /></label><label>Токены разбора<input type="number" min="256" max="64000" required value={form.organize_max_tokens} onChange={e => set('organize_max_tokens', Number(e.target.value))} /></label></div>
      <label>Источников в ответе<input type="number" min="1" max="20" required value={form.top_k} onChange={e => set('top_k', Number(e.target.value))} /></label>
      <label className="check-label"><input type="checkbox" checked={form.send_embedding_dimensions} onChange={e => set('send_embedding_dimensions', e.target.checked)} />Передавать dimensions={form.embedding_dimensions} эмбеддеру</label>
    </details>
    <div className="model-tests"><span>Проверить текущие поля без сохранения:</span><div className="assistant-actions">{[['chat', 'Ответы'], ['organize', 'Разбор'], ['embeddings', 'Эмбеддер'], ['rerank', 'Реранкер']].map(([task, label]) => <button type="button" key={task} className="button subtle" onClick={() => test(task)}>{label}</button>)}</div></div>
    {result && <p role="status" className={result.ok ? 'model-test-ok' : 'model-test-error'}>{result.ok ? <Check size={15} /> : <span>!</span>}{result.message || (result.models ? `Получено моделей: ${result.models.length}` : '')}{result.latency_ms != null && ` · ${result.latency_ms} мс`}</p>}
    <p className="form-hint">Проверки используют синтетический текст и могут расходовать небольшой объём API. Реальные заметки и секреты не отправляются.</p>
    <div className="assistant-actions"><button className="button primary" type="submit"><Check size={16} />Сохранить настройки моделей</button><button type="button" className="text-button" onClick={reset}>Вернуть настройки из .env</button></div>
    <button type="button" className="button" onClick={resume}><Sparkles size={16} />Повторить ожидающие разборы</button>
  </fieldset>{busy && <p className="form-hint"><LoaderCircle className="spin" size={16} />Обрабатываю…</p>}</form>
}
