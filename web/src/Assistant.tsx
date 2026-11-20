import { useEffect, useRef, useState } from 'react'
import { Check, LoaderCircle, Paperclip, ShieldCheck, Sparkles } from 'lucide-react'
import { api } from './api'
import type { AIPolicy, Capture, Entry, Me } from './types'

export function PolicyPicker({ value, set, disabled = false }: { value: AIPolicy; set: (v: AIPolicy) => void; disabled?: boolean }) {
  return <div className="ai-policy"><label className="check-label"><input type="checkbox" checked={value === 'external'} disabled={disabled} onChange={e => set(e.target.checked ? 'external' : 'trusted')} />Можно передавать обычной LLM</label>
    {value !== 'external' && <label>Чувствительные данные<select aria-label="Режим чувствительных данных" disabled={disabled} value={value} onChange={e => set(e.target.value as AIPolicy)}><option value="trusted">Только доверенная модель</option><option value="local">Не передавать никаким моделям</option></select></label>}
    <p className="form-hint"><ShieldCheck size={14} />{value === 'external' ? 'Текст и содержимое файлов этой записи можно отправлять обычной модели.' : value === 'trusted' ? 'Запись и её файлы доступны только доверенной модели. Пока она не подключена, они сохраняются без обработки.' : 'Заметка и файлы остаются без обработки ИИ. Доступен локальный поиск.'}</p></div>
}

export function Intake({ folderId, done, toast }: { folderId: string | null; done: () => void; toast: (s: string) => void }) {
  const [text, setText] = useState(''), [policy, setPolicy] = useState<AIPolicy>('external'), [files, setFiles] = useState<File[]>([]), [busy, setBusy] = useState(false)
  const saved = useRef<Entry | null>(null), uploaded = useRef(new Set<File>())
  async function submit(e: React.FormEvent) {
    e.preventDefault(); if (busy) return; setBusy(true)
    try {
      if (!saved.current) saved.current = await api<Entry>('/intake', { method: 'POST', body: JSON.stringify({ text: text.trim() || files.map(f => f.name).join('\n'), ai_policy: policy, folder_id: folderId, defer: true }) })
      for (const file of files) if (!uploaded.current.has(file)) { const form = new FormData(); form.set('file', file); await api(`/entries/${saved.current.id}/files`, { method: 'POST', body: form }); uploaded.current.add(file) }
      await api(`/entries/${saved.current.id}/organize`, { method: 'POST' })
      toast(policy === 'external' ? 'Сохранено. LLM разберёт информацию, исходник останется в истории.' : 'Сохранено в выбранном приватном режиме.'); done()
    } catch (e) { toast((e as Error).message) } finally { setBusy(false) }
  }
  return <form className="editor-form" onSubmit={submit}><p className="muted">Вставь мысли, переписку, список дел или документ. Модель выделит самостоятельные заметки, даст названия и разложит файлы по темам.</p>
    <PolicyPicker value={policy} set={setPolicy} disabled={busy || !!saved.current} />
    <label>Что сохранить<textarea aria-label="Информация для разбора" rows={10} maxLength={100000} value={text} disabled={busy || !!saved.current} onChange={e => setText(e.target.value)} placeholder="Просто напиши или вставь информацию…" /></label>
    <label className="file-picker"><Paperclip size={18} />Добавить файлы<input type="file" multiple disabled={busy || !!saved.current} onChange={e => setFiles([...files, ...Array.from(e.target.files || [])])} /></label>
    {files.map((file, i) => <div className="queued-file" key={i}><span>{file.name}</span><button type="button" className="text-button" disabled={busy || !!saved.current} onClick={() => setFiles(files.filter((_, n) => n !== i))}>Убрать</button></div>)}
    <button className="button primary full-width" disabled={busy || (!text.trim() && !files.length)}>{busy ? <LoaderCircle className="spin" size={17} /> : <Sparkles size={17} />}Сохранить и разобрать</button></form>
}

export function NoteAssistant({ entry, selection, done, toast }: { entry: Entry; selection: [number, number]; done: () => void; toast: (s: string) => void }) {
  const [instruction, setInstruction] = useState(''), [preview, setPreview] = useState<Capture | null>(null), [busy, setBusy] = useState(false), [history, setHistory] = useState<Capture[] | null>(null)
  useEffect(() => { setPreview(null) }, [instruction, selection[0], selection[1]])
  async function propose() {
    setBusy(true)
    try { setPreview(await api<Capture>(`/entries/${entry.id}/assist`, { method: 'POST', body: JSON.stringify({ instruction, start: selection[0], end: selection[1], revision: entry.revision }) })) }
    catch (e) { toast((e as Error).message) } finally { setBusy(false) }
  }
  async function apply() {
    if (!preview) return; setBusy(true)
    try { await api(`/captures/${preview.id}/apply`, { method: 'POST' }); toast('Фрагмент изменён, исходник сохранён.'); done() }
    catch (e) { toast((e as Error).message) } finally { setBusy(false) }
  }
  async function organize() {
    setBusy(true)
    try { await api(`/entries/${entry.id}/organize`, { method: 'POST' }); toast('Разбор поставлен в очередь. Исходник сохранён.'); done() }
    catch (e) { toast((e as Error).message) } finally { setBusy(false) }
  }
  async function loadHistory() {
    try { setHistory(await api<Capture[]>(`/entries/${entry.id}/sources`)) } catch (e) { toast((e as Error).message) }
  }
  async function retry(record: Capture) {
    setBusy(true)
    try { await api(`/captures/${record.id}/retry`, { method: 'POST' }); await loadHistory(); toast('Обработка поставлена в очередь') } catch (e) { toast((e as Error).message) } finally { setBusy(false) }
  }
  const statuses: Record<string, string> = { queued: 'В очереди', waiting: 'Модель не подключена', applied: 'Применено', preview: 'Предложение', failed: 'Не удалось обработать', stale: 'Запись изменилась', saved: 'Исходник сохранён' }
  return <section className="note-assistant"><h3><Sparkles size={17} />Работа с ИИ</h3><p className="form-hint">Сначала сохрани ручные правки. Выдели фрагмент в тексте выше или работай со всей заметкой. Модель получает контекст этой заметки.</p>
    <p className="form-hint">{entry.ai_policy === 'external' ? 'Обычная LLM' : entry.ai_policy === 'trusted' ? 'Только доверенная модель' : 'Модели отключены'} · {selection[1] - selection[0]} символов в выбранном фрагменте</p>
    <label>Что изменить<input aria-label="Инструкция для ИИ" maxLength={4000} value={instruction} onChange={e => setInstruction(e.target.value)} placeholder="Например: замени дату встречи на 25 октября" /></label>
    <div className="assistant-actions"><button type="button" className="button" disabled={busy || !instruction.trim() || entry.ai_policy === 'local'} onClick={propose}>Предложить правку</button><button type="button" className="button subtle" disabled={busy || entry.ai_policy === 'local'} onClick={organize}>Разобрать на заметки</button><button type="button" className="text-button" onClick={() => history ? setHistory(null) : loadHistory()}>Исходники и история</button></div>
    {busy && <p className="form-hint"><LoaderCircle className="spin" size={15} />Обрабатываю…</p>}
    {preview && <div className="ai-preview"><b>Замена выбранного фрагмента</b><div className="diff-before"><small>Было</small><pre>{preview.selected || '(пустой фрагмент)'}</pre></div><div className="diff-after"><small>Станет</small><pre>{preview.replacement || '(фрагмент будет удалён)'}</pre></div><button type="button" className="button primary" disabled={busy} onClick={apply}><Check size={16} />Применить только эту правку</button></div>}
    {history && <div className="source-history">{!history.length && <p className="muted">Исходников пока нет.</p>}{history.map(record => <details key={record.id}><summary>{new Date(record.created_at * 1000).toLocaleString('ru')} · {statuses[record.status] || record.status}</summary><b>{record.original?.title}</b><pre>{record.original?.body}</pre>{record.attachments?.map(a => <p key={a.id}>{a.filename}</p>)}{record.instruction && <p>Просьба: {record.instruction}</p>}{['waiting', 'failed'].includes(record.status) && record.mode === 'organize' && <button type="button" className="button" disabled={busy} onClick={() => retry(record)}>Повторить разбор</button>}</details>)}</div>}
  </section>
}

type Credentials = { login: string; password: string; url: string; notes: string }

export function TrustedVaultAssistant({ fields, apply, ready, toast }: { fields: Credentials; apply: (v: Credentials) => void; ready: boolean; toast: (s: string) => void }) {
  const [instruction, setInstruction] = useState(''), [preview, setPreview] = useState<Credentials | null>(null), [busy, setBusy] = useState(false), [consent, setConsent] = useState(false)
  useEffect(() => { setPreview(null) }, [fields, instruction])
  async function ask() {
    setBusy(true)
    try { setPreview(await api<Credentials>('/trusted-vault/assist', { method: 'POST', body: JSON.stringify({ instruction, fields }) })) }
    catch (e) { toast((e as Error).message) } finally { setBusy(false) }
  }
  return <section className="note-assistant"><h3><ShieldCheck size={17} />Доверенный помощник сейфа</h3><p className="form-hint">{ready ? 'По твоему разрешению открытые поля этой записи будут отправлены только на настроенный доверенный сервер. Обычная LLM не используется. Результат зашифруется при сохранении.' : 'Доверенная модель ещё не подключена. Содержимое сейфа не отправляется моделям.'}</p>
    {ready && <><label className="check-label"><input type="checkbox" checked={consent} onChange={e => { setConsent(e.target.checked); setPreview(null) }} />Разрешаю передать поля этой записи доверенной модели</label><label>Что изменить в секрете<input maxLength={4000} value={instruction} onChange={e => setInstruction(e.target.value)} /></label><button type="button" className="button" disabled={busy || !consent || !instruction.trim()} onClick={ask}>{busy ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}Предложить изменения</button>{preview && <div className="ai-preview"><p>Проверь предложенные поля перед применением.</p>{Object.entries(preview).map(([key, value]) => <label key={key}>{key}<textarea readOnly value={value} rows={2} /></label>)}<button type="button" className="button primary" onClick={() => { apply(preview); setPreview(null) }}>Применить к полям</button></div>}</>}
  </section>
}

export function BotPolicySettings({ me, updateMe, toast }: { me: Me; updateMe: (m: Me) => void; toast: (s: string) => void }) {
  const [busy, setBusy] = useState(false)
  async function change(policy: AIPolicy) {
    setBusy(true)
    try { await api('/bot-policy', { method: 'PUT', body: JSON.stringify({ ai_policy: policy }) }); updateMe({ ...me, bot_ai_policy: policy }); toast('Режим бота сохранён') }
    catch (e) { toast((e as Error).message) } finally { setBusy(false) }
  }
  return <section className="note-assistant"><h3>Две модели</h3><p className="form-hint">Обычная: {me.integrations.llm ? 'подключена' : 'не подключена'}<br />Доверенная: {me.integrations.trusted_llm ? 'подключена' : 'ещё не подключена'}</p><label>Новые сообщения боту<select aria-label="Режим новых сообщений боту" disabled={busy} value={me.bot_ai_policy || 'external'} onChange={e => change(e.target.value as AIPolicy)}><option value="external">Сразу разбирать обычной LLM</option><option value="trusted">Чувствительные · только доверенная LLM</option><option value="local">Сохранять без моделей</option></select></label><p className="form-hint">В боте: /private — чувствительный режим, /public — обычный, /local — без моделей. Файлы наследуют режим заметки. В сейфе передача доверенной модели разрешается отдельно.</p></section>
}
