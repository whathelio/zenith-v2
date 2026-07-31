import { useRef, useEffect, useState } from 'react'
import type { Message } from '../shared/api'
import { api } from '../shared/api'
import { lookupPlaceholder } from '../shared/security'
import Markdown from './Markdown'
import ThinkingBlock from './ThinkingBlock'
import TraceCard, { type TraceEntry } from './TraceCard'
import ConfirmCard, { extractConfirmCard, stripConfirmCard, type ConfirmOption } from './ConfirmCard'

interface ChatMessagesProps {
  messages: Message[]
  streamingText: string
  isLoading: boolean
  onSend?: (text: string) => void
  toolCallBubbles?: TraceEntry[]
  thinkingText?: string
  thinkingDone?: boolean
  thinkingStartTime?: number
  onRegenerate?: (msgId: number) => void
  onEditMessage?: (msgId: number, newContent: string) => void
  onDeleteMessage?: (msgId: number) => void
}

export default function ChatMessages({
  messages, streamingText, isLoading, onSend,
  toolCallBubbles, thinkingText, thinkingDone, thinkingStartTime,
  onRegenerate, onEditMessage, onDeleteMessage,
}: ChatMessagesProps) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editText, setEditText] = useState('')

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingText, thinkingText, toolCallBubbles])

  const handleContainerClick = (e: React.MouseEvent<HTMLDivElement>) => {
    // 外部链接
    const link = (e.target as HTMLElement).closest('.external-link') as HTMLElement
    if (link) {
      e.preventDefault()
      const url = link.dataset.url
      if (url) {
        api.openUrl(url).catch(() => {
          window.open(url, '_blank', 'noopener,noreferrer')
        })
      }
      return
    }

    // 安全占位符展开/折叠
    const token = (e.target as HTMLElement).closest('.secret-token') as HTMLElement
    if (token) {
      e.stopPropagation()
      const valEl = token.querySelector('.secret-val') as HTMLElement
      if (!valEl) return
      if (valEl.style.display === 'none' || !valEl.style.display) {
        const key = token.getAttribute('data-secret') || ''
        const raw = lookupPlaceholder(key)
        valEl.textContent = raw || '(映射已丢失)'
        valEl.style.display = 'inline'
        token.classList.add('revealed')
      } else {
        valEl.style.display = 'none'
        token.classList.remove('revealed')
      }
    }
  }

  const startEdit = (msg: Message) => {
    setEditingId(msg.id)
    setEditText(msg.content)
  }

  const cancelEdit = () => {
    setEditingId(null)
    setEditText('')
  }

  const saveEdit = (msg: Message) => {
    if (!editText.trim() || !onEditMessage) return
    onEditMessage(msg.id, editText.trim())
    setEditingId(null)
    setEditText('')
  }

  const copyMessage = async (msg: Message) => {
    try {
      await navigator.clipboard.writeText(msg.content)
    } catch { /* silent */ }
  }

  const lastMsgId = messages.length > 0 ? messages[messages.length - 1].id : null

  if (messages.length === 0 && !isLoading && !streamingText && !toolCallBubbles?.length) {
    return (
      <div className="chat-messages">
        <div className="empty-state">
          <div className="empty-state-icon">Z</div>
          <h3>Zenith v2</h3>
          <p>你的本地智能助手。可以聊天、管理日程、记录笔记、执行代码。</p>
        </div>
      </div>
    )
  }

  return (
    <div className="chat-messages" onClick={handleContainerClick}>
      {messages.filter(m => m.role !== 'system').map(msg => {
        const isUser = msg.role === 'user'
        const card = !isUser ? extractConfirmCard(msg.content) : null
        const textContent = card ? stripConfirmCard(msg.content) : msg.content
        const isLast = lastMsgId !== null && msg.id === lastMsgId
        const isEditing = editingId === msg.id

        return (
          <div key={msg.id} className={`message message-${isUser ? 'user' : 'ai'} message-group`}>
            <div className="message-avatar">
              {isUser ? 'I' : 'Z'}
            </div>
            <div className="message-content">
              {isEditing ? (
                <div className="msg-edit-box">
                  <textarea
                    className="msg-edit-textarea"
                    value={editText}
                    onChange={e => setEditText(e.target.value)}
                    autoFocus
                    rows={Math.min(10, Math.max(3, editText.split('\n').length))}
                  />
                  <div className="msg-edit-actions">
                    <button className="btn btn-sm" onClick={cancelEdit}>取消</button>
                    <button
                      className="btn btn-sm"
                      style={{ background: 'var(--color-accent-primary)', color: '#fff' }}
                      onClick={() => saveEdit(msg)}
                      disabled={!editText.trim()}
                    >
                      保存{isUser ? '并重新生成' : ''}
                    </button>
                  </div>
                </div>
              ) : (
                <div className={`message-bubble ${isUser ? 'bubble-user' : 'bubble-ai'}`}>
                  <Markdown content={textContent} />
                  {card && onSend && (
                    <ConfirmCard
                      data={card}
                      onSelect={(opt: ConfirmOption) => onSend(opt.confirmText)}
                    />
                  )}
                </div>
              )}
              <div className="message-time">
                {msg.created_at ? new Date(msg.created_at).toLocaleTimeString('zh-CN', {
                  hour: '2-digit', minute: '2-digit'
                }) : ''}
              </div>
              {/* 操作栏 — hover 显示 */}
              <div className="msg-actions">
                <button className="msg-action-btn" title="复制" onClick={() => copyMessage(msg)}>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                </button>
                {!isUser && isLast && onRegenerate && (
                  <button className="msg-action-btn" title="重新生成" onClick={() => onRegenerate(msg.id)}>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><polyline points="21 3 21 9 15 9"/></svg>
                  </button>
                )}
                <button className="msg-action-btn" title="编辑" onClick={() => startEdit(msg)}>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>
                </button>
                <button className="msg-action-btn" title="删除（含之后所有消息）" onClick={() => {
                  if (onDeleteMessage && window.confirm('删除该消息及其之后的所有消息？此操作不可撤销。')) {
                    onDeleteMessage(msg.id)
                  }
                }}>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
              </div>
            </div>
          </div>
        )
      })}

      {/* 工具痕迹 — 渲染在流式回复上方（对齐 WorkBuddy：先过程后结论） */}
      {toolCallBubbles && toolCallBubbles.length > 0 && (
        <div className="message message-ai message-group">
          <div className="message-avatar" style={{ visibility: 'hidden' }}>Z</div>
          <div className="message-content">
            {toolCallBubbles.map(entry => (
              <div key={entry.id} className="trace-stack-item">
                <TraceCard entry={entry} />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 流式回复 — 思考块 + 文本 */}
      {(streamingText || thinkingText) && (
        <div className="message message-ai message-group">
          <div className="message-avatar">Z</div>
          <div className="message-content">
            {thinkingText && (
              <ThinkingBlock
                content={thinkingText}
                done={thinkingDone}
                startTime={thinkingStartTime}
              />
            )}
            {streamingText && (
              <div className="message-bubble bubble-ai">
                <Markdown content={streamingText} streaming />
              </div>
            )}
            {!streamingText && isLoading && (
              <div className="message-bubble bubble-ai">
                <div className="typing-indicator">
                  <div className="typing-dot" />
                  <div className="typing-dot" />
                  <div className="typing-dot" />
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  )
}
