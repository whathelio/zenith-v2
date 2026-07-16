import { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type Schedule, type CalendarTemplate } from '../shared/api'

const IMPACT_MAP: Record<string, string> = {
  bullish: '利多',
  bearish: '利空',
  neutral: '中性',
}

const IMPACT_COLORS: Record<string, string> = {
  bullish: '#50fa7b',
  bearish: '#ff5555',
  neutral: '#8be9fd',
}

const CATEGORY_LABELS: Record<string, string> = {
  economic: '财经',
  market: '市场',
  reminder: '提醒',
  personal: '个人',
  other: '其他',
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

  // Form state
  const [formTitle, setFormTitle] = useState('')
  const [formDate, setFormDate] = useState(selectedDate)
  const [formTime, setFormTime] = useState('')
  const [formCategory, setFormCategory] = useState('economic')
  const [formImportance, setFormImportance] = useState(3)
  const [formImpact, setFormImpact] = useState('')
  const [formCountry, setFormCountry] = useState('')
  const [formRemind, setFormRemind] = useState(0)

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
      days.push({
        dayName: dayNames[i],
        day: d.getDate(),
        date: ds,
        isToday: ds === getToday(),
        count,
      })
    }
    return days
  }, [monday, events])

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

  useEffect(() => { loadEvents() }, [mondayStr])
  useEffect(() => { loadTemplates() }, [])

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
    const payload = {
      title: formTitle.trim(),
      start_time: formDate + (formTime ? `T${formTime}:00` : 'T00:00:00'),
      category: formCategory,
      importance: formImportance,
      impact: formImpact || '',
      country: formCountry,
      remind_before: formRemind,
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

  const weekLabel = `${monday.getMonth() + 1}月${monday.getDate()}日 - ${sunday.getMonth() + 1}月${sunday.getDate()}日`

  return (
    <>
      <div className="main-area" style={{ maxWidth: '100vw' }}>
        <div className="topbar">
          <span className="topbar-title">📅 日历</span>
          <div className="topbar-actions">
            <button className="btn btn-sm" onClick={() => navigate('/')}>🏠 主页</button>
            <button className="btn btn-sm" onClick={() => navigate('/schedules')}>📋 日程</button>
            <button className="btn btn-sm" onClick={() => navigate('/memories')}>🧠 记忆</button>
            <button className="btn btn-sm" onClick={() => navigate('/settings')}>⚙ 设置</button>
          </div>
        </div>

        <div className="calendar-container">
          {/* Header */}
          <div className="cal-header">
            <button className="cal-nav-btn" onClick={prevWeek}>‹</button>
            <span className="cal-week-label" onClick={goToday} style={{ cursor: 'pointer' }}>{weekLabel}</span>
            <button className="cal-nav-btn" onClick={nextWeek}>›</button>
            <button className="btn btn-sm" onClick={goToday} style={{ marginLeft: 8 }}>今天</button>
          </div>

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
              </div>
            ))}
          </div>

          {/* Add button */}
          <div className="cal-toolbar">
            <button className="btn btn-accent" onClick={() => openCreate()}>
              + 添加提醒
            </button>
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
                      {IMPACT_MAP[ev.impact] || ev.impact}
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
                <option value={1440}>提前1天</option>
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

      {/* Delete Confirm */}
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
    </>
  )
}
