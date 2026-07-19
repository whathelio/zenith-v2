import { useState, useEffect, useCallback, useMemo } from 'react'
import { Outlet, Link, useNavigate } from 'react-router-dom'
import { api, type CalendarData, type Goal, type GoalStats } from '../shared/api'
import {
  CalendarGoalContext,
  type GoalDisplayField,
  type GoalMarkInfo,
  type GoalAmountInfo,
  type GoalActiveInfo,
  type WeekDayInfo,
} from '../contexts/CalendarGoalContext'

const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日']
const GOAL_COLORS = ['#50fa7b', '#8be9fd', '#ff79c6', '#f1fa8c', '#bd93f9']

function formatDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function formatMoney(v: number): string {
  if (v >= 100000) return `${(v / 10000).toFixed(1)}万`
  if (v >= 10000) return `${(v / 10000).toFixed(2)}万`
  return v.toFixed(0)
}

function formatMoneyShort(v: number): string {
  if (v >= 10000) return `${(v / 10000).toFixed(1)}万`
  return v.toFixed(0)
}

function parseLocalDate(s: string): Date {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}

function addDaysStr(s: string, n: number): string {
  const d = parseLocalDate(s)
  d.setDate(d.getDate() + n)
  return formatDate(d)
}

function daysBetween(a: string, b: string): number {
  return Math.round((parseLocalDate(b).getTime() - parseLocalDate(a).getTime()) / 86400000)
}

function recalcDailyTarget(g: Goal, newEndDate: string): number {
  const cv = g.current_value
  const tv = g.target_value
  if (cv <= 0 || cv >= tv) return g.daily_target
  const remainDays = Math.max(daysBetween(formatDate(new Date()), newEndDate), 1)
  const requiredRate = Math.pow(tv / cv, 1 / remainDays) - 1
  return Math.round(requiredRate * 100 * 100) / 100
}

function calcDailyGoalAmounts(g: Goal): Map<string, number> {
  const map = new Map<string, number>()
  const cv = g.current_value
  const tv = g.target_value
  const rate = g.daily_target / 100
  if (rate <= 0 || cv <= 0 || !g.end_date) return map
  const todayDate = formatDate(new Date())
  const remainDays = Math.max(daysBetween(todayDate, g.end_date), 1)
  for (let i = 0; i <= remainDays; i++) {
    const dateKey = addDaysStr(todayDate, i)
    const expected = cv * Math.pow(1 + rate, i)
    map.set(dateKey, Math.min(expected, tv))
  }
  return map
}

/* ══════════════════════════════════════════
   AppLayout — 全站固定布局
   左侧面板：日历 + 目标追踪（所有页面共享）
   右侧面板：Outlet（各页面内容）
   ══════════════════════════════════════════ */
export default function AppLayout() {
  const navigate = useNavigate()
  const [currentDate, setCurrentDate] = useState(new Date())
  const [selectedDate, setSelectedDate] = useState(new Date())
  const todayStr = formatDate(new Date())

  // 目标显示字段切换
  const [goalDisplayField, setGoalDisplayField] = useState<GoalDisplayField>('current_value')

  // 日历数据
  const [data, setData] = useState<CalendarData | null>(null)
  const [loading, setLoading] = useState(true)

  // 目标
  const [goals, setGoals] = useState<Goal[]>([])
  const [goalStats, setGoalStats] = useState<Record<number, GoalStats>>({})

  // 目标 CRUD
  const [showGoalModal, setShowGoalModal] = useState(false)
  const [editingGoal, setEditingGoal] = useState<Goal | null>(null)
  const [showGoalDelete, setShowGoalDelete] = useState<Goal | null>(null)

  // 提议同步（来自 ChatView 的 CustomEvent）
  const [proposals, setProposals] = useState<any[]>([])

  // 周计算：以 selectedDate 为基准，显示其所在周（周一到周日）
  const refDate = selectedDate
  const monday = new Date(refDate.getTime() - ((refDate.getDay() || 7) - 1) * 86400000)
  const weekDays: WeekDayInfo[] = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(monday.getTime() + i * 86400000)
    return { date: d, key: formatDate(d), day: d.getDate(), weekday: WEEKDAYS[i] }
  })
  const selectedDayKey = formatDate(selectedDate)
  const selectedDayData = data?.days[selectedDayKey] ?? null

  // 加载日历
  const loadCalendar = useCallback(async () => {
    try {
      const y = currentDate.getFullYear(), m = currentDate.getMonth() + 1
      const result = await api.getCalendar(y, m)
      setData(result)
    } catch {} finally { setLoading(false) }
  }, [currentDate.getFullYear(), currentDate.getMonth() + 1])
  useEffect(() => { loadCalendar() }, [loadCalendar])

  // 监听 ChatView 的提议更新事件
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail
      setProposals(Array.isArray(detail) ? detail : [])
    }
    window.addEventListener('zenith:proposals', handler)
    return () => window.removeEventListener('zenith:proposals', handler)
  }, [])

  // 加载目标
  const loadGoals = useCallback(async () => {
    try {
      const g = await api.listGoals()
      setGoals(g.filter(g => g.status === 'active'))
      const st: Record<number, GoalStats> = {}
      for (const gl of g.filter(g => g.status === 'active')) {
        try { st[gl.id] = await api.getGoalStats(gl.id) } catch {}
      }
      setGoalStats(st)
    } catch {}
  }, [])
  useEffect(() => { loadGoals() }, [loadGoals])

  const prevWeek = () => {
    const d = new Date(selectedDate)
    d.setDate(d.getDate() - 7)
    setSelectedDate(d)
    setCurrentDate(d)
  }
  const nextWeek = () => {
    const d = new Date(selectedDate)
    d.setDate(d.getDate() + 7)
    setSelectedDate(d)
    setCurrentDate(d)
  }
  const goToday = () => {
    const now = new Date()
    setSelectedDate(now)
    setCurrentDate(now)
  }

  // 更新目标余额
  const updateGoalBalance = async (goalId: number, newValue: number) => {
    await api.updateGoal(goalId, { current_value: newValue })
    loadGoals()
  }

  // 提议确认/拒绝（左侧面板紧凑列表）
  const handleProposalConfirm = async (type: string, id: number) => {
    try {
      await api.confirmProposal(type, id)
      setProposals(prev => prev.filter(p => !(p.type === type && p.id === id)))
      loadCalendar()
    } catch (e) { console.error('确认提议失败', e) }
  }
  const handleProposalReject = async (type: string, id: number) => {
    try {
      await api.rejectProposal(type, id)
      setProposals(prev => prev.filter(p => !(p.type === type && p.id === id)))
    } catch (e) { console.error('拒绝提议失败', e) }
  }

  // 选定目标日期
  const updateGoalEndDate = async (goalId: number, newEndDate: string) => {
    const g = goals.find(g => g.id === goalId)
    if (!g) return
    const newDailyTarget = recalcDailyTarget(g, newEndDate)
    await api.updateGoal(goalId, { end_date: newEndDate, daily_target: newDailyTarget })
    loadGoals()
  }

  // 目标日期映射
  const goalDateMap = useMemo(() => {
    const map = new Map<string, GoalMarkInfo[]>()
    goals.forEach((g, idx) => {
      const color = GOAL_COLORS[idx % GOAL_COLORS.length]
      const add = (date: string, label: string) => {
        if (!date) return
        const list = map.get(date) || []
        list.push({ goalId: g.id, color, label, title: g.title })
        map.set(date, list)
      }
      add(g.start_date, '起')
      add(g.end_date, '止')
    })
    return map
  }, [goals])

  // 每日目标金额映射
  const goalAmountMap = useMemo(() => {
    const map = new Map<string, GoalAmountInfo[]>()
    goals.forEach((g, idx) => {
      const color = GOAL_COLORS[idx % GOAL_COLORS.length]
      const amounts = calcDailyGoalAmounts(g)
      amounts.forEach((amount, dateKey) => {
        const list = map.get(dateKey) || []
        list.push({ goalId: g.id, color, amount })
        map.set(dateKey, list)
      })
    })
    return map
  }, [goals])

  // 目标激活日期映射（基于 active_days）
  const goalActiveMap = useMemo(() => {
    const map = new Map<string, GoalActiveInfo[]>()
    goals.forEach((g, idx) => {
      const color = GOAL_COLORS[idx % GOAL_COLORS.length]
      const activeDays = g.active_days || []
      activeDays.forEach(date => {
        const list = map.get(date) || []
        list.push({ goalId: g.id, color, title: g.title })
        map.set(date, list)
      })
    })
    return map
  }, [goals])

  // 目标 CRUD
  const handleGoalSubmit = async (form: { title: string; start_value: string; target_value: string; daily_target: string; current_value?: string }) => {
    if (!form.title.trim() || !form.target_value) return
    try {
      if (editingGoal) {
        await api.updateGoal(editingGoal.id, {
          title: form.title.trim(),
          start_value: Number(form.start_value),
          target_value: Number(form.target_value),
          daily_target: Number(form.daily_target),
          current_value: form.current_value ? Number(form.current_value) : undefined,
        })
      } else {
        await api.createGoal({
          title: form.title.trim(),
          start_value: form.start_value ? Number(form.start_value) : 0,
          target_value: Number(form.target_value),
          daily_target: Number(form.daily_target),
        })
      }
      setShowGoalModal(false)
      setEditingGoal(null)
      loadGoals()
    } catch (err) {
      console.error('保存目标失败', err)
    }
  }

  const handleGoalDelete = async () => {
    if (!showGoalDelete) return
    try {
      await api.deleteGoal(showGoalDelete.id)
      setShowGoalDelete(null)
      setEditingGoal(null)
      loadGoals()
    } catch {}
  }

  // Context value
  const contextValue = useMemo(() => ({
    currentDate, setCurrentDate, selectedDate, setSelectedDate, todayStr,
    selectedDayKey, weekDays, data, loading, goals, goalStats,
    goalDateMap, goalAmountMap, goalActiveMap, selectedDayData,
    prevWeek, nextWeek, goToday, loadCalendar, loadGoals,
    updateGoalBalance, updateGoalEndDate,
    goalDisplayField, setGoalDisplayField,
    showGoalModal, setShowGoalModal, editingGoal, setEditingGoal,
    showGoalDelete, setShowGoalDelete, handleGoalSubmit, handleGoalDelete,
  }), [currentDate, selectedDate, todayStr, selectedDayKey, weekDays, data, loading,
    goals, goalStats, goalDateMap, goalAmountMap, goalActiveMap, selectedDayData,
    goalDisplayField, showGoalModal, editingGoal, showGoalDelete])

  return (
    <CalendarGoalContext.Provider value={contextValue}>
      <div className="app-layout">
        {/* Topbar */}
        <div className="topbar">
          <div className="topbar-brand">Zenith v2</div>
          <div className="topbar-actions">
            <Link to="/" className="btn btn-sm">🏠 主页</Link>
            <Link to="/chat" className="btn btn-sm">💬 对话</Link>
            <Link to="/calendar" className="btn btn-sm">📋 日程</Link>
            <Link to="/library" className="btn btn-sm">📚 笔记库</Link>
            <Link to="/knowledge" className="btn btn-sm">🧠 知识库</Link>
            <Link to="/goals" className="btn btn-sm">🎯 目标</Link>
            <Link to="/settings" className="btn btn-sm">⚙ 设置</Link>
          </div>
        </div>

        <div className="app-layout-body">
          {/* ====== 左侧面板：日历 + 目标追踪 ====== */}
          <div className="app-layout-left">
            {/* 待确认提议（来自对话） */}
            {proposals.length > 0 && (
              <div className="proposal-compact" style={{ marginBottom: 10 }}>
                <div className="proposal-compact-header">
                  <span>📋</span>待确认提议 ({proposals.length})
                </div>
                {proposals.slice(0, 5).map((p, i) => (
                  <div key={`${p.type}-${p.id}-${i}`} className="proposal-compact-item">
                    <span className="proposal-compact-item-title">
                      {p.type === 'schedule' ? '📅' : '📝'} {p.data?.title || p.data?.content || '提议'}
                    </span>
                    <button
                      className="proposal-compact-btn confirm"
                      onClick={() => handleProposalConfirm(p.type, p.id)}
                      title="确认"
                    >✓</button>
                    <button
                      className="proposal-compact-btn reject"
                      onClick={() => handleProposalReject(p.type, p.id)}
                      title="拒绝"
                    >✗</button>
                  </div>
                ))}
                {proposals.length > 5 && (
                  <div style={{ fontSize: 11, color: 'var(--color-text-muted)', textAlign: 'center', padding: '4px 0' }}>
                    +{proposals.length - 5} 条，去对话页查看
                  </div>
                )}
              </div>
            )}

            {/* 周导航 */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
              <button className="btn btn-sm" onClick={prevWeek}>‹</button>
              <button className="btn btn-sm" onClick={goToday} style={{ fontSize: 12 }}>今天</button>
              <button className="btn btn-sm" onClick={nextWeek}>›</button>
            </div>

            {/* 周日期条 */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 6, marginBottom: 12 }}>
              {weekDays.map(wd => {
                const dayData = data?.days[wd.key]
                const isToday = wd.key === todayStr
                const isSelected = wd.key === selectedDayKey
                const schedCount = dayData?.schedules?.length ?? 0
                const goalMarks = goalDateMap.get(wd.key) || []
                const goalAmts = goalAmountMap.get(wd.key) || []
                // 多目标显示：显示所有目标的颜色点 + 聚合数值
                let displayValue: { text: string; color: string } | null = null
                if (wd.key >= todayStr) {
                  if (goalDisplayField === 'current_value' && goals.length > 0) {
                    const total = goals.reduce((sum, g) => sum + g.current_value, 0)
                    displayValue = { text: formatMoneyShort(total), color: '#50fa7b' }
                  } else if (goalDisplayField === 'daily_target' && goalAmts.length > 0) {
                    const total = goalAmts.reduce((sum, a) => sum + a.amount, 0)
                    displayValue = { text: formatMoneyShort(total), color: '#8be9fd' }
                  } else if (goalDisplayField === 'target_value' && goals.length > 0) {
                    const total = goals.reduce((sum, g) => sum + g.target_value, 0)
                    displayValue = { text: formatMoneyShort(total), color: '#f1fa8c' }
                  }
                }
                return (
                  <div
                    key={wd.key}
                    onClick={() => setSelectedDate(wd.date)}
                    style={{
                      textAlign: 'center', padding: '12px 4px 8px', borderRadius: 8,
                      cursor: 'pointer',
                      background: isSelected ? 'var(--color-accent-primary)' : isToday ? 'rgba(189,147,249,0.12)' : 'var(--color-bg-panel)',
                      color: isSelected ? '#fff' : isToday ? 'var(--color-accent-primary)' : 'var(--color-text-secondary)',
                      border: `1px solid ${isSelected ? 'var(--color-accent-primary)' : 'var(--color-border)'}`,
                    }}
                  >
                    <div style={{ fontSize: 14, fontWeight: 500 }}>{wd.weekday}</div>
                    <div style={{ fontSize: 20, fontWeight: isToday ? 700 : 500 }}>{wd.day}</div>
                    {schedCount > 0 && (
                      <div style={{
                        fontSize: 13, background: isSelected ? '#fff3' : 'var(--color-accent-primary)',
                        color: '#fff', borderRadius: 10, padding: '2px 8px', marginTop: 4,
                      }}>
                        {schedCount}
                      </div>
                    )}
                    {displayValue && (
                      <div style={{
                        fontSize: 12, marginTop: 2,
                        color: isSelected ? '#fff9' : displayValue.color,
                        fontWeight: 600,
                      }}>
                        {displayValue.text}
                      </div>
                    )}
                    {goalMarks.length > 0 && (
                      <div style={{ display: 'flex', justifyContent: 'center', gap: 2, marginTop: 2, flexWrap: 'wrap' }}>
                        {goalMarks.slice(0, 5).map((m, i) => (
                          <div key={i} style={{
                            width: 7, height: 7, borderRadius: '50%', background: isSelected ? '#fff8' : m.color,
                          }} title={`${m.title} ${m.label}`} />
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>

            {/* 月概览（小月历） */}
            <div style={{ background: 'var(--color-bg-panel)', borderRadius: 10, padding: 16, border: '1px solid var(--color-border)', marginBottom: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                <span style={{ fontSize: 16, fontWeight: 600, color: 'var(--color-text-primary)' }}>
                  {currentDate.getFullYear()}年{currentDate.getMonth() + 1}月
                </span>
                <div style={{ display: 'flex', gap: 6 }}>
                  <button className="btn btn-sm" onClick={() => setCurrentDate(new Date(currentDate.getFullYear(), currentDate.getMonth() - 1, 1))} style={{ fontSize: 14, padding: '4px 10px' }}>‹</button>
                  <button className="btn btn-sm" onClick={() => setCurrentDate(new Date(currentDate.getFullYear(), currentDate.getMonth() + 1, 1))} style={{ fontSize: 14, padding: '4px 10px' }}>›</button>
                </div>
              </div>
              <MiniMonth
                year={currentDate.getFullYear()}
                month={currentDate.getMonth() + 1}
                data={data}
                todayStr={todayStr}
                selectedDate={selectedDate}
                onSelectDate={(d) => { setSelectedDate(d); setCurrentDate(d) }}
                goalDateMap={goalDateMap}
                goalAmountMap={goalAmountMap}
                goalActiveMap={goalActiveMap}
                goalDisplayField={goalDisplayField}
                goals={goals}
              />
              {data?.summary && (
                <div style={{ display: 'flex', gap: 14, fontSize: 14, color: 'var(--color-text-muted)', marginTop: 12, flexWrap: 'wrap' }}>
                  <span>📅{data.summary.schedules}</span>
                  <span>📝{data.summary.notes}</span>
                  <span>💬{data.summary.conversations}</span>
                  <span>🧠{data.summary.memories}</span>
                </div>
              )}
            </div>

            {/* ====== 目标追踪板块 ====== */}
            {goals.length > 0 ? (
              <Section title="目标追踪" icon="🎯" color="#50fa7b">
                {/* 显示字段切换 + 主目标说明 */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 8, flexWrap: 'wrap' }}>
                  {([
                    { key: 'current_value', label: '现金额' },
                    { key: 'daily_target', label: '日化目标' },
                    { key: 'target_value', label: '目标金额' },
                  ] as const).map(opt => (
                    <button
                      key={opt.key}
                      className="btn btn-sm"
                      onClick={() => setGoalDisplayField(opt.key)}
                      style={{
                        fontSize: 10, padding: '2px 8px',
                        background: goalDisplayField === opt.key ? 'var(--color-accent-primary)' : 'var(--color-bg-input)',
                        color: goalDisplayField === opt.key ? '#fff' : 'var(--color-text-muted)',
                        border: `1px solid ${goalDisplayField === opt.key ? 'var(--color-accent-primary)' : 'var(--color-border)'}`,
                        borderRadius: 4,
                      }}
                    >
                      {opt.label}
                    </button>
                  ))}
                  <span style={{ fontSize: 10, color: 'var(--color-text-muted)', marginLeft: 'auto' }}>
                    {goals.length} 个目标
                  </span>
                </div>

                {goals.map((g, idx) => {
                  const s = goalStats[g.id]
                  const progress = s?.progress ?? ((g.current_value - g.start_value) / (g.target_value - g.start_value) * 100)
                  const color = GOAL_COLORS[idx % GOAL_COLORS.length]
                  const activeDays = g.active_days || []
                  const rate = g.daily_target || 5
                  const factor = 1 + rate / 100
                  const requiredDays = (g.current_value > 0 && g.target_value > g.current_value && factor > 1)
                    ? Math.ceil(Math.log(g.target_value / g.current_value) / Math.log(factor))
                    : 0
                  return (
                    <div
                      key={g.id}
                      onClick={() => navigate('/calendar')}
                      style={{
                        padding: '8px 10px',
                        background: 'var(--color-bg-input)',
                        borderRadius: 8,
                        marginBottom: 6,
                        borderLeft: `3px solid ${color}`,
                        cursor: 'pointer',
                        transition: 'all 0.15s',
                      }}
                      title="点击进入日历查看详情"
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0, flex: 1 }}>
                          <div style={{ width: 8, height: 8, borderRadius: '50%', background: color, flexShrink: 0 }} />
                          <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {g.title}
                          </span>
                        </div>
                        <span style={{ fontSize: 12, fontWeight: 700, color: progress >= 100 ? '#50fa7b' : 'var(--color-text-secondary)', flexShrink: 0, marginLeft: 8 }}>
                          {progress.toFixed(0)}%
                        </span>
                      </div>

                      <div style={{ background: 'var(--color-bg-muted)', borderRadius: 4, height: 6, marginTop: 6, overflow: 'hidden' }}>
                        <div style={{ width: `${Math.min(progress, 100)}%`, height: 6, borderRadius: 4, background: color, transition: 'width 0.3s' }} />
                      </div>

                      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6, fontSize: 11, color: 'var(--color-text-muted)' }}>
                        <span>{formatMoney(g.current_value)} / {formatMoney(g.target_value)}</span>
                        <span style={{ color: activeDays.length >= requiredDays && requiredDays > 0 ? '#50fa7b' : '#f1fa8c' }}>
                          已激活 {activeDays.length} 天{requiredDays > 0 ? ` / 需 ${requiredDays} 天` : ''}
                        </span>
                      </div>
                    </div>
                  )
                })}
              </Section>
            ) : (
              <div style={{ textAlign: 'center', padding: 12, color: 'var(--color-text-muted)', fontSize: 12 }}>
                🎯 暂无目标
                <button
                  className="btn btn-sm"
                  onClick={() => { setEditingGoal(null); setShowGoalModal(true) }}
                  style={{ marginLeft: 8, fontSize: 11, background: 'var(--color-accent-primary)', color: '#fff' }}
                >
                  + 新建
                </button>
              </div>
            )}

            {/* 新建目标按钮 */}
            {goals.length > 0 && (
              <button
                className="btn btn-sm"
                onClick={() => { setEditingGoal(null); setShowGoalModal(true) }}
                style={{ width: '100%', fontSize: 11, background: 'var(--color-accent-primary)', color: '#fff', marginTop: 4 }}
              >
                + 新建目标
              </button>
            )}
          </div>

          {/* ====== 右侧面板：Outlet ====== */}
          <div className="app-layout-right">
            <Outlet />
          </div>
        </div>
      </div>

      {/* ===== 目标创建/编辑模态框 ===== */}
      {showGoalModal && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
        }} onClick={() => { setShowGoalModal(false); setEditingGoal(null) }}>
          <GoalModalForm
            editingGoal={editingGoal}
            onSubmit={handleGoalSubmit}
            onCancel={() => { setShowGoalModal(false); setEditingGoal(null) }}
            onDelete={editingGoal ? () => { setShowGoalDelete(editingGoal); setShowGoalModal(false) } : undefined}
          />
        </div>
      )}

      {/* ===== 目标删除确认 ===== */}
      {showGoalDelete && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
        }} onClick={() => setShowGoalDelete(null)}>
          <div style={{
            background: 'var(--color-bg-panel)', border: '1px solid var(--color-border)',
            borderRadius: 12, padding: 24, maxWidth: 400, width: '90%',
          }} onClick={e => e.stopPropagation()}>
            <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12 }}>确认删除</h3>
            <p style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>
              确定要删除 "{showGoalDelete.title}" 及其所有进度数据吗？
            </p>
            <div style={{ display: 'flex', gap: 8, marginTop: 20 }}>
              <button className="btn btn-sm" onClick={() => setShowGoalDelete(null)}>取消</button>
              <button className="btn btn-sm" style={{ background: '#ff5555', color: '#fff' }} onClick={handleGoalDelete}>删除</button>
            </div>
          </div>
        </div>
      )}
    </CalendarGoalContext.Provider>
  )
}

/* 目标表单组件 */
function GoalModalForm({ editingGoal, onSubmit, onCancel, onDelete }: {
  editingGoal: Goal | null
  onSubmit: (form: { title: string; start_value: string; target_value: string; daily_target: string; current_value?: string }) => Promise<void>
  onCancel: () => void
  onDelete?: () => void
}) {
  const [formTitle, setFormTitle] = useState(editingGoal?.title || '')
  const [formStart, setFormStart] = useState(editingGoal ? String(editingGoal.start_value) : '')
  const [formTarget, setFormTarget] = useState(editingGoal ? String(editingGoal.target_value) : '')
  const [formDaily, setFormDaily] = useState(editingGoal ? String(editingGoal.daily_target) : '5')
  const [formCurrent, setFormCurrent] = useState(editingGoal ? String(editingGoal.current_value) : '')

  return (
    <div style={{
      background: 'var(--color-bg-panel)', border: '1px solid var(--color-border)',
      borderRadius: 12, padding: 24, maxWidth: 500, width: '90%',
      boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
    }} onClick={e => e.stopPropagation()}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h3 style={{ fontSize: 16, fontWeight: 600 }}>{editingGoal ? '编辑目标' : '新建目标'}</h3>
        <button className="btn-icon" style={{ width: 32, height: 32, fontSize: 18 }} onClick={onCancel}>×</button>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <input className="form-input" placeholder="目标名称" value={formTitle} onChange={e => setFormTitle(e.target.value)} autoFocus />
        <div style={{ display: 'flex', gap: 8 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 4 }}>起始本金</div>
            <input type="number" className="form-input" placeholder="10000" value={formStart} onChange={e => setFormStart(e.target.value)} />
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 4 }}>目标金额</div>
            <input type="number" className="form-input" placeholder="150000" value={formTarget} onChange={e => setFormTarget(e.target.value)} />
          </div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 4 }}>目标日化收益率 (%)</div>
          <input type="number" className="form-input" placeholder="5" value={formDaily} onChange={e => setFormDaily(e.target.value)} step="0.1" />
        </div>
        {editingGoal && (
          <div>
            <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 4 }}>当前余额（更新进度）</div>
            <input type="number" className="form-input" placeholder={String(editingGoal.current_value)} value={formCurrent} onChange={e => setFormCurrent(e.target.value)} />
          </div>
        )}
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 20 }}>
        {onDelete && (
          <button className="btn btn-sm" style={{ background: '#ff5555', color: '#fff' }} onClick={onDelete}>删除</button>
        )}
        <button className="btn btn-sm" onClick={onCancel}>取消</button>
        <button className="btn btn-sm" style={{ background: 'var(--color-accent-primary)', color: '#fff' }} onClick={() => onSubmit({ title: formTitle, start_value: formStart, target_value: formTarget, daily_target: formDaily, current_value: formCurrent })}>
          {editingGoal ? '保存' : '创建'}
        </button>
      </div>
    </div>
  )
}

/* 小月历组件 */
function MiniMonth({ year, month, data, todayStr, selectedDate, onSelectDate, goalDateMap, goalAmountMap, goalActiveMap, goalDisplayField, goals }: {
  year: number; month: number; data: CalendarData | null; todayStr: string;
  selectedDate: Date;
  onSelectDate: (d: Date) => void;
  goalDateMap: Map<string, GoalMarkInfo[]>;
  goalAmountMap: Map<string, GoalAmountInfo[]>;
  goalActiveMap: Map<string, GoalActiveInfo[]>;
  goalDisplayField: GoalDisplayField;
  goals: Goal[];
}) {
  const firstDay = new Date(year, month - 1, 1).getDay() || 7
  const daysInMonth = new Date(year, month, 0).getDate()
  const miniWeekdays = ['一', '二', '三', '四', '五', '六', '日']
  const selectedKey = formatDate(selectedDate)

  const cells: ({ day: number; key: string } | null)[] = []
  for (let i = 1; i < firstDay; i++) cells.push(null)
  for (let d = 1; d <= daysInMonth; d++) {
    const key = `${year}-${String(month).padStart(2, '0')}-${String(d).padStart(2, '0')}`
    cells.push({ day: d, key })
  }
  while (cells.length % 7 !== 0) cells.push(null)

  // 根据显示字段决定月历上显示什么：多目标聚合
  const getDisplayForCell = (cellKey: string): { text: string; color: string } | null => {
    if (cellKey < todayStr || goals.length === 0) return null
    if (goalDisplayField === 'daily_target') {
      const goalAmts = goalAmountMap.get(cellKey)
      if (goalAmts && goalAmts.length > 0) {
        const total = goalAmts.reduce((sum, a) => sum + a.amount, 0)
        return { text: total >= 10000 ? `${(total / 10000).toFixed(1)}万` : total.toFixed(0), color: '#8be9fd' }
      }
      return null
    } else if (goalDisplayField === 'current_value') {
      const total = goals.reduce((sum, g) => sum + g.current_value, 0)
      return { text: total >= 10000 ? `${(total / 10000).toFixed(1)}万` : total.toFixed(0), color: '#50fa7b' }
    } else if (goalDisplayField === 'target_value') {
      const total = goals.reduce((sum, g) => sum + g.target_value, 0)
      return { text: total >= 10000 ? `${(total / 10000).toFixed(1)}万` : total.toFixed(0), color: '#f1fa8c' }
    }
    return null
  }

  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 4 }}>
        {miniWeekdays.map(w => (
          <div key={w} style={{ textAlign: 'center', fontSize: 13, color: 'var(--color-text-muted)', padding: '4px 0', fontWeight: 500 }}>{w}</div>
        ))}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 4 }}>
        {cells.map((cell, i) => {
          if (!cell) return <div key={i} style={{ minHeight: 42 }} />
          const hasEvents = (data?.days[cell.key]?.schedules?.length ?? 0) > 0
          const isToday = cell.key === todayStr
          const isSelected = cell.key === selectedKey
          const goalMarks = goalDateMap.get(cell.key) || []
          const goalActives = goalActiveMap.get(cell.key) || []
          const displayVal = getDisplayForCell(cell.key)
          const showAmount = displayVal !== null
          return (
            <div
              key={i}
              onClick={() => onSelectDate(parseLocalDate(cell.key))}
              style={{
                textAlign: 'center', fontSize: 14, padding: '6px 0 3px', borderRadius: 5,
                cursor: 'pointer',
                color: isSelected ? '#fff' : isToday ? 'var(--color-accent-primary)' : hasEvents ? 'var(--color-text-primary)' : 'var(--color-text-muted)',
                fontWeight: isSelected || isToday ? 700 : hasEvents ? 500 : 400,
                background: isSelected ? 'var(--color-accent-primary)' : isToday ? 'rgba(189,147,249,0.12)' : hasEvents ? 'var(--color-bg-input)' : 'transparent',
                position: 'relative',
              }}
            >
              <div>{cell.day}</div>
              {showAmount && (
                <div style={{ fontSize: 11, color: isSelected ? '#fff9' : displayVal!.color, fontWeight: 600, marginTop: 2 }}>
                  {displayVal!.text}
                </div>
              )}
              {/* 目标激活日期指示条 */}
              {goalActives.length > 0 && (
                <div style={{ display: 'flex', justifyContent: 'center', gap: 2, marginTop: 3, flexWrap: 'wrap' }}>
                  {goalActives.slice(0, 5).map((m, j) => (
                    <div key={j} style={{
                      width: 8, height: 4, borderRadius: 2, background: isSelected ? '#fff8' : m.color,
                    }} title={`${m.title} 已激活`} />
                  ))}
                </div>
              )}
              {goalMarks.length > 0 && (
                <div style={{ display: 'flex', justifyContent: 'center', gap: 2, marginTop: 2, flexWrap: 'wrap' }}>
                  {goalMarks.slice(0, 5).map((m, j) => (
                    <div key={j} style={{ width: 7, height: 7, borderRadius: '50%', background: isSelected ? '#fff8' : m.color }} title={`${m.title} ${m.label}`} />
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

/* 分节标题组件 */
function Section({ title, icon, color, children }: { title: string; icon: string; color: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color, marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
        <span>{icon}</span>{title}
      </div>
      {children}
    </div>
  )
}
