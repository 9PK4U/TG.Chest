import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Archive, ArrowDownToLine, ArrowLeft, ArrowRight, ArrowUpRight, Check, ChevronDown, ChevronRight, Copy, File, FileText, Folder as FolderIcon, FolderPlus, Grid2X2, Inbox, Link, List, LoaderCircle, LockKeyhole, Menu, MoreHorizontal, Paperclip, Plus, Search, Settings, ShieldCheck, Sparkles, Star, Trash2, X } from 'lucide-react'
import { api, download, initializeAuth } from './api'
import { decrypt, deriveKey, encrypt, randomSalt, VERIFIER } from './vault'
import type { AIPolicy, Entry, Folder, Me, SearchResult } from './types'
import WindowControls from './WindowControls'
import { BotPolicySettings, Intake, NoteAssistant, PolicyPicker, TrustedVaultAssistant } from './Assistant'
import AISettings from './AISettings'

type View = 'all' | 'inbox' | 'favorites' | 'files' | 'vault'
const views: { id: View; label: string; icon: typeof Inbox }[] = [
  { id: 'all', label: 'Все записи', icon: Archive }, { id: 'inbox', label: 'Входящие', icon: Inbox },
  { id: 'favorites', label: 'Избранное', icon: Star }, { id: 'files', label: 'Файлы', icon: Paperclip },
  { id: 'vault', label: 'Сейф', icon: LockKeyhole },
]
const date = (n: number) => new Date(n * 1000).toLocaleDateString('ru', { day: 'numeric', month: 'short' })
const bytes = (n: number) => n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} КБ` : `${(n / 1024 / 1024).toFixed(1)} МБ`
const kindIcon = (e: { kind: string; attachments?: unknown[] }) => e.kind === 'secret' ? LockKeyhole : e.attachments?.length ? Paperclip : e.kind === 'link' ? Link : FileText

function Modal({ title, close, children, wide = false }: { title: string; close: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLElement>(null)
  useEffect(() => {
    const prior = document.activeElement as HTMLElement | null
    const focusable = () => [...(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input, textarea, select, [tabindex="0"]') || [])]
    focusable()[0]?.focus()
    const handle = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close()
      if (e.key === 'Tab') {
        const elements = focusable(); const first = elements[0]; const last = elements.at(-1)
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus() }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus() }
      }
    }
    document.addEventListener('keydown', handle)
    const overflow = document.body.style.overflow; document.body.style.overflow = 'hidden'
    return () => { document.removeEventListener('keydown', handle); document.body.style.overflow = overflow; prior?.focus() }
  }, [close])
  return <div className="scrim" onMouseDown={e => { if (e.target === e.currentTarget) close() }}>
    <section ref={ref} className={`modal ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true" aria-label={title}>
      <header><h2>{title}</h2><button className="icon-button" aria-label="Закрыть" onClick={close}><X size={20} /></button></header>{children}
    </section></div>
}

export default function App() {
  const [me, setMe] = useState<Me | null>(null), [error, setError] = useState(''), [notice, setNotice] = useState('')
  const [folders, setFolders] = useState<Folder[]>([]), [entries, setEntries] = useState<Entry[]>([]), [total, setTotal] = useState(0)
  const [stats, setStats] = useState({ total: 0, pending: 0 }), [view, setView] = useState<View>(() => new URLSearchParams(location.search).get('view') === 'vault' ? 'vault' : 'all'), [folderId, setFolderId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true), [refresh, setRefresh] = useState(0), [sidebar, setSidebar] = useState(false)
  const [layout, setLayout] = useState<'grid' | 'list'>('grid'), [filter, setFilter] = useState('all')
  const [query, setQuery] = useState(''), [searching, setSearching] = useState(false), [result, setResult] = useState<SearchResult | null>(null)
  const [editor, setEditor] = useState<Entry | 'new' | 'secret' | null>(null), [folderModal, setFolderModal] = useState<'new' | Folder | null>(null)
  const [settings, setSettings] = useState(false), [unlock, setUnlock] = useState(false), [vaultKey, setVaultKey] = useState<CryptoKey | null>(null)
  const [hintEditor, setHintEditor] = useState(false)
  const [intake, setIntake] = useState(false), [searchPolicy, setSearchPolicy] = useState<AIPolicy>('external')
  const sequence = useRef(0)
  const toast = useCallback((text: string) => setNotice(text), [])
  const reload = useCallback(() => setRefresh(v => v + 1), [])
  const closeEditor = useCallback(() => setEditor(null), [])
  const closeFolder = useCallback(() => setFolderModal(null), [])
  const closeSettings = useCallback(() => setSettings(false), [])
  const closeUnlock = useCallback(() => setUnlock(false), [])
  const lock = useCallback(() => { setVaultKey(null); setHintEditor(false); setEditor(e => e === 'secret' || (typeof e === 'object' && e?.kind === 'secret') ? null : e) }, [])

  useEffect(() => { initializeAuth().then(() => api<Me>('/me')).then(setMe).catch(e => setError(e.message)) }, [])
  useEffect(() => { if (notice) { const timer = setTimeout(() => setNotice(''), 4500); return () => clearTimeout(timer) } }, [notice])
  useEffect(() => {
    if (!vaultKey) return
    let timer: ReturnType<typeof setTimeout>
    const reset = () => { clearTimeout(timer); timer = setTimeout(lock, 120000) }
    const hidden = () => { if (document.hidden) lock() }
    reset(); window.addEventListener('pointerdown', reset); window.addEventListener('keydown', reset); document.addEventListener('visibilitychange', hidden)
    return () => { clearTimeout(timer); window.removeEventListener('pointerdown', reset); window.removeEventListener('keydown', reset); document.removeEventListener('visibilitychange', hidden) }
  }, [vaultKey, lock])
  useEffect(() => {
    if (!me) return
    const current = ++sequence.current
    setLoading(true)
    const params = new URLSearchParams({ view, limit: '40' }); if (folderId) params.set('folder_id', folderId)
    Promise.all([api<{ items: Entry[]; total: number }>(`/entries?${params}`), api<Folder[]>('/folders'), api<typeof stats>('/stats')])
      .then(([data, fs, counts]) => { if (current === sequence.current) { setEntries(data.items); setTotal(data.total); setFolders(fs); setStats(counts) } })
      .catch(e => toast(e.message)).finally(() => { if (current === sequence.current) setLoading(false) })
  }, [me, view, folderId, refresh, toast])
  useEffect(() => { if (!me) return; const timer = setInterval(reload, 30000); return () => clearInterval(timer) }, [me, reload])

  function navigate(next: View, folder: string | null = null) { setView(next); setFolderId(folder); setResult(null); setQuery(''); setFilter('all'); setSidebar(false) }
  async function open(entry: { id: string }) {
    try { const full = await api<Entry>(`/entries/${entry.id}`); if (full.kind === 'secret' && !vaultKey) { setUnlock(true); return } setEditor(full) }
    catch (e) { toast((e as Error).message) }
  }
  async function runSearch(e: React.FormEvent) {
    e.preventDefault(); if (!query.trim() || searching) return
    setSearching(true)
    try { setResult(await api<SearchResult>('/search', { method: 'POST', body: JSON.stringify({ query, folder_id: folderId, ai_policy: searchPolicy }) })) }
    catch (e) { toast((e as Error).message) } finally { setSearching(false) }
  }
  async function favorite(entry: Entry) {
    try { const full = await api<Entry>(`/entries/${entry.id}`)
      await api(`/entries/${entry.id}`, { method: 'PUT', body: JSON.stringify({ ...entryPayload(full), favorite: !full.favorite, revision: full.revision }) }); reload()
    } catch (e) { toast((e as Error).message) }
  }
  async function loadMore() {
    setLoading(true)
    try { const params = new URLSearchParams({ view, offset: String(entries.length), limit: '40' }); if (folderId) params.set('folder_id', folderId)
      const data = await api<{ items: Entry[]; total: number }>(`/entries?${params}`); setEntries(e => [...e, ...data.items]); setTotal(data.total)
    } catch (e) { toast((e as Error).message) } finally { setLoading(false) }
  }
  const folder = folders.find(f => f.id === folderId)
  const heading = folder?.name || views.find(v => v.id === view)!.label
  const visible = entries.filter(e => filter === 'all' || (filter === 'files' ? !!e.attachments.length : e.kind === filter))
  const children = folders.filter(f => f.parent_id === folderId)
  const crumbs: Folder[] = []; let ancestor = folder
  while (ancestor && crumbs.length < 20) { crumbs.unshift(ancestor); ancestor = folders.find(f => f.id === ancestor!.parent_id) }

  if (!me) return <div className="startup"><div className="brand-mark"><Archive size={28} /></div><h1>Твой Chest</h1>
    {error ? <><p>{error}</p><p className="muted">Открой приложение кнопкой меню своего Telegram-бота.</p><button className="button" onClick={() => location.reload()}>Попробовать снова</button></> : <><LoaderCircle className="spin" /><p>Открываем личное пространство…</p></>}</div>

  return <div className="app-shell">
    {sidebar && <button className="sidebar-scrim" onClick={() => setSidebar(false)} aria-label="Закрыть меню" />}
    <aside className={`sidebar ${sidebar ? 'open' : ''}`}>
      <a className="brand" href="#" onClick={e => { e.preventDefault(); navigate('all') }}><span className="brand-mark"><Archive size={23} /></span>chest<span className="brand-dot">.</span><span className="personal-badge">личное</span></a>
      <button className="button primary side-create" onClick={() => { setEditor('new'); setSidebar(false) }}><Plus size={18} /> Добавить запись</button>
      <div className="nav-caption">ПРОСТРАНСТВО</div>
      <nav aria-label="Основная навигация">{views.map(({ id, label, icon: Icon }) => <button key={id} className={`nav-item ${view === id && !folderId ? 'active' : ''}`} onClick={() => navigate(id)}><Icon size={18} /><span>{label}</span>{id === 'all' && <small>{stats.total}</small>}{id === 'vault' && <span className="nav-lock">{vaultKey ? <ShieldCheck size={13} /> : <LockKeyhole size={13} />}</span>}</button>)}</nav>
      <div className="nav-caption folder-caption">РАЗДЕЛЫ<button className="icon-button" aria-label="Создать раздел" onClick={() => setFolderModal('new')}><Plus size={16} /></button></div>
      <div className="folder-nav">{folders.filter(f => !f.parent_id).map(f => <FolderBranch key={f.id} folder={f} folders={folders} selected={folderId} choose={id => navigate('all', id)} />)}
        {!folders.length && <button className="empty-folders" onClick={() => setFolderModal('new')}><FolderPlus size={16} /> Создай первый раздел</button>}</div>
      <div className="side-bottom"><div className="private-note"><span className="private-dot" /> Только для тебя<ShieldCheck size={14} /></div>
        <button className="profile" onClick={() => setSettings(true)}><span className="avatar">{me.name.slice(0, 1).toUpperCase()}</span><span><b>{me.name}</b><small>Личное пространство</small></span><Settings size={18} /></button></div>
    </aside>
    <div className="workspace"><header className="topbar"><div className="breadcrumb"><button className="icon-button mobile-menu" aria-label="Открыть меню" onClick={() => setSidebar(true)}><Menu size={21} /></button><span>Мой Chest</span><ChevronRight size={14} /><b>{heading}</b></div><div className="topbar-right"><WindowControls notify={toast} /><span className="private-top"><LockKeyhole size={13} /> Личное пространство</span><span className="avatar small">{me.name.slice(0, 1)}</span></div></header>
      <main><div className="page-intro"><div><div className="eyebrow">СОХРАНИТЬ. НАЙТИ. ВСПОМНИТЬ.</div><h1>{heading}<span className="heading-dot">.</span></h1><p>{view === 'vault' ? 'Важное — под твоим ключом.' : folder ? 'Всё по теме, в одном месте.' : 'Всё, что не хочется потерять.'}</p></div>
        <div className="intro-actions">{folder && <button className="icon-button folder-settings" aria-label="Изменить раздел" onClick={() => setFolderModal(folder)}><MoreHorizontal /></button>}
          {view !== 'vault' && <button className="button" onClick={() => setIntake(true)}><Sparkles size={17} />Вкинуть информацию</button>}
          {view === 'vault' && vaultKey && <button className="button subtle" onClick={lock}><LockKeyhole size={16} />Закрыть сейф</button>}
          {view === 'vault' && vaultKey && <button className="button subtle" onClick={() => setHintEditor(true)}>Подсказка к паролю</button>}
          <button className="button primary" onClick={() => view === 'vault' ? vaultKey ? setEditor('secret') : setUnlock(true) : setEditor('new')}><Plus size={18} /><span>{view === 'vault' ? 'Добавить пароль' : 'Новая запись'}</span></button></div></div>

      {view !== 'vault' && <form className={`search-box ${searching ? 'searching' : ''}`} onSubmit={runSearch}><span className="search-symbol"><Search size={21} /></span><input aria-label="Поиск по сохранённому" placeholder="Что ты хочешь найти? Можно своими словами" value={query} onChange={e => setQuery(e.target.value)} maxLength={2000} />{query && <button type="button" className="icon-button" aria-label="Очистить поиск" onClick={() => { setQuery(''); setResult(null) }}><X size={16} /></button>}<button className="search-submit" disabled={searching || !query.trim()} aria-label="Найти">{searching ? <LoaderCircle className="spin" size={17} /> : <><Sparkles size={15} /><span>Найти</span><ArrowRight size={16} /></>}</button></form>}
      {view !== 'vault' && <div className="search-privacy"><label>Режим поиска<select aria-label="Режим поиска" value={searchPolicy} onChange={e => { setSearchPolicy(e.target.value as AIPolicy); setResult(null) }}><option value="external">Обычная LLM · только разрешённые записи</option><option value="trusted">Доверенная LLM · включая чувствительные</option><option value="local">Локально · без отправки запроса моделям</option></select></label><p className="form-hint">{searchPolicy === 'external' ? 'Запрос получает обычная модель. Чувствительные записи исключены.' : searchPolicy === 'trusted' ? me.integrations.trusted_llm ? 'Запрос и найденные записи получает только доверенная модель.' : 'Доверенная модель не подключена. Поиск выполняется локально.' : 'Ищет по словам во всех обычных заметках. Никакие модели не вызываются.'}</p></div>}

      {result ? <section className="search-results"><div className="results-caption"><span>Результаты поиска · {result.results.length}</span><button className="text-button" onClick={() => setResult(null)}><ArrowLeft size={14} />К записям</button></div>
        {result.answer && <article className="answer"><div className="answer-label"><Sparkles size={17} /> Ответ по твоим записям</div><p>{result.answer}</p><small>Проверь детали в источниках ниже.</small></article>}
        {result.warnings.map(w => <p className="info-line" key={w}>{w}</p>)}
        {result.results.map((r, i) => <button className="result-card" key={r.id} onClick={() => open(r)}><span className="source-number">{i + 1}</span><div><h3>{r.title}</h3><p>{r.excerpt.slice(0, 450)}</p></div><ArrowUpRight size={18} /></button>)}
        {!result.results.length && <Empty title="Пока ничего не нашлось" text="Попробуй другие слова или дождись обработки новых записей." icon={<Search />} />}</section>
        : view === 'vault' && !vaultKey ? <div className="vault-landing"><div className="vault-art"><LockKeyhole size={44} strokeWidth={1.3} /><span><ShieldCheck size={17} /></span></div><span className="eyebrow">ЛИЧНЫЙ СЕЙФ</span><h2>{me.vault ? 'Доступ только с твоим ключом' : 'Спокойное место для секретов'}</h2><p>Пароли шифруются на твоём устройстве.<br />Мастер-пароль не покидает приложение.</p><button className="button primary" onClick={() => setUnlock(true)}><LockKeyhole size={16} />{me.vault ? 'Открыть сейф' : 'Настроить сейф'}<ArrowRight size={16} /></button><small>Секреты не участвуют в поиске. Доверенный помощник включается отдельно.</small></div>
        : <><div className="collection-toolbar"><div className="filter-tabs">{view === 'vault' ? <span className="selected-tab">Пароли</span> : [['all', 'Всё'], ['note', 'Заметки'], ['link', 'Ссылки'], ['files', 'Файлы']].map(([id, label]) => <button key={id} className={filter === id ? 'selected-tab' : ''} onClick={() => setFilter(id)}>{label}</button>)}</div><div className="collection-meta"><span>{total} записей</span><div className="layout-switch"><button className={layout === 'grid' ? 'active' : ''} onClick={() => setLayout('grid')} aria-label="Карточки"><Grid2X2 size={16} /></button><button className={layout === 'list' ? 'active' : ''} onClick={() => setLayout('list')} aria-label="Список"><List size={17} /></button></div></div></div>
        {folderId && <div className="folder-crumbs"><button onClick={() => navigate('all')}>Все записи</button>{crumbs.map(f => <span key={f.id}><ChevronRight size={12} /><button onClick={() => navigate('all', f.id)}>{f.name}</button></span>)}</div>}
        {children.length > 0 && view === 'all' && <div className="subfolders">{children.map(f => <button key={f.id} onClick={() => navigate('all', f.id)}><FolderIcon size={18} style={{ color: f.color }} />{f.name}<ChevronRight size={15} /></button>)}</div>}
        {loading && !entries.length ? <div className="loading"><LoaderCircle className="spin" />Загружаем записи…</div> : <div className={`entries ${layout}`}>{visible.map(e => <EntryCard key={e.id} entry={e} folder={folders.find(f => f.id === e.folder_id)} open={() => open(e)} favorite={() => favorite(e)} />)}</div>}
        {!loading && !visible.length && <Empty icon={view === 'files' ? <Paperclip /> : view === 'favorites' ? <Star /> : <Inbox />} title={view === 'favorites' ? 'Самое нужное — поближе' : filter !== 'all' ? 'Здесь пока пусто' : 'Место для твоих находок'} text={view === 'favorites' ? 'Нажми на звёздочку у записи, чтобы добавить её сюда.' : 'Отправь сообщение боту или создай первую запись здесь.'} action={view !== 'favorites' ? <button className="button" onClick={() => setEditor(view === 'vault' ? 'secret' : 'new')}><Plus size={17} />Добавить запись</button> : undefined} />}
        {entries.length < total && <button className="button load-more" disabled={loading} onClick={loadMore}>{loading ? 'Загружаем…' : 'Показать ещё'}</button>}
        {view !== 'vault' && <div className="capture-tip"><div className="tip-icon"><Inbox size={20} /></div><div><b>Просто отправь боту</b><p>Сообщения, ссылки и файлы попадут во входящие. Разложить по полочкам можно потом.</p></div><span className="tip-arrow"><ArrowUpRight size={20} /></span></div>}
        </>}
      <footer className="page-footer"><span><span className="private-dot" /> Твоё пространство. Твой порядок.</span><span>CHEST / {new Date().getFullYear()}</span></footer>
      </main></div>
    <nav className="mobile-nav" aria-label="Быстрая навигация">{views.filter(v => ['all', 'favorites', 'vault'].includes(v.id)).map(({ id, label, icon: Icon }) => <button key={id} className={view === id ? 'active' : ''} onClick={() => navigate(id)}><Icon size={20} /><span>{label}</span></button>)}<button onClick={() => setEditor('new')}><Plus size={22} /><span>Добавить</span></button></nav>
    {intake && <Modal title="Вкинуть информацию" close={() => setIntake(false)} wide><Intake folderId={folderId} done={() => { setIntake(false); reload() }} toast={toast} /></Modal>}
    {editor && <Editor value={editor} defaultFolder={folderId} folders={folders} vaultKey={vaultKey} trustedReady={me.integrations.trusted_llm} close={closeEditor} done={() => { closeEditor(); setResult(null); setQuery(''); reload() }} toast={toast} />}
    {folderModal && <FolderEditor value={folderModal} parent={folderId} folders={folders} close={closeFolder} done={() => { closeFolder(); navigate('all'); reload() }} toast={toast} />}
    {unlock && <Unlock me={me} close={closeUnlock} done={(key, next) => { setVaultKey(key); if (next) setMe(next); closeUnlock(); navigate('vault') }} toast={toast} />}
    {hintEditor && vaultKey && <VaultHintEditor me={me} close={() => setHintEditor(false)} done={next => { setMe(next); setHintEditor(false) }} toast={toast} />}
    {settings && <SettingsModal me={me} close={closeSettings} toast={toast} updateMe={setMe} />}
    {notice && <div className="toast" role="status"><Check size={17} />{notice}<button className="icon-button" aria-label="Скрыть уведомление" onClick={() => setNotice('')}><X size={15} /></button></div>}
  </div>
}

function FolderBranch({ folder, folders, selected, choose, depth = 0 }: { folder: Folder; folders: Folder[]; selected: string | null; choose: (id: string) => void; depth?: number }) {
  const [expanded, setExpanded] = useState(false)
  const children = folders.filter(f => f.parent_id === folder.id)
  return <><div className={`folder-row ${selected === folder.id ? 'active' : ''}`} style={{ paddingLeft: 8 + depth * 13 }}>
    <button className="folder-expand" aria-label={expanded ? 'Свернуть раздел' : 'Развернуть раздел'} onClick={() => setExpanded(!expanded)} disabled={!children.length}>{children.length ? expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} /> : <span className="folder-dot" style={{ background: folder.color }} />}</button>
    <button className="folder-name" onClick={() => choose(folder.id)}><FolderIcon size={16} style={{ color: folder.color }} /><span>{folder.name}</span></button></div>
    {expanded && depth < 20 && children.map(f => <FolderBranch key={f.id} folder={f} folders={folders} selected={selected} choose={choose} depth={depth + 1} />)}</>
}
function EntryCard({ entry: e, folder, open, favorite }: { entry: Entry; folder?: Folder; open: () => void; favorite: () => void }) {
  const Icon = kindIcon(e)
  return <article className={`entry-card kind-${e.kind}`}><button className="card-open" onClick={open} aria-label={`Открыть ${e.title}`}>
    <div className="card-top"><span className={`entry-icon ${e.attachments.length ? 'file-icon' : ''}`}><Icon size={19} /></span><span className="card-type">{e.kind === 'secret' ? 'СЕКРЕТ' : e.attachments.length ? 'ФАЙЛ' : e.kind === 'link' ? 'ССЫЛКА' : 'ЗАМЕТКА'}</span></div><h3>{e.title}</h3>
    {e.attachments.length ? <div className="attachment-preview"><File size={23} /><span>{e.attachments[0].filename}<small>{bytes(e.attachments[0].size)} · {e.attachments[0].status === 'ready' ? 'Сохранён' : e.attachments[0].status === 'failed' ? 'Ошибка загрузки' : 'В очереди'}</small></span></div> : <p className="entry-preview">{e.preview || 'Без описания'}</p>}
    {!!e.tags.length && <div className="tags">{e.tags.slice(0, 3).map(t => <span key={t}>#{t}</span>)}</div>}
    <div className="card-bottom"><span>{folder ? <><span className="folder-dot" style={{ background: folder.color }} />{folder.name}</> : <><Inbox size={12} />Входящие</>}</span><time>{date(e.created_at)}</time></div></button>
    <button className={`favorite-button ${e.favorite ? 'is-favorite' : ''}`} onClick={favorite} aria-label={e.favorite ? 'Убрать из избранного' : 'В избранное'}><Star size={16} fill={e.favorite ? 'currentColor' : 'none'} /></button></article>
}
function Empty({ title, text, icon, action }: { title: string; text: string; icon: ReactNode; action?: ReactNode }) {
  return <div className="empty-state"><div className="empty-art">{icon}<span className="empty-spark">✦</span></div><h2>{title}</h2><p>{text}</p>{action}</div>
}
function entryPayload(e: Entry) { return { title: e.title, body: e.body || '', kind: e.kind, folder_id: e.folder_id, tags: e.tags, favorite: e.favorite, secret: e.secret || null, ai_policy: e.ai_policy } }

function Editor({ value, defaultFolder, folders, vaultKey, trustedReady, close, done, toast }: { value: Entry | 'new' | 'secret'; defaultFolder: string | null; folders: Folder[]; vaultKey: CryptoKey | null; trustedReady: boolean; close: () => void; done: () => void; toast: (s: string) => void }) {
  const existing = typeof value === 'object' ? value : null
  const isSecret = value === 'secret' || existing?.kind === 'secret'
  const [title, setTitle] = useState(existing?.title || ''), [body, setBody] = useState(existing?.body || '')
  const [folder, setFolder] = useState(existing?.folder_id || defaultFolder || ''), [tags, setTags] = useState(existing?.tags.join(', ') || '')
  const [kind, setKind] = useState(existing?.kind || 'note'), [files, setFiles] = useState<File[]>([]), [busy, setBusy] = useState(false)
  const [credentials, setCredentials] = useState({ login: '', password: '', url: '', notes: '' }), [showPassword, setShowPassword] = useState(false)
  const [ready, setReady] = useState(!isSecret || !existing), [deleting, setDeleting] = useState(false)
  const [policy, setPolicy] = useState<AIPolicy>(existing?.ai_policy || 'external'), [selection, setSelection] = useState<[number, number]>([0, Array.from(existing?.body || '').length])
  // Retain the created entry after an upload failure so retry never duplicates the note.
  const savedEntry = useRef<Entry | null>(existing), uploaded = useRef(new Set<File>())
  useEffect(() => {
    let live = true
    if (isSecret && existing?.secret && vaultKey) decrypt(vaultKey, existing.secret).then(text => { if (live) { setCredentials(JSON.parse(text)); setReady(true) } }).catch(() => toast('Не удалось расшифровать запись'))
    return () => { live = false }
  }, [isSecret, existing, vaultKey, toast])
  async function save(e: React.FormEvent) {
    e.preventDefault(); if (busy || !ready) return; setBusy(true)
    try {
      if (isSecret && !vaultKey) throw new Error('Сначала открой сейф')
      const payload = { title, body: isSecret ? '' : body, kind: isSecret ? 'secret' : kind, folder_id: folder || null,
        tags: isSecret ? [] : tags.split(',').map(t => t.trim()).filter(Boolean), favorite: existing?.favorite || false,
        secret: isSecret ? await encrypt(vaultKey!, JSON.stringify(credentials)) : null, ai_policy: isSecret ? 'local' : policy }
      const previous = savedEntry.current
      const saved = await api<Entry>(previous ? `/entries/${previous.id}` : '/entries', { method: previous ? 'PUT' : 'POST', body: JSON.stringify({ ...payload, ...(previous ? { revision: previous.revision } : {}) }) })
      savedEntry.current = saved
      for (const file of files) if (!uploaded.current.has(file)) { const data = new FormData(); data.set('file', file); await api(`/entries/${saved.id}/files`, { method: 'POST', body: data }); uploaded.current.add(file) }
      toast('Запись сохранена'); done()
    } catch (e) { toast((e as Error).message) } finally { setBusy(false) }
  }
  async function remove() {
    const target = savedEntry.current
    if (!target) return; setBusy(true)
    try { await api(`/entries/${target.id}`, { method: 'DELETE' }); toast('Запись удалена'); done() }
    catch (e) { toast((e as Error).message); setBusy(false) }
  }
  return <Modal title={isSecret ? existing ? 'Запись в сейфе' : 'Новый пароль' : existing ? 'Твоя запись' : 'Новая запись'} close={busy ? () => {} : close} wide>
    <form onSubmit={save} className="editor-form"><label>Название<input required value={title} onChange={e => setTitle(e.target.value)} maxLength={200} placeholder={isSecret ? 'Например, почта' : 'О чём эта запись?'} /></label>
      {!isSecret && <PolicyPicker value={policy} set={setPolicy} disabled={busy} />}
      {isSecret ? <><label>Раздел<FolderSelect folders={folders} value={folder} set={setFolder} /></label><p className="form-hint"><ShieldCheck size={14} />Данные ниже шифруются. Название записи видно серверу.</p>{!ready ? <div className="loading"><LoaderCircle className="spin" />Расшифровываем…</div> : <>
        <label>Логин<input autoComplete="off" value={credentials.login} onChange={e => setCredentials({ ...credentials, login: e.target.value })} /></label>
        <label>Пароль<div className="password-input"><input aria-label="Пароль" autoComplete="new-password" type={showPassword ? 'text' : 'password'} value={credentials.password} onChange={e => setCredentials({ ...credentials, password: e.target.value })} /><button type="button" className="text-button" onClick={() => setShowPassword(!showPassword)}>{showPassword ? 'Скрыть' : 'Показать'}</button><button type="button" className="icon-button" aria-label="Скопировать пароль" onClick={() => navigator.clipboard.writeText(credentials.password).then(() => toast('Пароль скопирован')).catch(() => toast('Буфер обмена недоступен'))}><Copy size={16} /></button></div></label>
        <label>Адрес сайта<input value={credentials.url} onChange={e => setCredentials({ ...credentials, url: e.target.value })} /></label><label>Примечание<textarea rows={3} value={credentials.notes} onChange={e => setCredentials({ ...credentials, notes: e.target.value })} /></label><TrustedVaultAssistant fields={credentials} apply={setCredentials} ready={trustedReady} toast={toast} /></>}
      </> : <><div className="form-row"><label>Тип<select value={kind} disabled={!!existing} onChange={e => setKind(e.target.value as 'note' | 'link')}><option value="note">Заметка</option><option value="link">Ссылка</option></select></label><label>Раздел<FolderSelect folders={folders} value={folder} set={setFolder} /></label></div>
        <label>{kind === 'link' ? 'Ссылка и комментарий' : 'Содержимое'}<textarea rows={8} maxLength={100000} value={body} onChange={e => setBody(e.target.value)} onSelect={e => { const t = e.currentTarget; if (t.selectionStart !== t.selectionEnd) setSelection([Array.from(t.value.slice(0, t.selectionStart)).length, Array.from(t.value.slice(0, t.selectionEnd)).length]) }} placeholder="Мысль, инструкция, полезная ссылка. Сохрани как есть." /></label>
        {existing?.kind === 'link' && (() => { const url = body.match(/https?:\/\/[^\s<>]+/)?.[0]; return url ? <a className="text-button external-link" href={url} target="_blank" rel="noreferrer">Открыть ссылку<ArrowUpRight size={15} /></a> : null })()}
        <label>Теги <span className="optional">через запятую</span><input value={tags} onChange={e => setTags(e.target.value)} placeholder="идеи, работа, почитать" /></label>
        {existing?.attachments.map(a => <div className="download-row" key={a.id}><File size={19} /><span>{a.filename}<small>{bytes(a.size)} · {a.status === 'ready' ? 'Сохранён' : a.status === 'failed' ? 'Ошибка загрузки' : 'Обрабатывается'}</small></span><button type="button" className="icon-button" aria-label={`Скачать ${a.filename}`} onClick={() => download(a.id, a.filename).catch(e => toast(e.message))}><ArrowDownToLine size={19} /></button></div>)}
        <label className="file-picker"><Paperclip size={18} /><span>Прикрепить файлы</span><input type="file" multiple onChange={e => setFiles([...files, ...Array.from(e.target.files || [])])} /></label>
        {files.map((f, i) => <div className="queued-file" key={i}><span>{f.name} <small>{bytes(f.size)}</small></span><button type="button" className="icon-button" aria-label={`Убрать ${f.name}`} onClick={() => setFiles(files.filter((_, j) => i !== j))}><X size={14} /></button></div>)}
        {existing && (body === existing.body && policy === existing.ai_policy && title === existing.title && tags === existing.tags.join(', ') && !files.length ? <NoteAssistant entry={existing} selection={selection} done={done} toast={toast} /> : <p className="form-hint">Сохрани ручные изменения, чтобы редактировать заметку через ИИ.</p>)}
      </>}
      <div className="modal-footer">{existing && <button type="button" className="icon-button danger" aria-label="Удалить запись" onClick={() => setDeleting(true)} disabled={busy}><Trash2 size={18} /></button>}<span className="footer-spacer" /><button type="button" className="button subtle" disabled={busy} onClick={close}>Отмена</button><button className="button primary" disabled={busy || !ready}>{busy ? <LoaderCircle size={17} className="spin" /> : <Check size={17} />}Сохранить</button></div>
      {deleting && <div className="inline-confirm"><p>Удалить запись и все её файлы? Отменить это действие нельзя.</p><button type="button" className="button subtle" onClick={() => setDeleting(false)}>Оставить</button><button type="button" className="button danger" disabled={busy} onClick={remove}>Удалить</button></div>}
    </form></Modal>
}

function FolderSelect({ folders, value, set, exclude }: { folders: Folder[]; value: string; set: (s: string) => void; exclude?: string }) {
  function path(f: Folder, depth = 0): string { const parent = folders.find(p => p.id === f.parent_id); return parent && depth < 20 ? `${path(parent, depth + 1)} / ${f.name}` : f.name }
  return <select value={value} onChange={e => set(e.target.value)}><option value="">Входящие</option>{folders.filter(f => f.id !== exclude).map(f => <option key={f.id} value={f.id}>{path(f)}</option>)}</select>
}
function FolderEditor({ value, parent, folders, close, done, toast }: { value: Folder | 'new'; parent: string | null; folders: Folder[]; close: () => void; done: () => void; toast: (s: string) => void }) {
  const current = typeof value === 'object' ? value : null
  const [name, setName] = useState(current?.name || ''), [parentId, setParentId] = useState(current ? current.parent_id || '' : parent || ''), [color, setColor] = useState(current?.color || '#bba6f8'), [busy, setBusy] = useState(false), [confirm, setConfirm] = useState(false)
  async function save(e: React.FormEvent) { e.preventDefault(); setBusy(true); try { await api(current ? `/folders/${current.id}` : '/folders', { method: current ? 'PUT' : 'POST', body: JSON.stringify({ name, parent_id: parentId || null, color }) }); toast('Раздел сохранён'); done() } catch (e) { toast((e as Error).message) } finally { setBusy(false) } }
  async function remove() { if (!current) return; setBusy(true); try { await api(`/folders/${current.id}`, { method: 'DELETE' }); toast('Раздел удалён. Записи перемещены на уровень выше.'); done() } catch (e) { toast((e as Error).message) } finally { setBusy(false) } }
  return <Modal title={current ? 'Настроить раздел' : 'Новый раздел'} close={close}><form onSubmit={save} className="editor-form"><label>Название<input autoFocus required value={name} maxLength={100} onChange={e => setName(e.target.value)} placeholder="Например, работа" /></label><label>Родительский раздел<FolderSelect folders={folders} value={parentId} set={setParentId} exclude={current?.id} /></label><label>Цвет<div className="color-options">{['#bba6f8', '#91bca0', '#e7bd81', '#d8929b', '#91b5d5', '#b3b2ad'].map(c => <button type="button" key={c} aria-label={`Цвет ${c}`} style={{ background: c }} className={color === c ? 'chosen' : ''} onClick={() => setColor(c)}>{color === c && <Check size={16} />}</button>)}</div></label><div className="modal-footer">{current && <button type="button" className="icon-button danger" aria-label="Удалить раздел" onClick={() => setConfirm(true)}><Trash2 size={18} /></button>}<span className="footer-spacer" /><button className="button primary" disabled={busy}>Сохранить</button></div>{confirm && <div className="inline-confirm"><p>Удалить раздел? Записи и подразделы останутся и переместятся на уровень выше.</p><button type="button" className="button danger" disabled={busy} onClick={remove}>Удалить раздел</button></div>}</form></Modal>
}
function Unlock({ me, close, done, toast }: { me: Me; close: () => void; done: (k: CryptoKey, next?: Me) => void; toast: (s: string) => void }) {
  const [password, setPassword] = useState(''), [repeat, setRepeat] = useState(''), [busy, setBusy] = useState(false)
  const [hint, setHint] = useState('')
  async function submit(e: React.FormEvent) {
    e.preventDefault(); if (!me.vault && (password.length < 12 || password !== repeat)) { toast('Минимум 12 символов; пароли должны совпадать.'); return }
    if (!me.vault && hint.trim() && hint.trim() === password) { toast('Подсказка не должна совпадать с мастер-паролем.'); return }
    setBusy(true)
    try { const salt = me.vault?.salt || randomSalt(); const key = await deriveKey(password, salt)
      if (me.vault) { if (await decrypt(key, me.vault.verifier) !== VERIFIER) throw new Error('Неверный мастер-пароль'); done(key) }
      else { await api('/vault', { method: 'POST', body: JSON.stringify({ salt, verifier: await encrypt(key, VERIFIER), hint }) }); done(key, await api<Me>('/me')) }
      setPassword(''); setRepeat('')
    } catch { toast(me.vault ? 'Не удалось открыть сейф. Проверь мастер-пароль.' : 'Не удалось настроить сейф. Обнови страницу и попробуй снова.') } finally { setBusy(false) }
  }
  return <Modal title={me.vault ? 'Открыть сейф' : 'Настроить сейф'} close={busy ? () => {} : close}>
    <form className="editor-form" onSubmit={submit}>
      <p className="muted">{me.vault ? 'Введи мастер-пароль. Ключ останется только в памяти этого устройства.' : 'Придумай мастер-пароль от 12 символов. Сохрани его в надёжном месте: восстановить его или расшифровать записи без него нельзя.'}</p>
      {me.vault?.hint && <div className="security-note vault-hint"><span><b>Подсказка к мастер-паролю</b><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{me.vault.hint}</p></span></div>}
      <label>Мастер-пароль<input type="password" autoComplete={me.vault ? 'current-password' : 'new-password'} autoFocus required value={password} onChange={e => setPassword(e.target.value)} /></label>
      {!me.vault && <><label>Ещё раз<input type="password" autoComplete="new-password" required value={repeat} onChange={e => setRepeat(e.target.value)} /></label><HintField value={hint} set={setHint} /></>}
      <div className="security-note"><ShieldCheck size={18} /><span>AES-256-GCM · ключ из мастер-пароля<br />Автоблокировка при сворачивании и через 2 минуты бездействия</span></div>
      <button className="button primary full-width" disabled={busy}>{busy ? <LoaderCircle className="spin" size={17} /> : <LockKeyhole size={17} />}{me.vault ? 'Открыть сейф' : 'Создать сейф'}</button>
    </form></Modal>
}

function HintField({ value, set }: { value: string; set: (value: string) => void }) {
  return <><label>Подсказка к мастер-паролю <span className="optional">необязательно</span><input aria-label="Подсказка к мастер-паролю" maxLength={300} value={value} onChange={e => set(e.target.value)} placeholder="Напоминание, понятное только тебе" /></label><p className="form-hint">Подсказка видна до открытия сейфа и хранится без шифрования. Не указывай сам пароль или его часть.</p></>
}

function VaultHintEditor({ me, close, done, toast }: { me: Me; close: () => void; done: (me: Me) => void; toast: (text: string) => void }) {
  const [hint, setHint] = useState(me.vault?.hint || ''), [busy, setBusy] = useState(false)
  async function save(event: React.FormEvent) {
    event.preventDefault(); setBusy(true)
    try { await api('/vault/hint', { method: 'PUT', body: JSON.stringify({ hint }) }); done(await api<Me>('/me')); toast(hint.trim() ? 'Подсказка сохранена' : 'Подсказка удалена') }
    catch (e) { toast((e as Error).message) } finally { setBusy(false) }
  }
  return <Modal title="Подсказка к паролю" close={busy ? () => {} : close}><form className="editor-form" onSubmit={save}><HintField value={hint} set={setHint} /><p className="form-hint">Оставь поле пустым, чтобы удалить подсказку.</p><button className="button primary" disabled={busy}>{busy ? <LoaderCircle size={17} className="spin" /> : <Check size={17} />}Сохранить подсказку</button></form></Modal>
}
function SettingsModal({ me, close, toast, updateMe }: { me: Me; close: () => void; toast: (s: string) => void; updateMe: (m: Me) => void }) {
  const [jobs, setJobs] = useState<Record<string, number>>({}), [busy, setBusy] = useState(false)
  useEffect(() => { api<Record<string, number>>('/jobs').then(setJobs).catch(e => toast(e.message)) }, [toast])
  async function action(path: string, text: string) { setBusy(true); try { await api(path, { method: 'POST' }); toast(text); setJobs(await api('/jobs')) } catch (e) { toast((e as Error).message) } finally { setBusy(false) } }
  async function exportData() { setBusy(true); try { const data = await api('/export'); const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })); const a = document.createElement('a'); a.href = url; a.download = `chest-${new Date().toISOString().slice(0, 10)}.json`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 10000) } catch (e) { toast((e as Error).message) } finally { setBusy(false) } }
  return <Modal title="Твоё пространство" close={close}><div className="settings-body"><BotPolicySettings me={me} updateMe={updateMe} toast={toast} /><AISettings updateMe={updateMe} toast={toast} /><div className="settings-profile"><span className="avatar">{me.name[0]}</span><div><b>{me.name}</b><p>Telegram ID: {me.id}</p></div></div>{[['Файлы', me.integrations.storage === 'yandex' ? 'Яндекс Диск' : 'Локальное хранилище'], ['Поиск по смыслу', me.integrations.embeddings ? 'Эмбеддер подключён' : 'Только текстовый поиск'], ['Ответы по записям', me.integrations.llm ? 'Включены' : 'Модель не настроена']].map(([k, v]) => <div className="settings-row" key={k}><span>{k}</span><b>{v}</b></div>)}<p className="form-hint">Обычная модель получает только разрешённые записи и их файлы. Чувствительные данные обрабатывает доверенная модель; без неё они сохраняются локально. Сейф передаётся ей только по отдельному разрешению.</p><div className="settings-actions"><button className="button" disabled={busy} onClick={exportData}><ArrowDownToLine size={16} />Экспорт записей</button><button className="button" disabled={busy} onClick={() => action('/reindex', 'Переиндексация запущена')}><Sparkles size={16} />Пересобрать поиск</button>{!!jobs.failed && <button className="button" disabled={busy} onClick={() => action('/jobs/retry', 'Задачи возвращены в очередь')}>Повторить неудачные задачи ({jobs.failed})</button>}</div><p className="form-hint">В очереди: {(jobs.pending || 0) + (jobs.running || 0)} · Ошибок: {jobs.failed || 0}<br />Экспорт содержит заметки и зашифрованный сейф; сами файлы скачиваются отдельно.</p></div></Modal>
}
