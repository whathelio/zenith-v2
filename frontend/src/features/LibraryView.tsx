import { useState, useEffect, useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, type Note, type Memory, type ModuleSkill, type McpServer } from '../shared/api'
import { TransformButton } from '../components/TransformButton'
import { lookupPlaceholder } from '../shared/security'

/** 将 {{SEC_xxx}} 占位符渲染为可点击令牌（点击本地展开明文，不发送、不落盘） */
function renderSecrets(text: string): string {
  if (!text) return ''
  // 先 HTML 转义，再替换占位符为令牌
  const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return escaped.replace(
    /\{\{(SEC_\d+)\}\}/g,
    (_, key) =>
      `<span class="secret-token" data-secret="${key}" title="点击展开原始值（仅本地）">&#128274; <span style="color:var(--color-accent-warning)">${key}</span><span class="secret-val" style="display:none;margin-left:6px;color:var(--color-accent-danger);font-size:11px;word-break:break-all;font-family:monospace;background:var(--color-bg-muted);padding:1px 4px;border-radius:3px;"></span></span>`
  )
}

/** 点击 secret-token 展开/折叠明文（从 localStorage 读取，映射丢失则提示） */
function handleSecretClick(e: React.MouseEvent<HTMLElement>) {
  const token = (e.target as HTMLElement).closest('.secret-token') as HTMLElement
  if (!token) return
  e.stopPropagation()
  const valEl = token.querySelector('.secret-val') as HTMLElement
  if (!valEl) return
  if (valEl.style.display === 'none' || !valEl.style.display) {
    const key = token.getAttribute('data-secret') || ''
    const raw = lookupPlaceholder(key)
    valEl.textContent = raw || '(映射已丢失，仅存在于本地映射表)'
    valEl.style.display = 'inline'
    token.classList.add('revealed')
  } else {
    valEl.style.display = 'none'
    token.classList.remove('revealed')
  }
}

const MEMORY_TYPE_COLORS: Record<string, string> = {
  personal_info: '#8be9fd', preference: '#ff79c6', event: '#50fa7b',
  decision: '#bd93f9', fact: '#f1fa8c', experience: '#ff6e40',
}
const MEMORY_TYPE_NAMES: Record<string, string> = {
  personal_info: '个人信息', preference: '偏好', event: '事件',
  decision: '决定', fact: '事实', experience: '经验',
}
const MEMORY_TYPE_ICONS: Record<string, string> = {
  personal_info: '👤', preference: '💕', event: '📅',
  decision: '⚖️', fact: '💡', experience: '🔥',
}
const SKILL_CONFIRMED_COLORS: Record<number, { bg: string; text: string; label: string }> = {
  0: { bg: '#ff5c5c22', text: '#ff5c5c', label: '待确认' },
  1: { bg: '#1ae86522', text: '#1ae865', label: '已确认' },
}

type Tab = 'notes' | 'memories' | 'skills' | 'mcp' | 'traces'

export default function LibraryView() {
  const [searchParams, setSearchParams] = useSearchParams()
  const initialTab = (searchParams.get('tab') as Tab) || 'notes'
  const [activeTab, setActiveTab] = useState<Tab>(
    ['notes', 'memories', 'skills', 'mcp', 'traces'].includes(initialTab) ? initialTab : 'notes'
  )

  useEffect(() => {
    const tab = searchParams.get('tab') as Tab
    if (tab && ['notes', 'memories', 'skills', 'mcp', 'traces'].includes(tab)) {
      setActiveTab(tab)
    }
  }, [searchParams])

  // Notes state
  const [notes, setNotes] = useState<Note[]>([])
  const [notesLoading, setNotesLoading] = useState(true)
  const [noteSearch, setNoteSearch] = useState('')
  const [noteTagFilter, setNoteTagFilter] = useState('')
  const [noteSourceFilter, setNoteSourceFilter] = useState('')
  const [noteEditingId, setNoteEditingId] = useState<number | null>(null)
  const [noteEditForm, setNoteEditForm] = useState<Partial<Note>>({})
  const [showNoteCreate, setShowNoteCreate] = useState(false)
  const [newNote, setNewNote] = useState({ title: '', content: '', tags: '' })
  const [expandedNoteIds, setExpandedNoteIds] = useState<Set<number>>(new Set())

  // 笔记筛选：搜索 + 标签 + 来源
  const allNoteTags = useMemo(() => {
    const set = new Set<string>()
    notes.forEach(n => n.tags?.split(',').forEach(t => { const s = t.trim(); if (s) set.add(s) }))
    return Array.from(set).sort()
  }, [notes])

  const filteredNotes = useMemo(() => {
    return notes.filter(n => {
      if (noteTagFilter && !n.tags?.split(',').map(t => t.trim()).includes(noteTagFilter)) return false
      if (noteSourceFilter && (n.source || 'manual') !== noteSourceFilter) return false
      return true
    })
  }, [notes, noteTagFilter, noteSourceFilter])

  // Memories state
  const [memories, setMemories] = useState<Memory[]>([])
  const [memoriesLoading, setMemoriesLoading] = useState(true)
  const [memoryFilter, setMemoryFilter] = useState('')
  const [memorySearch, setMemorySearch] = useState('')
  const [expandedMemoryId, setExpandedMemoryId] = useState<number | null>(null)
  // Memory 编辑
  const [memoryEditingId, setMemoryEditingId] = useState<number | null>(null)
  const [memoryEditForm, setMemoryEditForm] = useState<{ type: string; content: string; importance: number; keywords: string }>({ type: 'fact', content: '', importance: 3, keywords: '' })
  const [memoryEditSaving, setMemoryEditSaving] = useState(false)

  // Skills state (from memories)
  const [skills, setSkills] = useState<ModuleSkill[]>([])
  const [skillsLoading, setSkillsLoading] = useState(true)
  const [skillSearch, setSkillSearch] = useState('')
  const [expandedSkillId, setExpandedSkillId] = useState<number | null>(null)

  // MCP state
  const [mcpServers, setMcpServers] = useState<McpServer[]>([])
  const [mcpLoading, setMcpLoading] = useState(true)
  const [showMcpAdd, setShowMcpAdd] = useState(false)
  const [newMcp, setNewMcp] = useState({ name: '', url: '', enabled: true })

  // Skill 导入
  const [showSkillImport, setShowSkillImport] = useState(false)
  const [skillImportPath, setSkillImportPath] = useState('')
  const [skillImportLoading, setSkillImportLoading] = useState(false)
  // MCP 文件导入
  const [showMcpFileImport, setShowMcpFileImport] = useState(false)
  const [mcpFileImportPath, setMcpFileImportPath] = useState('')
  const [mcpFileImportName, setMcpFileImportName] = useState('')
  const [mcpFileImportArgs, setMcpFileImportArgs] = useState('')
  const [mcpFileImportLoading, setMcpFileImportLoading] = useState(false)

  const [toast, setToast] = useState<string | null>(null)

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(null), 2500) }

  // Load notes
  const loadNotes = useCallback(async () => {
    setNotesLoading(true)
    try { const list = await api.listNotes(noteSearch); setNotes(list) } catch {} finally { setNotesLoading(false) }
  }, [noteSearch])

  useEffect(() => { loadNotes() }, [loadNotes])

  const handleCreateNote = async () => {
    if (!newNote.title.trim()) return
    try {
      const created = await api.createNote(newNote)
      setNotes(prev => [created, ...prev])
      setNewNote({ title: '', content: '', tags: '' })
      setShowNoteCreate(false)
    } catch {}
  }

  const handleDeleteNote = async (id: number) => {
    if (!confirm('删除此笔记？')) return
    try { await api.deleteNote(id); setNotes(prev => prev.filter(n => n.id !== id)) } catch {}
  }

  const startNoteEdit = (n: Note) => {
    setNoteEditingId(n.id)
    setNoteEditForm({ title: n.title, content: n.content, tags: n.tags })
  }

  const saveNoteEdit = async () => {
    if (noteEditingId === null) return
    try {
      await api.updateNote(noteEditingId, noteEditForm)
      setNotes(prev => prev.map(n => n.id === noteEditingId ? { ...n, ...noteEditForm } : n))
      setNoteEditingId(null); setNoteEditForm({})
    } catch {}
  }

  const toggleNoteExpand = (id: number) => {
    setExpandedNoteIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  // Load memories
  const loadMemories = useCallback(async () => {
    setMemoriesLoading(true)
    try { const ms = await api.listMemories(memoryFilter, memorySearch); setMemories(ms) } catch {} finally { setMemoriesLoading(false) }
  }, [memoryFilter, memorySearch])

  useEffect(() => { loadMemories() }, [loadMemories])

  const handleDeleteMemory = async (id: number) => {
    if (!confirm('删除此记忆？')) return
    try { await api.deleteMemory(id); setMemories(prev => prev.filter(m => m.id !== id)) } catch {}
  }

  const startMemoryEdit = (m: Memory) => {
    setMemoryEditingId(m.id)
    setMemoryEditForm({ type: m.type || 'fact', content: m.content || '', importance: m.importance || 3, keywords: m.keywords || '' })
  }

  const saveMemoryEdit = async () => {
    if (memoryEditingId === null) return
    if (!memoryEditForm.content.trim()) { showToast('内容不能为空'); return }
    setMemoryEditSaving(true)
    try {
      const r = await api.updateMemory(memoryEditingId, memoryEditForm)
      setMemories(prev => prev.map(m => m.id === memoryEditingId ? (r.memory || { ...m, ...memoryEditForm }) : m))
      setMemoryEditingId(null)
      showToast('记忆已更新 ✓')
    } catch (e: any) { showToast(`更新失败: ${e?.message || e}`) } finally { setMemoryEditSaving(false) }
  }

  const formatMemoryDate = (dateStr: string) => {
    const d = new Date(dateStr)
    return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  }

  // Load skills
  const loadSkills = useCallback(async () => {
    setSkillsLoading(true)
    try { setSkills(await api.listSkills(skillSearch)) } catch {} finally { setSkillsLoading(false) }
  }, [skillSearch])

  useEffect(() => { loadSkills() }, [loadSkills])

  const handleDeleteSkill = async (id: number) => { try { await api.deleteSkill(id); setExpandedSkillId(null); loadSkills() } catch {} }

  // MCP
  const loadMcpServers = useCallback(async () => {
    setMcpLoading(true)
    try { const result = await api.listMcpServers(); setMcpServers(result.servers) } catch {} finally { setMcpLoading(false) }
  }, [])
  useEffect(() => { if (activeTab === 'mcp') loadMcpServers() }, [activeTab])

  const handleAddMcp = async () => {
    if (!newMcp.name.trim() || !newMcp.url.trim()) return
    try { await api.addMcpServer(newMcp); setNewMcp({ name: '', url: '', enabled: true }); setShowMcpAdd(false); loadMcpServers(); showToast('MCP 已添加 ✓') } catch {}
  }
  const handleDeleteMcp = async (name: string) => {
    if (!confirm(`删除 MCP 服务器 "${name}"？`)) return
    try { await api.deleteMcpServer(name); loadMcpServers(); showToast('已删除') } catch {}
  }

  const handleToggleMcp = async (name: string, enabled: boolean) => {
    try {
      await api.updateMcpServer(name, { enabled: !enabled })
      loadMcpServers()
      showToast(enabled ? '已禁用' : '已启用')
    } catch {}
  }

  // ===== 执行痕迹（工具调用历史查询）=====
  const [traces, setTraces] = useState<any[]>([])
  const [tracesLoading, setTracesLoading] = useState(false)
  const [traceSearch, setTraceSearch] = useState('')
  const [traceType, setTraceType] = useState('')
  const [expandedTraceId, setExpandedTraceId] = useState<number | null>(null)

  const loadTraces = useCallback(async () => {
    setTracesLoading(true)
    try {
      const list = await api.getTraceHistory({
        keyword: traceSearch.trim() || undefined,
        trace_type: traceType || undefined,
        limit: 100,
      })
      setTraces(list)
    } catch {} finally { setTracesLoading(false) }
  }, [traceSearch, traceType])

  useEffect(() => { if (activeTab === 'traces') loadTraces() }, [activeTab, loadTraces])

  const parseTraceData = (t: any): { name: string; args: any; result: string; duration?: number } => {
    let d: any = {}
    try { d = typeof t.data === 'string' ? JSON.parse(t.data) : (t.data || {}) } catch { d = {} }
    return {
      name: d.name || t.trace_type || 'unknown',
      args: d.args || {},
      result: d.result_summary || d.result || d.error || '',
      duration: d.duration_ms,
    }
  }

  // Skill 文件导入
  const handleImportSkillFile = async () => {
    const path = skillImportPath.trim()
    if (!path) { showToast('请输入文件路径'); return }
    setSkillImportLoading(true)
    try {
      const result = await api.importSkillFile(path)
      showToast(`已导入技能: ${result.name} ✓`)
      setSkillImportPath('')
      setShowSkillImport(false)
      loadSkills()
    } catch (e: any) {
      showToast(`导入失败: ${e?.message || e}`)
    } finally { setSkillImportLoading(false) }
  }

  // Skill 目录批量导入
  const handleImportSkillsDir = async () => {
    setSkillImportLoading(true)
    try {
      const result = await api.importSkillsFromDir()
      showToast(`扫描 ${result.scanned} 个, 导入 ${result.imported} 个, 错误 ${result.errors} 个 ✓`)
      setShowSkillImport(false)
      loadSkills()
    } catch (e: any) {
      showToast(`导入失败: ${e?.message || e}`)
    } finally { setSkillImportLoading(false) }
  }

  // MCP 文件导入
  const handleImportMcpFile = async () => {
    const path = mcpFileImportPath.trim()
    if (!path) { showToast('请输入脚本路径'); return }
    setMcpFileImportLoading(true)
    try {
      const opts: any = {}
      if (mcpFileImportName.trim()) opts.name = mcpFileImportName.trim()
      if (mcpFileImportArgs.trim()) opts.args = mcpFileImportArgs.split(/\s+/).filter(Boolean)
      const result = await api.importMcpFile(path, opts)
      showToast(`已注册 MCP: ${result.server.name} (${result.detection.type}) ${result.replaced ? '已更新' : ''} ✓`)
      setMcpFileImportPath(''); setMcpFileImportName(''); setMcpFileImportArgs('')
      setShowMcpFileImport(false)
      loadMcpServers()
    } catch (e: any) {
      showToast(`注册失败: ${e?.message || e}`)
    } finally { setMcpFileImportLoading(false) }
  }

  const tabs: { key: Tab; label: string; icon: string }[] = [
    { key: 'notes', label: '笔记', icon: '📝' },
    { key: 'memories', label: '记忆', icon: '🧠' },
    { key: 'skills', label: '技能', icon: '⚡' },
    { key: 'mcp', label: 'MCP', icon: '🔌' },
    { key: 'traces', label: '执行痕迹', icon: '🔧' },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflowY: 'auto' }}>
      {/* Toast */}
      {toast && (
        <div style={{
          position: 'fixed', top: 16, left: '50%', transform: 'translateX(-50%)',
          padding: '8px 20px', borderRadius: 8, background: 'var(--color-bg-panel)',
          border: '1px solid var(--color-accent-primary)', color: 'var(--color-accent-primary)',
          fontSize: 13, fontWeight: 600, zIndex: 999,
        }}>{toast}</div>
      )}

      {/* Header */}
      <div style={{ padding: '12px 16px 0', borderBottom: '1px solid var(--color-border)' }}>
        <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 10 }}>
          📚 知识库
        </div>
        <div style={{ display: 'flex', gap: 4 }}>
          {tabs.map(t => (
            <button
              key={t.key}
              onClick={() => { setActiveTab(t.key); setSearchParams({ tab: t.key }) }}
              style={{
                padding: '8px 16px', fontSize: 13, fontWeight: 600,
                background: activeTab === t.key ? 'var(--color-accent-primary)' : 'transparent',
                color: activeTab === t.key ? '#fff' : 'var(--color-text-muted)',
                border: 'none', borderRadius: '6px 6px 0 0', cursor: 'pointer',
                borderBottom: activeTab === t.key ? '2px solid var(--color-accent-primary)' : '2px solid transparent',
              }}
            >
              {t.icon} {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div style={{ flex: 1, padding: '12px 16px', overflowY: 'auto' }}>
        {/* ===== Notes Tab ===== */}
        {activeTab === 'notes' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {/* 搜索 + 标签筛选 + 来源筛选 */}
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <input className="form-input" style={{ flex: 1, minWidth: 180, fontSize: 12 }} placeholder="搜索笔记标题/内容..." value={noteSearch} onChange={e => setNoteSearch(e.target.value)} />
              <select
                className="form-select"
                style={{ fontSize: 12, width: 'auto' }}
                value={noteTagFilter}
                onChange={e => setNoteTagFilter(e.target.value)}
              >
                <option value="">所有标签</option>
                {allNoteTags.map(tag => <option key={tag} value={tag}>#{tag}</option>)}
              </select>
              <select
                className="form-select"
                style={{ fontSize: 12, width: 'auto' }}
                value={noteSourceFilter}
                onChange={e => setNoteSourceFilter(e.target.value)}
              >
                <option value="">所有来源</option>
                <option value="manual">✍ 手动</option>
                <option value="chat">💬 对话</option>
                <option value="distill">🔥 蒸馏</option>
                <option value="analysis">📋 分析</option>
              </select>
              <button className="btn btn-sm" style={{ background: 'var(--color-accent-primary)', color: '#fff' }} onClick={() => setShowNoteCreate(!showNoteCreate)}>
                {showNoteCreate ? '取消' : '+ 新建'}
              </button>
            </div>
            {showNoteCreate && (
              <div style={{ padding: 14, background: 'var(--color-bg-panel)', border: '1px solid var(--color-accent-primary)', borderRadius: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
                <input className="form-input" style={{ fontSize: 12 }} placeholder="标题" value={newNote.title} onChange={e => setNewNote({ ...newNote, title: e.target.value })} />
                <textarea className="form-input" style={{ fontSize: 12 }} placeholder="内容..." rows={3} value={newNote.content} onChange={e => setNewNote({ ...newNote, content: e.target.value })} />
                <input className="form-input" style={{ fontSize: 12 }} placeholder="标签（逗号分隔）" value={newNote.tags} onChange={e => setNewNote({ ...newNote, tags: e.target.value })} />
                <button className="btn btn-sm" style={{ background: 'var(--color-accent-primary)', color: '#fff', alignSelf: 'flex-start' }} onClick={handleCreateNote}>保存</button>
              </div>
            )}
            <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
              共 {filteredNotes.length} 条{filteredNotes.length < notes.length && ` (筛选自 ${notes.length})`}
            </span>
            {notesLoading ? (
              <div className="spinner"><div className="spinner-dot" /><div className="spinner-dot" /><div className="spinner-dot" /></div>
            ) : filteredNotes.length === 0 ? (
              <div className="empty-state"><p style={{ fontSize: 13 }}>暂无笔记</p></div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {filteredNotes.map(n => (
                  <div key={n.id} style={{ padding: '10px 14px', background: 'var(--color-bg-panel)', border: '1px solid var(--color-border)', borderRadius: 6 }}>
                    {noteEditingId === n.id ? (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                        <input className="form-input" style={{ fontSize: 12 }} value={noteEditForm.title || ''} onChange={e => setNoteEditForm(f => ({ ...f, title: e.target.value }))} placeholder="标题" />
                        <textarea className="form-input" style={{ fontSize: 12 }} value={noteEditForm.content || ''} onChange={e => setNoteEditForm(f => ({ ...f, content: e.target.value }))} placeholder="内容" rows={3} />
                        <input className="form-input" style={{ fontSize: 12 }} value={noteEditForm.tags || ''} onChange={e => setNoteEditForm(f => ({ ...f, tags: e.target.value }))} placeholder="标签" />
                        <div style={{ display: 'flex', gap: 6 }}>
                          <button className="btn btn-sm" style={{ background: 'var(--color-accent-primary)', color: '#fff' }} onClick={saveNoteEdit}>保存</button>
                          <button className="btn btn-sm" onClick={() => { setNoteEditingId(null); setNoteEditForm({}) }}>取消</button>
                        </div>
                      </div>
                    ) : (
                      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                        <div style={{ flex: 1 }}>
                          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-primary)' }}>{n.title}</div>
                          {n.content && (
                            <>
                              <div
                                style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginTop: 3, lineHeight: 1.5, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
                                onClick={handleSecretClick}
                                dangerouslySetInnerHTML={{
                                  __html: n.content.length > 150 && !expandedNoteIds.has(n.id)
                                    ? renderSecrets(n.content.slice(0, 150) + '...')
                                    : renderSecrets(n.content),
                                }}
                              />
                              {n.content.length > 150 && (
                                <button className="btn btn-sm" style={{ marginTop: 3, fontSize: 10, padding: '1px 6px', color: 'var(--color-accent-primary)', background: 'transparent', border: 'none', cursor: 'pointer' }} onClick={() => toggleNoteExpand(n.id)}>
                                  {expandedNoteIds.has(n.id) ? '收起 ▲' : '展开 ▼'}
                                </button>
                              )}
                            </>
                          )}
                          <div style={{ marginTop: 4, display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                            {n.tags && n.tags.split(',').map((tag, i) => (
                              <span key={i} style={{ background: 'rgba(189,147,249,0.15)', color: '#bd93f9', padding: '1px 6px', borderRadius: 4, fontSize: 10 }}>#{tag.trim()}</span>
                            ))}
                            {n.source && n.source !== 'manual' && (
                              <span style={{
                                fontSize: 10, padding: '1px 6px', borderRadius: 4,
                                background: n.source === 'chat' ? 'rgba(139,233,253,0.12)' : n.source === 'distill' ? 'rgba(255,110,64,0.12)' : 'rgba(80,250,123,0.12)',
                                color: n.source === 'chat' ? '#8be9fd' : n.source === 'distill' ? '#ff6e40' : '#50fa7b',
                              }}>
                                {n.source === 'chat' ? '💬 对话' : n.source === 'distill' ? '🔥 蒸馏' : n.source === 'analysis' ? '📋 分析' : n.source}
                              </span>
                            )}
                          </div>
                        </div>
                        <div style={{ display: 'flex', gap: 3, flexShrink: 0 }}>
                          <button
                            className="btn-icon"
                            style={{ width: 24, height: 24, fontSize: 10, color: '#50fa7b' }}
                            onClick={async () => {
                              try {
                                const r = await api.startLearning('note', n.id)
                                showToast(`已创建学习对话 ✓`)
                                setTimeout(() => { window.location.href = `/chat/${r.conversation_id}` }, 600)
                              } catch (e: any) { showToast(`创建失败: ${e?.message || e}`) }
                            }}
                            title="创建学习对话"
                          >📚</button>
                          <button
                            className="btn-icon"
                            style={{ width: 24, height: 24, fontSize: 10, color: '#ff6e40' }}
                            onClick={async () => {
                              try {
                                await api.transform('note', n.id, 'memory')
                                showToast(`已转为记忆 ✓`)
                                loadNotes()
                              } catch { showToast('转化失败') }
                            }}
                            title="转为记忆"
                          >🧠</button>
                          <TransformButton sourceType="note" sourceId={n.id} onTransformed={() => loadNotes()} />
                          <button className="btn-icon" style={{ width: 24, height: 24, fontSize: 11 }} onClick={() => startNoteEdit(n)} title="编辑">✎</button>
                          <button className="btn-icon" style={{ width: 24, height: 24, fontSize: 11 }} onClick={() => handleDeleteNote(n.id)} title="删除">×</button>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ===== Memories Tab ===== */}
        {activeTab === 'memories' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <select className="form-select" style={{ width: 130, fontSize: 12 }} value={memoryFilter} onChange={e => setMemoryFilter(e.target.value)}>
                <option value="">全部类型</option>
                {Object.entries(MEMORY_TYPE_NAMES).map(([k, v]) => <option key={k} value={k}>{MEMORY_TYPE_ICONS[k]} {v}</option>)}
              </select>
              <input className="form-input" style={{ width: 200, fontSize: 12 }} placeholder="搜索..." value={memorySearch} onChange={e => setMemorySearch(e.target.value)} />
              <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>共 {memories.length} 条</span>
            </div>
            {!memoryFilter && memories.length > 0 && (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {Object.entries(MEMORY_TYPE_NAMES).map(([k, v]) => {
                  const count = memories.filter(m => m.type === k).length
                  if (count === 0) return null
                  return (
                    <button key={k} className="btn btn-sm" style={{ background: MEMORY_TYPE_COLORS[k], color: '#fff', border: 'none', fontSize: 10, padding: '2px 8px' }} onClick={() => setMemoryFilter(k)}>
                      {MEMORY_TYPE_ICONS[k]} {v} ({count})
                    </button>
                  )
                })}
              </div>
            )}
            {memoriesLoading ? (
              <div className="spinner"><div className="spinner-dot" /><div className="spinner-dot" /><div className="spinner-dot" /></div>
            ) : memories.length === 0 ? (
              <div className="empty-state"><p style={{ fontSize: 13 }}>暂无记忆</p></div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                {memories.map(m => {
                  const isExpanded = expandedMemoryId === m.id
                  const isEditing = memoryEditingId === m.id
                  const tc = MEMORY_TYPE_COLORS[m.type] || '#717e95'
                  return (
                    <div key={m.id} style={{
                      display: 'flex', alignItems: 'flex-start', gap: 10,
                      padding: isExpanded || isEditing ? '12px 14px' : '8px 14px',
                      background: 'var(--color-bg-panel)',
                      border: `1px solid ${isEditing ? '#50fa7b' : isExpanded ? tc : 'var(--color-border)'}`,
                      borderRadius: 6, cursor: isEditing ? 'default' : 'pointer', transition: 'all 0.2s',
                    }} onClick={() => { if (!isEditing) setExpandedMemoryId(isExpanded ? null : m.id) }}>
                      <span style={{ background: tc, color: '#fff', padding: '2px 6px', borderRadius: 4, fontSize: 10, fontWeight: 600, flexShrink: 0, whiteSpace: 'nowrap' }}>
                        {MEMORY_TYPE_ICONS[m.type] || ''} {MEMORY_TYPE_NAMES[m.type] || m.type}
                      </span>
                      <div style={{ flex: 1 }}>
                        {isEditing ? (
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }} onClick={e => e.stopPropagation()}>
                            <div style={{ display: 'flex', gap: 6 }}>
                              <select className="form-select" style={{ width: 130, fontSize: 12 }} value={memoryEditForm.type} onChange={e => setMemoryEditForm(f => ({ ...f, type: e.target.value }))}>
                                {Object.entries(MEMORY_TYPE_NAMES).map(([k, v]) => <option key={k} value={k}>{MEMORY_TYPE_ICONS[k]} {v}</option>)}
                              </select>
                              <select className="form-select" style={{ width: 110, fontSize: 12 }} value={memoryEditForm.importance} onChange={e => setMemoryEditForm(f => ({ ...f, importance: Number(e.target.value) }))}>
                                {[5, 4, 3, 2, 1].map(i => <option key={i} value={i}>{'⭐'.repeat(i)}{i === 3 ? ' (默认)' : ''}</option>)}
                              </select>
                            </div>
                            <textarea className="form-input" rows={3} style={{ fontSize: 12, width: '100%' }} placeholder="记忆内容..." value={memoryEditForm.content} onChange={e => setMemoryEditForm(f => ({ ...f, content: e.target.value }))} />
                            <input className="form-input" style={{ fontSize: 12, width: '100%' }} placeholder="关键词（逗号分隔，可选）" value={memoryEditForm.keywords} onChange={e => setMemoryEditForm(f => ({ ...f, keywords: e.target.value }))} />
                            <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                              <button className="btn btn-sm" disabled={memoryEditSaving} onClick={() => setMemoryEditingId(null)}>取消</button>
                              <button className="btn btn-sm" style={{ background: '#50fa7b', color: '#222', border: 'none' }} disabled={memoryEditSaving} onClick={saveMemoryEdit}>{memoryEditSaving ? '保存中...' : '保存'}</button>
                            </div>
                          </div>
                        ) : (
                          <>
                            <div style={{ fontSize: 12, lineHeight: 1.5, maxHeight: isExpanded ? 'none' : '3em', overflow: isExpanded ? 'visible' : 'hidden', textOverflow: 'ellipsis', color: 'var(--color-text-secondary)' }}>
                              {m.content}
                            </div>
                            {isExpanded && (
                              <div style={{ marginTop: 8 }}>
                                {m.keywords && <div style={{ fontSize: 10, color: 'var(--color-text-muted)', marginBottom: 4 }}>关键词: {m.keywords}</div>}
                                {m.importance && <div style={{ fontSize: 10, color: 'var(--color-text-muted)', marginBottom: 4 }}>重要性: {'⭐'.repeat(Math.min(m.importance, 5))}</div>}
                                <div style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>创建: {formatMemoryDate(m.created_at)}</div>
                              </div>
                            )}
                          </>
                        )}
                      </div>
                      <div style={{ display: 'flex', gap: 3, flexShrink: 0, alignItems: 'center' }} onClick={e => e.stopPropagation()}>
                        {!isEditing && (
                          <button className="btn-icon" style={{ width: 24, height: 24, fontSize: 12 }} onClick={() => startMemoryEdit(m)} title="编辑">✏️</button>
                        )}
                        {!isEditing && (
                          <button
                            className="btn-icon"
                            style={{ width: 24, height: 24, fontSize: 10, color: '#50fa7b' }}
                            onClick={async () => {
                              try {
                                const r = await api.startLearning('memory', m.id)
                                showToast(`已创建学习对话 ✓`)
                                setTimeout(() => { window.location.href = `/chat/${r.conversation_id}` }, 600)
                              } catch (e: any) { showToast(`创建失败: ${e?.message || e}`) }
                            }}
                            title="创建学习对话"
                          >📚</button>
                        )}
                        {!isEditing && <TransformButton sourceType="memory" sourceId={m.id} onTransformed={() => loadMemories()} />}
                        <button className="btn-icon" style={{ width: 24, height: 24, fontSize: 12, opacity: isEditing ? 0.4 : 1 }} disabled={isEditing} onClick={() => handleDeleteMemory(m.id)} title="删除">×</button>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}

        {/* ===== Skills Tab ===== */}
        {activeTab === 'skills' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <input value={skillSearch} onChange={e => setSkillSearch(e.target.value)} placeholder="搜索技能..." style={{ flex: 1, minWidth: 140, fontSize: 12, padding: '6px 10px', background: 'var(--color-bg-input)', border: '1px solid var(--color-border)', borderRadius: 6, color: 'var(--color-text-primary)', outline: 'none' }} />
              <button
                className="btn btn-sm"
                style={{ background: 'var(--color-accent-primary)', color: '#000', whiteSpace: 'nowrap' }}
                onClick={() => { setShowSkillImport(!showSkillImport); setSkillImportPath('') }}
              >
                {showSkillImport ? '取消' : '📥 导入本地 Skill'}
              </button>
            </div>

            {/* 导入弹窗 */}
            {showSkillImport && (
              <div style={{ padding: 14, background: 'var(--color-bg-panel)', border: '1px solid var(--color-accent-primary)', borderRadius: 8, display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-primary)' }}>导入本地 Skill 文件</div>

                {/* 单文件导入 */}
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <input
                    value={skillImportPath}
                    onChange={e => setSkillImportPath(e.target.value)}
                    placeholder="SKILL.md 路径，如 ~/.workbuddy/skills/stock-analyzer/SKILL.md"
                    style={{ flex: 1, fontSize: 12, padding: '6px 10px', background: 'var(--color-bg-input)', border: '1px solid var(--color-border)', borderRadius: 6, color: 'var(--color-text-primary)', outline: 'none' }}
                  />
                  <button
                    className="btn btn-sm"
                    style={{ background: '#50fa7b', color: '#000', whiteSpace: 'nowrap' }}
                    onClick={handleImportSkillFile}
                    disabled={skillImportLoading}
                  >
                    {skillImportLoading ? '导入中...' : '导入'}
                  </button>
                </div>
                <div style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>支持 SKILL.md 文件路径或所在目录路径</div>

                {/* 分隔线 */}
                <div style={{ borderTop: '1px solid var(--color-border)', margin: '4px 0' }} />

                {/* 批量导入 */}
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-secondary)' }}>批量扫描导入（从 WorkBuddy skills 目录）</div>
                <button
                  className="btn btn-sm"
                  style={{ background: '#bd93f9', color: '#000', alignSelf: 'flex-start' }}
                  onClick={handleImportSkillsDir}
                  disabled={skillImportLoading}
                >
                  {skillImportLoading ? '扫描中...' : '📂 扫描 ~/.workbuddy/skills/ 并导入全部'}
                </button>
              </div>
            )}

            <div style={{ display: 'flex', gap: 12, fontSize: 11, color: 'var(--color-text-muted)' }}>
              <span>总计 {skills.length}</span>
            </div>
            {skillsLoading ? (
              <div className="spinner"><div className="spinner-dot" /><div className="spinner-dot" /><div className="spinner-dot" /></div>
            ) : skills.length === 0 ? (
              <div className="empty-state"><p style={{ fontSize: 13 }}>暂无技能卡片</p></div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {skills.map(skill => {
                  const isExpanded = expandedSkillId === skill.id
                  return (
                    <div key={skill.id} style={{ background: 'var(--color-bg-panel)', borderRadius: 8, padding: isExpanded ? '10px 14px' : '8px 14px', cursor: 'pointer', border: `1px solid ${isExpanded ? 'var(--color-accent-primary)' : 'var(--color-border)'}` }} onClick={() => setExpandedSkillId(isExpanded ? null : skill.id)}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-primary)', flex: 1 }}>{skill.name}</span>
                        <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 4, background: 'rgba(80,250,123,0.12)', color: '#50fa7b' }}>已确认</span>
                      </div>
                      {skill.tags && skill.tags.length > 0 && (
                        <div style={{ display: 'flex', gap: 4, marginTop: 4, flexWrap: 'wrap' }}>
                          {skill.tags.map((tag, i) => (
                            <span key={i} style={{ fontSize: 10, padding: '1px 6px', borderRadius: 4, background: 'rgba(189,147,249,0.12)', color: 'var(--color-accent-primary)' }}>{tag}</span>
                          ))}
                        </div>
                      )}
                      {isExpanded && (
                        <div style={{ marginTop: 8 }}>
                          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 4 }}>技能描述:</div>
                          <div style={{ fontSize: 12, color: 'var(--color-text-secondary)', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
                            {skill.content || skill.trigger_scene}
                          </div>
                          {skill.mcp_required && skill.mcp_required.length > 0 && (
                            <div style={{ marginTop: 10 }}>
                              <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 4 }}>依赖 MCP:</div>
                              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                                {skill.mcp_required.map(dep => {
                                  const srv = mcpServers.find(m => m.name === dep)
                                  const status = !srv ? 'missing' : (srv.enabled ? 'ok' : 'disabled')
                                  const c = status === 'ok' ? '#50fa7b' : status === 'disabled' ? '#f1fa8c' : '#ff5555'
                                  const label = status === 'ok' ? '已启用' : status === 'disabled' ? '已禁用' : '未安装'
                                  return (
                                    <span key={dep} title={label} style={{ fontSize: 10, padding: '2px 8px', borderRadius: 4, background: 'rgba(255,255,255,0.04)', border: `1px solid ${c}`, color: c, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                                      <span style={{ width: 6, height: 6, borderRadius: '50%', background: c }} />
                                      {dep}
                                    </span>
                                  )
                                })}
                              </div>
                            </div>
                          )}
                          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                            <button onClick={e => { e.stopPropagation(); handleDeleteSkill(skill.id) }} style={{ fontSize: 11, padding: '4px 10px', borderRadius: 5, background: 'rgba(255,85,85,0.12)', color: '#ff5555', border: '1px solid rgba(255,85,85,0.3)', cursor: 'pointer' }}>删除</button>
                          </div>
                          <div style={{ fontSize: 10, color: 'var(--color-text-muted)', marginTop: 6 }}>来源: {skill.source_conv_id || 'N/A'} | 创建: {(skill.created_at || '').slice(0, 10)} | 重要性: {'*'.repeat(Math.min(skill.importance || 3, 5))}</div>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}

        {/* ===== MCP Tab ===== */}
        {activeTab === 'mcp' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button className="btn btn-sm" style={{ background: 'var(--color-accent-primary)', color: '#000' }} onClick={() => { setShowMcpAdd(!showMcpAdd); if (showMcpFileImport) setShowMcpFileImport(false) }}>{showMcpAdd ? '取消' : '+ 手动添加'}</button>
              <button className="btn btn-sm" style={{ background: '#bd93f9', color: '#000' }} onClick={() => { setShowMcpFileImport(!showMcpFileImport); if (showMcpAdd) setShowMcpAdd(false); setMcpFileImportPath(''); setMcpFileImportName(''); setMcpFileImportArgs('') }}>{showMcpFileImport ? '取消' : '📥 导入本地脚本'}</button>
            </div>

            {/* 手动添加弹窗 */}
            {showMcpAdd && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: 12, background: 'var(--color-bg-input)', borderRadius: 8 }}>
                <input value={newMcp.name} onChange={e => setNewMcp({ ...newMcp, name: e.target.value })} placeholder="服务名称（如 fact-check）" style={{ fontSize: 12, padding: '6px 10px', background: 'var(--color-bg-panel)', border: '1px solid var(--color-border)', borderRadius: 6, color: 'var(--color-text-primary)', outline: 'none' }} />
                <input value={newMcp.url} onChange={e => setNewMcp({ ...newMcp, url: e.target.value })} placeholder="服务地址（如 http://localhost:8080/mcp）" style={{ fontSize: 12, padding: '6px 10px', background: 'var(--color-bg-panel)', border: '1px solid var(--color-border)', borderRadius: 6, color: 'var(--color-text-primary)', outline: 'none' }} />
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--color-text-muted)' }}>
                  <input type="checkbox" checked={newMcp.enabled} onChange={e => setNewMcp({ ...newMcp, enabled: e.target.checked })} /> 启用
                </label>
                <button className="btn btn-sm" style={{ background: '#50fa7b', color: '#000', alignSelf: 'flex-start' }} onClick={handleAddMcp}>保存</button>
              </div>
            )}

            {/* 导入本地脚本弹窗 */}
            {showMcpFileImport && (
              <div style={{ padding: 14, background: 'var(--color-bg-panel)', border: '1px solid #bd93f9', borderRadius: 8, display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-primary)' }}>导入本地 MCP 脚本</div>
                <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>自动检测运行时 (.py→Python, .js→Node.js, .sh→Bash) 并识别 stdio/HTTP 类型</div>
                <input
                  value={mcpFileImportPath}
                  onChange={e => setMcpFileImportPath(e.target.value)}
                  placeholder="脚本路径，如 ~/.workbuddy/skills/zenith-auditor/scripts/fact_check_mcp.py"
                  style={{ fontSize: 12, padding: '6px 10px', background: 'var(--color-bg-input)', border: '1px solid var(--color-border)', borderRadius: 6, color: 'var(--color-text-primary)', outline: 'none' }}
                />
                <div style={{ display: 'flex', gap: 8 }}>
                  <input
                    value={mcpFileImportName}
                    onChange={e => setMcpFileImportName(e.target.value)}
                    placeholder="名称（留空=从文件名推导）"
                    style={{ flex: 1, fontSize: 12, padding: '6px 10px', background: 'var(--color-bg-input)', border: '1px solid var(--color-border)', borderRadius: 6, color: 'var(--color-text-primary)', outline: 'none' }}
                  />
                  <input
                    value={mcpFileImportArgs}
                    onChange={e => setMcpFileImportArgs(e.target.value)}
                    placeholder="额外参数（空格分隔）"
                    style={{ flex: 1, fontSize: 12, padding: '6px 10px', background: 'var(--color-bg-input)', border: '1px solid var(--color-border)', borderRadius: 6, color: 'var(--color-text-primary)', outline: 'none' }}
                  />
                </div>
                <button
                  className="btn btn-sm"
                  style={{ background: '#50fa7b', color: '#000', alignSelf: 'flex-start' }}
                  onClick={handleImportMcpFile}
                  disabled={mcpFileImportLoading}
                >
                  {mcpFileImportLoading ? '注册中...' : '注册'}
                </button>
              </div>
            )}

            <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
              <span>总计 {mcpServers.length} 个服务 | 已启用 {mcpServers.filter(s => s.enabled).length} 个</span>
            </div>
            {mcpLoading ? (
              <div className="spinner"><div className="spinner-dot" /><div className="spinner-dot" /><div className="spinner-dot" /></div>
            ) : mcpServers.length === 0 ? (
              <div className="empty-state"><p style={{ fontSize: 13 }}>暂无 MCP 服务</p></div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {mcpServers.map(s => {
                  const addr = s.serverUrl || s.url || (s.command ? `${s.command}${s.args && s.args.length ? ' ' + s.args.join(' ') : ''}` : '(未配置地址)')
                  return (
                  <div key={s.name} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', background: 'var(--color-bg-panel)', borderRadius: 8, border: '1px solid var(--color-border)' }}>
                    <span style={{ width: 8, height: 8, borderRadius: '50%', background: s.enabled ? '#50fa7b' : '#ff5555', flexShrink: 0 }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-primary)' }}>{s.name}</div>
                      <div style={{ fontSize: 10, color: 'var(--color-text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={addr}>{addr}</div>
                    </div>
                    <span style={{ fontSize: 10, color: s.enabled ? '#50fa7b' : '#ff5555' }}>{s.enabled ? '已启用' : '已禁用'}</span>
                    <label title="启用 / 禁用" style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, color: 'var(--color-text-muted)', cursor: 'pointer' }}>
                      <input
                        type="checkbox"
                        checked={!!s.enabled}
                        onChange={() => handleToggleMcp(s.name, s.enabled)}
                        style={{ cursor: 'pointer', accentColor: '#50fa7b', width: 14, height: 14, margin: 0 }}
                      />
                      启用
                    </label>
                    <button onClick={() => handleDeleteMcp(s.name)} style={{ fontSize: 10, color: '#ff5555', background: 'none', border: 'none', cursor: 'pointer' }}>删除</button>
                  </div>
                  )
                })}
              </div>
            )}
          </div>
        )}

        {/* ===== 执行痕迹 Tab（工具调用历史查询）===== */}
        {activeTab === 'traces' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <input
                value={traceSearch}
                onChange={e => setTraceSearch(e.target.value)}
                placeholder="按工具名/关键词搜索（如 search_memory、execute_code）..."
                style={{ flex: 1, minWidth: 160, fontSize: 12, padding: '6px 10px', background: 'var(--color-bg-input)', border: '1px solid var(--color-border)', borderRadius: 6, color: 'var(--color-text-primary)', outline: 'none' }}
              />
              <select className="form-select" style={{ width: 140, fontSize: 12 }} value={traceType} onChange={e => setTraceType(e.target.value)}>
                <option value="">全部类型</option>
                <option value="tool_call">工具调用</option>
                <option value="llm_call">LLM 调用</option>
                <option value="error">错误</option>
                <option value="validation">校验</option>
              </select>
              <button className="btn btn-sm" onClick={loadTraces}>🔍 查询</button>
              <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>共 {traces.length} 条</span>
            </div>

            {tracesLoading ? (
              <div className="empty-state"><p style={{ fontSize: 13 }}>加载中...</p></div>
            ) : traces.length === 0 ? (
              <div className="empty-state"><p style={{ fontSize: 13 }}>暂无执行痕迹（有工具调用后才会记录）</p></div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                {traces.map(t => {
                  const isExpanded = expandedTraceId === t.id
                  const info = parseTraceData(t)
                  const tType = t.trace_type || ''
                  const tColor = tType === 'error' ? '#ff5555' : tType === 'validation' ? '#ffab40' : '#8be9fd'
                  return (
                    <div key={t.id} style={{
                      display: 'flex', alignItems: 'flex-start', gap: 10,
                      padding: isExpanded ? '12px 14px' : '8px 14px',
                      background: 'var(--color-bg-panel)',
                      border: `1px solid ${isExpanded ? tColor : 'var(--color-border)'}`,
                      borderRadius: 6, cursor: 'pointer', transition: 'all 0.2s',
                    }} onClick={() => setExpandedTraceId(isExpanded ? null : t.id)}>
                      <span style={{
                        background: tColor, color: '#111', padding: '2px 6px', borderRadius: 4,
                        fontSize: 10, fontWeight: 600, flexShrink: 0, whiteSpace: 'nowrap',
                      }}>
                        {tType === 'error' ? '✗' : '✓'} {tType || 'trace'}
                      </span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-primary)' }}>
                          {info.name}
                          {info.duration !== undefined && <span style={{ fontSize: 10, color: 'var(--color-text-muted)', fontWeight: 400, marginLeft: 8 }}>{info.duration}ms</span>}
                        </div>
                        <div style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>
                          对话 {t.conv_id?.slice(0, 8) || '-'} · {new Date(t.created_at).toLocaleString('zh-CN')}
                        </div>
                        {isExpanded && (
                          <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
                            {Object.keys(info.args).length > 0 && (
                              <pre style={{ fontSize: 11, background: 'var(--color-bg-code, rgba(0,0,0,0.3))', padding: 8, borderRadius: 4, margin: 0, overflowX: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all', color: 'var(--color-text-secondary)' }}>
                                <span style={{ color: '#f1fa8c', fontWeight: 600 }}>参数:</span>{'\n'}
                                {Object.entries(info.args).map(([k, v]) => `${k}: ${typeof v === 'string' ? (v.length > 500 ? v.slice(0, 500) + '…' : v) : JSON.stringify(v)}`).join('\n')}
                              </pre>
                            )}
                            {info.result && (
                              <pre style={{ fontSize: 11, background: 'var(--color-bg-code, rgba(0,0,0,0.3))', padding: 8, borderRadius: 4, margin: 0, overflowX: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all', color: tType === 'error' ? '#ff5555' : 'var(--color-text-secondary)' }}>
                                <span style={{ color: tType === 'error' ? '#ff5555' : '#50fa7b', fontWeight: 600 }}>{tType === 'error' ? '错误:' : '结果:'}</span>{'\n'}
                                {info.result.length > 2000 ? info.result.slice(0, 2000) + '…' : info.result}
                              </pre>
                            )}
                          </div>
                        )}
                      </div>
                      <span className={`trace-caret ${isExpanded ? 'open' : ''}`}>▾</span>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
