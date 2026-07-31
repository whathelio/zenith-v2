/* ThinkingBlock — 模型思考过程折叠块（对齐 WorkBuddy 的 thinking 展示）
 *
 * - 流式期间：展开显示 + spinner + 实时累计的"已思考 Ns"
 * - 完成后：可折叠/展开，标题行显示耗时
 * - 内容超出时内部滚动（不撑爆消息流）
 */
import { useEffect, useRef, useState } from 'react'

interface ThinkingBlockProps {
  content: string
  /** 思考是否已结束（流式结束 / 完成） */
  done?: boolean
  /** 思考开始时间戳（ms），用于显示已思考时长 */
  startTime?: number
}

export default function ThinkingBlock({ content, done = false, startTime }: ThinkingBlockProps) {
  const [collapsed, setCollapsed] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const bodyRef = useRef<HTMLDivElement>(null)

  // 流式期间每秒刷新"已思考 Ns"
  useEffect(() => {
    if (done) return
    if (!startTime) return
    const timer = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startTime) / 1000))
    }, 500)
    return () => clearInterval(timer)
  }, [done, startTime])

  // 新内容到达时自动滚动到底部
  useEffect(() => {
    if (!collapsed && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight
    }
  }, [content, collapsed])

  const displayElapsed = startTime
    ? (done ? (elapsed || Math.max(1, Math.floor((Date.now() - startTime) / 1000))) : elapsed)
    : 0

  return (
    <div className={`thinking-block ${done ? 'thinking-block-done' : 'thinking-block-running'}`}>
      <div
        className="thinking-header"
        onClick={() => setCollapsed(!collapsed)}
        role="button"
        tabIndex={0}
      >
        <span className="thinking-indicator">
          {!done && (
            <svg width="12" height="12" viewBox="0 0 12 12" className="thinking-spinner">
              <circle cx="6" cy="6" r="5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="20 10" />
            </svg>
          )}
          {done && <span className="thinking-done-mark">✓</span>}
        </span>
        <span className="thinking-title">思考过程</span>
        {startTime && (
          <span className="thinking-elapsed">已思考 {displayElapsed}s</span>
        )}
        <span className={`thinking-arrow ${collapsed ? '' : 'open'}`}>▾</span>
      </div>
      {!collapsed && content && (
        <div ref={bodyRef} className="thinking-body">
          {content}
        </div>
      )}
      {!collapsed && !content && (
        <div className="thinking-body thinking-empty">正在思考…</div>
      )}
    </div>
  )
}
