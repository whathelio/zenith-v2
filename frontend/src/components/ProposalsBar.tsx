import { useState } from 'react'
import type { Proposal } from '../shared/api'

interface ProposalsBarProps {
  proposals: Proposal[]
  onConfirm: (type: string, id: number) => void
  onReject: (type: string, id: number) => void
  onModify?: (type: string, id: number, changes: Record<string, string>) => void
}

export default function ProposalsBar({ proposals, onConfirm, onReject, onModify }: ProposalsBarProps) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editValues, setEditValues] = useState<Record<string, string>>({})

  if (proposals.length === 0) return null

  const startEdit = (p: Proposal) => {
    const key = `${p.type}-${p.id}`
    setEditingId(key)
    if (p.type === 'schedule') {
      setEditValues({ title: p.title, start_time: p.time || '', description: p.description || '' })
    } else {
      setEditValues({ title: p.title, content: p.content || '', tags: p.tags || '' })
    }
  }

  const submitEdit = (p: Proposal) => {
    if (onModify) {
      onModify(p.type, p.id, editValues)
    }
    setEditingId(null)
  }

  return (
    <div className="proposals-bar">
      <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-text-muted)', marginBottom: 8 }}>
        AI 提议 ({proposals.length}) — 请确认、修改或忽略
      </div>
      {proposals.map(p => {
        const key = `${p.type}-${p.id}`
        const isEditing = editingId === key
        return (
          <div key={key} className="proposal-item">
            <span className="proposal-icon">
              {p.type === 'schedule' ? '📅' : '📝'}
            </span>
            {isEditing ? (
              <div className="proposal-edit-form">
                {p.type === 'schedule' ? (
                  <>
                    <input
                      className="proposal-input"
                      value={editValues.title || ''}
                      onChange={e => setEditValues({ ...editValues, title: e.target.value })}
                      placeholder="标题"
                    />
                    <input
                      className="proposal-input"
                      value={editValues.start_time || ''}
                      onChange={e => setEditValues({ ...editValues, start_time: e.target.value })}
                      placeholder="时间 (YYYY-MM-DD HH:MM)"
                    />
                    <input
                      className="proposal-input"
                      value={editValues.description || ''}
                      onChange={e => setEditValues({ ...editValues, description: e.target.value })}
                      placeholder="备注"
                    />
                  </>
                ) : (
                  <>
                    <input
                      className="proposal-input"
                      value={editValues.title || ''}
                      onChange={e => setEditValues({ ...editValues, title: e.target.value })}
                      placeholder="标题"
                    />
                    <textarea
                      className="proposal-input"
                      value={editValues.content || ''}
                      onChange={e => setEditValues({ ...editValues, content: e.target.value })}
                      placeholder="内容"
                      rows={2}
                    />
                    <input
                      className="proposal-input"
                      value={editValues.tags || ''}
                      onChange={e => setEditValues({ ...editValues, tags: e.target.value })}
                      placeholder="标签（逗号分隔）"
                    />
                  </>
                )}
              </div>
            ) : (
              <span className="proposal-text">
                <strong>{p.title}</strong>
                {p.time && <span style={{ marginLeft: 8, color: 'var(--color-text-muted)' }}>{p.time}</span>}
                {p.description && <span style={{ marginLeft: 8, color: 'var(--color-text-muted)', fontSize: 12 }}>{p.description}</span>}
                {p.content && <span style={{ marginLeft: 8, color: 'var(--color-text-muted)', fontSize: 12 }}>{p.content}</span>}
              </span>
            )}
            <div className="proposal-actions">
              {isEditing ? (
                <>
                  <button className="btn btn-sm btn-primary" onClick={() => submitEdit(p)}>保存</button>
                  <button className="btn btn-sm" onClick={() => setEditingId(null)}>取消</button>
                </>
              ) : (
                <>
                  <button className="btn btn-sm btn-primary" onClick={() => onConfirm(p.type, p.id)}>确认</button>
                  {onModify && <button className="btn btn-sm" onClick={() => startEdit(p)}>修改</button>}
                  <button className="btn btn-sm" onClick={() => onReject(p.type, p.id)}>忽略</button>
                </>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
