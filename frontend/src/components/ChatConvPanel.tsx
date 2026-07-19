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

export default function ChatConvPanel({
  conversations, activeId, onSelect, onDelete, onNew, collapsed, onToggle,
}: ChatConvPanelProps) {
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

  // 展开态：200px 完整列表
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
          conversations.map(conv => (
            <div
              key={conv.id}
              className={`conv-item ${conv.id === activeId ? 'active' : ''}`}
              onClick={() => onSelect(conv.id)}
            >
              <span className="conv-item-title">{conv.title}</span>
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
          ))
        )}
      </div>
    </div>
  )
}
