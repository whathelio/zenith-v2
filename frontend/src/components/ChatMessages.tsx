import { useRef, useEffect } from 'react'
import type { Message } from '../shared/api'
import { api } from '../shared/api'
import ConfirmCard, { extractConfirmCard, stripConfirmCard, type ConfirmOption } from './ConfirmCard'

interface ChatMessagesProps {
  messages: Message[]
  streamingText: string
  isLoading: boolean
  onSend?: (text: string) => void
}

export default function ChatMessages({ messages, streamingText, isLoading, onSend }: ChatMessagesProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingText])

  const handleLinkClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const link = (e.target as HTMLElement).closest('.external-link') as HTMLElement
    if (link) {
      e.preventDefault()
      const url = link.dataset.url
      if (url) {
        api.openUrl(url).catch(() => {
          // 回退：直接在新标签打开
          window.open(url, '_blank', 'noopener,noreferrer')
        })
      }
    }
  }

  if (messages.length === 0 && !isLoading && !streamingText) {
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
    <div className="chat-messages" onClick={handleLinkClick}>
      {messages.filter(m => m.role !== 'system').map(msg => {
        const card = msg.role !== 'user' ? extractConfirmCard(msg.content) : null
        const textContent = card ? stripConfirmCard(msg.content) : msg.content
        return (
          <div key={msg.id} className={`message message-${msg.role === 'user' ? 'user' : 'ai'}`}>
            <div className="message-avatar">
              {msg.role === 'user' ? 'I' : 'Z'}
            </div>
            <div>
              <div
                className="message-bubble"
                dangerouslySetInnerHTML={{ __html: formatContent(textContent) }}
              />
              {card && onSend && (
                <ConfirmCard
                  data={card}
                  onSelect={(opt: ConfirmOption) => onSend(opt.confirmText)}
                />
              )}
              <div className="message-time">
                {msg.created_at ? new Date(msg.created_at).toLocaleTimeString('zh-CN', {
                  hour: '2-digit', minute: '2-digit'
                }) : ''}
              </div>
            </div>
          </div>
        )
      })}
      {streamingText && (
        <div className="message message-ai">
          <div className="message-avatar">Z</div>
          <div>
            <div
              className="message-bubble"
              dangerouslySetInnerHTML={{ __html: formatContent(streamingText) }}
            />
          </div>
        </div>
      )}
      {isLoading && !streamingText && (
        <div className="message message-ai">
          <div className="message-avatar">Z</div>
          <div className="message-bubble">
            <div className="typing-indicator">
              <div className="typing-dot" />
              <div className="typing-dot" />
              <div className="typing-dot" />
            </div>
          </div>
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  )
}

function formatContent(content: string): string {
  if (!content) return ''

  // 1. 提取代码块（内部 URL 不自动链接）
  const codeBlocks: string[] = []
  let html = content.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
    const escaped = code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    codeBlocks.push(`<pre><code>${escaped}</code></pre>`)
    return `\u0000CB${codeBlocks.length - 1}\u0000`
  })

  // 2. 提取行内代码
  const inlineCodes: string[] = []
  html = html.replace(/`([^`]+)`/g, (_, code) => {
    const escaped = code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    inlineCodes.push(`<code>${escaped}</code>`)
    return `\u0000IC${inlineCodes.length - 1}\u0000`
  })

  // 3. HTML 转义
  html = html.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

  // 4. Markdown 链接 [text](url)
  html = html.replace(
    /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    '<a class="external-link" data-url="$2">$1</a>'
  )

  // 5. 自动识别裸 URL
  html = html.replace(
    /(^|[\s(])(https?:\/\/[^\s<)\]]+)/g,
    '$1<a class="external-link" data-url="$2">$2</a>'
  )

  // 6. 其他格式
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>')
  html = html.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>')
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>')
  html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>')
  html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>')
  html = html.replace(/\n\n/g, '</p><p>')
  html = html.replace(/\n/g, '<br/>')
  html = '<p>' + html + '</p>'
  html = html.replace(/<p><\/p>/g, '')

  // 7. 恢复行内代码
  html = html.replace(/\u0000IC(\d+)\u0000/g, (_, i) => inlineCodes[parseInt(i)])
  // 8. 恢复代码块
  html = html.replace(/\u0000CB(\d+)\u0000/g, (_, i) => codeBlocks[parseInt(i)])

  return html
}
