/* ThinkingBlock — 模型思考过程（对齐 dsh ReasoningRow 的「Think」折叠行）
 *
 * - 单行 disclosure：图标 + "Think" 标题 + 单行摘要（流式中取末行、完成取首行）
 * - 展开后 22px 缩进、灰字、14px/24px
 * - 流式 running 态带扫光动画（由 CSS 实现）
 */
import { useEffect, useRef, useState } from 'react'

interface ThinkingBlockProps {
  content: string
  /** 思考是否已结束（流式结束 / 完成） */
  done?: boolean
}

function firstLine(text: string): string {
  const newline = text.indexOf('\n')
  return newline === -1 ? text : text.slice(0, newline)
}

function latestLine(text: string): string {
  const visible = text.trimEnd()
  const newline = visible.lastIndexOf('\n')
  return newline === -1 ? visible : visible.slice(newline + 1)
}

export default function ThinkingBlock({ content, done = false }: ThinkingBlockProps) {
  const [expanded, setExpanded] = useState(false)
  const summaryRef = useRef<HTMLSpanElement>(null)
  const running = !done

  const summary = running ? latestLine(content) : firstLine(content)

  // 流式中让摘要末尾跟随最新内容（右对齐到可见区）
  useEffect(() => {
    const el = summaryRef.current
    if (!el) return
    el.scrollLeft = running ? el.scrollWidth - el.clientWidth : 0
  }, [summary, running])

  return (
    <div className="thinking-block" data-state={running ? 'running' : 'ok'}>
      <button
        type="button"
        className="thinking-row"
        onClick={() => setExpanded(v => !v)}
        aria-expanded={expanded}
      >
        <span className="thinking-leading">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.2">
            <circle cx="7" cy="7" r="5.5" />
            <path d="M7 4.5v3l2 1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
        <span className="thinking-title">Think</span>
        <span className="thinking-separator" aria-hidden="true" />
        <span ref={summaryRef} className="thinking-summary" data-follow-end={running || undefined}>
          {summary}
        </span>
        <span className={`thinking-chevron ${expanded ? 'open' : ''}`} aria-hidden="true">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M4 2.5L8 6L4 9.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      </button>
      {expanded && content && (
        <div className="thinking-body">{content}</div>
      )}
      {expanded && !content && (
        <div className="thinking-body thinking-empty">正在思考…</div>
      )}
    </div>
  )
}
