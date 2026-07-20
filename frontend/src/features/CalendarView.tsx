import { useState, useEffect, useMemo, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type Schedule, type CalendarTemplate, type Goal, type GoalStats } from '../shared/api'
import { TransformButton } from '../components/TransformButton'
import GoalDetailModal from '../components/GoalDetailModal'
import { useCalendarGoal } from '../contexts/CalendarGoalContext'
import {
  STATUS_COLORS, STATUS_BG_COLORS, STATUS_NAMES, STATUS_ICONS,
  PRIORITY_COLORS, PRIORITY_NAMES, CATEGORY_LABELS, IMPACT_COLORS, IMPACT_LABELS,
  isScheduleOverdue, sortSchedules, formatDateTime,
} from '../shared/scheduleHelpers'

const GOAL_COLORS = ['#50fa7b', '#8be9fd', '#ff79c6', '#f1fa8c', '#bd93f9']

function formatMoney(v: number): string {
  if (v >= 100000) return `${(v / 10000).toFixed(1)}万`
  if (v >= 10000) return `${(v / 10000).toFixed(2)}万`
  return v.toFixed(0)
}

function formatMoneyShort(v: number): string {
  if (v >= 10000) return `${(v / 10000).toFixed(1)}万`
  return v.toFixed(0)
}

function formatDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function getToday(): string {
  return formatDate(new Date())
}

function getWeekRange(date: Date): { monday: Date; sunday: Date; mondayStr: string; sundayStr: string } {
  const dow = date.getDay() || 7 // Sunday=7 for ISO
  const monday = new Date(date)
  monday.setDate(date.getDate() - dow + 1)
  const sunday = new Date(monday)
  sunday.setDate(monday.getDate() + 6)
  return { monday, sunday, mondayStr: formatDate(monday), sundayStr: formatDate(sunday) }
}

export default function CalendarView() {
  const navigate = useNavigate()
  const [refDate, setRefDate] = useState(() => new Date())
  const [events, setEvents] = useState<Schedule[]>([])
  const [selectedDate, setSelectedDate] = useState(getToday())
  const [loading, setLoading] = useState(false)
  const [templates, setTemplates] = useState<CalendarTemplate[]>([])
  const [showCreate, setShowCreate] = useState(false)
  const [editingEvent, setEditingEvent] = useState<Schedule | null>(null)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState<Schedule | null>(null)

  // 列表视图状态
  const [viewMode, setViewMode] = useState<'week' | 'list'>('week')
  const [listFilter, setListFilter] = useState('')
  const [listLoading, setListLoading] = useState(false)
  const [allSchedules, setAllSchedules] = useState<Schedule[]>([])
  const [batchMode, setBatchMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [listEditingId, setListEditingId] = useState<number | null>(null)
  const [listEditForm, setListEditForm] = useState<Partial<Schedule>>({})
  const [listToast, setListToast] = useState<string | null>(null)
  const [showListDelete, setShowListDelete] = useState<Schedule | null>(null)

  // Form state
  const [formTitle, setFormTitle] = useState('')
  const [formDate, setFormDate] = useState(selectedDate)
  const [formTime, setFormTime] = useState('')
  const [formCategory, setFormCategory] = useState('economic')
  const [formImportance, setFormImportance] = useState(3)
  const [formImpact, setFormImpact] = useState('')
  const [formCountry, setFormCountry] = useState('')
  const [formRemind, setFormRemind] = useState(0)

  const [formGoalId, setFormGoalId] = useState<number | null>(null)
  const [formRecurrence, setFormRecurrence] = useState('')

  // S1 灰度收敛：目标状态来自 Context
  const { goals: ctxGoals, goalStats: ctxStats, loadGoals: reloadGoals } = useCalendarGoal()
  const [selectedGoalId, setSelectedGoalId] = useState<number | null>(null)
  // S4：目标详情弹窗统一入口
  const [detailGoal, setDetailGoal] = useState<Goal | null>(null)

  const { monday, sunday, mondayStr, sundayStr } = useMemo(() => getWeekRange(refDate), [refDate])

  // Build week days
  const weekDays = useMemo(() => {
    const days = []
    const dayNames = ['一', '二', '三', '四', '五', '六', '日']
    for (let i = 0; i < 7; i++) {
      const d = new Date(monday)
      d.setDate(monday.getDate() + i)
      const ds = formatDate(d)
      const count = events.filter(e => (e.start_time || '').startsWith(ds)).length
      // 只收集激活该日期的目标（未激活不显示）
      const activeGoals = ctxGoals
        .map((g, idx) => ({ g, idx, color: GOAL_COLORS[idx % GOAL_COLORS.length] }))
        .filter(({ g }) => (g.active_days || []).includes(ds))
      days.push({
        dayName: dayNames[i],
        day: d.getDate(),
        date: ds,
        isToday: ds === getToday(),
        count,
        activeGoals,
      })
    }
    return days
  }, [monday, events, ctxGoals])

  // 日期到激活目标的映射（用于详情面板）
  const activeGoalsForSelected = useMemo(() => {
    return ctxGoals
      .map((g, idx) => ({ g, idx, color: GOAL_COLORS[idx % GOAL_COLORS.length] }))
      .filter(({ g }) => (g.active_days || []).includes(selectedDate))
  }, [ctxGoals, selectedDate])

  const displayEvents = useMemo(() => {
    const today = getToday()
    const now = new Date()
    const nowTime = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`
    const isToday = selectedDate === today

    return events
      .filter(e => (e.start_time || '').startsWith(selectedDate))
      .map(e => {
        const timeStr = e.start_time ? e.start_time.slice(11, 16) : ''
        const expired = isToday && timeStr && timeStr < nowTime
        return { ...e, timeStr, expired }
      })
      .sort((a, b) => {
        if (a.expired !== b.expired) return a.expired ? 1 : -1
        return (a.timeStr || '99:99').localeCompare(b.timeStr || '99:99')
      })
  }, [events, selectedDate])

  // Load events for the week
  const loadEvents = async () => {
    setLoading(true)
    try {
      const data = await api.getCalendarWeek(mondayStr)
      setEvents(data.events || [])
    } catch { /* silent */ }
    finally { setLoading(false) }
  }

  const loadTemplates = async () => {
    try {
      const t = await api.getCalendarTemplates()
      setTemplates(t)
    } catch { /* silent */ }
  }

  // 加载全部日程（列表视图）
  const loadAllSchedules = useCallback(async () => {
    setListLoading(true)
    try {
      const list = await api.listSchedules(listFilter)
      const now = new Date().toISOString()
      const filtered = listFilter ? list : list.filter((s: Schedule) => {
        if (s.status === 'done' || s.status === 'cancelled') return false
        if (s.end_time && s.end_time < now && (s.status === 'proposed' || s.status === 'confirmed')) return false
        return true
      })
      setAllSchedules(filtered)
      setSelectedIds(new Set())
    } catch { /* silent */ } finally { setListLoading(false) }
  }, [listFilter])

  useEffect(() => { loadEvents() }, [mondayStr])
  useEffect(() => { loadTemplates() }, [])
  // 目标数据由 CalendarGoalContext 提供，不再本地加载

  useEffect(() => { if (viewMode === 'list') loadAllSchedules() }, [viewMode, loadAllSchedules])

  // Navigation
  const prevWeek = () => {
    const d = new Date(monday); d.setDate(d.getDate() - 7); setRefDate(d)
  }
  const nextWeek = () => {
    const d = new Date(monday); d.setDate(d.getDate() + 7); setRefDate(d)
  }
  const goToday = () => {
    setRefDate(new Date()); setSelectedDate(getToday())
  }

  // Open create form
  const openCreate = (date?: string) => {
    const d = date || selectedDate
    setEditingEvent(null)
    setFormTitle('')
    setFormDate(d)
    setFormTime('')
    setFormCategory('economic')
    setFormImportance(3)
    setFormImpact('')
    setFormCountry('')
    setFormRemind(0)
    setFormGoalId(null)
    setFormRecurrence('')
    setShowCreate(true)
  }

  // Open edit form
  const openEdit = (e: Schedule) => {
    setEditingEvent(e)
    setFormTitle(e.title)
    setFormDate((e.start_time || '').slice(0, 10))
    setFormTime((e.start_time || '').slice(11, 16))
    setFormCategory(e.category || 'other')
    setFormImportance(e.importance || 3)
    setFormImpact(e.impact || '')
    setFormCountry(e.country || '')
    setFormRemind(e.remind_before || 0)
    setFormGoalId(e.goal_id || null)
    setFormRecurrence(e.recurrence || '')
    setShowCreate(true)
  }

  // Apply template
  const applyTemplate = (tpl: CalendarTemplate) => {
    setFormTitle(tpl.title)
    setFormCategory(tpl.category)
    setFormImportance(tpl.importance)
    setFormRemind(tpl.remind_before)
    if (tpl.default_time) setFormTime(tpl.default_time)
  }

  // Submit form
  const handleSubmit = async () => {
    if (!formTitle.trim()) return
    const payload: Partial<Schedule> = {
      title: formTitle.trim(),
      start_time: formDate + (formTime ? `T${formTime}:00` : 'T00:00:00'),
      category: formCategory,
      importance: formImportance,
      impact: formImpact || '',
      country: formCountry,
      remind_before: formRemind,
      goal_id: formGoalId,
      recurrence: formRecurrence,
    }

    try {
      if (editingEvent) {
        await api.updateSchedule(editingEvent.id, payload)
      } else {
        await api.createSchedule(payload)
      }
      setShowCreate(false)
      loadEvents()
    } catch (err) {
      console.error('保存失败', err)
    }
  }

  // Delete
  const handleDelete = async () => {
    if (!showDeleteConfirm) return
    try {
      await api.deleteSchedule(showDeleteConfirm.id)
      setShowDeleteConfirm(null)
      loadEvents()
    } catch (err) {
      console.error('删除失败', err)
    }
  }

  // ===== 列表视图操作 =====
  const showToast = (msg: string) => { setListToast(msg); setTimeout(() => setListToast(null), 2000) }

  const handleListStatusChange = async (id: number, status: string) => {
    try {
      await api.updateSchedule(id, { status })
      if (status === 'done' || status === 'cancelled') {
        setAllSchedules(prev => prev.filter(s => s.id !== id))
      } else {
        setAllSchedules(prev => prev.map(s => s.id === id ? { ...s, status } : s))
      }
      showToast(`${STATUS_NAMES[status] || status}`)
    } catch {}
  }

  const handleListConfirm = (id: number) => handleListStatusChange(id, 'confirmed')
  const handleListReject = (id: number) => handleListStatusChange(id, 'cancelled')
  const handleListDone = (id: number) => handleListStatusChange(id, 'done')

  const startListEdit = (s: Schedule) => {
    setListEditingId(s.id)
    setListEditForm({ title: s.title, description: s.description, start_time: s.start_time, end_time: s.end_time, location: s.location, priority: s.priority })
  }

  const saveListEdit = async () => {
    if (listEditingId === null) return
    try {
      await api.updateSchedule(listEditingId, listEditForm)
      setAllSchedules(prev => prev.map(s => s.id === listEditingId ? { ...s, ...listEditForm } : s))
      setListEditingId(null)
      setListEditForm({})
      showToast('已保存')
    } catch {}
  }

  const cancelListEdit = () => { setListEditingId(null); setListEditForm({}) }

  const handleListDelete = async () => {
    if (!showListDelete) return
    try {
      await api.deleteSchedule(showListDelete.id)
      setAllSchedules(prev => prev.filter(s => s.id !== showListDelete.id))
      setShowListDelete(null)
      showToast('已删除')
    } catch {}
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
    if (selectedIds.size === allSchedules.length) setSelectedIds(new Set())
    else setSelectedIds(new Set(allSchedules.map(s => s.id)))
  }

  const handleBatchDelete = async () => {
    if (selectedIds.size === 0) return
    if (!confirm(`确认删除选中的 ${selectedIds.size} 条日程？`)) return
    const ids = Array.from(selectedIds)
    try {
      await Promise.all(ids.map(id => api.deleteSchedule(id)))
      setAllSchedules(prev => prev.filter(s => !selectedIds.has(s.id)))
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
      if (status === 'done' || status === 'cancelled') {
        setAllSchedules(prev => prev.filter(s => !selectedIds.has(s.id)))
      } else {
        setAllSchedules(prev => prev.map(s => selectedIds.has(s.id) ? { ...s, status } : s))
      }
      setSelectedIds(new Set())
      showToast(`已设为${STATUS_NAMES[status] || status} ${ids.length} 条`)
    } catch {}
  }

  const handleBatchConfirm = () => handleBatchStatus('confirmed')
  const exitBatchMode = () => { setBatchMode(false); setSelectedIds(new Set()) }
  const allSelected = allSchedules.length > 0 && selectedIds.size === allSchedules.length
  const partialSelected = selectedIds.size > 0 && selectedIds.size < allSchedules.length
  const proposedCount = allSchedules.filter(s => s.status === 'proposed').length

  const formatListDate = (dt: string) => {
    if (!dt) return ''
    const d = dt.replace('T', ' ').slice(0, 16)
    const parts = d.split(' ')
    if (parts.length === 2) {
      const [y, m, day] = parts[0].split('-')
      return `${m}/${day} ${parts[1]}`
    }
    return d
  }

  const formatTimeOnly = (dt: string) => dt ? dt.slice(11, 16) : ''

  const statusCounts: Record<string, number> = {}
  allSchedules.forEach(s => { statusCounts[s.status] = (statusCounts[s.status] || 0) + 1 })

  const weekLabel = `${monday.getMonth() + 1}月${monday.getDate()}日 - ${sunday.getMonth() + 1}月${sunday.getDate()}日`

  return (
    <>
      <div className="main-area" style={{ maxWidth: '100vw' }}>
        {/* Toast */}
        {listToast && (
          <div style={{
            position: 'fixed', top: 16, left: '50%', transform: 'translateX(-50%)',
            padding: '8px 20px', borderRadius: 8,
            background: 'var(--color-bg-panel)', border: '1px solid var(--color-accent-primary)',
            color: 'var(--color-accent-primary)', fontSize: 13, fontWeight: 600,
            zIndex: 999,
          }}>
            {listToast}
          </div>
        )}

        <div className="calendar-container">
          {/* Header */}
          <div className="cal-header">
            <button className="cal-nav-btn" onClick={prevWeek}>‹</button>
            <span className="cal-week-label" onClick={goToday} style={{ cursor: 'pointer' }}>{weekLabel}</span>
            <button className="cal-nav-btn" onClick={nextWeek}>›</button>
            <button className="btn btn-sm" onClick={goToday} style={{ marginLeft: 8 }}>今天</button>
            <div style={{ flex: 1 }} />
            <button
              className="btn btn-sm"
              onClick={() => setViewMode('week')}
              style={{ background: viewMode === 'week' ? 'var(--color-accent-primary)' : 'var(--color-bg-input)', color: viewMode === 'week' ? '#fff' : 'var(--color-text-muted)' }}
            >
              周视图
            </button>
            <button
              className="btn btn-sm"
              onClick={() => setViewMode('list')}
              style={{ background: viewMode === 'list' ? 'var(--color-accent-primary)' : 'var(--color-bg-input)', color: viewMode === 'list' ? '#fff' : 'var(--color-text-muted)' }}
            >
              列表
            </button>
          </div>

          {viewMode === 'week' ? (
            <>
              {/* Date strip */}
              <div className="cal-date-strip">
                {weekDays.map(d => (
                  <div
                    key={d.date}
                    className={`cal-date-cell ${d.date === selectedDate ? 'cal-selected' : ''} ${d.isToday ? 'cal-today' : ''}`}
                    onClick={() => setSelectedDate(d.date)}
                  >
                    <span className="cal-weekday">{d.dayName}</span>
                    <span className="cal-daynum">{d.day}</span>
                    {d.count > 0 && <span className="cal-badge">{d.count}</span>}
                    {d.activeGoals.length > 0 && (
                      <div style={{ display: 'flex', justifyContent: 'center', gap: 2, marginTop: 3, flexWrap: 'wrap' }}>
                        {d.activeGoals.map((ag, i) => (
                          <div key={i} style={{ width: 6, height: 6, borderRadius: '50%', background: ag.color }} title={ag.g.title} />
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>

              {/* 目标追踪面板（直接嵌入主日历页面） */}
              <GoalTrackerPanel
                goals={ctxGoals}
                stats={ctxStats}
                selectedDate={selectedDate}
                activeGoalsForDate={activeGoalsForSelected}
                onUpdate={reloadGoals}
                onOpenDetail={(g) => setDetailGoal(g)}
              />

              {/* Add button + 快速创建 */}
              <div className="cal-toolbar">
                <button className="btn btn-accent" onClick={() => openCreate()}>
                  + 添加提醒
                </button>
                <input
                  type="text"
                  placeholder="或输入标题快速创建（Enter）..."
                  style={{
                    flex: 1, marginLeft: 8, padding: '6px 10px', borderRadius: 5,
                    background: 'var(--color-bg-input)', border: '1px solid var(--color-border)',
                    color: 'var(--color-text-primary)', fontSize: 12,
                  }}
                  onKeyDown={async (e) => {
                    if (e.key === 'Enter') {
                      const val = (e.target as HTMLInputElement).value.trim()
                      if (!val) return
                      try {
                        await api.createSchedule({
                          title: val,
                          start_time: `${selectedDate} 09:00`,
                          end_time: `${selectedDate} 10:00`,
                          category: 'reminder',
                          importance: 3,
                          status: 'confirmed',
                        })
                        ;(e.target as HTMLInputElement).value = ''
                        loadEvents()
                      } catch (err) { console.error('快速创建失败', err) }
                    }
                  }}
                />
                {loading && <span className="cal-loading">加载中...</span>}
              </div>

              {/* Event list */}
              <div className="cal-event-list">
                {displayEvents.length === 0 ? (
                  <div className="cal-empty">
                    <span style={{ fontSize: 32 }}>📅</span>
                    <span>当日暂无提醒</span>
                  </div>
                ) : (
                  displayEvents.map(ev => (
                    <div
                      key={ev.id}
                      className={`cal-event-card ${ev.expired ? 'cal-expired' : ''}`}
                      onClick={() => openEdit(ev)}
                    >
                      <div className="cal-event-left">
                        <span className="cal-event-time">{ev.timeStr || '全天'}</span>
                        <span className="cal-event-stars">
                          {Array.from({ length: 5 }, (_, i) => (
                            <span key={i} className={i < (ev.importance || 3) ? 'star-filled' : 'star-empty'}>
                              ★
                            </span>
                          ))}
                        </span>
                      </div>
                      <div className="cal-event-center">
                        <span className="cal-event-title">{ev.title}</span>
                        <div className="cal-event-tags">
                          {ev.country && <span className="cal-tag">{ev.country}</span>}
                          <span className="cal-tag cal-tag-cat">{CATEGORY_LABELS[ev.category] || ev.category}</span>
                        </div>
                      </div>
                      {ev.impact && (
                        <div className="cal-event-impact" style={{ background: IMPACT_COLORS[ev.impact] + '22', color: IMPACT_COLORS[ev.impact], border: `1px solid ${IMPACT_COLORS[ev.impact]}44` }}>
                          {IMPACT_LABELS[ev.impact] || ev.impact}
                        </div>
                      )}
                      <button
                        className="cal-delete-btn"
                        onClick={(e) => { e.stopPropagation(); setShowDeleteConfirm(ev) }}
                        title="删除"
                      >
                        ✕
                      </button>
                    </div>
                  ))
                )}
              </div>
            </>
          ) : (
            <>
              {/* 列表视图工具栏 */}
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
                <select
                  className="form-select"
                  style={{ width: 130, fontSize: 12 }}
                  value={listFilter}
                  onChange={e => setListFilter(e.target.value)}
                >
                  <option value="">全部状态</option>
                  {Object.entries(STATUS_NAMES).map(([k, v]) => (
                    <option key={k} value={k}>{v} ({statusCounts[k] || 0})</option>
                  ))}
                </select>
                <span style={{ color: 'var(--color-text-muted)', fontSize: 11 }}>共 {allSchedules.length} 条</span>
                <div style={{ flex: 1 }} />
                {!batchMode ? (
                  <>
                    {proposedCount > 0 && (
                      <button className="btn btn-sm" onClick={handleBatchConfirm}
                        style={{ background: STATUS_COLORS.proposed, color: '#000', fontWeight: 600, fontSize: 11 }}>
                        ✓ 全部确认
                      </button>
                    )}
                    <button className="btn btn-sm" onClick={() => setBatchMode(true)} disabled={allSchedules.length === 0} style={{ opacity: allSchedules.length === 0 ? 0.4 : 1 }}>
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
                  display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12,
                }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 12 }}>
                    <input type="checkbox" checked={allSelected} ref={el => { if (el) el.indeterminate = partialSelected }} onChange={toggleSelectAll} style={{ width: 16, height: 16 }} />
                    {allSelected ? '取消全选' : '全选'}
                  </label>
                  <span style={{ color: 'var(--color-text-muted)', fontSize: 11 }}>已选 {selectedIds.size}/{allSchedules.length}</span>
                  <div style={{ flex: 1 }} />
                  {selectedIds.size > 0 && (
                    <>
                      <button className="btn btn-sm" style={{ background: STATUS_COLORS.confirmed, color: '#000', fontWeight: 600 }} onClick={handleBatchConfirm}>✓ 确认所选</button>
                      <button className="btn btn-sm" style={{ background: STATUS_COLORS.cancelled, color: '#fff' }} onClick={() => handleBatchStatus('cancelled')}>✗ 取消所选</button>
                      <button className="btn btn-sm" style={{ background: STATUS_COLORS.done, color: '#000' }} onClick={() => handleBatchStatus('done')}>✅ 完成所选</button>
                    </>
                  )}
                  <button className="btn btn-sm btn-danger" style={{ opacity: selectedIds.size === 0 ? 0.4 : 1 }} onClick={handleBatchDelete} disabled={selectedIds.size === 0}>
                    删除 ({selectedIds.size})
                  </button>
                </div>
              )}

              {/* 日程列表 */}
              {listLoading ? (
                <div className="spinner"><div className="spinner-dot" /><div className="spinner-dot" /><div className="spinner-dot" /></div>
              ) : allSchedules.length === 0 ? (
                <div className="empty-state">
                  <p style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>暂无日程。通过对话让 AI 创建。</p>
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {allSchedules.map(s => {
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
                        {batchMode && listEditingId !== s.id && (
                          <input type="checkbox" checked={selectedIds.has(s.id)} onChange={() => toggleSelect(s.id)} style={{ width: 16, height: 16, marginTop: 3, flexShrink: 0, accentColor: borderColor }} />
                        )}

                        {listEditingId === s.id ? (
                          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6 }}>
                            <input className="form-input" value={listEditForm.title || ''} onChange={e => setListEditForm(f => ({ ...f, title: e.target.value }))} placeholder="标题" />
                            <textarea className="form-input" value={listEditForm.description || ''} onChange={e => setListEditForm(f => ({ ...f, description: e.target.value }))} placeholder="描述" rows={2} />
                            <div style={{ display: 'flex', gap: 6 }}>
                              <input className="form-input" value={listEditForm.start_time || ''} onChange={e => setListEditForm(f => ({ ...f, start_time: e.target.value }))} placeholder="开始时间" />
                              <input className="form-input" value={listEditForm.end_time || ''} onChange={e => setListEditForm(f => ({ ...f, end_time: e.target.value }))} placeholder="结束时间" />
                            </div>
                            <div style={{ display: 'flex', gap: 6 }}>
                              <input className="form-input" value={listEditForm.location || ''} onChange={e => setListEditForm(f => ({ ...f, location: e.target.value }))} placeholder="地点" />
                              <select className="form-select" value={listEditForm.priority || 'normal'} onChange={e => setListEditForm(f => ({ ...f, priority: e.target.value }))}>
                                <option value="low">低</option><option value="normal">中</option><option value="high">高</option>
                              </select>
                            </div>
                            <div style={{ display: 'flex', gap: 6 }}>
                              <button className="btn btn-sm" style={{ background: 'var(--color-accent-primary)', color: '#fff' }} onClick={saveListEdit}>保存</button>
                              <button className="btn btn-sm" onClick={cancelListEdit}>取消</button>
                            </div>
                          </div>
                        ) : (
                          <>
                            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2, minWidth: 28, flexShrink: 0 }}>
                              <span style={{ fontSize: 16 }}>{STATUS_ICONS[s.status] || '•'}</span>
                              <span style={{ fontSize: 9, color: borderColor, fontWeight: 600 }}>{STATUS_NAMES[s.status]}</span>
                            </div>

                            <div style={{ width: 3, height: 36, borderRadius: 2, background: priorityColor, flexShrink: 0, alignSelf: 'center', opacity: s.priority === 'normal' ? 0.3 : 0.8 }} />

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
                                    {formatListDate(s.start_time)}{s.end_time ? ` → ${formatTimeOnly(s.end_time)}` : ''}
                                  </span>
                                )}
                                {s.location && <span>📍 {s.location}</span>}
                                {s.source && s.source !== 'manual' && <span style={{ fontSize: 10, opacity: 0.7 }}>来源: {s.source}</span>}
                              </div>
                            </div>

                            {!batchMode && (
                              <div style={{ display: 'flex', gap: 4, flexShrink: 0, alignItems: 'flex-start' }}>
                                {isProposed ? (
                                  <div style={{ display: 'flex', gap: 4 }}>
                                    <button
                                      style={{ padding: '4px 12px', borderRadius: 5, background: STATUS_COLORS.confirmed, color: '#000', fontWeight: 700, fontSize: 12, border: 'none', cursor: 'pointer' }}
                                      onClick={() => handleListConfirm(s.id)}
                                    >✓ 确认</button>
                                    <button
                                      style={{ padding: '4px 10px', borderRadius: 5, background: 'rgba(255,85,85,0.15)', color: '#ff5555', fontWeight: 600, fontSize: 12, border: '1px solid rgba(255,85,85,0.3)', cursor: 'pointer' }}
                                      onClick={() => handleListReject(s.id)}
                                    >✗ 拒绝</button>
                                  </div>
                                ) : s.status === 'confirmed' ? (
                                  <button
                                    style={{ padding: '3px 10px', borderRadius: 5, background: 'rgba(139,233,253,0.12)', color: '#8be9fd', fontWeight: 600, fontSize: 11, border: '1px solid rgba(139,233,253,0.3)', cursor: 'pointer' }}
                                    onClick={() => handleListDone(s.id)}
                                  >✅ 完成</button>
                                ) : (
                                  <select value={s.status} onChange={e => handleListStatusChange(s.id, e.target.value)} style={{ fontSize: 10, padding: '2px 4px', background: 'var(--color-bg-input)', border: '1px solid var(--color-border)', borderRadius: 4, color: 'var(--color-text-primary)' }}>
                                    {Object.entries(STATUS_NAMES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                                  </select>
                                )}
                                <div onClick={e => e.stopPropagation()}>
                                  <TransformButton sourceType="schedule" sourceId={s.id} onTransformed={() => { loadAllSchedules(); showToast('已转化并创建') }} />
                                </div>
                                <button className="btn-icon" style={{ width: 24, height: 24, fontSize: 11 }} onClick={() => startListEdit(s)} title="编辑">✎</button>
                                <button className="btn-icon" style={{ width: 24, height: 24, fontSize: 11, color: 'var(--color-accent-danger)' }} onClick={() => setShowListDelete(s)} title="删除">×</button>
                              </div>
                            )}
                          </>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Create/Edit Modal */}
      {showCreate && (
        <div className="modal-overlay" onClick={() => setShowCreate(false)}>
          <div className="modal-panel" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <span>{editingEvent ? '编辑提醒' : '新建提醒'}</span>
              <button className="modal-close" onClick={() => setShowCreate(false)}>✕</button>
            </div>

            {/* Quick Templates */}
            <div className="cal-templates">
              {templates.map((tpl, i) => (
                <button key={i} className="cal-tpl-btn" onClick={() => applyTemplate(tpl)}>
                  {tpl.label}
                </button>
              ))}
            </div>

            <div className="cal-form">
              <input
                className="cal-form-input"
                placeholder="标题（如：非农数据发布）"
                value={formTitle}
                onChange={e => setFormTitle(e.target.value)}
                autoFocus
              />
              <div className="cal-form-row">
                <input type="date" className="cal-form-input" value={formDate} onChange={e => setFormDate(e.target.value)} />
                <input type="time" className="cal-form-input" value={formTime} onChange={e => setFormTime(e.target.value)} />
              </div>

              {/* Category */}
              <div className="cal-form-label">分类</div>
              <div className="cal-form-options">
                {Object.entries(CATEGORY_LABELS).map(([k, v]) => (
                  <button
                    key={k}
                    className={`cal-opt ${formCategory === k ? 'cal-opt-active' : ''}`}
                    onClick={() => setFormCategory(k)}
                  >
                    {v}
                  </button>
                ))}
              </div>

              {/* Importance */}
              <div className="cal-form-label">重要度</div>
              <div className="cal-stars-input">
                {[1, 2, 3, 4, 5].map(l => (
                  <span
                    key={l}
                    className={`star-big ${l <= formImportance ? 'star-filled' : 'star-empty'}`}
                    onClick={() => setFormImportance(l)}
                  >
                    ★
                  </span>
                ))}
              </div>

              {/* Impact */}
              <div className="cal-form-label">影响方向</div>
              <div className="cal-form-options">
                {[
                  { k: '', v: '无' },
                  { k: 'bullish', v: '利多' },
                  { k: 'bearish', v: '利空' },
                  { k: 'neutral', v: '中性' },
                ].map(({ k, v }) => (
                  <button
                    key={k}
                    className={`cal-opt ${formImpact === k ? 'cal-opt-active' : ''}`}
                    style={k === 'bullish' ? { color: 'var(--color-accent-success)' } : k === 'bearish' ? { color: 'var(--color-accent-danger)' } : undefined}
                    onClick={() => setFormImpact(k)}
                  >
                    {v}
                  </button>
                ))}
              </div>

              {/* Country */}
              <div className="cal-form-label">国家/地区</div>
              <input
                className="cal-form-input"
                placeholder="如：US, CN, EU"
                value={formCountry}
                onChange={e => setFormCountry(e.target.value)}
              />

              {/* Remind */}
              <div className="cal-form-label">提醒</div>
              <select className="cal-form-input" value={formRemind} onChange={e => setFormRemind(Number(e.target.value))}>
                <option value={0}>不提醒</option>
                <option value={15}>提前15分钟</option>
                <option value={30}>提前30分钟</option>
                <option value={60}>提前1小时</option>
                <option value={120}>提前2小时</option>
                <option value={1440}>提前1天</option>
              </select>

              {/* Goal */}
              <div className="cal-form-label">关联目标</div>
              <select className="cal-form-input" value={formGoalId ?? ''} onChange={e => setFormGoalId(e.target.value ? Number(e.target.value) : null)}>
                <option value="">不关联</option>
                {ctxGoals.map(g => (
                  <option key={g.id} value={g.id}>{g.title}</option>
                ))}
              </select>

              {/* Recurrence */}
              <div className="cal-form-label">重复</div>
              <select className="cal-form-input" value={formRecurrence} onChange={e => setFormRecurrence(e.target.value)}>
                <option value="">不重复</option>
                <option value="daily">每天</option>
                <option value="weekdays">工作日</option>
                <option value="weekly">每周</option>
                <option value="monthly">每月</option>
              </select>
            </div>

            <div className="modal-actions">
              {editingEvent && (
                <button className="btn btn-danger" onClick={() => { setShowDeleteConfirm(editingEvent); setShowCreate(false) }}>
                  删除
                </button>
              )}
              <button className="btn" onClick={() => setShowCreate(false)}>取消</button>
              <button className="btn btn-accent" onClick={handleSubmit}>
                {editingEvent ? '保存' : '创建'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirm (week view) */}
      {showDeleteConfirm && (
        <div className="modal-overlay" onClick={() => setShowDeleteConfirm(null)}>
          <div className="modal-panel modal-sm" onClick={e => e.stopPropagation()}>
            <div className="modal-header">确认删除</div>
            <p style={{ padding: '16px 20px', margin: 0 }}>确定要删除 "{showDeleteConfirm.title}" 吗？</p>
            <div className="modal-actions">
              <button className="btn" onClick={() => setShowDeleteConfirm(null)}>取消</button>
              <button className="btn btn-danger" onClick={handleDelete}>删除</button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirm (list view) */}
      {showListDelete && (
        <div className="modal-overlay" onClick={() => setShowListDelete(null)}>
          <div className="modal-panel modal-sm" onClick={e => e.stopPropagation()}>
            <div className="modal-header">确认删除</div>
            <p style={{ padding: '16px 20px', margin: 0 }}>确定要删除 "{showListDelete.title}" 吗？</p>
            <div className="modal-actions">
              <button className="btn" onClick={() => setShowListDelete(null)}>取消</button>
              <button className="btn btn-danger" onClick={handleListDelete}>删除</button>
            </div>
          </div>
        </div>
      )}

      {/* S4：目标详情弹窗（激活日操作统一入口） */}
      {detailGoal && (
        <GoalDetailModal
          goal={detailGoal}
          stats={ctxStats[detailGoal.id] || null}
          onClose={() => setDetailGoal(null)}
          onUpdate={reloadGoals}
        />
      )}
    </>
  )
}

/* 目标追踪面板（嵌入主日历页面） */
function GoalTrackerPanel({
  goals,
  stats,
  selectedDate,
  activeGoalsForDate,
  onUpdate,
  onOpenDetail,
}: {
  goals: Goal[]
  stats: Record<number, GoalStats>
  selectedDate: string
  activeGoalsForDate: { g: Goal; idx: number; color: string }[]
  onUpdate: () => void
  onOpenDetail: (g: Goal) => void
}) {
  const [editingGoalId, setEditingGoalId] = useState<number | null>(null)
  const [editForm, setEditForm] = useState<Partial<Goal>>({})
  const [currentValueInput, setCurrentValueInput] = useState<string>('')
  // S3：显示字段统一走 Context
  const { goalDisplayField } = useCalendarGoal()

  if (goals.length === 0) return null

  const displayValue = (g: Goal) => {
    if (goalDisplayField === 'daily_target') return `${g.daily_target}%`
    if (goalDisplayField === 'target_value') return formatMoney(g.target_value)
    return formatMoney(g.current_value)
  }
  const displayLabel = goalDisplayField === 'daily_target' ? '日化' : goalDisplayField === 'target_value' ? '目标' : '当前'

  const activeDateGoals = activeGoalsForDate

  const startEdit = (g: Goal) => {
    setEditingGoalId(g.id)
    setEditForm({
      title: g.title,
      start_value: g.start_value,
      target_value: g.target_value,
      daily_target: g.daily_target,
      current_value: g.current_value,
    })
    setCurrentValueInput(String(g.current_value))
  }

  const saveEdit = async (g: Goal) => {
    const payload: Partial<Goal> = {}
    if (editForm.title !== undefined) payload.title = editForm.title
    if (editForm.start_value !== undefined) payload.start_value = Number(editForm.start_value)
    if (editForm.target_value !== undefined) payload.target_value = Number(editForm.target_value)
    if (editForm.daily_target !== undefined) payload.daily_target = Number(editForm.daily_target)
    const cv = Number(currentValueInput)
    if (!isNaN(cv) && cv >= 0) payload.current_value = cv
    try {
      await api.updateGoal(g.id, payload)
      setEditingGoalId(null)
      onUpdate()
    } catch (err) {
      console.error('保存目标失败', err)
    }
  }

  return (
    <div style={{
      background: 'var(--color-bg-panel)',
      border: '1px solid var(--color-border)',
      borderRadius: 10,
      padding: 14,
      marginBottom: 12,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <span style={{ fontSize: 14, fontWeight: 600, color: '#50fa7b' }}>🎯 目标追踪</span>
        <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{goals.length} 个目标</span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 10 }}>
        {goals.map((g, idx) => {
          const s = stats[g.id]
          const progress = s?.progress ?? ((g.current_value - g.start_value) / (g.target_value - g.start_value) * 100)
          const color = GOAL_COLORS[idx % GOAL_COLORS.length]
          const activeDays = g.active_days || []
          const rate = g.daily_target || 5
          const factor = 1 + rate / 100
          const requiredDays = (g.current_value > 0 && g.target_value > g.current_value && factor > 1)
            ? Math.ceil(Math.log(g.target_value / g.current_value) / Math.log(factor))
            : 0

          if (editingGoalId === g.id) {
            return (
              <div key={g.id} style={{ padding: 10, background: 'var(--color-bg-input)', borderRadius: 8, border: `1px solid ${color}` }}>
                <input className="form-input" value={editForm.title || ''} onChange={e => setEditForm(f => ({ ...f, title: e.target.value }))} placeholder="名称" style={{ marginBottom: 6, fontSize: 12 }} />
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 6 }}>
                  <input className="form-input" type="number" value={editForm.start_value ?? ''} onChange={e => setEditForm(f => ({ ...f, start_value: Number(e.target.value) }))} placeholder="起始" style={{ fontSize: 12 }} />
                  <input className="form-input" type="number" value={editForm.target_value ?? ''} onChange={e => setEditForm(f => ({ ...f, target_value: Number(e.target.value) }))} placeholder="目标" style={{ fontSize: 12 }} />
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 6 }}>
                  <input className="form-input" type="number" step="0.1" value={editForm.daily_target ?? ''} onChange={e => setEditForm(f => ({ ...f, daily_target: Number(e.target.value) }))} placeholder="日化%" style={{ fontSize: 12 }} />
                  <input className="form-input" type="number" value={currentValueInput} onChange={e => setCurrentValueInput(e.target.value)} placeholder="当前值" style={{ fontSize: 12 }} />
                </div>
                <div style={{ display: 'flex', gap: 6 }}>
                  <button className="btn btn-sm" onClick={() => saveEdit(g)} style={{ flex: 1, background: color, color: '#fff', border: 'none' }}>保存</button>
                  <button className="btn btn-sm" onClick={() => setEditingGoalId(null)} style={{ flex: 1 }}>取消</button>
                </div>
              </div>
            )
          }

          return (
            <div
              key={g.id}
              onClick={() => onOpenDetail(g)}
              style={{
                padding: 10,
                background: 'var(--color-bg-input)',
                borderRadius: 8,
                borderLeft: `3px solid ${color}`,
                position: 'relative',
                cursor: 'pointer',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
                  {g.title}
                </span>
                <button className="btn-icon" onClick={() => startEdit(g)} title="编辑" style={{ width: 22, height: 22, fontSize: 11, marginLeft: 4 }}>✎</button>
              </div>

              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 4 }}>
                <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{displayLabel} {displayValue(g)}</span>
                <span style={{ fontSize: 12, fontWeight: 700, color: progress >= 100 ? '#50fa7b' : color }}>{progress.toFixed(0)}%</span>
              </div>
              <div style={{ background: 'var(--color-bg-muted)', borderRadius: 4, height: 5, overflow: 'hidden', marginBottom: 8 }}>
                <div style={{ width: `${Math.min(progress, 100)}%`, height: 5, borderRadius: 4, background: color, transition: 'width 0.3s' }} />
              </div>

              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 11, color: 'var(--color-text-muted)' }}>
                <span>已激活 {activeDays.length} 天{requiredDays > 0 ? ` / 需 ${requiredDays}` : ''}</span>
                <button
                  className="btn btn-sm"
                  onClick={(e) => { e.stopPropagation(); onOpenDetail(g) }}
                  style={{
                    fontSize: 10,
                    padding: '2px 8px',
                    background: 'transparent',
                    color: color,
                    border: `1px solid ${color}`,
                    borderRadius: 4,
                  }}
                >
                  详情
                </button>
              </div>
            </div>
          )
        })}
      </div>

      {activeDateGoals.length > 0 && (
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--color-border)' }}>
          <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 6 }}>
            {selectedDate} 已激活目标：
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {activeDateGoals.map(({ g, color }) => (
              <span key={g.id} style={{ fontSize: 12, color, background: 'var(--color-bg-muted)', padding: '2px 8px', borderRadius: 4 }}>
                {g.title}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
