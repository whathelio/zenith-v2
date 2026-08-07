import { useMemo, useState } from 'react'

interface Conversation {
  id: string
  title: string
  msg_count: number
  updated_at: string
}

interface ChatConvPanelProps {
  conversations: Conversation[]
  activeId: string
  onSelect: (id: string) => void
  onDelete: (id: string) => void
  onNew: () => void
  collapsed: boolean
  onToggle: () => void
}

/** 时间分组定义：标签 + 判定函数 */
type GroupKey = 'today' | 'yesterday' | 'week' | 'month' | 'older'

const GROUP_LABEL: Record<GroupKey, string> = {
  today: '今天',
  yesterday: '昨天',
  week: '近 7 天',
  month: '近 30 天',
  older: '更早',
}

const GROUP_ORDER: GroupKey[] = ['today', 'yesterday', 'week', 'month', 'older']

function groupOf(d: Date, now: Date): GroupKey {
  const startOfDay = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const dayMs = 86400000
  const diff = Math.floor((startOfDay(now) - startOfDay(d)) / dayMs)
  if (diff <= 0) return 'today'
  if (diff === 1) return 'yesterday'
  if (diff < 7) return 'week'
  if (diff < 30) return 'month'
  return 'older'
}

export default function ChatConvPanel({
  conversations, activeId, onSelect, onDelete, onNew, collapsed, onToggle,
}: ChatConvPanelProps) {
  // 每个分组的折叠状态（默认展开）
  const [collapsedGroups, setCollapsedGroups] = useState<Set<GroupKey>>(new Set())

  const formatDate = (dateStr: string) => {
    try {
      const d = new Date(dateStr)
      const now = new Date()
      if (d.toDateString() === now.toDateString()) {
        return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
      }
      return d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
    } catch {
      return ''
    }
  }

  // 按时间分组 + 组内按时间倒序
  const groups = useMemo(() => {
    const now = new Date()
    const map: Record<GroupKey, Conversation[]> = {
      today: [], yesterday: [], week: [], month: [], older: [],
    }
    for (const c of conversations) {
      const d = new Date(c.updated_at)
      if (isNaN(d.getTime())) {
        map.older.push(c)
        continue
      }
      map[groupOf(d, now)].push(c)
    }
    return GROUP_ORDER
      .map(key => ({ key, label: GROUP_LABEL[key], items: map[key] }))
      .filter(g => g.items.length > 0)
  }, [conversations])

  const toggleGroup = (key: GroupKey) => {
    setCollapsedGroups(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  // 折叠态：竖条，只有展开按钮 + 新对话按钮 + 会话色点
  if (collapsed) {
    return (
      <div className="chat-conv-panel collapsed">
        <button className="conv-toggle" onClick={onToggle} title="展开会话列表">≡</button>
        <button className="conv-new-icon" onClick={onNew} title="新对话">+</button>
        <div className="conv-dots">
          {conversations.slice(0, 20).map(c => (
            <div
              key={c.id}
              className={`conv-dot ${c.id === activeId ? 'active' : ''}`}
              onClick={() => onSelect(c.id)}
              title={c.title}
            />
          ))}
        </div>
      </div>
    )
  }

  // 展开态：200px 完整列表（按时间分组，可折叠）
  return (
    <div className="chat-conv-panel">
      <div className="conv-header">
        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-secondary)' }}>
          对话 ({conversations.length})
        </span>
        <button className="conv-toggle" onClick={onToggle} title="收起">‹</button>
      </div>
      <button className="btn btn-primary btn-full btn-sm" onClick={onNew} style={{ marginBottom: 6 }}>
        + 新对话
      </button>
      <div className="conv-list">
        {conversations.length === 0 ? (
          <div style={{ padding: 12, fontSize: 12, color: 'var(--color-text-muted)', textAlign: 'center' }}>
            暂无对话
          </div>
        ) : (
          groups.map(group => {
            const isCollapsed = collapsedGroups.has(group.key)
            return (
              <div key={group.key} className="conv-group">
                <button
                  className="conv-group-header"
                  onClick={() => toggleGroup(group.key)}
                  title={isCollapsed ? '展开' : '折叠'}
                >
                  <span className={`conv-group-arrow ${isCollapsed ? 'collapsed' : ''}`}>▾</span>
                  <span className="conv-group-label">{group.label}</span>
                  <span className="conv-group-count">{group.items.length}</span>
                </button>
                {!isCollapsed && group.items.map(conv => (
                  <div
                    key={conv.id}
                    className={`conv-item ${conv.id === activeId ? 'active' : ''}`}
                    onClick={() => onSelect(conv.id)}
                  >
                    <span className="conv-item-title">
                      {(conv as any).source_type && <span title="学习对话" style={{ marginRight: 3 }}>📖</span>}
                      {conv.title}
                    </span>
                    <span className="conv-item-meta">
                      {conv.msg_count > 0 ? `${conv.msg_count}条` : ''} {formatDate(conv.updated_at)}
                    </span>
                    <button
                      className="conv-item-del"
                      onClick={e => {
                        e.stopPropagation()
                        if (confirm('删除此对话？')) onDelete(conv.id)
                      }}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
