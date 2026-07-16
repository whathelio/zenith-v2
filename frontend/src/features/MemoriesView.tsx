import { useState, useEffect, useCallback } from 'react'
import { api, type Memory } from '../shared/api'
import { TransformButton } from '../components/TransformButton'

const TYPE_COLORS: Record<string, string> = {
  personal_info: '#8be9fd', preference: '#ff79c6', event: '#50fa7b',
  decision: '#bd93f9', fact: '#f1fa8c', experience: '#ff6e40',
}
const TYPE_NAMES: Record<string, string> = {
  personal_info: '个人信息', preference: '偏好', event: '事件',
  decision: '决定', fact: '事实', experience: '经验',
}
const TYPE_ICONS: Record<string, string> = {
  personal_info: '👤', preference: '💕', event: '📅',
  decision: '⚖️', fact: '💡', experience: '🔥',
}

export default function MemoriesView() {
  const [memories, setMemories] = useState<Memory[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')
  const [search, setSearch] = useState('')
  const [expandedId, setExpandedId] = useState<number | null>(null)

  const loadMemories = useCallback(async () => {
    setLoading(true)
    try { const ms = await api.listMemories(filter, search); setMemories(ms) } catch {} finally { setLoading(false) }
  }, [filter, search])

  useEffect(() => { loadMemories() }, [loadMemories])

  const handleDelete = async (id: number) => {
    if (!confirm('删除此记忆？')) return
    try { await api.deleteMemory(id); setMemories(prev => prev.filter(m => m.id !== id)) } catch {}
  }

  const formatDate = (dateStr: string) => {
    const d = new Date(dateStr)
    return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, height: '100%', overflowY: 'auto', padding: '12px 16px' }}>
      <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--color-text-primary)' }}>🧠 记忆管理</div>

      {/* 筛选与搜索 */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <select className="form-select" style={{ width: 130, fontSize: 12 }} value={filter} onChange={e => setFilter(e.target.value)}>
          <option value="">全部类型</option>
          {Object.entries(TYPE_NAMES).map(([k, v]) => <option key={k} value={k}>{TYPE_ICONS[k]} {v}</option>)}
        </select>
        <input className="form-input" style={{ width: 200, fontSize: 12 }} placeholder="搜索..." value={search} onChange={e => setSearch(e.target.value)} />
        <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>共 {memories.length} 条</span>
      </div>

      {/* 类型统计条 */}
      {!filter && memories.length > 0 && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {Object.entries(TYPE_NAMES).map(([k, v]) => {
            const count = memories.filter(m => m.type === k).length
            if (count === 0) return null
            return (
              <button key={k} className="btn btn-sm" style={{ background: TYPE_COLORS[k], color: '#fff', border: 'none', fontSize: 10, padding: '2px 8px' }} onClick={() => setFilter(k)}>
                {TYPE_ICONS[k]} {v} ({count})
              </button>
            )
          })}
        </div>
      )}

      {/* 记忆列表 */}
      {loading ? (
        <div className="spinner"><div className="spinner-dot" /><div className="spinner-dot" /><div className="spinner-dot" /></div>
      ) : memories.length === 0 ? (
        <div className="empty-state"><p style={{ fontSize: 13 }}>暂无记忆</p></div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          {memories.map(m => {
            const isExpanded = expandedId === m.id
            const tc = TYPE_COLORS[m.type] || '#717e95'
            return (
              <div key={m.id} style={{
                display: 'flex', alignItems: 'flex-start', gap: 10,
                padding: isExpanded ? '12px 14px' : '8px 14px',
                background: 'var(--color-bg-panel)',
                border: `1px solid ${isExpanded ? tc : 'var(--color-border)'}`,
                borderRadius: 6, cursor: 'pointer', transition: 'all 0.2s',
              }} onClick={() => setExpandedId(isExpanded ? null : m.id)}>
                <span style={{ background: tc, color: '#fff', padding: '2px 6px', borderRadius: 4, fontSize: 10, fontWeight: 600, flexShrink: 0, whiteSpace: 'nowrap' }}>
                  {TYPE_ICONS[m.type] || ''} {TYPE_NAMES[m.type] || m.type}
                </span>
                <div style={{ flex: 1 }}>
                  <div style={{
                    fontSize: 12, lineHeight: 1.5,
                    maxHeight: isExpanded ? 'none' : '3em',
                    overflow: isExpanded ? 'visible' : 'hidden',
                    textOverflow: 'ellipsis',
                    color: 'var(--color-text-secondary)',
                  }}>
                    {m.content}
                  </div>
                  {isExpanded && (
                    <div style={{ marginTop: 8 }}>
                      {m.keywords && <div style={{ fontSize: 10, color: 'var(--color-text-muted)', marginBottom: 4 }}>关键词: {m.keywords}</div>}
                      {m.importance && <div style={{ fontSize: 10, color: 'var(--color-text-muted)', marginBottom: 4 }}>重要性: {'⭐'.repeat(Math.min(m.importance, 5))}</div>}
                      <div style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>创建: {formatDate(m.created_at)}</div>
                    </div>
                  )}
                </div>
                <div style={{ display: 'flex', gap: 3, flexShrink: 0, alignItems: 'center' }}>
                  <div onClick={e => e.stopPropagation()}>
                    <TransformButton sourceType="memory" sourceId={m.id} onTransformed={() => loadMemories()} />
                  </div>
                  <button className="btn-icon" style={{ width: 24, height: 24, fontSize: 12 }} onClick={e => { e.stopPropagation(); handleDelete(m.id) }} title="删除">×</button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
