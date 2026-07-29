import { useState, useEffect } from 'react'
import { useCalendarGoal } from '../contexts/CalendarGoalContext'
import { useNavigate } from 'react-router-dom'
import { api, type CalendarData } from '../shared/api'
import { Icon } from '../components/Icon'
import { setPendingMessage } from '../shared/pendingMessage'

const WK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

export default function DashboardView() {
  const { todayStr, data, loading } = useCalendarGoal()
  const navigate = useNavigate()
  const [stats, setStats] = useState({ conv: 0, notes: 0, memories: 0, skills: 0, kb: '检测中' })
  const [localCal, setLocalCal] = useState<CalendarData | null>(null)

  // 当天日程：优先用 AppLayout 已加载的 context 数据
  const ctxSchedules = data?.days?.[todayStr]?.schedules ?? []
  useEffect(() => {
    if (!loading && data && !data.days?.[todayStr]) {
      const d = new Date()
      api.getCalendar(d.getFullYear(), d.getMonth() + 1).then(setLocalCal).catch(() => {})
    }
  }, [loading, data, todayStr])
  const rawSchedules = (localCal ?? data)?.days?.[todayStr]?.schedules ?? ctxSchedules
  const todaySchedules = [...rawSchedules].sort((a, b) =>
    (a.start_time || '99').localeCompare(b.start_time || '99'))

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const [conv, notes, memories, skills] = await Promise.all([
          api.listConversations(),
          api.listNotes(),
          api.listMemories(),
          api.listSkills(),
        ])
        if (!alive) return
        let kb = '离线'
        try { const h = await api.knowledgeHealth(); kb = h.status === 'ok' ? '就绪' : '异常' } catch { kb = '离线' }
        if (!alive) return
        setStats({ conv: conv.length, notes: notes.length, memories: memories.length, skills: skills.length, kb })
      } catch {
        setStats(s => ({ ...s, kb: '离线' }))
      }
    })()
    return () => { alive = false }
  }, [])

  const hour = new Date().getHours()
  const greet = hour < 6 ? '凌晨好' : hour < 12 ? '早上好' : hour < 14 ? '中午好' : hour < 18 ? '下午好' : '晚上好'

  const [y, m, d] = todayStr.split('-').map(Number)
  const dateLabel = `${y}年${m}月${d}日 ${WK[new Date(y, m - 1, d).getDay()]}`

  const SECTIONS = [
    { key: 'chat', name: '对话', path: '/chat', color: '#8be9fd', icon: 'chat', badge: String(stats.conv), desc: 'AI 智能对话，自动识别日程 / 笔记 / 记忆' },
    { key: 'calendar', name: '日历日程', path: '/calendar', color: '#50fa7b', icon: 'calendar', badge: `${todaySchedules.length} 今日`, desc: '周视图日历 + 财经事件联动 + 目标追踪' },
    { key: 'notes', name: '笔记', path: '/notes', color: '#bd93f9', icon: 'note', badge: String(stats.notes), desc: 'AI 智能分类、内容互转与 Markdown 渲染' },
    { key: 'memories', name: '记忆库', path: '/memories', color: '#ffb86c', icon: 'memory', badge: String(stats.memories), desc: '自动蒸馏提取、多维分类与记忆整理' },
    { key: 'skills', name: '技能卡片', path: '/skills', color: '#ff79c6', icon: 'skill', badge: String(stats.skills), desc: '可复用操作流程，AI 自动匹配场景' },
    { key: 'knowledge', name: '知识库', path: '/knowledge', color: '#f1fa8c', icon: 'knowledge', badge: stats.kb, desc: 'PDF 入库 + 向量检索 + 上下文增强' },
  ]

  return (
    <div className="dash-view">
      <div className="dash-scroll">
        {/* 问候 + 日期 */}
        <div className="dash-greet">
          <div>
            <h2>{greet}，whathelio 👋</h2>
            <div className="dash-date">{dateLabel}</div>
          </div>
          <div className="dash-quick">
            <button className="btn btn-primary" onClick={() => navigate('/chat')}>💬 新对话</button>
            <button className="btn btn-ghost" onClick={() => navigate('/notes')}>📝 记笔记</button>
          </div>
        </div>

        {/* 今日日程 */}
        <div>
          <div className="dash-today-head">
            <span className="t">今日日程</span>
            <span className="c">{todaySchedules.length} 项</span>
          </div>
          {todaySchedules.length === 0 ? (
            <div className="dash-today-empty">今天还没有安排，去 /calendar 添加吧</div>
          ) : (
            <div className="dash-today-list">
              {todaySchedules.map(s => {
                const time = (s.start_time || '').slice(11, 16) || '—'
                const done = /done|completed|finished/i.test(s.status || '')
                const high = /high|important/i.test(s.priority || '')
                const cls = done ? 'today-tag--done' : high ? 'today-tag--high' : 'today-tag--default'
                const label = done ? '已完成' : high ? '重要' : '待办'
                return (
                  <div key={s.id} className={`today-row ${done ? 'is-done' : ''}`}>
                    <span className="today-time">{time}</span>
                    <span className="today-title">{s.title}</span>
                    <span className={`today-tag ${cls}`}>{label}</span>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* 板块总览 */}
        <div className="dash-section-title">
          <span>功能板块总览</span>
        </div>
        <div className="dash-grid">
          {SECTIONS.map(s => (
            <div key={s.key} className="ov-card" style={{ ['--ov-color' as any]: s.color }} onClick={() => navigate(s.path)}>
              <div className="ov-head">
                <div className="ov-title">
                  <span className="ov-icon" style={{ background: s.color + '22', color: s.color }}>
                    <Icon name={s.icon} />
                  </span>
                  {s.name}
                </div>
                <span className="ov-badge" style={{
                  background: s.key === 'knowledge'
                    ? (stats.kb === '就绪' ? 'rgba(80,250,123,0.15)' : 'rgba(255,85,85,0.15)')
                    : 'var(--color-bg-input)',
                  color: s.key === 'knowledge'
                    ? (stats.kb === '就绪' ? '#50fa7b' : '#ff5555')
                    : 'var(--color-text-secondary)',
                }}>{s.badge}</span>
              </div>
              <div className="ov-desc">{s.desc}</div>
              <div className="ov-link">进入 {s.name} →</div>
            </div>
          ))}
        </div>
      </div>

      {/* 底部 AI 快捷输入条 */}
      <QuickInputBar />
    </div>
  )
}

function QuickInputBar() {
  const [text, setText] = useState('')
  const navigate = useNavigate()
  const send = () => {
    const t = text.trim()
    if (!t) return
    setPendingMessage(t)
    navigate('/chat')
  }
  return (
    <div className="dash-quickbar">
      <div className="dash-quickbar-inner">
        <textarea
          className="dash-quick-input" rows={1}
          placeholder="输入内容，AI 自动识别日程 / 笔记 / 记忆..."
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
        />
        <button className="dash-quick-send" onClick={send} disabled={!text.trim()} title="发送 (Enter)">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" /></svg>
        </button>
      </div>
      <div className="dash-quick-chips">
        {['记一条笔记', '加个日程', '总结今天'].map(c => (
          <span key={c} className="dash-chip" onClick={() => setText(c)}>{c}</span>
        ))}
      </div>
    </div>
  )
}
