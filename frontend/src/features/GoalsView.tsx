import { useState } from 'react'
import { api, type Goal } from '../shared/api'
import GoalDetailModal from '../components/GoalDetailModal'
import { useCalendarGoal } from '../contexts/CalendarGoalContext'

function formatMoney(v: number): string {
  if (v >= 100000) return `${(v / 10000).toFixed(1)}万`
  if (v >= 10000) return `${(v / 10000).toFixed(2)}万`
  return v.toLocaleString()
}

/* ══════════════════════════════════════════
   GoalsView — 目标列表 + 详情入口
   ══════════════════════════════════════════ */
export default function GoalsView() {
  // 目标状态统一走 CalendarGoalContext（S2 灰度收敛）
  const { goals, goalStats: stats, loadGoals: reloadGoals } = useCalendarGoal()
  const [showCreate, setShowCreate] = useState(false)
  const [editingGoal, setEditingGoal] = useState<Goal | null>(null)
  const [showDelete, setShowDelete] = useState<Goal | null>(null)
  const [detailGoal, setDetailGoal] = useState<Goal | null>(null)

  // Form
  const [formTitle, setFormTitle] = useState('')
  const [formStart, setFormStart] = useState('')
  const [formTarget, setFormTarget] = useState('')
  const [formDaily, setFormDaily] = useState('5')
  const [formCurrent, setFormCurrent] = useState('')

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
      reloadGoals()
    } catch (err) {
      console.error('保存失败', err)
    }
  }

  const handleDelete = async () => {
    if (!showDelete) return
    try {
      await api.deleteGoal(showDelete.id)
      setShowDelete(null)
      setEditingGoal(null)
      reloadGoals()
    } catch (err) {
      console.error('删除失败', err)
    }
  }

  const handleUpdateProgress = async (g: Goal, currentValue: number) => {
    try {
      await api.updateGoal(g.id, { current_value: currentValue })
      reloadGoals()
    } catch (err) {
      console.error('更新进度失败', err)
    }
  }

  return (
    <>
      <div className="main-area" style={{ maxWidth: '100vw' }}>
        <div className="topbar">
          <span className="topbar-title">🎯 目标追踪</span>
          <div className="topbar-actions">
            <a href="/" className="btn btn-sm">🏠 主页</a>
            <a href="/calendar" className="btn btn-sm">📅 日程</a>
            <a href="/library" className="btn btn-sm">📚 知识库</a>
            <a href="/settings" className="btn btn-sm">⚙ 设置</a>
          </div>
        </div>

        <div className="goals-container">
          <div className="goals-toolbar">
            <button className="btn btn-accent" onClick={openCreate}>
              + 新建目标
            </button>
          </div>

          {goals.length === 0 ? (
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
                    onClick={() => setDetailGoal(g)}
                    style={{ cursor: 'pointer' }}
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
                      <span>{progress.toFixed(0)}%</span>
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

      {/* Goal Detail Modal */}
      {detailGoal && (
        <GoalDetailModal
          goal={detailGoal}
          stats={stats[detailGoal.id] || null}
          onClose={() => setDetailGoal(null)}
          onUpdate={() => {
            reloadGoals()
            api.getGoal(detailGoal.id).then(g => setDetailGoal(g)).catch(() => {})
          }}
          onEdit={() => {
            setEditingGoal(detailGoal)
            setFormTitle(detailGoal.title)
            setFormStart(String(detailGoal.start_value))
            setFormTarget(String(detailGoal.target_value))
            setFormDaily(String(detailGoal.daily_target))
            setFormCurrent(String(detailGoal.current_value))
            setShowCreate(true)
            setDetailGoal(null)
          }}
        />
      )}

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
