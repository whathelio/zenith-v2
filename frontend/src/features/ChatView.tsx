import { useState, useEffect, useRef, useCallback } from 'react'
import { useParams, useNavigate, useLocation, Link } from 'react-router-dom'
import ChatConvPanel from '../components/ChatConvPanel'
import ChatMessages from '../components/ChatMessages'
import ChatInput from '../components/ChatInput'
import ProposalsBar from '../components/ProposalsBar'
import { toTraceEntry, type TraceEntry } from '../components/TraceCard'
import { api, type Message, type Proposal, type ConversationSummary, type DistillResult } from '../shared/api'
import { takePendingMessage } from '../shared/pendingMessage'

let _msgIdCounter = 0

/**
 * HTML 转义 —— 凡进 dangerouslySetInnerHTML 的文本都必须先过这一层。
 * reminder 的正文来自后端 check_reminders()，其中含日程 title（用户可写 / LLM 可生成），
 * 原实现直接 replace('**'→'<strong>') 而不转义，等于给出了一条可注入的路径。
 */
function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

export default function ChatView() {
  const { convId } = useParams<{ convId: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  // 记录已导航到的对话 ID，防止 useEffect 因 URL 规范化差异反复 navigate 造成渲染振荡
  const navigatedConvRef = useRef<string | null>(null)
  // 当前正在查看的对话 id（ref，SSE 回调里实时判断，避免闭包过期导致串号）
  const activeConvIdRef = useRef<string>('')

  const [conversations, setConversations] = useState<any[]>([])
  const [activeConv, setActiveConv] = useState<any>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [streamingText, setStreamingText] = useState('')
  const [proposals, setProposals] = useState<Proposal[]>([])
  const [reminder, setReminder] = useState('')
  const [reminderDismissed, setReminderDismissed] = useState(false)
  const [error, setError] = useState('')
  // B-13① 2026-09-11：校验警告原先被**完全丢弃** —— `warning` 分支只有一句注释没有代码，
  // 于是后端发来的 L3 输出校验（绝对化表述/高风险领域/记忆矛盾）与工具结果校验警告
  // 用户永远看不到。现收集后渲染（带去重 + 可关闭）。
  const [warnings, setWarnings] = useState<string[]>([])
  const [summarizing, setSummarizing] = useState(false)
  const [summaryResult, setSummaryResult] = useState<ConversationSummary | null>(null)
  // 蒸馏独立于总结：蒸馏会额外把经验/知识点落库并写 txt 产物，不能与总结共用一份结果 state
  const [distilling, setDistilling] = useState(false)
  const [distillResult, setDistillResult] = useState<DistillResult | null>(null)
  const [convCollapsed, setConvCollapsed] = useState(false)
  const [toolCallBubbles, setToolCallBubbles] = useState<TraceEntry[]>([])
  const [thinkingText, setThinkingText] = useState('')
  const [thinkingDone, setThinkingDone] = useState(false)
  const [selectedProvider, setSelectedProvider] = useState('')
  const [providers, setProviders] = useState<{ name: string; model: string }[]>([])
  const [selectedPersona, setSelectedPersona] = useState('')
  const [personas, setPersonas] = useState<{ name: string }[]>([])
  const [convBackground, setConvBackground] = useState('')
  const [backgroundDraft, setBackgroundDraft] = useState('')
  const [backgroundModal, setBackgroundModal] = useState(false)
  const [convBgImage, setConvBgImage] = useState('')

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
      // 同步当前查看对话 id（SSE 回调据此判断是否写 UI，切走后静默丢弃，不 abort 后台流）
      activeConvIdRef.current = id
      // 清空流式残留状态（思考/文本/工具气泡），切换到新对话时先展示干净，再由 loadConversation 拉取该对话数据
      setThinkingText('')
      setThinkingDone(false)
      setStreamingText('')
      setToolCallBubbles([])

      const conv = await api.getConversation(id)
      setActiveConv(conv)
      const msgs: Message[] = conv.messages || []
      setMessages(msgs)
      setSelectedPersona((conv as any).persona_name || '')
      setConvBackground((conv as any).background || '')
      const bgImg = (conv as any).background_image
      setConvBgImage(bgImg ? `/api/conversations/${id}/background-image?t=${Date.now()}` : '')

      // 回读历史执行痕迹（conversation_traces 表）— 切换模块回来不丢失工具气泡
      try {
        const traces = await api.getConvTraces(id)
        // 只保留有 message_id 的痕迹：无归属的（所属轮次消息已删）丢弃，
        // 否则会落入 unboundTraces 堆在消息列表底部挡住结论
        const bubbles = traces
          .filter((t: any) => t.trace_type === 'tool_call' && t.message_id != null)
          .reverse()
          .map((t: any) => {
            let data: any = {}
            try { data = JSON.parse(t.data || '{}') } catch { data = {} }
            return toTraceEntry({
              id: String(t.id),
              name: data.name || 'tool',
              args: data.args || {},
              status: 'done',
              resultSummary: data.result_summary || data.result || '',
              durationMs: data.duration_ms,
              success: data.success,
              round: t.round_num,
              messageId: t.message_id,
              stdout: data.stdout,
              stderr: data.stderr,
              exit_code: data.exit_code,
              lang: data.lang,
            })
          })
        setToolCallBubbles(bubbles)
      } catch {
        // 历史 traces 读取失败不阻断对话加载
      }
    } catch (e: any) {
      setError(e.message)
    }
  }, [])

  useEffect(() => {
    loadConversations()
    loadProposals()
    loadProviders()
    loadPersonas()
  }, [loadConversations])

  // 加载指定对话 — 仅在 convId 变化时触发（不依赖 conversations，避免循环）
  useEffect(() => {
    if (convId) {
      loadConversation(convId)
      // 仅在 URL 路径与目标不一致时才导航（replace 避免历史堆积 + 防振荡）
      if (navigatedConvRef.current !== convId || location.pathname !== `/chat/${convId}`) {
        navigatedConvRef.current = convId
        navigate(`/chat/${convId}`, { replace: true })
      }
    }
  }, [convId]) // eslint-disable-line react-hooks/exhaustive-deps

  // 无 convId 时回退到最近对话（依赖 conversations，但只做一次导航，不加载对话内容）
  useEffect(() => {
    if (convId) return
    if (conversations.length === 0) return
    const firstId = conversations[0].id
    if (navigatedConvRef.current !== firstId) {
      navigatedConvRef.current = firstId
      navigate(`/chat/${firstId}`, { replace: true })
    }
  }, [convId, conversations, navigate])

  // 从 Dashboard 底部快捷输入过来的待发送消息：会话就绪后自动发送
  useEffect(() => {
    if (!activeConv) return
    const p = takePendingMessage()
    if (p) handleSend(p)
  }, [activeConv])

  const handleNewChat = async () => {
    try {
      const conv = await api.createConversation(undefined, selectedPersona)
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

  /** 重置一轮对话的临时状态（发送/重新生成/编辑前调用） */
  const resetRoundState = () => {
    setError('')
    setWarnings([])
    setReminder('')
    setProposals([])
    setToolCallBubbles([])
    setStreamingText('')
    setThinkingText('')
    setThinkingDone(false)
  }

  /** SSE 流统一消费：text / thinking / tool_call / reminder / proposal ... */
  const consumeSSE = useCallback(async (res: Response): Promise<string> => {
    const reader = res.body?.getReader()
    if (!reader) throw new Error('No response body')

    const decoder = new TextDecoder()
    let buffer = ''
    let assistantText = ''
    let convIdRef = activeConv?.id || ''

    // 不再 abort 旧流：后台继续生成，切走后由 activeConvIdRef 守卫静默丢弃 UI 更新
    const ac = new AbortController()
    abortRef.current = ac

    try {
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
          // 守卫：该 SSE 流不属于当前正在查看的对话时，静默跳过（不写 UI 状态，避免串号）
          if (convIdRef && activeConvIdRef.current && convIdRef !== activeConvIdRef.current) {
            continue
          }
          try {
            const data = JSON.parse(dataStr)

            if (data.type === 'text') {
              assistantText += data.content
              setStreamingText(assistantText)
            } else if (data.type === 'thinking') {
              setThinkingText(prev => prev + (data.content || ''))
            } else if (data.type === 'full_text') {
              if (data.conversation_id && data.conversation_id !== convIdRef) {
                convIdRef = data.conversation_id
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
              loadProposals()
            } else if (data.type === 'tool_call_start') {
              setToolCallBubbles(prev => [...prev, toTraceEntry({
                id: data.id,
                name: data.name,
                args: data.args || {},
                status: 'pending',
                round: data.round,
              })])
            } else if (data.type === 'tool_call_end') {
              setToolCallBubbles(prev => prev.map(b =>
                b.id === data.id ? {
                  ...b,
                  status: 'done',
                  resultSummary: data.result_summary,
                  durationMs: data.duration_ms,
                  success: data.success,
                  stdout: data.stdout,
                  stderr: data.stderr,
                  exitCode: data.exit_code,
                  lang: data.lang,
                } : b
              ))
            } else if (data.type === 'warning') {
              // B-13①：原先此处是空分支（只有一行注释），警告被静默丢弃。
              // 后端字段：工具结果校验发 {level, message, tool}，L3 输出校验发 {level, message}
              // —— 兼容读取 message/content 两种历史写法，避免任一漏显示。
              const wText = String(data.message || data.content || '').trim()
              if (wText) {
                const line = `${data.tool ? `[${data.tool}] ` : ''}${wText}`
                setWarnings(prev => (prev.includes(line) ? prev : [...prev, line]))
              }
            } else if (data.type === 'error') {
              setError(data.message || '生成出错')
            } else if (data.type === 'done') {
              setThinkingDone(true)
            }
          } catch {
            // skip malformed JSON
          }
        }
      }
    } catch (e: any) {
      if (ac.signal.aborted) {
        // 流被取消（用户停止 / 组件卸载）
        return assistantText
      }
      setError(e.message)
    } finally {
      abortRef.current = null
    }
    return assistantText
  }, [activeConv, navigate])

  const handleSend = async (text: string) => {
    if (!text.trim() || isLoading) return
    resetRoundState()

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
        activeConvIdRef.current = conv.id
        await loadConversations()
        navigate(`/chat/${conv.id}`, { replace: true })
        userMsg.conversation_id = conv.id
      } catch (e: any) {
        setError(e.message)
        return
      }
    }

    setIsLoading(true)
    const convId = activeConv?.id || userMsg.conversation_id

    try {
      const res = await api.chat(text, convId, undefined, selectedProvider)
      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: res.statusText }))
        throw new Error(err.error || '请求失败')
      }
      await consumeSSE(res)
      // 流式结束后重新拉取完整对话：assistant 结论 + 工具痕迹从数据库回读，
      // 痕迹归位到 user 消息下方（结论之上），同时清空流式残留的 unboundTraces
      await loadConversation(convId)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setIsLoading(false)
      setStreamingText('')
    }

    await loadConversations()
  }

  /** 重新生成最后一条 AI 回复 */
  const handleRegenerate = async () => {
    if (isLoading || !activeConv?.id) return
    resetRoundState()
    setIsLoading(true)
    try {
      const res = await api.regenerate(activeConv.id, selectedProvider)
      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: res.statusText }))
        throw new Error(err.error || '重新生成失败')
      }
      await consumeSSE(res)
      // 后端已删除旧的 assistant 消息，重新拉取完整对话（含新结论 + 归位后的工具痕迹）
      await loadConversation(activeConv.id)
    } catch (e: any) {
      setError(e.message)
      await loadConversation(activeConv.id)
    } finally {
      setIsLoading(false)
      setStreamingText('')
    }
    await loadConversations()
  }

  /** 编辑消息（user 编辑触发重新生成；assistant 编辑仅保存） */
  const handleEditMessage = async (msgId: number, content: string) => {
    if (!activeConv?.id) return
    resetRoundState()
    setIsLoading(true)
    try {
      const res = await api.editMessage(activeConv.id, msgId, content, selectedProvider)
      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: res.statusText }))
        throw new Error(err.error || '编辑失败')
      }
      const ctype = res.headers.get('content-type') || ''
      if (ctype.includes('text/event-stream')) {
        await consumeSSE(res)
        await loadConversation(activeConv.id)
      } else {
        // assistant 消息编辑 — 仅保存，刷新列表
        await loadConversation(activeConv.id)
      }
    } catch (e: any) {
      setError(e.message)
    } finally {
      setIsLoading(false)
      setStreamingText('')
    }
    await loadConversations()
  }

  /** 删除消息（及之后所有消息） */
  const handleDeleteMessage = async (msgId: number) => {
    if (!activeConv?.id) return
    try {
      await api.deleteMessage(msgId)
      await loadConversation(activeConv.id)
      await loadConversations()
    } catch (e: any) {
      setError(e.message)
    }
  }

  /** 停止当前生成 */
  const handleStop = async () => {
    abortRef.current?.abort()
    try {
      if (activeConv?.id) await api.stopChat(activeConv.id)
    } catch { /* silent */ }
    setIsLoading(false)
    setStreamingText('')
    setThinkingText('')
    setThinkingDone(true)
    setToolCallBubbles([])
    // 后端不保存半成品 — 重新拉取真实状态
    if (activeConv?.id) await loadConversation(activeConv.id)
  }

  const handleConfirm = async (type: string, id: number) => {
    try {
      const res = await api.confirmProposal(type, id)
      setProposals(prev => prev.filter(p => !(p.type === type && p.id === id)))
      // delete_message 确认后刷新当前对话（消息已删除）
      if (type === 'action' && activeConv?.id) {
        const payload = (res as any)?.action?.payload
        if (payload?.msg_id) await loadConversation(activeConv.id)
      }
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

  const loadProviders = async () => {
    try {
      const s = await api.getSettings()
      const ps = (s as any).providers
      if (ps && Array.isArray(ps)) {
        setProviders(ps.map((p: any) => ({ name: p.name, model: p.model })))
        if (!selectedProvider && (s as any).default_provider) {
          setSelectedProvider((s as any).default_provider)
        }
      }
    } catch {
      // silent
    }
  }

  const loadPersonas = async () => {
    try {
      const s = await api.getSettings()
      const ps = (s as any).personas
      if (ps && Array.isArray(ps)) {
        setPersonas(ps.map((p: any) => ({ name: p.name })))
      }
    } catch {
      // silent
    }
  }

  /** 保存对话背景 */
  const handleSaveBackground = async () => {
    if (!activeConv?.id) return
    try {
      await api.renameConversation(activeConv.id, activeConv.title || 'New Chat', undefined, backgroundDraft)
      setConvBackground(backgroundDraft)
      setBackgroundModal(false)
      await loadConversations()
    } catch (e: any) {
      setError(e.message)
    }
  }

  /** 打开背景编辑弹窗 */
  const handleOpenBackground = () => {
    setBackgroundDraft(convBackground)
    setBackgroundModal(true)
  }

  /** 上传对话背景图片 */
  const handleUploadBgImage = async (file: File | undefined) => {
    if (!activeConv?.id || !file) return
    try {
      setError('')
      await api.uploadBackgroundImage(activeConv.id, file)
      await loadConversation(activeConv.id)
    } catch (e: any) {
      setError(e.message || '上传失败')
    }
  }

  /** 清除对话背景图片 */
  const handleClearBgImage = async () => {
    if (!activeConv?.id) return
    try {
      await api.clearBackgroundImage(activeConv.id)
      setConvBgImage('')
      await loadConversation(activeConv.id)
    } catch (e: any) {
      setError(e.message || '清除失败')
    }
  }

  // proposals 变化时通知左侧面板
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('zenith:proposals', { detail: proposals }))
  }, [proposals])

  // activeConv 变化时通知左侧面板
  useEffect(() => {
    if (activeConv) {
      window.dispatchEvent(new CustomEvent('zenith:conv-change', {
        detail: { id: activeConv.id, title: activeConv.title }
      }))
    }
  }, [activeConv])

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

  /**
   * 蒸馏当前对话 —— 后端 `POST /api/distill/conversation/{id}` 本来就是活接口
   * （LLM 工具通道调用过 7 次、data/distill/ 已积压 119 份 txt 产物），
   * 但前端一直没有入口，每次都得绕道让模型自己去调工具。此处补上。
   */
  const handleDistill = async () => {
    if (!activeConv?.id || distilling) return
    if (messages.length < 2) {
      setError('对话至少需要2条消息才能蒸馏')
      return
    }
    setDistilling(true)
    setDistillResult(null)
    setError('')
    try {
      const result = await api.distillConversation(activeConv.id)
      setDistillResult(result)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setDistilling(false)
    }
  }

  return (
    <div className="chat-layout">
      {/* 会话列表 — 瘦身可折叠 */}
      <ChatConvPanel
        conversations={conversations}
        activeId={activeConv?.id || ''}
        onSelect={handleSelectConv}
        onDelete={handleDeleteConv}
        onNew={handleNewChat}
        collapsed={convCollapsed}
        onToggle={() => setConvCollapsed(!convCollapsed)}
      />

      {/* 主聊天区 */}
      {/* 背景优先级：对话独立背景 > 全局背景（settings.background_image）。
          未设对话背景时 chat-main 透明，透出 body 上的全局背景（由 GlobalBackground 设置），
          避免每个对话都要单独设置一次。 */}
      <div className="chat-main" style={convBgImage ? {
        backgroundImage: `url("${convBgImage}")`,
        backgroundSize: 'cover',
        backgroundPosition: 'center',
        backgroundAttachment: 'local',
      } : { background: 'transparent' }}>
        {/* 背景图半透明遮罩 — 保证文字可读性 */}
        {convBgImage && <div className="chat-bg-overlay" />}
        {/* 工具条: 标题 + 总结按钮 */}
        <div className="chat-toolbar">
          <span className="chat-title">
            {(activeConv as any)?.source_type && <span title="学习对话">📖 </span>}
            {activeConv?.title || '新对话'}
            {(activeConv as any)?.source_type && (
              <span style={{ fontSize: 10, color: 'var(--color-text-muted)', fontWeight: 400, marginLeft: 6 }}>
                来自{((activeConv as any).source_type === 'note' ? '笔记' : '记忆')}#{(activeConv as any).source_id}
              </span>
            )}
          </span>
          <div className="chat-toolbar-actions">
            {providers.length > 0 && (
              <select
                className="provider-select"
                value={selectedProvider}
                onChange={(e) => setSelectedProvider(e.target.value)}
                style={{
                  background: 'var(--color-bg-input)',
                  color: 'var(--color-text-primary)',
                  border: '1px solid var(--color-border)',
                  borderRadius: '6px',
                  padding: '4px 8px',
                  fontSize: '12px',
                  cursor: 'pointer',
                }}
              >
                {providers.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.name} ({p.model})
                  </option>
                ))}
              </select>
            )}
            {personas.length > 0 && (
              <select
                className="persona-select"
                value={selectedPersona}
                onChange={async (e) => {
                  const name = e.target.value
                  setSelectedPersona(name)
                  if (activeConv?.id) {
                    try {
                      await api.renameConversation(activeConv.id, activeConv.title || 'New Chat', name)
                    } catch { /* silent */ }
                  }
                }}
                style={{
                  background: 'var(--color-bg-input)',
                  color: 'var(--color-text-primary)',
                  border: '1px solid var(--color-border)',
                  borderRadius: '6px',
                  padding: '4px 8px',
                  fontSize: '12px',
                  cursor: 'pointer',
                }}
              >
                <option value="">默认模式</option>
                {personas.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.name}
                  </option>
                ))}
              </select>
            )}
            {activeConv && (
              <button
                className="btn btn-sm"
                onClick={handleOpenBackground}
                title={convBackground ? '编辑对话背景（已设置）' : '设置对话背景'}
                style={{
                  background: convBackground ? 'rgba(240,160,48,0.15)' : 'var(--color-bg-input)',
                  color: convBackground ? 'var(--color-accent-warning)' : 'var(--color-text-secondary)',
                  border: `1px solid ${convBackground ? 'var(--color-accent-warning)' : 'var(--color-border)'}`,
                  cursor: 'pointer',
                }}
              >
                📖 背景
              </button>
            )}
            {activeConv && (
              <>
                <input
                  type="file"
                  id="bg-image-input"
                  accept="image/*"
                  style={{ display: 'none' }}
                  onChange={e => { handleUploadBgImage(e.target.files?.[0]); e.target.value = '' }}
                />
                <button
                  className="btn btn-sm"
                  onClick={() => document.getElementById('bg-image-input')?.click()}
                  title={convBgImage ? '更换背景图片' : '设置对话背景图片'}
                  style={{
                    background: convBgImage ? 'rgba(189,147,249,0.15)' : 'var(--color-bg-input)',
                    color: convBgImage ? 'var(--color-accent-primary)' : 'var(--color-text-secondary)',
                    border: `1px solid ${convBgImage ? 'var(--color-accent-primary)' : 'var(--color-border)'}`,
                    cursor: 'pointer',
                  }}
                >
                  🖼 背景图
                </button>
                {convBgImage && (
                  <button
                    className="btn btn-sm"
                    onClick={handleClearBgImage}
                    title="清除背景图片"
                    style={{ cursor: 'pointer', color: 'var(--color-accent-danger)' }}
                  >
                    ✕
                  </button>
                )}
              </>
            )}
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
            {activeConv && messages.length >= 2 && (
              <button
                className="btn btn-sm"
                title="蒸馏 = 总结 + 知识提取 + 经验入库 + 生成 txt 产物（data/distill/）"
                style={{
                  background: distilling ? 'var(--color-bg-muted)' : 'var(--color-accent-secondary)',
                  color: '#fff',
                  cursor: distilling ? 'wait' : 'pointer',
                }}
                onClick={handleDistill}
                disabled={distilling}
              >
                {distilling ? '⏳ 蒸馏中...' : '⚗️ 蒸馏本对话'}
              </button>
            )}
          </div>
        </div>

        {reminder && !reminderDismissed && (
          <div style={{
            margin: '0 12px',
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
                __html: escapeHtml(reminder)
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
            onSend={handleSend}
            toolCallBubbles={toolCallBubbles}
            thinkingText={thinkingText}
            thinkingDone={thinkingDone}
            onRegenerate={handleRegenerate}
            onEditMessage={handleEditMessage}
            onDeleteMessage={handleDeleteMessage}
          />
          {error && (
            <div style={{ padding: '8px 24px', color: 'var(--color-accent-danger)', fontSize: 'var(--font-size-sm)' }}>
              ⚠ {error}
            </div>
          )}
          {warnings.length > 0 && (
            <div style={{
              margin: '0 24px 8px', padding: '8px 12px', borderRadius: 6,
              background: 'rgba(240,160,48,0.10)', border: '1px solid rgba(240,160,48,0.35)',
              color: 'var(--color-accent-warning, #f0a030)', fontSize: 'var(--font-size-sm)',
              display: 'flex', alignItems: 'flex-start', gap: 8,
            }}>
              <div style={{ flex: 1, whiteSpace: 'pre-line', lineHeight: 1.6 }}>
                {warnings.map(w => `⚠ ${w}`).join('\n')}
              </div>
              <button
                onClick={() => setWarnings([])}
                title="关闭校验提示"
                aria-label="关闭校验提示"
                style={{
                  background: 'none', border: 'none', cursor: 'pointer',
                  color: 'var(--color-text-muted)', fontSize: 15, lineHeight: 1, padding: 0, flex: 'none',
                }}
              >×</button>
            </div>
          )}
        </div>

        {proposals.length > 0 && (
          <ProposalsBar
            proposals={proposals}
            onConfirm={handleConfirm}
            onReject={handleReject}
            onModify={handleModify}
          />
        )}
        <ChatInput onSend={handleSend} isLoading={isLoading} onStop={handleStop} />
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
            padding: 16,
          }}
          onClick={() => setSummaryResult(null)}
        >
          {/* 三段式布局：头部固定 / 内容区唯一滚动 / 底部按钮固定。
              原先 overflow:auto 挂在整个卡片上、按钮排在内容末尾 —— 总结内容一长
              按钮就被推到滚动区底部，看起来像「溢出屏幕、点不到」。 */}
          <div
            style={{
              background: 'var(--color-bg-panel)',
              border: '1px solid var(--color-border)',
              borderRadius: 12,
              maxWidth: 700,
              width: '90%',
              maxHeight: '85vh',
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden',
              boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
            }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: '16px 24px 12px', flexShrink: 0,
              borderBottom: '1px solid var(--color-border)',
            }}>
              <h3 style={{ fontSize: 18, fontWeight: 600 }}>🧪 对话总结与经验蒸馏</h3>
              <button
                className="btn-icon"
                style={{ width: 32, height: 32, fontSize: 18 }}
                onClick={() => setSummaryResult(null)}
              >
                ×
              </button>
            </div>

            <div style={{
              flex: 1, minHeight: 0,
              overflowY: 'auto', overflowX: 'hidden',
              padding: '12px 24px',
              wordBreak: 'break-word', overflowWrap: 'anywhere',
            }}>

            <div style={{ marginBottom: 12, fontSize: 12, color: 'var(--color-text-muted)' }}>
              对话: {summaryResult.title} | 消息数: {summaryResult.message_count}
              {summaryResult.experiences_saved > 0 && ` | 已保存 ${summaryResult.experiences_saved} 条经验`}
            </div>

            {summaryResult.summary && (
              <div style={{ marginBottom: 16, padding: 12, background: 'var(--color-bg-primary)', borderRadius: 8 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-accent-primary)', marginBottom: 6 }}>📝 概要</div>
                <div style={{ fontSize: 13, lineHeight: 1.6 }}>{summaryResult.summary}</div>
              </div>
            )}

            {summaryResult.key_decisions.length > 0 && (
              <div style={{ marginBottom: 16, padding: 12, background: 'var(--color-bg-primary)', borderRadius: 8 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: '#bd93f9', marginBottom: 6 }}>⚖️ 关键决定</div>
                {summaryResult.key_decisions.map((d, i) => (
                  <div key={i} style={{ fontSize: 13, lineHeight: 1.6 }}>• {d}</div>
                ))}
              </div>
            )}

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

            {summaryResult.knowledge.length > 0 && (
              <div style={{ marginBottom: 16, padding: 12, background: 'var(--color-bg-primary)', borderRadius: 8 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: '#f1fa8c', marginBottom: 6 }}>💡 知识点</div>
                {summaryResult.knowledge.map((k, i) => (
                  <div key={i} style={{ fontSize: 13, lineHeight: 1.6 }}>• {k}</div>
                ))}
              </div>
            )}

            {summaryResult.action_items.length > 0 && (
              <div style={{ marginBottom: 16, padding: 12, background: 'var(--color-bg-primary)', borderRadius: 8 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: '#50fa7b', marginBottom: 6 }}>✅ 行动项</div>
                {summaryResult.action_items.map((a, i) => (
                  <div key={i} style={{ fontSize: 13, lineHeight: 1.6 }}>• {a}</div>
                ))}
              </div>
            )}

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

            </div>

            <div style={{
              display: 'flex', gap: 12, padding: '12px 24px 16px', flexShrink: 0,
              borderTop: '1px solid var(--color-border)',
            }}>
              <Link to="/library?tab=memories" className="btn btn-sm" style={{ background: '#ff6e40', color: '#fff' }}>
                🧠 查看知识库
              </Link>
              <button className="btn btn-sm" onClick={() => setSummaryResult(null)}>
                关闭
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 蒸馏结果模态框 — 比总结模态框薄：蒸馏的额外产出是「落了哪些库 + txt 落盘路径」，
          逐条清单已经在总结里看得到，这里只需要告诉用户产物在哪、去哪儿查 */}
      {distillResult && (
        <div
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
            padding: 16,
          }}
          onClick={() => setDistillResult(null)}
        >
          {/* 同总结弹窗：三段式，保证底部按钮始终可见 */}
          <div
            style={{
              background: 'var(--color-bg-panel)', border: '1px solid var(--color-accent-secondary)',
              borderRadius: 12, maxWidth: 620, width: '90%', maxHeight: '85vh',
              display: 'flex', flexDirection: 'column', overflow: 'hidden',
              boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
            }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: '16px 24px 12px', flexShrink: 0,
              borderBottom: '1px solid var(--color-border)',
            }}>
              <h3 style={{ fontSize: 17, fontWeight: 600, color: 'var(--color-accent-secondary)' }}>⚗️ 对话蒸馏完成</h3>
              <button className="btn-icon" style={{ width: 32, height: 32, fontSize: 18 }} onClick={() => setDistillResult(null)}>×</button>
            </div>

            <div style={{
              flex: 1, minHeight: 0,
              overflowY: 'auto', overflowX: 'hidden',
              padding: '12px 24px',
              wordBreak: 'break-word', overflowWrap: 'anywhere',
            }}>

            {distillResult.title && (
              <div style={{ marginBottom: 10, fontSize: 12, color: 'var(--color-text-muted)' }}>对话: {distillResult.title}</div>
            )}

            {distillResult.summary && (
              <div style={{ marginBottom: 14, padding: 12, background: 'var(--color-bg-primary)', borderRadius: 8 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-accent-primary)', marginBottom: 6 }}>📝 概要</div>
                <div style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--color-text-primary)' }}>{distillResult.summary}</div>
              </div>
            )}

            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 14, fontSize: 12, color: 'var(--color-text-secondary)' }}>
              <span>🔥 经验 {distillResult.experiences?.length ?? 0} 条</span>
              <span>💡 知识点 {distillResult.knowledge?.length ?? 0} 条</span>
              {!!distillResult.saved_count && <span>✅ 新入库 {distillResult.saved_count} 条</span>}
              {!!distillResult.skip_count && <span style={{ color: 'var(--color-text-muted)' }}>↩︎ 已存在跳过 {distillResult.skip_count} 条</span>}
            </div>

            {distillResult.txt_path && (
              <div style={{ marginBottom: 14, padding: 10, background: 'var(--color-bg-muted)', borderRadius: 6 }}>
                <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 4 }}>txt 产物已落盘</div>
                <div style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--color-text-secondary)', wordBreak: 'break-all' }}>
                  {distillResult.txt_path}
                </div>
              </div>
            )}

            </div>

            <div style={{
              display: 'flex', gap: 12, padding: '12px 24px 16px', flexShrink: 0,
              borderTop: '1px solid var(--color-border)',
            }}>
              <Link to="/library?tab=memories" className="btn btn-sm" style={{ background: 'var(--color-accent-secondary)', color: '#000' }}>
                🧠 查看知识库
              </Link>
              <button className="btn btn-sm" onClick={() => setDistillResult(null)}>关闭</button>
            </div>
          </div>
        </div>
      )}

      {/* 对话背景编辑模态框 */}
      {backgroundModal && activeConv && (
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
          onClick={() => setBackgroundModal(false)}
        >
          <div
            style={{
              background: 'var(--color-bg-panel)',
              border: '1px solid var(--color-border)',
              borderRadius: 12,
              padding: 24,
              maxWidth: 560,
              width: '90%',
              boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
            }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <h3 style={{ fontSize: 16, fontWeight: 600 }}>📖 对话背景</h3>
              <button
                className="btn-icon"
                style={{ width: 32, height: 32, fontSize: 18 }}
                onClick={() => setBackgroundModal(false)}
              >
                ×
              </button>
            </div>

            <p style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 10, lineHeight: 1.6 }}>
              定义这个对话的世界观背景——你是谁、身处什么情境、有什么目标。
              Zenith 会把它注入到 AI 的提示词中，让回答贴合你的设定。
              支持 Markdown 格式，留空则使用默认背景。
            </p>

            <textarea
              className="form-input"
              rows={8}
              value={backgroundDraft}
              onChange={e => setBackgroundDraft(e.target.value)}
              placeholder={'例如：\n你是我的投资研究助理。我们在 2026 年，正在一起分析 A 股市场的半导体板块。\n你的风格是数据驱动，每次结论都要附上数据来源。'}
              style={{
                width: '100%',
                boxSizing: 'border-box',
                fontFamily: 'inherit',
                marginBottom: 12,
              }}
            />

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              {convBackground && (
                <button
                  className="btn btn-sm"
                  onClick={() => setBackgroundDraft('')}
                  style={{ color: 'var(--color-accent-danger)', cursor: 'pointer' }}
                >
                  清除背景
                </button>
              )}
              <button
                className="btn btn-sm"
                onClick={() => setBackgroundModal(false)}
                style={{ cursor: 'pointer' }}
              >
                取消
              </button>
              <button
                className="btn btn-sm"
                onClick={handleSaveBackground}
                style={{
                  background: 'var(--color-accent-primary)',
                  color: '#fff',
                  cursor: 'pointer',
                  fontWeight: 600,
                }}
              >
                💾 保存
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
