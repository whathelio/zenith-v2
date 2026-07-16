import { useState, useEffect, useRef, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import Sidebar from '../components/Sidebar'
import ChatMessages from '../components/ChatMessages'
import ChatInput from '../components/ChatInput'
import ProposalsBar from '../components/ProposalsBar'
import ReminderBanner from '../components/ReminderBanner'
import { api, type Message, type Proposal, type ConversationSummary } from '../shared/api'

let _msgIdCounter = 0

export default function ChatView() {
  const { convId } = useParams<{ convId: string }>()
  const navigate = useNavigate()

  const [conversations, setConversations] = useState<any[]>([])
  const [activeConv, setActiveConv] = useState<any>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [streamingText, setStreamingText] = useState('')
  const [proposals, setProposals] = useState<Proposal[]>([])
  const [reminder, setReminder] = useState('')
  const [reminderDismissed, setReminderDismissed] = useState(false)
  const [error, setError] = useState('')
  const [summarizing, setSummarizing] = useState(false)
  const [summaryResult, setSummaryResult] = useState<ConversationSummary | null>(null)

  const chatEndRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  // 组件卸载时取消正在进行的 SSE 流（后端后台任务继续处理不受影响）
  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  const loadConversations = useCallback(async () => {
    try {
      const convs = await api.listConversations()
      setConversations(convs)
    } catch (e: any) {
      setError(e.message)
    }
  }, [])

  const loadConversation = useCallback(async (id: string) => {
    try {
      const conv = await api.getConversation(id)
      setActiveConv(conv)
      setMessages(conv.messages || [])
    } catch (e: any) {
      setError(e.message)
    }
  }, [])

  useEffect(() => {
    loadConversations()
    loadProposals()
  }, [loadConversations])

  useEffect(() => {
    if (convId) {
      loadConversation(convId)
      navigate(`/chat/${convId}`, { replace: true })
    } else if (conversations.length > 0) {
      const firstId = conversations[0].id
      navigate(`/chat/${firstId}`, { replace: true })
      loadConversation(firstId)
    }
  }, [convId, conversations])

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingText])

  const handleNewChat = async () => {
    try {
      const conv = await api.createConversation()
      await loadConversations()
      setMessages([])
      setActiveConv(conv)
      setStreamingText('')
      navigate(`/chat/${conv.id}`)
    } catch (e: any) {
      setError(e.message)
    }
  }

  const handleSelectConv = (id: string) => {
    navigate(`/chat/${id}`)
    loadConversation(id)
  }

  const handleDeleteConv = async (id: string) => {
    try {
      await api.deleteConversation(id)
      await loadConversations()
      if (activeConv?.id === id) {
        setActiveConv(null)
        setMessages([])
        if (conversations.length > 1) {
          const next = conversations.find(c => c.id !== id)
          if (next) {
            navigate(`/chat/${next.id}`)
            loadConversation(next.id)
          }
        } else {
          navigate('/')
        }
      }
    } catch (e: any) {
      setError(e.message)
    }
  }

  const handleSend = async (text: string) => {
    if (!text.trim() || isLoading) return
    setError('')
    setReminder('')
    setProposals([])
    setStreamingText('')

    const userMsg: Message = {
      id: ++_msgIdCounter,
      conversation_id: activeConv?.id || '',
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    }
    const newMessages = [...messages, userMsg]
    setMessages(newMessages)

    if (!activeConv) {
      try {
        const conv = await api.createConversation()
        setActiveConv(conv)
        await loadConversations()
        navigate(`/chat/${conv.id}`, { replace: true })
        userMsg.conversation_id = conv.id
      } catch (e: any) {
        setError(e.message)
        return
      }
    }

    setIsLoading(true)
    let assistantText = ''

    // 创建 AbortController — 组件卸载时取消 fetch，但后端后台任务继续处理
    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac

    try {
      const res = await api.chat(text, activeConv?.id || userMsg.conversation_id, ac.signal)
      const reader = res.body?.getReader()
      if (!reader) throw new Error('No response body')

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const dataStr = line.slice(6)
          if (!dataStr) continue
          try {
            const data = JSON.parse(dataStr)

            if (data.type === 'text') {
              assistantText += data.content
              setStreamingText(assistantText)
            } else if (data.type === 'full_text') {
              // 后端发送完整文本和新对话 ID
              if (data.conversation_id && data.conversation_id !== (activeConv?.id || userMsg.conversation_id)) {
                navigate(`/chat/${data.conversation_id}`, { replace: true })
              }
            } else if (data.type === 'reminder') {
              setReminder(data.content)
              setReminderDismissed(false)
            } else if (data.type === 'proposal') {
              setProposals(prev => [...prev, data.data])
            } else if (data.type === 'proposals') {
              setProposals(data.proposals)
            } else if (data.type === 'tool_results') {
              // 工具结果 — 刷新提议列表
              loadProposals()
            } else if (data.type === 'done') {
              // done
            }
          } catch {
            // skip malformed JSON
          }
        }
      }
    } catch (e: any) {
      if (ac.signal.aborted) {
        // SSE 流被取消（用户切换了模块）— 后端后台任务继续处理
        // 返回后重新挂载时会从后端重新加载完整对话
        return
      }
      setError(e.message)
    } finally {
      setIsLoading(false)
      abortRef.current = null
    }

    if (assistantText.trim()) {
      const assistantMsg: Message = {
        id: ++_msgIdCounter,
        conversation_id: activeConv?.id || userMsg.conversation_id,
        role: 'assistant',
        content: assistantText,
        created_at: new Date().toISOString(),
      }
      setMessages(prev => [...prev, assistantMsg])
      setStreamingText('')
    } else {
      setStreamingText('')
    }

    await loadConversations()
  }

  const handleConfirm = async (type: string, id: number) => {
    try {
      await api.confirmProposal(type, id)
      setProposals(prev => prev.filter(p => !(p.type === type && p.id === id)))
    } catch (e: any) {
      setError(e.message)
    }
  }

  const handleReject = async (type: string, id: number) => {
    try {
      await api.rejectProposal(type, id)
      setProposals(prev => prev.filter(p => !(p.type === type && p.id === id)))
    } catch (e: any) {
      setError(e.message)
    }
  }

  const handleModify = async (type: string, id: number, changes: Record<string, string>) => {
    try {
      await api.modifyProposal(type, id, changes)
      setProposals(prev => prev.filter(p => !(p.type === type && p.id === id)))
    } catch (e: any) {
      setError(e.message)
    }
  }

  const loadProposals = async () => {
    try {
      const ps = await api.getProposals()
      setProposals(ps)
    } catch {
      // silent
    }
  }

  const handleSummarize = async () => {
    if (!activeConv?.id || summarizing) return
    if (messages.length < 2) {
      setError('对话至少需要2条消息才能总结')
      return
    }
    setSummarizing(true)
    setSummaryResult(null)
    setError('')
    try {
      const result = await api.summarizeConversation(activeConv.id)
      setSummaryResult(result)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setSummarizing(false)
    }
  }

  return (
    <div className="app-shell">
      <Sidebar
        conversations={conversations}
        activeId={activeConv?.id || ''}
        onSelect={handleSelectConv}
        onDelete={handleDeleteConv}
        onNew={handleNewChat}
      />
        <div className="main-content">
          <div className="topbar">
            <span className="topbar-title">
              {activeConv?.title || 'Zenith v2'}
            </span>
            <div className="topbar-actions">
              {activeConv && messages.length >= 2 && (
                <button
                  className="btn btn-sm"
                  style={{
                    background: summarizing ? 'var(--color-bg-muted)' : 'var(--color-accent-primary)',
                    color: '#fff',
                    cursor: summarizing ? 'wait' : 'pointer',
                  }}
                onClick={handleSummarize}
                disabled={summarizing}
              >
                {summarizing ? '⏳ 总结中...' : '🧪 总结对话'}
              </button>
            )}
            <a href="/" className="btn btn-sm">🏠 主页</a>
            <a href="/schedules" className="btn btn-sm">📅 日程</a>
            <a href="/notes" className="btn btn-sm">📝 笔记</a>
            <a href="/memories" className="btn btn-sm">🧠 记忆</a>
            <a href="/analysis" className="btn btn-sm">📋 文件分析</a>
            <a href="/settings" className="btn btn-sm">⚙ 设置</a>
          </div>
        </div>
        {reminder && !reminderDismissed && (
          <div style={{
            margin: '0 16px',
            padding: '10px 14px',
            background: 'var(--color-bg-panel)',
            border: '1px solid var(--color-border)',
            borderRadius: 'var(--radius-md)',
            fontSize: 'var(--font-size-sm)',
            lineHeight: 1.6,
            display: 'flex',
            alignItems: 'flex-start',
            gap: 8,
          }}>
            <div style={{ flex: 1, whiteSpace: 'pre-line' }}
              dangerouslySetInnerHTML={{
                __html: reminder
                  .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
                  .replace(/\*(.+?)\*/g, '<em>$1</em>')
              }}
            />
            <button
              onClick={() => setReminderDismissed(true)}
              style={{
                background: 'none', border: 'none', cursor: 'pointer',
                color: 'var(--color-text-secondary)', fontSize: 16, lineHeight: 1, padding: 0,
              }}
              title="收起提醒"
            >
              ×
            </button>
          </div>
        )}
        <div className="chat-area">
          <ChatMessages
            messages={messages}
            streamingText={streamingText}
            isLoading={isLoading}
          />
          {error && (
            <div style={{ padding: '8px 24px', color: 'var(--color-accent-danger)', fontSize: 'var(--font-size-sm)' }}>
              ⚠ {error}
            </div>
          )}
          <div ref={chatEndRef} />
        </div>
        {proposals.length > 0 && (
          <ProposalsBar
            proposals={proposals}
            onConfirm={handleConfirm}
            onReject={handleReject}
            onModify={handleModify}
          />
        )}
        <ChatInput onSend={handleSend} isLoading={isLoading} />
      </div>

      {/* 总结结果模态框 */}
      {summaryResult && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.5)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
          }}
          onClick={() => setSummaryResult(null)}
        >
          <div
            style={{
              background: 'var(--color-bg-panel)',
              border: '1px solid var(--color-border)',
              borderRadius: 12,
              padding: 24,
              maxWidth: 700,
              width: '90%',
              maxHeight: '80vh',
              overflow: 'auto',
              boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
            }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <h3 style={{ fontSize: 18, fontWeight: 600 }}>🧪 对话总结与经验蒸馏</h3>
              <button
                className="btn-icon"
                style={{ width: 32, height: 32, fontSize: 18 }}
                onClick={() => setSummaryResult(null)}
              >
                ×
              </button>
            </div>

            {/* 基本信息 */}
            <div style={{ marginBottom: 12, fontSize: 12, color: 'var(--color-text-muted)' }}>
              对话: {summaryResult.title} | 消息数: {summaryResult.message_count}
              {summaryResult.experiences_saved > 0 && ` | 已保存 ${summaryResult.experiences_saved} 条经验`}
            </div>

            {/* 摘要 */}
            {summaryResult.summary && (
              <div style={{ marginBottom: 16, padding: 12, background: 'var(--color-bg-primary)', borderRadius: 8 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-accent-primary)', marginBottom: 6 }}>📝 概要</div>
                <div style={{ fontSize: 13, lineHeight: 1.6 }}>{summaryResult.summary}</div>
              </div>
            )}

            {/* 关键决定 */}
            {summaryResult.key_decisions.length > 0 && (
              <div style={{ marginBottom: 16, padding: 12, background: 'var(--color-bg-primary)', borderRadius: 8 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: '#bd93f9', marginBottom: 6 }}>⚖️ 关键决定</div>
                {summaryResult.key_decisions.map((d, i) => (
                  <div key={i} style={{ fontSize: 13, lineHeight: 1.6 }}>• {d}</div>
                ))}
              </div>
            )}

            {/* 蒸馏经验 */}
            {summaryResult.experiences.length > 0 && (
              <div style={{ marginBottom: 16, padding: 12, background: 'var(--color-bg-primary)', borderRadius: 8 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: '#ff6e40', marginBottom: 6 }}>
                  🔥 蒸馏经验 {summaryResult.experiences_saved > 0 && `(已自动保存 ${summaryResult.experiences_saved} 条)`}
                </div>
                {summaryResult.experiences.map((exp, i) => (
                  <div key={i} style={{
                    fontSize: 13,
                    lineHeight: 1.6,
                    padding: '6px 0',
                    borderBottom: i < summaryResult.experiences.length - 1 ? '1px solid var(--color-border)' : 'none',
                  }}>
                    <span style={{ color: '#ff6e40', fontWeight: 600 }}>经验{i + 1}</span> {exp.content}
                    {exp.keywords && (
                      <span style={{ fontSize: 11, color: 'var(--color-text-muted)', marginLeft: 8 }}>
                        [{exp.keywords}]
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* 知识点 */}
            {summaryResult.knowledge.length > 0 && (
              <div style={{ marginBottom: 16, padding: 12, background: 'var(--color-bg-primary)', borderRadius: 8 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: '#f1fa8c', marginBottom: 6 }}>💡 知识点</div>
                {summaryResult.knowledge.map((k, i) => (
                  <div key={i} style={{ fontSize: 13, lineHeight: 1.6 }}>• {k}</div>
                ))}
              </div>
            )}

            {/* 行动项 */}
            {summaryResult.action_items.length > 0 && (
              <div style={{ marginBottom: 16, padding: 12, background: 'var(--color-bg-primary)', borderRadius: 8 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: '#50fa7b', marginBottom: 6 }}>✅ 行动项</div>
                {summaryResult.action_items.map((a, i) => (
                  <div key={i} style={{ fontSize: 13, lineHeight: 1.6 }}>• {a}</div>
                ))}
              </div>
            )}

            {/* 标签 */}
            {summaryResult.tags.length > 0 && (
              <div style={{ marginBottom: 12, display: 'flex', gap: 6 }}>
                {summaryResult.tags.map((t, i) => (
                  <span key={i} style={{
                    background: 'var(--color-bg-muted)',
                    padding: '2px 8px',
                    borderRadius: 4,
                    fontSize: 11,
                  }}>
                    {t}
                  </span>
                ))}
              </div>
            )}

            <div style={{ display: 'flex', gap: 12, marginTop: 16 }}>
              <a href="/memories" className="btn btn-sm" style={{ background: '#ff6e40', color: '#fff' }}>
                🧠 查看记忆库
              </a>
              <button className="btn btn-sm" onClick={() => setSummaryResult(null)}>
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
