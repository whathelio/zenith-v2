import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type Goal, type GoalStats } from '../shared/api'

export default function GoalsView() {
  const navigate = useNavigate()
  const [goals, setGoals] = useState<Goal[]>([])
  const [stats, setStats] = useState<Record<number, GoalStats>>({})
  const [loading, setLoading] = useState(false)
  const [showCreate, setShowCreate] = useState(false)
  const [editingGoal, setEditingGoal] = useState<Goal | null>(null)
  const [showDelete, setShowDelete] = useState<Goal | null>(null)

  // Form
  const [formTitle, setFormTitle] = useState('')
  const [formStart, setFormStart] = useState('')
  const [formTarget, setFormTarget] = useState('')
  const [formDaily, setFormDaily] = useState('5')
  const [formCurrent, setFormCurrent] = useState('')

  const loadGoals = async () => {
    setLoading(true)
    try {
      const gs = await api.listGoals()
      setGoals(gs)
      // Load stats for each
      const ss: Record<number, GoalStats> = {}
      for (const g of gs) {
        try {
          ss[g.id] = await api.getGoalStats(g.id)
        } catch { /* skip */ }
      }
      setStats(ss)
    } catch { /* silent */ }
    finally { setLoading(false) }
  }

  useEffect(() => { loadGoals() }, [])

  const openCreate = () => {
    setEditingGoal(null)
    setFormTitle('')
    setFormStart('')
    setFormTarget('')
    setFormDaily('5')
    setFormCurrent('')
    setShowCreate(true)
  }

  const openEdit = (g: Goal) => {
    setEditingGoal(g)
    setFormTitle(g.title)
    setFormStart(String(g.start_value))
    setFormTarget(String(g.target_value))
    setFormDaily(String(g.daily_target))
    setFormCurrent(String(g.current_value))
    setShowCreate(true)
  }

  const handleSubmit = async () => {
    if (!formTitle.trim() || !formTarget) return
    try {
      if (editingGoal) {
        await api.updateGoal(editingGoal.id, {
          title: formTitle.trim(),
          start_value: Number(formStart),
          target_value: Number(formTarget),
          daily_target: Number(formDaily),
          current_value: formCurrent ? Number(formCurrent) : undefined,
        })
      } else {
        await api.createGoal({
          title: formTitle.trim(),
          start_value: formStart ? Number(formStart) : 0,
          target_value: Number(formTarget),
          daily_target: Number(formDaily),
        })
      }
      setShowCreate(false)
      loadGoals()
    } catch (err) {
      console.error('保存失败', err)
    }
  }

  const handleDelete = async () => {
    if (!showDelete) return
    try {
      await api.deleteGoal(showDelete.id)
      setShowDelete(null)
      loadGoals()
    } catch (err) {
      console.error('删除失败', err)
    }
  }

  const handleUpdateProgress = async (g: Goal, currentValue: number) => {
    try {
      await api.updateGoal(g.id, { current_value: currentValue })
      loadGoals()
    } catch (err) {
      console.error('更新进度失败', err)
    }
  }

  const formatMoney = (v: number) => {
    if (v >= 10000) return (v / 10000).toFixed(2) + '万'
    return v.toLocaleString()
  }

  return (
    <>
      <div className="main-area" style={{ maxWidth: '100vw' }}>
        <div className="topbar">
          <span className="topbar-title">🎯 目标追踪</span>
          <div className="topbar-actions">
            <a href="/" className="btn btn-sm">🏠 主页</a>
            <a href="/calendar" className="btn btn-sm">📅 日历</a>
            <a href="/schedules" className="btn btn-sm">📋 日程</a>
            <a href="/memories" className="btn btn-sm">🧠 记忆</a>
            <a href="/settings" className="btn btn-sm">⚙ 设置</a>
          </div>
        </div>

        <div className="goals-container">
          <div className="goals-toolbar">
            <button className="btn btn-accent" onClick={openCreate}>
              + 新建目标
            </button>
          </div>

          {loading ? (
            <div className="cal-empty"><span>加载中...</span></div>
          ) : goals.length === 0 ? (
            <div className="cal-empty">
              <span style={{ fontSize: 32 }}>🎯</span>
              <span>暂无目标</span>
              <span style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-text-muted)' }}>
                创建一个复利目标，开始追踪你的进度
              </span>
            </div>
          ) : (
            <div className="goals-list">
              {goals.map(g => {
                const s = stats[g.id]
                const progress = s?.progress ?? 0
                return (
                  <div
                    key={g.id}
                    className={`goal-card ${g.status === 'active' ? 'goal-active' : ''}`}
                    onClick={() => openEdit(g)}
                  >
                    <div className="goal-header">
                      <span className="goal-title">{g.title}</span>
                      <span className={`goal-status goal-status-${g.status}`}>
                        {g.status === 'active' ? '进行中' : g.status === 'completed' ? '已完成' : '已取消'}
                      </span>
                    </div>

                    {/* Progress bar */}
                    <div className="goal-progress-bar">
                      <div className="goal-progress-fill" style={{ width: `${Math.min(progress, 100)}%` }} />
                    </div>
                    <div className="goal-progress-text">
                      <span>{progress}%</span>
                      <span>{formatMoney(g.current_value)} / {formatMoney(g.target_value)}</span>
                    </div>

                    {s && g.status === 'active' && (
                      <div className="goal-stats-row">
                        <div className="goal-stat">
                          <span className="goal-stat-label">日化收益</span>
                          <span className={`goal-stat-value ${s.daily_return >= g.daily_target ? 'goal-stat-good' : 'goal-stat-bad'}`}>
                            {s.daily_return}%
                          </span>
                        </div>
                        <div className="goal-stat">
                          <span className="goal-stat-label">已过天数</span>
                          <span className="goal-stat-value">{s.days_passed}/{s.days_total}</span>
                        </div>
                        <div className="goal-stat">
                          <span className="goal-stat-label">剩余</span>
                          <span className="goal-stat-value">{formatMoney(s.remaining)}</span>
                        </div>
                        <div className="goal-stat">
                          <span className="goal-stat-label">状态</span>
                          <span className={`goal-stat-value ${s.on_track ? 'goal-stat-good' : 'goal-stat-bad'}`}>
                            {s.on_track ? '✓ 在轨' : '⚠ 偏离'}
                          </span>
                        </div>
                      </div>
                    )}

                    {/* Quick update (only for active) */}
                    {g.status === 'active' && (
                      <div className="goal-quick-update" onClick={e => e.stopPropagation()}>
                        <span className="goal-quick-label">快速更新余额：</span>
                        <input
                          type="number"
                          className="goal-quick-input"
                          placeholder={String(g.current_value)}
                          onKeyDown={e => {
                            if (e.key === 'Enter') {
                              handleUpdateProgress(g, Number((e.target as HTMLInputElement).value))
                            }
                          }}
                        />
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* Create/Edit Modal */}
      {showCreate && (
        <div className="modal-overlay" onClick={() => setShowCreate(false)}>
          <div className="modal-panel" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <span>{editingGoal ? '编辑目标' : '新建目标'}</span>
              <button className="modal-close" onClick={() => setShowCreate(false)}>✕</button>
            </div>

            <div className="cal-form">
              <input
                className="cal-form-input"
                placeholder="目标名称"
                value={formTitle}
                onChange={e => setFormTitle(e.target.value)}
                autoFocus
              />
              <div className="cal-form-row">
                <div style={{ flex: 1 }}>
                  <span className="cal-form-label">起始本金</span>
                  <input
                    type="number"
                    className="cal-form-input"
                    placeholder="10000"
                    value={formStart}
                    onChange={e => setFormStart(e.target.value)}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <span className="cal-form-label">目标金额</span>
                  <input
                    type="number"
                    className="cal-form-input"
                    placeholder="20000"
                    value={formTarget}
                    onChange={e => setFormTarget(e.target.value)}
                  />
                </div>
              </div>
              <div>
                <span className="cal-form-label">目标日化收益率 (%)</span>
                <input
                  type="number"
                  className="cal-form-input"
                  placeholder="5"
                  value={formDaily}
                  onChange={e => setFormDaily(e.target.value)}
                  step="0.1"
                />
              </div>
              {editingGoal && (
                <div>
                  <span className="cal-form-label">当前余额（更新进度）</span>
                  <input
                    type="number"
                    className="cal-form-input"
                    placeholder={String(editingGoal.current_value)}
                    value={formCurrent}
                    onChange={e => setFormCurrent(e.target.value)}
                  />
                </div>
              )}
            </div>

            <div className="modal-actions">
              {editingGoal && (
                <button className="btn btn-danger" onClick={() => { setShowDelete(editingGoal); setShowCreate(false) }}>
                  删除
                </button>
              )}
              <button className="btn" onClick={() => setShowCreate(false)}>取消</button>
              <button className="btn btn-accent" onClick={handleSubmit}>
                {editingGoal ? '保存' : '创建'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirm */}
      {showDelete && (
        <div className="modal-overlay" onClick={() => setShowDelete(null)}>
          <div className="modal-panel modal-sm" onClick={e => e.stopPropagation()}>
            <div className="modal-header">确认删除</div>
            <p style={{ padding: '16px 20px', margin: 0 }}>确定要删除 "{showDelete.title}" 及其所有进度数据吗？</p>
            <div className="modal-actions">
              <button className="btn" onClick={() => setShowDelete(null)}>取消</button>
              <button className="btn btn-danger" onClick={handleDelete}>删除</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
