import { useCalendarGoal } from '../contexts/CalendarGoalContext'
import { useNavigate } from 'react-router-dom'
import { api, type Schedule } from '../shared/api'

const STATUS_COLORS: Record<string, string> = {
  confirmed: '#50fa7b', proposed: '#f1fa8c', done: '#8be9fd', cancelled: '#ff5555',
}
const STATUS_BG_COLORS: Record<string, string> = {
  proposed: 'rgba(241,250,140,0.08)', confirmed: 'rgba(80,250,123,0.06)', done: 'rgba(139,233,253,0.05)', cancelled: 'rgba(113,126,149,0.04)',
}
const statusNames: Record<string, string> = {
  proposed: '待确认', confirmed: '已确认', done: '已完成', cancelled: '已取消',
}
const PRIORITY_COLORS: Record<string, string> = {
  high: '#ff5555', normal: '#bd93f9', low: '#717e95',
}
const MEMORY_TYPE_COLORS: Record<string, string> = {
  personal_info: '#ff79c6', preference: '#8be9fd', event: '#50fa7b',
  decision: '#f1fa8c', fact: '#bd93f9', experience: '#ff6e40',
}

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

export default function DashboardView() {
  const { selectedDayKey, selectedDayData, loading, todayStr, marketReport } = useCalendarGoal()
  const navigate = useNavigate()

  const hasContent = selectedDayData && (
    selectedDayData.schedules.length > 0 ||
    selectedDayData.notes.length > 0 ||
    selectedDayData.conversations.length > 0 ||
    selectedDayData.memories.length > 0
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, height: '100%', overflowY: 'auto', padding: '12px 16px' }}>
      <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--color-text-primary)' }}>
        {selectedDayKey === todayStr ? '今天' : selectedDayKey} 的详情
      </div>

      {loading ? (
        <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>加载中...</div>
      ) : !hasContent ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'center', padding: 24, color: 'var(--color-text-muted)' }}>
          <div style={{ fontSize: 40, opacity: 0.4 }}>📭</div>
          <div style={{ fontSize: 14 }}>当天暂无内容</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-sm" onClick={() => navigate('/schedules')} style={{ background: 'var(--color-accent-primary)', color: '#fff' }}>
              + 新日程
            </button>
            <button className="btn btn-sm" onClick={() => navigate('/notes')} style={{ background: 'var(--color-accent-secondary)', color: '#fff' }}>
              + 新笔记
            </button>
          </div>
        </div>
      ) : (
        <>
          {/* 日程 — 可点击跳转，待确认可一键确认/拒绝 */}
          {selectedDayData!.schedules.length > 0 && (
            <Section title="日程" icon="📅" color="#bd93f9">
              {selectedDayData!.schedules.map(s => {
                const isProposed = s.status === 'proposed'
                const isConfirmed = s.status === 'confirmed'
                const borderColor = STATUS_COLORS[s.status] || '#717e95'
                const bgColor = STATUS_BG_COLORS[s.status] || 'var(--color-bg-input)'
                return (
                  <div
                    key={s.id}
                    style={{
                      padding: '8px 12px', marginBottom: 5, borderRadius: 5,
                      background: bgColor, fontSize: 14,
                      borderLeft: `4px solid ${borderColor}`,
                      cursor: 'pointer', transition: 'all 0.15s',
                    }}
                    onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--color-accent-primary)'; e.currentTarget.style.background = 'var(--color-bg-hover)' }}
                    onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--color-border)'; e.currentTarget.style.background = bgColor }}
                    onClick={() => navigate('/schedules')}
                  >
                    <div style={{ color: 'var(--color-text-primary)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span style={{ fontWeight: 500 }}>{s.title}</span>
                      <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                        <span style={{ fontSize: 11, color: borderColor, fontWeight: 600 }}>
                          {isProposed ? '⏳' : isConfirmed ? '✓' : s.status === 'done' ? '✅' : '✗'} {statusNames[s.status]}
                        </span>
                        {isProposed && (
                          <>
                            <button
                              style={{ padding: '2px 8px', borderRadius: 4, background: STATUS_COLORS.confirmed, color: '#000', fontWeight: 700, fontSize: 10, border: 'none', cursor: 'pointer' }}
                              onClick={(e) => { e.stopPropagation(); api.updateSchedule(s.id, { status: 'confirmed' }) }}
                            >✓ 确认</button>
                            <button
                              style={{ padding: '2px 6px', borderRadius: 4, background: 'rgba(255,85,85,0.15)', color: '#ff5555', fontWeight: 600, fontSize: 10, border: '1px solid rgba(255,85,85,0.3)', cursor: 'pointer' }}
                              onClick={(e) => { e.stopPropagation(); api.updateSchedule(s.id, { status: 'cancelled' }) }}
                            >✗</button>
                          </>
                        )}
                        {isConfirmed && (
                          <button
                            style={{ padding: '2px 6px', borderRadius: 4, background: 'rgba(139,233,253,0.12)', color: '#8be9fd', fontWeight: 600, fontSize: 10, border: '1px solid rgba(139,233,253,0.3)', cursor: 'pointer' }}
                            onClick={(e) => { e.stopPropagation(); api.updateSchedule(s.id, { status: 'done' }) }}
                          >✅</button>
                        )}
                      </div>
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 3 }}>
                      {s.start_time?.slice(11, 16)}{s.end_time ? ` - ${s.end_time.slice(11, 16)}` : ''}
                      {s.location ? ` | ${s.location}` : ''}
                    </div>
                  </div>
                )
              })}
            </Section>
          )}

          {/* 笔记 — 可点击跳转 */}
          {selectedDayData!.notes.length > 0 && (
            <Section title="笔记" icon="📝" color="#8be9fd">
              {selectedDayData!.notes.map(n => (
                <div
                  key={n.id}
                  onClick={() => navigate('/notes')}
                  style={{
                    display: 'block', padding: '8px 12px', marginBottom: 5, borderRadius: 5,
                    background: 'var(--color-bg-input)', fontSize: 14, color: 'var(--color-text-primary)',
                    cursor: 'pointer', transition: 'all 0.15s',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--color-accent-primary)'; e.currentTarget.style.background = 'var(--color-bg-hover)' }}
                  onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--color-border)'; e.currentTarget.style.background = 'var(--color-bg-input)' }}
                >
                  {n.title}{n.tags && <span style={{ fontSize: 11, color: 'var(--color-accent-secondary)', marginLeft: 6 }}>#{n.tags}</span>}
                </div>
              ))}
            </Section>
          )}

          {/* 对话 — 可点击跳转 */}
          {selectedDayData!.conversations.length > 0 && (
            <Section title="对话" icon="💬" color="#717e95">
              {selectedDayData!.conversations.map(c => (
                <div
                  key={c.id}
                  onClick={() => navigate(`/chat/${c.id}`)}
                  style={{
                    display: 'block', padding: '8px 12px', marginBottom: 5, borderRadius: 5,
                    background: 'var(--color-bg-input)', fontSize: 14, color: 'var(--color-text-primary)',
                    cursor: 'pointer', transition: 'all 0.15s',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--color-accent-primary)'; e.currentTarget.style.background = 'var(--color-bg-hover)' }}
                  onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--color-border)'; e.currentTarget.style.background = 'var(--color-bg-input)' }}
                >
                  {c.title} <span style={{ color: 'var(--color-text-muted)' }}>({c.msg_count}条)</span>
                </div>
              ))}
            </Section>
          )}

          {/* 记忆 — 可点击跳转 */}
          {selectedDayData!.memories.length > 0 && (
            <Section title="记忆" icon="🧠" color="#ff79c6">
              {selectedDayData!.memories.map(m => (
                <div
                  key={m.id}
                  onClick={() => navigate('/memories')}
                  style={{
                    padding: '8px 12px', marginBottom: 5, borderRadius: 5,
                    background: 'var(--color-bg-input)', fontSize: 14,
                    borderLeft: `3px solid ${MEMORY_TYPE_COLORS[m.type] || '#bd93f9'}`,
                    cursor: 'pointer', transition: 'all 0.15s',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--color-accent-primary)'; e.currentTarget.style.background = 'var(--color-bg-hover)' }}
                  onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--color-border)'; e.currentTarget.style.background = 'var(--color-bg-input)' }}
                >
                  <span style={{ color: 'var(--color-text-primary)' }}>{m.content}</span>
                  <span style={{ fontSize: 11, color: 'var(--color-text-muted)', marginLeft: 6 }}>{m.importance}/5</span>
                </div>
              ))}
            </Section>
          )}
          </>
      )}

      {marketReport && (
        <Section title="行情分析" icon="📊" color="#f1fa8c">
          <div style={{ padding: 12, background: 'var(--color-bg-input)', borderRadius: 6, fontSize: 14, lineHeight: 1.6 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
              <span style={{ color: 'var(--color-text-primary)', fontWeight: 500 }}>黄金现货</span>
              <span style={{ color: 'var(--color-accent-primary)', fontWeight: 600 }}>{marketReport.gold_price}</span>
            </div>
            {marketReport.daily_advice && (
              <div style={{ color: 'var(--color-text-secondary)', marginBottom: 6 }}>
                <span style={{ fontWeight: 600 }}>日内建议：</span>{marketReport.daily_advice}
              </div>
            )}
            {marketReport.weekly_advice && (
              <div style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>
                <span style={{ fontWeight: 600 }}>周内观点：</span>{marketReport.weekly_advice}
              </div>
            )}
            <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 6 }}>
              报告日期: {marketReport.report_date}
            </div>
          </div>
        </Section>
      )}
    </div>
  )
}
