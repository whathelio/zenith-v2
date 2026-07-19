import { useState } from 'react'
import { useCalendarGoal } from '../contexts/CalendarGoalContext'
import { useNavigate } from 'react-router-dom'
import { api } from '../shared/api'
import {
  STATUS_COLORS, STATUS_BG_COLORS, STATUS_NAMES, STATUS_ICONS, isScheduleOverdue,
} from '../shared/scheduleHelpers'

export default function DashboardView() {
  const { selectedDayKey, selectedDayData, loading, todayStr, loadCalendar } = useCalendarGoal()
  const navigate = useNavigate()
  const [chatInput, setChatInput] = useState('')
  const [sending, setSending] = useState(false)

  const isToday = selectedDayKey === todayStr
  const schedules = selectedDayData?.schedules || []
  const notes = selectedDayData?.notes || []
  const conversations = selectedDayData?.conversations || []
  const memories = selectedDayData?.memories || []

  // 发送对话：创建新对话 + 发送消息 → 跳转 /chat/:id
  const handleSendChat = async () => {
    const text = chatInput.trim()
    if (!text || sending) return
    setSending(true)
    try {
      const conv = await api.createConversation()
      // 用 SSE 发送第一条消息（后台处理），然后直接跳转
      // 不等 SSE 结束，跳转后 ChatView 会接管
      api.chat(text, conv.id).catch(() => {/* ChatView 会处理 */})
      setChatInput('')
      navigate(`/chat/${conv.id}`)
    } catch (e) {
      console.error('创建对话失败', e)
    } finally {
      setSending(false)
    }
  }

  const handleConfirmSchedule = async (s: any) => {
    await api.updateSchedule(s.id, { status: 'confirmed' })
    loadCalendar()
  }
  const handleRejectSchedule = async (s: any) => {
    await api.updateSchedule(s.id, { status: 'cancelled' })
    loadCalendar()
  }
  const handleCompleteSchedule = async (s: any) => {
    await api.updateSchedule(s.id, { status: 'done' })
    loadCalendar()
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '12px 16px' }}>
      {/* 标题 */}
      <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 12 }}>
        {isToday ? '今天' : selectedDayKey}
        {schedules.length > 0 && (
          <span style={{ fontSize: 12, color: 'var(--color-text-muted)', marginLeft: 8, fontWeight: 400 }}>
            {schedules.length} 项日程
          </span>
        )}
      </div>

      {/* 内容区 — 滚动 */}
      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        {loading ? (
          <div style={{ color: 'var(--color-text-muted)', fontSize: 14, padding: 24, textAlign: 'center' }}>加载中...</div>
        ) : schedules.length === 0 && notes.length === 0 && conversations.length === 0 && memories.length === 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'center', padding: 32, color: 'var(--color-text-muted)' }}>
            <div style={{ fontSize: 40, opacity: 0.4 }}>📭</div>
            <div style={{ fontSize: 14 }}>当天暂无内容</div>
            <div style={{ fontSize: 12, opacity: 0.7 }}>在下方输入框开始对话，或去左侧添加日程</div>
          </div>
        ) : (
          <>
            {/* 日程 */}
            {schedules.length > 0 && (
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#bd93f9', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span>📅</span>日程 ({schedules.length})
                </div>
                {schedules.map(s => {
                  const isProposed = s.status === 'proposed'
                  const isConfirmed = s.status === 'confirmed'
                  const overdue = isScheduleOverdue(s)
                  const displayStatus = overdue ? 'overdue' : s.status
                  const borderColor = STATUS_COLORS[displayStatus] || (overdue ? '#ff5555' : '#717e95')
                  const bgColor = STATUS_BG_COLORS[s.status] || 'var(--color-bg-input)'
                  return (
                    <div
                      key={s.id}
                      style={{
                        padding: '8px 12px', marginBottom: 5, borderRadius: 5,
                        background: bgColor, fontSize: 14,
                        borderLeft: `4px solid ${borderColor}`,
                      }}
                    >
                      <div style={{ color: 'var(--color-text-primary)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span style={{ fontWeight: 500, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.title}</span>
                        <div style={{ display: 'flex', gap: 4, alignItems: 'center', flexShrink: 0 }}>
                          <span style={{ fontSize: 11, color: borderColor, fontWeight: 600 }}>
                            {overdue ? '⚠' : STATUS_ICONS[s.status]}
                          </span>
                          {isProposed && (
                            <>
                              <button
                                style={{ padding: '2px 8px', borderRadius: 4, background: STATUS_COLORS.confirmed, color: '#000', fontWeight: 700, fontSize: 10, border: 'none', cursor: 'pointer' }}
                                onClick={() => handleConfirmSchedule(s)}
                                title="确认"
                              >✓</button>
                              <button
                                style={{ padding: '2px 6px', borderRadius: 4, background: 'rgba(255,85,85,0.15)', color: '#ff5555', fontWeight: 600, fontSize: 10, border: '1px solid rgba(255,85,85,0.3)', cursor: 'pointer' }}
                                onClick={() => handleRejectSchedule(s)}
                                title="取消"
                              >✗</button>
                            </>
                          )}
                          {isConfirmed && (
                            <button
                              style={{ padding: '2px 6px', borderRadius: 4, background: 'rgba(139,233,253,0.12)', color: '#8be9fd', fontWeight: 600, fontSize: 10, border: '1px solid rgba(139,233,253,0.3)', cursor: 'pointer' }}
                              onClick={() => handleCompleteSchedule(s)}
                              title="完成"
                            >✅</button>
                          )}
                        </div>
                      </div>
                      <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 3 }}>
                        {s.start_time?.slice(11, 16)}{s.end_time ? ` - ${s.end_time.slice(11, 16)}` : ''}
                        {s.location ? ` | ${s.location}` : ''}
                        <span style={{ marginLeft: 6, color: borderColor }}>{overdue ? '已逾期' : STATUS_NAMES[s.status]}</span>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}

            {/* 笔记 */}
            {notes.length > 0 && (
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#8be9fd', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span>📝</span>笔记 ({notes.length})
                </div>
                {notes.map(n => (
                  <div
                    key={n.id}
                    onClick={() => navigate('/library?tab=notes')}
                    style={{
                      display: 'block', padding: '8px 12px', marginBottom: 5, borderRadius: 5,
                      background: 'var(--color-bg-input)', fontSize: 14, color: 'var(--color-text-primary)',
                      cursor: 'pointer',
                    }}
                  >
                    {n.title}{n.tags && <span style={{ fontSize: 11, color: 'var(--color-accent-secondary)', marginLeft: 6 }}>#{n.tags}</span>}
                  </div>
                ))}
              </div>
            )}

            {/* 对话 */}
            {conversations.length > 0 && (
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#717e95', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span>💬</span>对话 ({conversations.length})
                </div>
                {conversations.map(c => (
                  <div
                    key={c.id}
                    onClick={() => navigate(`/chat/${c.id}`)}
                    style={{
                      display: 'block', padding: '8px 12px', marginBottom: 5, borderRadius: 5,
                      background: 'var(--color-bg-input)', fontSize: 14, color: 'var(--color-text-primary)',
                      cursor: 'pointer',
                    }}
                  >
                    {c.title} <span style={{ color: 'var(--color-text-muted)' }}>({c.msg_count}条)</span>
                  </div>
                ))}
              </div>
            )}

            {/* 记忆 */}
            {memories.length > 0 && (
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#ff79c6', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span>🧠</span>记忆 ({memories.length})
                </div>
                {memories.map(m => (
                  <div
                    key={m.id}
                    onClick={() => navigate('/library?tab=memories')}
                    style={{
                      padding: '8px 12px', marginBottom: 5, borderRadius: 5,
                      background: 'var(--color-bg-input)', fontSize: 14,
                      borderLeft: `3px solid #bd93f9`,
                      cursor: 'pointer',
                    }}
                  >
                    <span style={{ color: 'var(--color-text-primary)' }}>{m.content}</span>
                    <span style={{ fontSize: 11, color: 'var(--color-text-muted)', marginLeft: 6 }}>{m.importance}/5</span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {/* 底部对话入口 */}
      <div style={{
        flexShrink: 0, paddingTop: 12, borderTop: '1px solid var(--color-border)',
        display: 'flex', gap: 8, alignItems: 'flex-end',
      }}>
        <textarea
          value={chatInput}
          onChange={e => setChatInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              handleSendChat()
            }
          }}
          placeholder="输入消息开始对话... (Enter 发送, Shift+Enter 换行)"
          rows={2}
          style={{
            flex: 1, padding: '8px 12px', borderRadius: 8,
            background: 'var(--color-bg-input)', border: '1px solid var(--color-border)',
            color: 'var(--color-text-primary)', fontSize: 13, fontFamily: 'inherit',
            resize: 'none', outline: 'none', minHeight: 40, maxHeight: 100,
          }}
        />
        <button
          className="btn btn-primary"
          onClick={handleSendChat}
          disabled={!chatInput.trim() || sending}
          style={{ padding: '8px 16px', borderRadius: 8, fontSize: 13 }}
        >
          {sending ? '⏳' : '发送 💬'}
        </button>
      </div>
    </div>
  )
}
