import { useState, useEffect } from 'react'
import { api, type Schedule } from '../shared/api'
import { TransformButton } from '../components/TransformButton'

const STATUS_COLORS: Record<string, string> = {
  proposed: '#f1fa8c', confirmed: '#50fa7b', done: '#8be9fd', cancelled: '#717e95',
}
const PRIORITY_COLORS: Record<string, string> = {
  low: '#6272a4', normal: '#bd93f9', high: '#ff5555',
}
const STATUS_BG_COLORS: Record<string, string> = {
  proposed: 'rgba(241,250,140,0.08)', confirmed: 'rgba(80,250,123,0.06)', done: 'rgba(139,233,253,0.05)', cancelled: 'rgba(113,126,149,0.04)',
}

const statusNames: Record<string, string> = {
  proposed: '待确认', confirmed: '已确认', done: '已完成', cancelled: '已取消',
}
const priorityNames: Record<string, string> = {
  low: '低', normal: '中', high: '高',
}
const statusIcons: Record<string, string> = {
  proposed: '⏳', confirmed: '✓', done: '✅', cancelled: '✗',
}

export default function SchedulesView() {
  const [schedules, setSchedules] = useState<Schedule[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editForm, setEditForm] = useState<Partial<Schedule>>({})
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [batchMode, setBatchMode] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  useEffect(() => { loadSchedules() }, [filter])

  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(null), 2000)
  }

  const loadSchedules = async () => {
    try {
      const list = await api.listSchedules(filter)
      setSchedules(list)
      setSelectedIds(new Set())
    } catch {} finally { setLoading(false) }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('删除此日程？')) return
    try {
      await api.deleteSchedule(id)
      setSchedules(prev => prev.filter(s => s.id !== id))
      showToast('已删除')
    } catch {}
  }

  const handleStatusChange = async (id: number, status: string) => {
    try {
      await api.updateSchedule(id, { status })
      setSchedules(prev => prev.map(s => s.id === id ? { ...s, status } : s))
      showToast(`${statusNames[status]}`)
    } catch {}
  }

  const handleConfirm = (id: number) => handleStatusChange(id, 'confirmed')
  const handleReject = (id: number) => handleStatusChange(id, 'cancelled')
  const handleDone = (id: number) => handleStatusChange(id, 'done')

  const startEdit = (s: Schedule) => {
    setEditingId(s.id)
    setEditForm({ title: s.title, description: s.description, start_time: s.start_time, end_time: s.end_time, location: s.location, priority: s.priority })
  }

  const saveEdit = async () => {
    if (editingId === null) return
    try {
      await api.updateSchedule(editingId, editForm)
      setSchedules(prev => prev.map(s => s.id === editingId ? { ...s, ...editForm } : s))
      setEditingId(null)
      setEditForm({})
      showToast('已保存')
    } catch {}
  }

  const cancelEdit = () => { setEditingId(null); setEditForm({}) }

  const formatDate = (dt: string) => {
    if (!dt) return ''
    const d = dt.replace('T', ' ').slice(0, 16)
    // 格式化为更友好的日期
    const parts = d.split(' ')
    if (parts.length === 2) {
      const datePart = parts[0] // 2026-07-16
      const timePart = parts[1] // 14:30
      const [y, m, day] = datePart.split('-')
      return `${m}/${day} ${timePart}`
    }
    return d
  }

  const formatTimeOnly = (dt: string) => {
    if (!dt) return ''
    return dt.slice(11, 16) // just HH:MM
  }

  // 批量操作
  const toggleSelect = (id: number) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  const toggleSelectAll = () => {
    if (selectedIds.size === schedules.length) setSelectedIds(new Set())
    else setSelectedIds(new Set(schedules.map(s => s.id)))
  }

  const handleBatchDelete = async () => {
    if (selectedIds.size === 0) return
    if (!confirm(`确认删除选中的 ${selectedIds.size} 条日程？`)) return
    const ids = Array.from(selectedIds)
    try {
      await Promise.all(ids.map(id => api.deleteSchedule(id)))
      setSchedules(prev => prev.filter(s => !selectedIds.has(s.id)))
      setSelectedIds(new Set())
      setBatchMode(false)
      showToast(`已删除 ${ids.length} 条`)
    } catch {}
  }

  const handleBatchStatus = async (status: string) => {
    if (selectedIds.size === 0) return
    const ids = Array.from(selectedIds)
    try {
      await Promise.all(ids.map(id => api.updateSchedule(id, { status })))
      setSchedules(prev => prev.map(s => selectedIds.has(s.id) ? { ...s, status } : s))
      setSelectedIds(new Set())
      showToast(`已设为${statusNames[status]} ${ids.length} 条`)
    } catch {}
  }

  const handleBatchConfirm = () => handleBatchStatus('confirmed')

  const exitBatchMode = () => { setBatchMode(false); setSelectedIds(new Set()) }
  const allSelected = schedules.length > 0 && selectedIds.size === schedules.length
  const partialSelected = selectedIds.size > 0 && selectedIds.size < schedules.length
  const proposedCount = schedules.filter(s => s.status === 'proposed').length

  // 按状态分组统计
  const statusCounts: Record<string, number> = {}
  schedules.forEach(s => { statusCounts[s.status] = (statusCounts[s.status] || 0) + 1 })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, height: '100%', overflowY: 'auto', padding: '12px 16px' }}>
      {/* Toast 反馈 */}
      {toast && (
        <div style={{
          position: 'fixed', top: 16, left: '50%', transform: 'translateX(-50%)',
          padding: '8px 20px', borderRadius: 8,
          background: 'var(--color-bg-panel)', border: '1px solid var(--color-accent-primary)',
          color: 'var(--color-accent-primary)', fontSize: 13, fontWeight: 600,
          boxShadow: 'var(--shadow-md)', zIndex: 999,
          animation: 'toastIn 0.3s ease',
        }}>
          {toast}
        </div>
      )}

      {/* 标题 + 待确认提示 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--color-text-primary)' }}>📋 日程管理</div>
        {proposedCount > 0 && (
          <span style={{
            background: STATUS_COLORS.proposed, color: '#000', padding: '2px 10px',
            borderRadius: 12, fontSize: 11, fontWeight: 700,
          }}>
            {proposedCount} 条待确认
          </span>
        )}
      </div>

      {/* 工具栏 */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <select
          className="form-select"
          style={{ width: 130, fontSize: 12 }}
          value={filter}
          onChange={e => setFilter(e.target.value)}
        >
          <option value="">全部状态</option>
          {Object.entries(statusNames).map(([k, v]) => (
            <option key={k} value={k}>{v} ({statusCounts[k] || 0})</option>
          ))}
        </select>
        <span style={{ color: 'var(--color-text-muted)', fontSize: 11 }}>
          共 {schedules.length} 条
        </span>
        <div style={{ flex: 1 }} />
        {!batchMode ? (
          <>
            {proposedCount > 0 && (
              <button className="btn btn-sm" onClick={handleBatchConfirm}
                style={{ background: STATUS_COLORS.proposed, color: '#000', fontWeight: 600, fontSize: 11 }}>
                ✓ 全部确认
              </button>
            )}
            <button className="btn btn-sm" onClick={() => setBatchMode(true)} disabled={schedules.length === 0} style={{ opacity: schedules.length === 0 ? 0.4 : 1 }}>
              ☑ 批量
            </button>
          </>
        ) : (
          <button className="btn btn-sm" onClick={exitBatchMode}>退出批量</button>
        )}
      </div>

      {/* 批量操作栏 */}
      {batchMode && (
        <div style={{
          padding: '8px 12px', background: 'var(--color-bg-panel)',
          border: '1px solid var(--color-accent-primary)', borderRadius: 8,
          display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap',
        }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 12 }}>
            <input type="checkbox" checked={allSelected} ref={el => { if (el) el.indeterminate = partialSelected }} onChange={toggleSelectAll} style={{ width: 16, height: 16 }} />
            {allSelected ? '取消全选' : '全选'}
          </label>
          <span style={{ color: 'var(--color-text-muted)', fontSize: 11 }}>已选 {selectedIds.size}/{schedules.length}</span>
          <div style={{ flex: 1 }} />
          {selectedIds.size > 0 && (
            <>
              <button className="btn btn-sm" style={{ background: STATUS_COLORS.confirmed, color: '#000', fontWeight: 600 }}
                onClick={handleBatchConfirm}>
                ✓ 确认所选
              </button>
              <button className="btn btn-sm" style={{ background: STATUS_COLORS.cancelled, color: '#fff' }}
                onClick={() => handleBatchStatus('cancelled')}>
                ✗ 取消所选
              </button>
              <button className="btn btn-sm" style={{ background: STATUS_COLORS.done, color: '#000' }}
                onClick={() => handleBatchStatus('done')}>
                ✅ 完成所选
              </button>
            </>
          )}
          <button className="btn btn-sm btn-danger" style={{ opacity: selectedIds.size === 0 ? 0.4 : 1 }} onClick={handleBatchDelete} disabled={selectedIds.size === 0}>
            删除 ({selectedIds.size})
          </button>
        </div>
      )}

      {/* 日程列表 */}
      {loading ? (
        <div className="spinner"><div className="spinner-dot" /><div className="spinner-dot" /><div className="spinner-dot" /></div>
      ) : schedules.length === 0 ? (
        <div className="empty-state">
          <p style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>暂无日程。通过对话让 AI 创建。</p>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {schedules.map(s => {
            const isProposed = s.status === 'proposed'
            const isCancelled = s.status === 'cancelled'
            const borderColor = STATUS_COLORS[s.status] || '#717e95'
            const bgColor = STATUS_BG_COLORS[s.status] || 'var(--color-bg-panel)'
            const priorityColor = PRIORITY_COLORS[s.priority] || '#6272a4'

            return (
              <div key={s.id} style={{
                padding: '10px 14px',
                background: selectedIds.has(s.id) ? 'rgba(80,250,123,0.08)' : bgColor,
                borderLeft: `4px solid ${borderColor}`,
                borderRight: selectedIds.has(s.id) ? '1px solid #50fa7b' : '1px solid var(--color-border)',
                borderTop: selectedIds.has(s.id) ? '1px solid #50fa7b' : '1px solid var(--color-border)',
                borderBottom: selectedIds.has(s.id) ? '1px solid #50fa7b' : '1px solid var(--color-border)',
                borderRadius: 6, opacity: isCancelled ? 0.5 : 1,
                display: 'flex', alignItems: 'flex-start', gap: 10,
                transition: 'all 0.15s',
              }}
              onMouseEnter={e => { if (!selectedIds.has(s.id)) e.currentTarget.style.background = 'var(--color-bg-hover)' }}
              onMouseLeave={e => { if (!selectedIds.has(s.id)) e.currentTarget.style.background = bgColor }}
              >
                {batchMode && editingId !== s.id && (
                  <input type="checkbox" checked={selectedIds.has(s.id)} onChange={() => toggleSelect(s.id)} style={{ width: 16, height: 16, marginTop: 3, flexShrink: 0, accentColor: borderColor }} />
                )}

                {editingId === s.id ? (
                  <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6 }}>
                    <input className="form-input" value={editForm.title || ''} onChange={e => setEditForm(f => ({ ...f, title: e.target.value }))} placeholder="标题" />
                    <textarea className="form-input" value={editForm.description || ''} onChange={e => setEditForm(f => ({ ...f, description: e.target.value }))} placeholder="描述" rows={2} />
                    <div style={{ display: 'flex', gap: 6 }}>
                      <input className="form-input" value={editForm.start_time || ''} onChange={e => setEditForm(f => ({ ...f, start_time: e.target.value }))} placeholder="开始时间" />
                      <input className="form-input" value={editForm.end_time || ''} onChange={e => setEditForm(f => ({ ...f, end_time: e.target.value }))} placeholder="结束时间" />
                    </div>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <input className="form-input" value={editForm.location || ''} onChange={e => setEditForm(f => ({ ...f, location: e.target.value }))} placeholder="地点" />
                      <select className="form-select" value={editForm.priority || 'normal'} onChange={e => setEditForm(f => ({ ...f, priority: e.target.value }))}>
                        <option value="low">低</option><option value="normal">中</option><option value="high">高</option>
                      </select>
                    </div>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button className="btn btn-sm" style={{ background: 'var(--color-accent-primary)', color: '#fff' }} onClick={saveEdit}>保存</button>
                      <button className="btn btn-sm" onClick={cancelEdit}>取消</button>
                    </div>
                  </div>
                ) : (
                  <>
                    {/* 状态图标列 */}
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2, minWidth: 28, flexShrink: 0 }}>
                      <span style={{ fontSize: 16 }}>{statusIcons[s.status] || '•'}</span>
                      <span style={{ fontSize: 9, color: borderColor, fontWeight: 600 }}>{statusNames[s.status]}</span>
                    </div>

                    {/* 优先级标记 */}
                    <div style={{
                      width: 3, height: 36, borderRadius: 2, background: priorityColor, flexShrink: 0,
                      alignSelf: 'center', opacity: s.priority === 'normal' ? 0.3 : 0.8,
                    }} />

                    {/* 内容区 */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--color-text-primary)', display: 'flex', alignItems: 'center', gap: 6 }}>
                        {s.title}
                        {s.priority === 'high' && <span style={{ fontSize: 9, background: PRIORITY_COLORS.high, color: '#fff', padding: '1px 5px', borderRadius: 3, fontWeight: 700 }}>紧急</span>}
                      </div>
                      {s.description && (
                        <div style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginTop: 3, lineHeight: 1.5 }}>{s.description}</div>
                      )}
                      <div style={{ marginTop: 5, fontSize: 11, color: 'var(--color-text-muted)', display: 'flex', flexWrap: 'wrap', gap: '4px 10px', alignItems: 'center' }}>
                        {s.start_time && (
                          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                            {formatDate(s.start_time)}{s.end_time ? ` → ${formatTimeOnly(s.end_time)}` : ''}
                          </span>
                        )}
                        {s.location && <span>📍 {s.location}</span>}
                        {s.source && s.source !== 'manual' && <span style={{ fontSize: 10, opacity: 0.7 }}>来源: {s.source}</span>}
                      </div>
                    </div>

                    {/* 操作区 */}
                    {!batchMode && (
                      <div style={{ display: 'flex', gap: 4, flexShrink: 0, alignItems: 'flex-start' }}>
                        {isProposed ? (
                          /* 待确认：醒目确认/拒绝按钮 */
                          <div style={{ display: 'flex', gap: 4 }}>
                            <button
                              style={{ padding: '4px 12px', borderRadius: 5, background: STATUS_COLORS.confirmed, color: '#000', fontWeight: 700, fontSize: 12, border: 'none', cursor: 'pointer', transition: 'all 0.15s' }}
                              onClick={() => handleConfirm(s.id)}
                              onMouseEnter={e => e.currentTarget.style.background = '#3deb6a'}
                              onMouseLeave={e => e.currentTarget.style.background = STATUS_COLORS.confirmed}
                            >
                              ✓ 确认
                            </button>
                            <button
                              style={{ padding: '4px 10px', borderRadius: 5, background: 'rgba(255,85,85,0.15)', color: '#ff5555', fontWeight: 600, fontSize: 12, border: '1px solid rgba(255,85,85,0.3)', cursor: 'pointer', transition: 'all 0.15s' }}
                              onClick={() => handleReject(s.id)}
                              onMouseEnter={e => { e.currentTarget.style.background = '#ff5555'; e.currentTarget.style.color = '#fff' }}
                              onMouseLeave={e => { e.currentTarget.style.background = 'rgba(255,85,85,0.15)'; e.currentTarget.style.color = '#ff5555' }}
                            >
                              ✗ 拒绝
                            </button>
                          </div>
                        ) : s.status === 'confirmed' ? (
                          /* 已确认：完成按钮 */
                          <button
                            style={{ padding: '3px 10px', borderRadius: 5, background: 'rgba(139,233,253,0.12)', color: '#8be9fd', fontWeight: 600, fontSize: 11, border: '1px solid rgba(139,233,253,0.3)', cursor: 'pointer', transition: 'all 0.15s' }}
                            onClick={() => handleDone(s.id)}
                            onMouseEnter={e => { e.currentTarget.style.background = '#8be9fd'; e.currentTarget.style.color = '#000' }}
                            onMouseLeave={e => { e.currentTarget.style.background = 'rgba(139,233,253,0.12)'; e.currentTarget.style.color = '#8be9fd' }}
                          >
                            ✅ 完成
                          </button>
                        ) : (
                          /* 其他状态：下拉改状态 */
                          <select value={s.status} onChange={e => handleStatusChange(s.id, e.target.value)} style={{ fontSize: 10, padding: '2px 4px', background: 'var(--color-bg-input)', border: '1px solid var(--color-border)', borderRadius: 4, color: 'var(--color-text-primary)' }}>
                            {Object.entries(statusNames).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                          </select>
                        )}
                        <TransformButton sourceType="schedule" sourceId={s.id} onTransformed={() => { loadSchedules(); showToast('已转化并创建') }} />
                        <button className="btn-icon" style={{ width: 24, height: 24, fontSize: 11 }} onClick={() => startEdit(s)} title="编辑">✎</button>
                        <button className="btn-icon" style={{ width: 24, height: 24, fontSize: 11, color: 'var(--color-accent-danger)' }} onClick={() => handleDelete(s.id)} title="删除">×</button>
                      </div>
                    )}
                  </>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
