interface Conversation {
  id: string
  title: string
  msg_count: number
  updated_at: string
}

interface SidebarProps {
  conversations: Conversation[]
  activeId: string
  onSelect: (id: string) => void
  onDelete: (id: string) => void
  onNew: () => void
}

export default function Sidebar({ conversations, activeId, onSelect, onDelete, onNew }: SidebarProps) {
  const formatDate = (dateStr: string) => {
    try {
      const d = new Date(dateStr)
      const now = new Date()
      if (d.toDateString() === now.toDateString()) {
        return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
      }
      return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
    } catch {
      return ''
    }
  }

  return (
    <div className="sidebar">
      <div className="sidebar-header">
        <div className="sidebar-logo">
          <div className="sidebar-logo-icon">Z</div>
          <span>Zenith v2</span>
        </div>
      </div>
      <div className="sidebar-actions">
        <button className="btn btn-primary btn-full btn-sm" onClick={onNew}>
          + 新对话
        </button>
      </div>
      <div className="sidebar-list">
        {conversations.length === 0 ? (
          <div className="sidebar-empty">暂无对话<br />点击上方按钮开始</div>
        ) : (
          conversations.map(conv => (
            <div
              key={conv.id}
              className={`sidebar-item ${conv.id === activeId ? 'active' : ''}`}
              onClick={() => onSelect(conv.id)}
            >
              <span className="sidebar-item-title">{conv.title}</span>
              <span className="sidebar-item-meta">
                {conv.msg_count > 0 ? `${conv.msg_count}` : ''}
              </span>
              <span className="sidebar-item-meta" style={{ marginLeft: 4 }}>
                {formatDate(conv.updated_at)}
              </span>
              <span
                className="sidebar-item-del"
                onClick={e => {
                  e.stopPropagation()
                  if (confirm('删除此对话？')) onDelete(conv.id)
                }}
              >
                ×
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
