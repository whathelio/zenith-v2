import { useState, useRef, useEffect, KeyboardEvent, ClipboardEvent } from 'react'
import { scanSecrets, maskSecrets, getPastedText, type SecretMatch } from '../shared/security'

interface ChatInputProps {
  onSend: (text: string) => void
  isLoading: boolean
  onStop?: () => void
}

export default function ChatInput({ onSend, isLoading, onStop }: ChatInputProps) {
  const [text, setText] = useState('')
  const [sanitizeDialog, setSanitizeDialog] = useState<{
    matches: SecretMatch[]
    raw: string
  } | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    textareaRef.current?.focus()
  }, [])

  const handleSend = () => {
    const trimmed = text.trim()
    if (!trimmed || isLoading) return
    onSend(trimmed)
    setText('')
  }

  const handleStop = () => {
    onStop?.()
  }

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (isLoading) {
        handleStop()
      } else {
        handleSend()
      }
    }
  }

  const adjustHeight = () => {
    const ta = textareaRef.current
    if (ta) {
      ta.style.height = 'auto'
      ta.style.height = Math.min(ta.scrollHeight, 200) + 'px'
    }
  }

  /** 粘贴事件 — 检测并提示脱敏 */
  const handlePaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const pasted = getPastedText(e as unknown as React.ClipboardEvent<Element>)
    if (!pasted) return

    const matches = scanSecrets(pasted)
    if (matches.length === 0) return // 无敏感内容，正常粘贴

    // 阻止默认粘贴，弹出确认对话框
    e.preventDefault()
    setSanitizeDialog({ matches, raw: pasted })
  }

  /** 用户确认脱敏 */
  const handleSanitizeConfirm = () => {
    if (!sanitizeDialog) return
    const { masked, count } = maskSecrets(sanitizeDialog.raw)
    // 替换当前文本（追加，而非覆盖已有内容）
    const ta = textareaRef.current
    if (ta) {
      const start = ta.selectionStart
      const end = ta.selectionEnd
      const before = text.slice(0, start)
      const after = text.slice(end)
      // 如果在光标处插入（替换选区或直接插入）
      if (start !== end) {
        setText(before + masked + after)
      } else {
        // 追加到光标位置
        setText(before + masked + after)
      }
      adjustHeight()
    }
    setSanitizeDialog(null)
  }

  /** 用户拒绝脱敏��原文粘贴 */
  const handleSanitizeReject = () => {
    if (!sanitizeDialog) return
    const ta = textareaRef.current
    if (ta) {
      const start = ta.selectionStart
      const before = text.slice(0, start)
      const after = text.slice(start)
      setText(before + sanitizeDialog.raw + after)
      adjustHeight()
    }
    setSanitizeDialog(null)
  }

  return (
    <div className="chat-input-area">
      {/* 脱敏确认弹窗 */}
      {sanitizeDialog && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          zIndex: 2000,
        }} onClick={() => setSanitizeDialog(null)}>
          <div style={{
            background: 'var(--color-bg-panel)',
            border: '1px solid var(--color-accent-warning, #f0a030)',
            borderRadius: 12, padding: 24, maxWidth: 480, width: '90%',
            boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
          }} onClick={e => e.stopPropagation()}>
            <h3 style={{ margin: '0 0 8px', color: 'var(--color-accent-warning, #f0a030)', fontSize: 16 }}>
              ⚠ 检测到 {sanitizeDialog.matches.length} 处疑似敏感内容
            </h3>
            <p style={{ fontSize: 13, color: 'var(--color-text-muted)', margin: '0 0 16px' }}>
              粘贴内容中包含 API Key / Token / 密钥。建议脱敏后发送，Zenith 只会看到占位符 {'{{SEC_xxx}}'}，不会存储原文。
            </p>
            <div style={{
              maxHeight: 160, overflow: 'auto', marginBottom: 16,
              background: 'var(--color-bg-input)', borderRadius: 8, padding: 12,
              fontSize: 12, fontFamily: 'monospace',
            }}>
              {sanitizeDialog.matches.map((m, i) => (
                <div key={i} style={{ marginBottom: 4 }}>
                  <span style={{ color: 'var(--color-accent-danger)' }}>🔒 {m.placeholder}</span>
                  {' ← '}
                  <span style={{ color: 'var(--color-text-muted)' }}>
                    {m.raw.length > 30 ? m.raw.slice(0, 30) + '...' : m.raw}
                  </span>
                  <span style={{ color: 'var(--color-text-muted)', fontSize: 10, marginLeft: 8 }}>({m.name})</span>
                </div>
              ))}
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button
                onClick={handleSanitizeReject}
                style={{
                  padding: '8px 18px', borderRadius: 6, border: '1px solid var(--color-border)',
                  background: 'transparent', color: 'var(--color-text)', cursor: 'pointer',
                  fontSize: 13,
                }}
              >
                直接发送原文
              </button>
              <button
                onClick={handleSanitizeConfirm}
                style={{
                  padding: '8px 18px', borderRadius: 6, border: 'none',
                  background: 'var(--color-accent-primary)', color: '#fff',
                  cursor: 'pointer', fontSize: 13, fontWeight: 600,
                }}
              >
                🛡 脱敏后发送
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="chat-input-wrapper">
        <textarea
          ref={textareaRef}
          className="chat-input"
          placeholder={isLoading ? '回复生成中… (Enter 停止)' : '输入消息，Enter 发送，Shift+Enter 换行...'}
          value={text}
          onChange={e => { setText(e.target.value); adjustHeight() }}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          rows={1}
        />
        {isLoading ? (
          <button
            className="btn-icon primary chat-stop-btn"
            onClick={handleStop}
            title="停止生成 (Enter)"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
          </button>
        ) : (
          <button
            className="btn-icon primary"
            onClick={handleSend}
            disabled={!text.trim()}
            title="发送 (Enter)"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="22" y1="2" x2="11" y2="13" />
              <polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
          </button>
        )}
      </div>
    </div>
  )
}
