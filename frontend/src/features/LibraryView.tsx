import { useState, useEffect, useCallback, useMemo } from 'react'
import { api, type Note, type Memory, type Skill, type SkillSuggestion } from '../shared/api'
import { TransformButton } from '../components/TransformButton'

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

type Tab = 'notes' | 'memories' | 'skills'

export default function LibraryView() {
  const [activeTab, setActiveTab] = useState<Tab>('notes')

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

  // Skills state
  const [skills, setSkills] = useState<Skill[]>([])
  const [skillsLoading, setSkillsLoading] = useState(true)
  const [skillSearch, setSkillSearch] = useState('')
  const [expandedSkillId, setExpandedSkillId] = useState<number | null>(null)
  const [feedbackSkillId, setFeedbackSkillId] = useState<number | null>(null)
  const [feedbackText, setFeedbackText] = useState('')
  const [feedbackRating, setFeedbackRating] = useState(3)
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false)
  const [suggestionSkillId, setSuggestionSkillId] = useState<number | null>(null)
  const [suggestion, setSuggestion] = useState<SkillSuggestion | null>(null)
  const [suggestionLoading, setSuggestionLoading] = useState(false)
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

  const handleConfirmSkill = async (id: number) => { try { await api.confirmSkill(id); loadSkills() } catch {} }
  const handleUseSkill = async (id: number) => { try { await api.useSkill(id); showToast('技能已使用'); loadSkills() } catch {} }
  const handleDeleteSkill = async (id: number) => { try { await api.deleteSkill(id); setExpandedSkillId(null); loadSkills() } catch {} }

  const submitFeedback = async () => {
    if (!feedbackSkillId || !feedbackText.trim()) return
    setFeedbackSubmitting(true)
    try {
      await api.feedbackSkill(feedbackSkillId, feedbackText, feedbackRating)
      showToast('反馈已记录 ✓')
      setFeedbackSkillId(null); setFeedbackText(''); setFeedbackRating(3)
      loadSkills()
    } catch { showToast('反馈提交失败') }
    finally { setFeedbackSubmitting(false) }
  }

  const loadSuggestions = async (sid: number) => {
    setSuggestionSkillId(sid); setSuggestion(null); setSuggestionLoading(true)
    try {
      const result = await api.getSkillSuggestions(sid)
      setSuggestion(result)
    } catch { showToast('获取建议失败'); setSuggestionSkillId(null) }
    finally { setSuggestionLoading(false) }
  }

  const applyImprovement = async () => {
    if (!suggestion?.improved_steps || !suggestionSkillId) return
    try {
      await api.improveSkill(suggestionSkillId, suggestion.improved_steps)
      showToast('技能已更新 ✓')
      setSuggestion(null); setSuggestionSkillId(null); setExpandedSkillId(null)
      loadSkills()
    } catch { showToast('更新失败') }
  }

  const tabs: { key: Tab; label: string; icon: string }[] = [
    { key: 'notes', label: '笔记', icon: '📝' },
    { key: 'memories', label: '记忆', icon: '🧠' },
    { key: 'skills', label: '技能', icon: '⚡' },
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
              onClick={() => setActiveTab(t.key)}
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
                              <div style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginTop: 3, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>
                                {n.content.length > 150 && !expandedNoteIds.has(n.id) ? n.content.slice(0, 150) + '...' : n.content}
                              </div>
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
                  const tc = MEMORY_TYPE_COLORS[m.type] || '#717e95'
                  return (
                    <div key={m.id} style={{
                      display: 'flex', alignItems: 'flex-start', gap: 10,
                      padding: isExpanded ? '12px 14px' : '8px 14px',
                      background: 'var(--color-bg-panel)',
                      border: `1px solid ${isExpanded ? tc : 'var(--color-border)'}`,
                      borderRadius: 6, cursor: 'pointer', transition: 'all 0.2s',
                    }} onClick={() => setExpandedMemoryId(isExpanded ? null : m.id)}>
                      <span style={{ background: tc, color: '#fff', padding: '2px 6px', borderRadius: 4, fontSize: 10, fontWeight: 600, flexShrink: 0, whiteSpace: 'nowrap' }}>
                        {MEMORY_TYPE_ICONS[m.type] || ''} {MEMORY_TYPE_NAMES[m.type] || m.type}
                      </span>
                      <div style={{ flex: 1 }}>
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
                      </div>
                      <div style={{ display: 'flex', gap: 3, flexShrink: 0, alignItems: 'center' }} onClick={e => e.stopPropagation()}>
                        <TransformButton sourceType="memory" sourceId={m.id} onTransformed={() => loadMemories()} />
                        <button className="btn-icon" style={{ width: 24, height: 24, fontSize: 12 }} onClick={() => handleDeleteMemory(m.id)} title="删除">×</button>
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
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <input value={skillSearch} onChange={e => setSkillSearch(e.target.value)} placeholder="搜索技能..." style={{ flex: 1, fontSize: 12, padding: '6px 10px', background: 'var(--color-bg-input)', border: '1px solid var(--color-border)', borderRadius: 6, color: 'var(--color-text-primary)', outline: 'none' }} />
            </div>
            <div style={{ display: 'flex', gap: 12, fontSize: 11, color: 'var(--color-text-muted)' }}>
              <span>总计 {skills.length}</span>
              <span>已确认 {skills.filter(s => s.confirmed_by_user === 1).length}</span>
            </div>
            {skillsLoading ? (
              <div className="spinner"><div className="spinner-dot" /><div className="spinner-dot" /><div className="spinner-dot" /></div>
            ) : skills.length === 0 ? (
              <div className="empty-state"><p style={{ fontSize: 13 }}>暂无技能卡片</p></div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {skills.map(skill => {
                  const conf = SKILL_CONFIRMED_COLORS[skill.confirmed_by_user] || SKILL_CONFIRMED_COLORS[0]
                  const isExpanded = expandedSkillId === skill.id
                  return (
                    <div key={skill.id} style={{ background: 'var(--color-bg-panel)', borderRadius: 8, padding: isExpanded ? '10px 14px' : '8px 14px', cursor: 'pointer', border: `1px solid ${isExpanded ? 'var(--color-accent-primary)' : 'var(--color-border)'}`, }} onClick={() => setExpandedSkillId(isExpanded ? null : skill.id)}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-primary)', flex: 1 }}>⚡ {skill.name}</span>
                        <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 4, background: conf.bg, color: conf.text }}>{conf.label}</span>
                        <span style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>使用 {skill.usage_count} 次</span>
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--color-text-secondary)', marginTop: 4 }}>触发场景: {skill.trigger_scene}</div>
                      {skill.tags && skill.tags.length > 0 && (
                        <div style={{ display: 'flex', gap: 4, marginTop: 4, flexWrap: 'wrap' }}>
                          {skill.tags.map((tag, i) => (
                            <span key={i} style={{ fontSize: 10, padding: '1px 6px', borderRadius: 4, background: 'rgba(189,147,249,0.12)', color: 'var(--color-accent-primary)' }}>{tag}</span>
                          ))}
                        </div>
                      )}
                      {isExpanded && skill.steps && skill.steps.length > 0 && (
                        <div style={{ marginTop: 8 }}>
                          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 4 }}>操作步骤:</div>
                          {skill.steps.map((step, i) => (
                            <div key={i} style={{ fontSize: 12, color: 'var(--color-text-secondary)', paddingLeft: 12, marginBottom: 3 }}>{i + 1}. {step}</div>
                          ))}
                          <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
                            {skill.confirmed_by_user === 0 && (
                              <button onClick={e => { e.stopPropagation(); handleConfirmSkill(skill.id) }} style={{ fontSize: 11, padding: '4px 10px', borderRadius: 5, background: 'rgba(80,250,123,0.12)', color: '#50fa7b', border: '1px solid rgba(80,250,123,0.3)', cursor: 'pointer' }}>✅ 确认</button>
                            )}
                            <button onClick={e => { e.stopPropagation(); handleUseSkill(skill.id) }} style={{ fontSize: 11, padding: '4px 10px', borderRadius: 5, background: 'rgba(189,147,249,0.12)', color: '#bd93f9', border: '1px solid rgba(189,147,249,0.3)', cursor: 'pointer' }}>📋 使用</button>
                            <button onClick={e => { e.stopPropagation(); setFeedbackSkillId(skill.id); setFeedbackText(''); setFeedbackRating(3) }} style={{ fontSize: 11, padding: '4px 10px', borderRadius: 5, background: 'rgba(255,179,71,0.12)', color: '#ffb347', border: '1px solid rgba(255,179,71,0.3)', cursor: 'pointer' }}>💬 反馈</button>
                            <button onClick={e => { e.stopPropagation(); loadSuggestions(skill.id) }} style={{ fontSize: 11, padding: '4px 10px', borderRadius: 5, background: 'rgba(139,233,253,0.12)', color: '#8be9fd', border: '1px solid rgba(139,233,253,0.3)', cursor: 'pointer' }}>🔧 改进建议</button>
                            <button onClick={e => { e.stopPropagation(); handleDeleteSkill(skill.id) }} style={{ fontSize: 11, padding: '4px 10px', borderRadius: 5, background: 'rgba(255,85,85,0.12)', color: '#ff5555', border: '1px solid rgba(255,85,85,0.3)', cursor: 'pointer' }}>🗑 删除</button>
                          </div>
                          <div style={{ fontSize: 10, color: 'var(--color-text-muted)', marginTop: 6 }}>来源: {skill.source_conv_id || 'N/A'} | 创建: {skill.created_at?.slice(0, 10)}</div>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Feedback Modal */}
      {feedbackSkillId && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setFeedbackSkillId(null)}>
          <div style={{ maxWidth: 420, width: '90%', padding: 20, borderRadius: 10, background: 'var(--color-bg-panel)', border: '1px solid var(--color-accent-primary)' }} onClick={e => e.stopPropagation()}>
            <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--color-accent-primary)', marginBottom: 12 }}>💬 技能反馈</div>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 10 }}>记录这次技能使用的体验</div>
            <textarea value={feedbackText} onChange={e => setFeedbackText(e.target.value)} placeholder="描述经验或改进建议..." style={{ width: '100%', height: 80, padding: 10, borderRadius: 6, background: 'var(--color-bg-input)', border: '1px solid var(--color-border)', color: 'var(--color-text-primary)', fontSize: 12, resize: 'vertical' }} rows={3} />
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10 }}>
              <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>评分:</span>
              {[1, 2, 3, 4, 5].map(r => (
                <button key={r} onClick={() => setFeedbackRating(r)} style={{ fontSize: 18, cursor: 'pointer', background: 'none', border: 'none', color: r <= feedbackRating ? '#ffb347' : 'var(--color-text-muted)', padding: 2 }}>★</button>
              ))}
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 14, justifyContent: 'flex-end' }}>
              <button className="btn btn-sm" onClick={() => setFeedbackSkillId(null)}>取消</button>
              <button className="btn btn-sm" style={{ background: '#ffb347', color: '#000', fontWeight: 600 }} onClick={submitFeedback} disabled={feedbackSubmitting || !feedbackText.trim()}>提交反馈</button>
            </div>
          </div>
        </div>
      )}

      {/* Suggestion Modal */}
      {suggestionSkillId && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => { setSuggestion(null); setSuggestionSkillId(null) }}>
          <div style={{ maxWidth: 560, width: '90%', maxHeight: '80vh', overflowY: 'auto', padding: 20, borderRadius: 10, background: 'var(--color-bg-panel)', border: '1px solid var(--color-accent-secondary)' }} onClick={e => e.stopPropagation()}>
            <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--color-accent-secondary)', marginBottom: 12 }}>🔧 技能改进建议</div>
            {suggestionLoading ? (
              <div className="spinner"><div className="spinner-dot" /><div className="spinner-dot" /><div className="spinner-dot" /><span style={{ marginLeft: 8, fontSize: 12 }}>AI 分析中...</span></div>
            ) : suggestion ? (
              suggestion.ready ? (
                <>
                  <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 8 }}>基于 {suggestion.feedback_count} 条反馈</div>
                  {suggestion.analysis && (
                    <div style={{ padding: 10, borderRadius: 6, background: 'rgba(255,179,71,0.08)', border: '1px solid rgba(255,179,71,0.2)', marginBottom: 12, fontSize: 12, color: 'var(--color-text-secondary)', lineHeight: 1.6 }}>{suggestion.analysis}</div>
                  )}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                    <div>
                      <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 6 }}>旧步骤</div>
                      {suggestion.current_steps?.map((s, i) => (
                        <div key={i} style={{ padding: '6px 10px', borderRadius: 4, background: 'var(--color-bg-input)', border: '1px solid var(--color-border)', fontSize: 11, color: 'var(--color-text-secondary)', marginBottom: 3 }}>{i + 1}. {s}</div>
                      ))}
                    </div>
                    <div>
                      <div style={{ fontSize: 11, fontWeight: 600, color: '#50fa7b', marginBottom: 6 }}>建议新步骤</div>
                      {suggestion.improved_steps?.map((s, i) => (
                        <div key={i} style={{ padding: '6px 10px', borderRadius: 4, background: 'rgba(80,250,123,0.06)', border: '1px solid rgba(80,250,123,0.2)', fontSize: 11, color: '#50fa7b', marginBottom: 3 }}>{i + 1}. {s}</div>
                      ))}
                    </div>
                  </div>
                  {suggestion.reason && (
                    <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 10, fontStyle: 'italic' }}>改进理由: {suggestion.reason}</div>
                  )}
                  <div style={{ display: 'flex', gap: 8, marginTop: 14, justifyContent: 'flex-end' }}>
                    <button className="btn btn-sm" onClick={() => { setSuggestion(null); setSuggestionSkillId(null) }}>关闭</button>
                    <button className="btn btn-sm" style={{ background: '#50fa7b', color: '#000', fontWeight: 600 }} onClick={applyImprovement}>✓ 应用改进</button>
                  </div>
                </>
              ) : (
                <div style={{ textAlign: 'center', padding: 20, color: 'var(--color-text-muted)', fontSize: 13 }}>
                  😅 反馈不足（当前 {suggestion.feedback_count} 条，需要至少 {suggestion.min_required} 条）<br />
                  <span style={{ fontSize: 11 }}>多使用几次技能并提交反馈后再试</span>
                </div>
              )
            ) : null}
          </div>
        </div>
      )}
    </div>
  )
}
