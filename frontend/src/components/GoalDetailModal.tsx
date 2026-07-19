import { useState, useEffect, useMemo } from 'react'
import { api, type Goal, type GoalStats } from '../shared/api'

const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日']

function formatDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function parseLocalDate(s: string): Date {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}

function formatMoney(v: number): string {
  if (v >= 100000) return `${(v / 10000).toFixed(1)}万`
  if (v >= 10000) return `${(v / 10000).toFixed(2)}万`
  return v.toLocaleString()
}

function formatMoneyShort(v: number): string {
  if (v >= 10000) return `${(v / 10000).toFixed(1)}万`
  return v.toFixed(0)
}

export interface GoalDetailModalProps {
  goal: Goal
  stats: GoalStats | null
  onClose: () => void
  onUpdate: () => void
  onEdit?: () => void
}

/* ══════════════════════════════════════════
   GoalDetailModal — 百尺式目标详情 + 月历激活
   ══════════════════════════════════════════ */
export default function GoalDetailModal({
  goal,
  stats,
  onClose,
  onUpdate,
  onEdit,
}: GoalDetailModalProps) {
  const [viewDate, setViewDate] = useState(new Date())
  const [localActiveDays, setLocalActiveDays] = useState<string[]>(goal.active_days || [])
  const [currentValueInput, setCurrentValueInput] = useState(String(goal.current_value))
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setLocalActiveDays(goal.active_days || [])
    setCurrentValueInput(String(goal.current_value))
  }, [goal.id])

  const progress = useMemo(() => {
    const range = goal.target_value - goal.start_value
    return range > 0
      ? Math.max(0, Math.min(((goal.current_value - goal.start_value) / range) * 100, 100))
      : 0
  }, [goal])

  const rate = goal.daily_target || 5
  const factor = 1 + rate / 100

  const requiredDays = useMemo(() => {
    if (goal.current_value <= 0 || goal.target_value <= goal.current_value || rate <= 0) return 0
    const ratio = goal.target_value / goal.current_value
    if (factor <= 1) return 0
    return Math.ceil(Math.log(ratio) / Math.log(factor))
  }, [goal.current_value, goal.target_value, rate, factor])

  const expectedValue = useMemo(() => {
    const n = localActiveDays.length
    if (n <= 0 || goal.current_value <= 0 || rate <= 0) return goal.current_value
    return goal.current_value * Math.pow(factor, n)
  }, [goal.current_value, localActiveDays.length, rate, factor])

  const dailyFirstQuota = useMemo(() => {
    if (goal.current_value > 0 && rate > 0) {
      return goal.current_value * (factor - 1)
    }
    return 0
  }, [goal.current_value, rate, factor])

  const sortedActiveDays = useMemo(() => [...localActiveDays].sort(), [localActiveDays])

  const calendar = useMemo(() => {
    const year = viewDate.getFullYear()
    const month = viewDate.getMonth()
    const monthNames = ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月']
    const title = `${year}年${monthNames[month]}`

    const firstDay = new Date(year, month, 1)
    const daysInMonth = new Date(year, month + 1, 0).getDate()
    const todayStr = formatDate(new Date())

    let startDow = firstDay.getDay() - 1
    if (startDow < 0) startDow = 6

    const goalStart = goal.start_date || ''
    const goalEnd = goal.end_date || ''

    const cells: {
      day: number
      date: string
      isToday: boolean
      isWeekend: boolean
      isFuture: boolean
      isActive: boolean
      inRange: boolean
      quota: number
    }[] = []

    for (let d = 1; d <= daysInMonth; d++) {
      const date = new Date(year, month, d)
      const dateStr = formatDate(date)
      const dow = date.getDay()
      const isWeekend = dow === 0 || dow === 6
      const isToday = dateStr === todayStr
      const isFuture = date > new Date(new Date().setHours(23, 59, 59, 999))
      const inRange = !!(goalStart && goalEnd && dateStr >= goalStart && dateStr <= goalEnd)
      const isActive = localActiveDays.includes(dateStr)

      let quota = 0
      if (isActive && goal.current_value > 0 && rate > 0) {
        const idx = sortedActiveDays.indexOf(dateStr)
        if (idx >= 0) {
          const prevVal = goal.current_value * Math.pow(factor, idx)
          const dayTarget = goal.current_value * Math.pow(factor, idx + 1)
          quota = dayTarget - prevVal
        }
      }

      cells.push({ day: d, date: dateStr, isToday, isWeekend, isFuture, isActive, inRange, quota })
    }

    return { title, cells, startOffset: startDow }
  }, [viewDate, localActiveDays, sortedActiveDays, goal.start_date, goal.end_date, goal.current_value, rate, factor])

  const prevMonth = () => {
    setViewDate(new Date(viewDate.getFullYear(), viewDate.getMonth() - 1, 1))
  }

  const nextMonth = () => {
    setViewDate(new Date(viewDate.getFullYear(), viewDate.getMonth() + 1, 1))
  }

  const toggleDay = (date: string) => {
    setLocalActiveDays(prev => {
      const idx = prev.indexOf(date)
      if (idx >= 0) {
        const next = [...prev]
        next.splice(idx, 1)
        return next
      }
      return [...prev, date].sort()
    })
  }

  const saveActiveDays = async () => {
    setSaving(true)
    try {
      await api.updateGoal(goal.id, { active_days: localActiveDays })
      onUpdate()
    } catch (err) {
      console.error('保存激活日期失败', err)
    } finally {
      setSaving(false)
    }
  }

  const updateCurrentValue = async () => {
    const val = Number(currentValueInput)
    if (isNaN(val) || val < 0) return
    try {
      await api.updateGoal(goal.id, { current_value: val })
      onUpdate()
    } catch (err) {
      console.error('更新当前值失败', err)
    }
  }

  const changeDailyTarget = async (delta: number) => {
    const newVal = Math.max(0.1, Number((goal.daily_target + delta).toFixed(1)))
    try {
      await api.updateGoal(goal.id, { daily_target: newVal })
      onUpdate()
    } catch (err) {
      console.error('更新日化目标失败', err)
    }
  }

  const diffDays = requiredDays > 0 ? Math.abs(localActiveDays.length - requiredDays) : 0
  const diffLabel = localActiveDays.length >= requiredDays && requiredDays > 0 ? '多出' : '还差'

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-panel"
        onClick={e => e.stopPropagation()}
        style={{ maxWidth: 540, width: '90%', maxHeight: '90vh', overflow: 'auto' }}
      >
        <div className="modal-header">
          <span>🎯 目标详情</span>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>

        {/* 目标概览卡片 */}
        <div style={{
          background: 'var(--color-bg-panel)',
          border: '1px solid var(--color-border)',
          borderRadius: 12,
          padding: 16,
          marginBottom: 16,
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
            <span style={{ fontSize: 18, fontWeight: 700, color: 'var(--color-text-primary)' }}>{goal.title}</span>
            <span style={{
              fontSize: 24,
              fontWeight: 800,
              color: progress >= 100 ? '#50fa7b' : 'var(--color-accent-primary)',
              fontFamily: 'var(--font-mono)',
            }}>
              {progress.toFixed(0)}%
            </span>
          </div>

          <div style={{ background: 'var(--color-bg-muted)', borderRadius: 5, height: 10, overflow: 'hidden', marginBottom: 16 }}>
            <div style={{
              width: `${Math.min(progress, 100)}%`,
              height: 10,
              borderRadius: 5,
              background: progress >= 100 ? '#50fa7b' : 'linear-gradient(90deg, #ff79c6, #ff5555)',
              transition: 'width 0.3s',
            }} />
          </div>

          {/* 核心参数行 */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-around', padding: '10px 0', borderBottom: '1px solid var(--color-border)' }}>
            <div style={{ textAlign: 'center', flex: 1 }}>
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>起始值</div>
              <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--color-text-secondary)', fontFamily: 'var(--font-mono)' }}>{formatMoney(goal.start_value)}</div>
            </div>
            <div style={{ width: 1, height: 30, background: 'var(--color-border)' }} />
            <div style={{ textAlign: 'center', flex: 1 }}>
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>当前值</div>
              <input
                type="number"
                value={currentValueInput}
                onChange={e => setCurrentValueInput(e.target.value)}
                onBlur={updateCurrentValue}
                onKeyDown={e => { if (e.key === 'Enter') updateCurrentValue() }}
                style={{
                  width: 100,
                  padding: '4px 6px',
                  textAlign: 'center',
                  fontSize: 18,
                  fontWeight: 700,
                  color: 'var(--color-accent-primary)',
                  fontFamily: 'var(--font-mono)',
                  background: 'var(--color-bg-muted)',
                  border: '1px solid var(--color-accent-primary)',
                  borderRadius: 6,
                }}
              />
            </div>
            <div style={{ width: 1, height: 30, background: 'var(--color-border)' }} />
            <div style={{ textAlign: 'center', flex: 1 }}>
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>总目标</div>
              <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--color-text-secondary)', fontFamily: 'var(--font-mono)' }}>{formatMoney(goal.target_value)}</div>
            </div>
          </div>

          {/* 每日增幅 */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 0', borderBottom: '1px solid var(--color-border)' }}>
            <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>每日增幅</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <button className="btn btn-sm" onClick={() => changeDailyTarget(-0.1)} style={{ fontSize: 16, padding: '2px 10px' }}>-</button>
              <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--color-accent-primary)', fontFamily: 'var(--font-mono)', minWidth: 60, textAlign: 'center' }}>
                {goal.daily_target.toFixed(1)}%
              </span>
              <button className="btn btn-sm" onClick={() => changeDailyTarget(0.1)} style={{ fontSize: 16, padding: '2px 10px' }}>+</button>
            </div>
          </div>

          {/* 计算结果 */}
          <div style={{ display: 'flex', justifyContent: 'space-around', paddingTop: 12 }}>
            <div style={{ textAlign: 'center', flex: 1 }}>
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>所需</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--color-text-secondary)', fontFamily: 'var(--font-mono)' }}>{requiredDays}天</div>
            </div>
            <div style={{ textAlign: 'center', flex: 1 }}>
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>已激活</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--color-accent-primary)', fontFamily: 'var(--font-mono)' }}>{localActiveDays.length}天</div>
            </div>
            <div style={{ textAlign: 'center', flex: 1 }}>
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{diffLabel}</div>
              <div style={{
                fontSize: 16,
                fontWeight: 700,
                color: diffLabel === '多出' ? '#50fa7b' : '#f1fa8c',
                fontFamily: 'var(--font-mono)',
              }}>
                {diffDays}天
              </div>
            </div>
            <div style={{ textAlign: 'center', flex: 1 }}>
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>预期值</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: '#50fa7b', fontFamily: 'var(--font-mono)' }}>{formatMoneyShort(expectedValue)}</div>
            </div>
          </div>

          {dailyFirstQuota > 0 && (
            <div style={{ marginTop: 10, fontSize: 12, color: 'var(--color-text-muted)', textAlign: 'center' }}>
              第一天配额约 <span style={{ color: '#ff79c6', fontWeight: 700 }}>{formatMoneyShort(dailyFirstQuota)}</span>，随激活天数复利递增
            </div>
          )}
        </div>

        {/* 统计行 */}
        {stats && (
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, 1fr)',
            gap: 8,
            marginBottom: 16,
            padding: 12,
            background: 'var(--color-bg-input)',
            borderRadius: 10,
          }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>日化收益</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: stats.daily_return >= goal.daily_target ? '#50fa7b' : '#ff5555' }}>
                {stats.daily_return.toFixed(2)}%
              </div>
            </div>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>已过/总天数</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--color-text-secondary)' }}>{stats.days_passed}/{stats.days_total}</div>
            </div>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>剩余</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--color-text-secondary)' }}>{formatMoney(stats.remaining)}</div>
            </div>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>状态</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: stats.on_track ? '#50fa7b' : '#ff5555' }}>
                {stats.on_track ? '✓ 在轨' : '⚠ 偏离'}
              </div>
            </div>
          </div>
        )}

        {/* 月历 */}
        <div style={{
          background: 'var(--color-bg-panel)',
          border: '1px solid var(--color-border)',
          borderRadius: 12,
          padding: 16,
          marginBottom: 16,
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <button className="btn btn-sm" onClick={prevMonth}>‹</button>
            <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--color-text-primary)' }}>{calendar.title}</span>
            <button className="btn btn-sm" onClick={nextMonth}>›</button>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 4, marginBottom: 8 }}>
            {WEEKDAYS.map(w => (
              <div key={w} style={{ textAlign: 'center', fontSize: 12, color: 'var(--color-text-muted)', fontWeight: 600 }}>{w}</div>
            ))}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 4 }}>
            {Array.from({ length: calendar.startOffset }).map((_, i) => (
              <div key={`empty-${i}`} style={{ minHeight: 52 }} />
            ))}
            {calendar.cells.map(cell => {
              const dayQuota = cell.isActive && cell.quota > 0 ? cell.quota : 0
              return (
                <div
                  key={cell.date}
                  onClick={() => toggleDay(cell.date)}
                  style={{
                    minHeight: 52,
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                    padding: '4px 2px',
                    borderRadius: 6,
                    cursor: 'pointer',
                    background: cell.isActive
                      ? 'rgba(255, 121, 198, 0.12)'
                      : cell.isToday
                        ? 'rgba(189, 147, 249, 0.12)'
                        : cell.inRange
                          ? 'rgba(255,255,255,0.02)'
                          : 'transparent',
                    border: `1px solid ${cell.isActive ? 'rgba(255, 121, 198, 0.4)' : 'transparent'}`,
                  }}
                >
                  <span style={{
                    fontSize: 14,
                    fontWeight: cell.isToday ? 800 : 600,
                    color: cell.isToday ? 'var(--color-accent-primary)' : cell.isWeekend ? 'var(--color-text-muted)' : 'var(--color-text-primary)',
                  }}>
                    {cell.day}
                  </span>
                  {cell.isActive && (
                    <div style={{ width: 6, height: 6, borderRadius: '50%', background: '#ff79c6', marginTop: 2 }} />
                  )}
                  {dayQuota > 0 && (
                    <span style={{ fontSize: 10, color: '#ff79c6', fontWeight: 700, fontFamily: 'var(--font-mono)', marginTop: 1 }}>
                      {formatMoneyShort(dayQuota)}
                    </span>
                  )}
                </div>
              )
            })}
          </div>

          <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 8, marginTop: 12, paddingTop: 10, borderTop: '1px solid var(--color-border)' }}>
            <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#ff79c6' }} />
            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>已激活日期（点击切换）</span>
          </div>
        </div>

        {/* 操作栏 */}
        <div className="modal-actions">
          <button className="btn" onClick={onClose}>关闭</button>
          {onEdit && <button className="btn btn-sm" onClick={onEdit}>编辑目标</button>}
          <button
            className="btn btn-accent"
            onClick={saveActiveDays}
            disabled={saving}
          >
            {saving ? '保存中...' : `保存激活日 (${localActiveDays.length})`}
          </button>
        </div>
      </div>
    </div>
  )
}
