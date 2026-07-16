import { useState, useEffect } from 'react'
import { api, type Note } from '../shared/api'
import { TransformButton } from '../components/TransformButton'

export default function NotesView() {
  const [notes, setNotes] = useState<Note[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editForm, setEditForm] = useState<Partial<Note>>({})
  const [showCreate, setShowCreate] = useState(false)
  const [newNote, setNewNote] = useState({ title: '', content: '', tags: '' })
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set())

  useEffect(() => { loadNotes() }, [search])

  const loadNotes = async () => {
    try { const list = await api.listNotes(search); setNotes(list) } catch {} finally { setLoading(false) }
  }

  const handleCreate = async () => {
    if (!newNote.title.trim()) return
    try {
      const created = await api.createNote(newNote)
      setNotes(prev => [created, ...prev])
      setNewNote({ title: '', content: '', tags: '' })
      setShowCreate(false)
    } catch {}
  }

  const handleDelete = async (id: number) => {
    if (!confirm('删除此笔记？')) return
    try { await api.deleteNote(id); setNotes(prev => prev.filter(n => n.id !== id)) } catch {}
  }

  const startEdit = (n: Note) => {
    setEditingId(n.id)
    setEditForm({ title: n.title, content: n.content, tags: n.tags })
  }

  const saveEdit = async () => {
    if (editingId === null) return
    try {
      await api.updateNote(editingId, editForm)
      setNotes(prev => prev.map(n => n.id === editingId ? { ...n, ...editForm } : n))
      setEditingId(null); setEditForm({})
    } catch {}
  }

  const cancelEdit = () => { setEditingId(null); setEditForm({}) }
  const toggleExpand = (id: number) => {
    setExpandedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, height: '100%', overflowY: 'auto', padding: '12px 16px' }}>
      <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--color-text-primary)' }}>📝 笔记管理</div>

      {/* 工具栏 */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <input className="form-input" style={{ flex: 1, fontSize: 12 }} placeholder="搜索笔记..." value={search} onChange={e => setSearch(e.target.value)} />
        <button className="btn btn-sm" style={{ background: 'var(--color-accent-primary)', color: '#fff' }} onClick={() => setShowCreate(!showCreate)}>
          {showCreate ? '取消' : '+ 新建'}
        </button>
      </div>

      {/* 新建表单 */}
      {showCreate && (
        <div style={{ padding: 14, background: 'var(--color-bg-panel)', border: '1px solid var(--color-accent-primary)', borderRadius: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <input className="form-input" style={{ fontSize: 12 }} placeholder="标题" value={newNote.title} onChange={e => setNewNote({ ...newNote, title: e.target.value })} />
          <textarea className="form-input" style={{ fontSize: 12 }} placeholder="内容..." rows={3} value={newNote.content} onChange={e => setNewNote({ ...newNote, content: e.target.value })} />
          <input className="form-input" style={{ fontSize: 12 }} placeholder="标签（逗号分隔）" value={newNote.tags} onChange={e => setNewNote({ ...newNote, tags: e.target.value })} />
          <button className="btn btn-sm" style={{ background: 'var(--color-accent-primary)', color: '#fff', alignSelf: 'flex-start' }} onClick={handleCreate}>保存</button>
        </div>
      )}

      <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>共 {notes.length} 条</span>

      {loading ? (
        <div className="spinner"><div className="spinner-dot" /><div className="spinner-dot" /><div className="spinner-dot" /></div>
      ) : notes.length === 0 ? (
        <div className="empty-state"><p style={{ fontSize: 13 }}>暂无笔记</p></div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {notes.map(n => (
            <div key={n.id} style={{ padding: '10px 14px', background: 'var(--color-bg-panel)', border: '1px solid var(--color-border)', borderRadius: 6 }}>
              {editingId === n.id ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <input className="form-input" style={{ fontSize: 12 }} value={editForm.title || ''} onChange={e => setEditForm(f => ({ ...f, title: e.target.value }))} placeholder="标题" />
                  <textarea className="form-input" style={{ fontSize: 12 }} value={editForm.content || ''} onChange={e => setEditForm(f => ({ ...f, content: e.target.value }))} placeholder="内容" rows={3} />
                  <input className="form-input" style={{ fontSize: 12 }} value={editForm.tags || ''} onChange={e => setEditForm(f => ({ ...f, tags: e.target.value }))} placeholder="标签" />
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button className="btn btn-sm" style={{ background: 'var(--color-accent-primary)', color: '#fff' }} onClick={saveEdit}>保存</button>
                    <button className="btn btn-sm" onClick={cancelEdit}>取消</button>
                  </div>
                </div>
              ) : (
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-primary)' }}>{n.title}</div>
                    {n.content && (
                      <>
                        <div style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginTop: 3, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>
                          {n.content.length > 150 && !expandedIds.has(n.id) ? n.content.slice(0, 150) + '...' : n.content}
                        </div>
                        {n.content.length > 150 && (
                          <button className="btn btn-sm" style={{ marginTop: 3, fontSize: 10, padding: '1px 6px', color: 'var(--color-accent-primary)', background: 'transparent', border: 'none', cursor: 'pointer' }} onClick={() => toggleExpand(n.id)}>
                            {expandedIds.has(n.id) ? '收起 ▲' : '展开 ▼'}
                          </button>
                        )}
                      </>
                    )}
                    <div style={{ marginTop: 4, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      {n.tags && n.tags.split(',').map((tag, i) => (
                        <span key={i} style={{ background: 'rgba(189,147,249,0.15)', color: '#bd93f9', padding: '1px 6px', borderRadius: 4, fontSize: 10 }}>#{tag.trim()}</span>
                      ))}
                      {n.source && n.source !== 'manual' && <span style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>来源: {n.source}</span>}
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 3, flexShrink: 0 }}>
                    <TransformButton sourceType="note" sourceId={n.id} onTransformed={() => loadNotes()} />
                    <button className="btn-icon" style={{ width: 24, height: 24, fontSize: 11 }} onClick={() => startEdit(n)} title="编辑">✎</button>
                    <button className="btn-icon" style={{ width: 24, height: 24, fontSize: 11 }} onClick={() => handleDelete(n.id)} title="删除">×</button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
