import { useState } from 'react'

export interface ToolCallEntry {
  id: string
  name: string
  args: Record<string, any>
  status: 'pending' | 'done'
  resultSummary?: string
  durationMs?: number
  success?: boolean
  round?: number
}

interface ToolCallBubbleProps {
  entry: ToolCallEntry
}

// 工具类别 → 颜色编码
function toolColor(name: string): string {
  const n = name.toLowerCase()
  if (/web_search|web_fetch|search/.test(n)) return '#4fc3f7'   // 蓝色: 搜索
  if (/execute_code|run_code|python/.test(n)) return '#1ae865'    // 绿色: 代码执行
  if (/add_schedule|add_note|mem_add|goal_/.test(n)) return '#ffab40'  // 橙色: CRUD
  if (/distill|analyze|smart_classify|consolidate/.test(n)) return '#c792ea'  // 紫色: 分析
  return '#888'  // 默认灰色
}

function toolLabel(name: string): string {
  const labels: Record<string, string> = {
    web_search: '搜索',
    web_fetch: '读取网页',
    execute_code: '执行代码',
    add_schedule: '创建日程',
    add_note: '创建笔记',
    mem_add: '添加记忆',
    smart_classify: '智能分类',
    time_plan: '时间规划',
  }
  return labels[name] || name
}

function formatArgs(args: Record<string, any>): string {
  if (!args || Object.keys(args).length === 0) return '无参数'
  const lines = Object.entries(args).map(([k, v]) => {
    const val = typeof v === 'string' && v.length > 200 ? v.substring(0, 200) + '...' : JSON.stringify(v)
    return `${k}: ${val}`
  })
  return lines.join('\n')
}

export default function ToolCallBubble({ entry }: ToolCallBubbleProps) {
  const [expanded, setExpanded] = useState(false)
  const borderColor = toolColor(entry.name)
  const isPending = entry.status === 'pending'

  return (
    <div style={{
      marginTop: 6,
      padding: '10px 14px',
      background: 'var(--color-bg-panel)',
      border: `1px solid var(--color-border)`,
      borderLeft: `3px solid ${borderColor}`,
      borderRadius: 10,
      maxWidth: 520,
      opacity: isPending ? 0.7 : 1,
      transition: 'opacity 0.2s',
      cursor: 'pointer',
      userSelect: 'none',
    }} onClick={() => setExpanded(!expanded)}>
      {/* 头部：工具名 + 状态 + 耗时 */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 8,
      }}>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 12,
          fontWeight: 600,
          color: 'var(--color-text-primary)',
        }}>
          {isPending ? (
            <span style={{ display: 'inline-block', width: 12, height: 12 }}>
              <svg width="12" height="12" viewBox="0 0 12 12" style={{ animation: 'spin 1s linear infinite' }}>
                <circle cx="6" cy="6" r="5" fill="none" stroke={borderColor} strokeWidth="1.5" strokeDasharray="20 10" />
              </svg>
            </span>
          ) : (
            <span style={{ color: entry.success !== false ? '#1ae865' : '#ff5555', fontSize: 14 }}>
              {entry.success !== false ? '✓' : '✗'}
            </span>
          )}
          <span style={{ color: borderColor }}>{toolLabel(entry.name)}</span>
          <span style={{ color: 'var(--color-text-muted)', fontWeight: 400 }}>
            {entry.name}
          </span>
        </div>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 11,
          color: 'var(--color-text-muted)',
        }}>
          {isPending ? (
            <span>执行中...</span>
          ) : (
            <>
              {entry.durationMs !== undefined && (
                <span>{entry.durationMs < 1000 ? `${entry.durationMs}ms` : `${(entry.durationMs / 1000).toFixed(1)}s`}</span>
              )}
              <span style={{ transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }}>
                ▾
              </span>
            </>
          )}
        </div>
      </div>

      {/* 展开：参数 + 结果 */}
      {expanded && !isPending && (
        <div style={{ marginTop: 10, fontSize: 12 }}>
          {entry.args && Object.keys(entry.args).length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{
                fontWeight: 600,
                color: 'var(--color-text-secondary)',
                marginBottom: 4,
              }}>
                参数
              </div>
              <pre style={{
                margin: 0,
                padding: '8px 10px',
                background: 'var(--color-bg-muted)',
                borderRadius: 6,
                color: 'var(--color-text-secondary)',
                fontSize: 11,
                lineHeight: 1.5,
                overflow: 'auto',
                maxHeight: 160,
              }}>
                {formatArgs(entry.args)}
              </pre>
            </div>
          )}
          {entry.resultSummary && (
            <div>
              <div style={{
                fontWeight: 600,
                color: 'var(--color-text-secondary)',
                marginBottom: 4,
              }}>
                结果
              </div>
              <pre style={{
                margin: 0,
                padding: '8px 10px',
                background: 'var(--color-bg-muted)',
                borderRadius: 6,
                color: entry.success !== false ? 'var(--color-text-secondary)' : '#ff5555',
                fontSize: 11,
                lineHeight: 1.5,
                overflow: 'auto',
                maxHeight: 200,
              }}>
                {entry.resultSummary}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
