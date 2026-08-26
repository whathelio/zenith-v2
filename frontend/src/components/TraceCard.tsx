/* TraceCard — 对话执行痕迹统一卡片（对齐 WorkBuddy 工具调用/代码痕迹）
 *
 * 分类渲染：
 * - kind=code      代码执行：语言标签 + 退出码 + stdout/stderr + 耗时
 * - kind=file_edit 文件编辑（预留）：文件路径 + diff 视图
 * - kind=tool      通用工具调用：参数 + 结果摘要折叠
 *
 * 状态：pending（spinner 执行中）/ done ✓ / failed ✗
 */
import { useState } from 'react'

export type TraceKind = 'tool' | 'code' | 'file_edit' | 'schedule' | 'note' | 'memory' | 'classify' | 'web' | 'mcp'

export interface TraceEntry {
  id: string
  name: string
  kind: TraceKind
  args: Record<string, any>
  status: 'pending' | 'done'
  resultSummary?: string
  durationMs?: number
  success?: boolean
  round?: number
  // 关联的 user 消息 id（用于前端交错渲染）
  messageId?: number | null
  // 代码执行
  stdout?: string
  stderr?: string
  exitCode?: number | null
  lang?: string
  // 文件编辑（预留）
  filePath?: string
  oldText?: string
  newText?: string
}

interface TraceCardProps {
  entry: TraceEntry
  /** 强制展开/折叠：true=全部展开, false=全部折叠, null/undefined=各自默认状态 */
  forceExpand?: boolean | null
}

/* ── 工具分类 ── */

export function classifyTool(name: string): TraceKind {
  const n = name.toLowerCase()
  if (n === 'execute_code') return 'code'
  if (/add_schedule|complete_schedule|create_plan_schedule|sync_calendar|update_schedule|time_plan|list_schedule/.test(n)) return 'schedule'
  if (/add_note|distill_note|distill_conversation|distill_schedules|distill_memories|distill_all|distill_daily|distill_weekly|list_notes|create_tutorial/.test(n)) return 'note'
  if (/mem_add|add_memory|consolidate_memories|search_memory|retrieve_docs|kb_stats/.test(n)) return 'memory'
  if (/smart_classify|analyze_content|scan_file_safety/.test(n)) return 'classify'
  if (/web_search|web_fetch/.test(n)) return 'web'
  if (/call_mcp/.test(n)) return 'mcp'
  return 'tool'
}

const KIND_META: Record<TraceKind, { icon: string; label: string; color: string }> = {
  code:      { icon: '▶', label: '执行代码',   color: '#1ae865' },
  file_edit: { icon: '✎', label: '编辑文件',   color: '#ffab40' },
  schedule:  { icon: '📅', label: '日程',       color: '#ffab40' },
  note:      { icon: '📝', label: '笔记',       color: '#f1fa8c' },
  memory:    { icon: '🧠', label: '记忆',       color: '#c792ea' },
  classify:  { icon: '🔀', label: '智能分类',   color: '#c792ea' },
  web:       { icon: '🌐', label: '搜索',       color: '#4fc3f7' },
  mcp:       { icon: '🔌', label: 'MCP 工具',   color: '#8be9fd' },
  tool:      { icon: '🛠', label: '工具',       color: '#888888' },
}

/* ── 工具辅助 ── */

function formatArgs(args: Record<string, any>, maxLen = 600): string {
  if (!args || Object.keys(args).length === 0) return '无参数'
  const lines = Object.entries(args).map(([k, v]) => {
    let val: string
    if (typeof v === 'string') {
      val = v.length > maxLen ? v.substring(0, maxLen) + '...' : v
    } else {
      try { val = JSON.stringify(v) } catch { val = String(v) }
    }
    return `${k}: ${val}`
  })
  return lines.join('\n')
}

function fmtDuration(ms?: number): string {
  if (ms === undefined) return ''
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

function ToolBadge({ name }: { name: string }) {
  return <span className="trace-tool-badge">{name}</span>
}

/* ── 代码执行卡片 ── */

function CodeTrace({ entry, forceExpand }: { entry: TraceEntry; forceExpand?: boolean | null }) {
  const [showArgs, setShowArgs] = useState(false)
  const [showOutput, setShowOutput] = useState(false)
  // 受控模式：forceExpand 覆盖局部状态（null 时不干预）
  const effArgs = forceExpand !== null && forceExpand !== undefined ? !!forceExpand : showArgs
  const effOutput = forceExpand !== null && forceExpand !== undefined ? !!forceExpand : showOutput
  const code = typeof entry.args?.code === 'string' ? entry.args.code : ''
  const isFail = entry.success === false

  return (
    <div className={`trace-card trace-code ${isFail ? 'trace-fail' : ''}`}>
      {/* header */}
      <div className="trace-header">
        <span className="trace-icon" style={{ color: isFail ? '#ff5555' : '#1ae865' }}>{isFail ? '✗' : '✓'}</span>
        <span className="trace-label" style={{ color: '#1ae865' }}>执行代码</span>
        {entry.lang && <span className="trace-lang">{entry.lang}</span>}
        <span className="trace-name">{entry.name}</span>
        <span className="trace-spacer" />
        {entry.exitCode !== undefined && entry.exitCode !== null && (
          <span className={`trace-exit ${entry.exitCode === 0 ? 'ok' : 'err'}`}>exit {entry.exitCode}</span>
        )}
        <span className="trace-duration">{fmtDuration(entry.durationMs)}</span>
      </div>

      {/* 代码（折叠） */}
      {code && (
        <div className="trace-section">
          <button className="trace-section-toggle" onClick={() => setShowArgs(!showArgs)}>
            <span className={`trace-caret ${effArgs ? 'open' : ''}`}>▸</span> 代码
          </button>
          {effArgs && (
            <pre className="trace-code-args">{code.split('\n').slice(0, 25).join('\n')}{code.split('\n').length > 25 ? '\n…' : ''}</pre>
          )}
        </div>
      )}

      {/* 输出 */}
      {(entry.stdout || entry.stderr || entry.resultSummary) && (
        <div className="trace-section">
          <button className="trace-section-toggle" onClick={() => setShowOutput(!showOutput)}>
            <span className={`trace-caret ${effOutput ? 'open' : ''}`}>▸</span> 输出
            {entry.stdout?.length ? <span className="trace-count">{entry.stdout.length} 字符</span> : null}
          </button>
          {effOutput && (
            <div className="trace-output">
              {entry.stdout ? <pre className="trace-stdout">{entry.stdout}</pre> : null}
              {entry.stderr ? <pre className="trace-stderr">{entry.stderr}</pre> : null}
              {!entry.stdout && !entry.stderr && entry.resultSummary
                ? <pre className={`trace-stdout ${isFail ? 'trace-stderr' : ''}`}>{entry.resultSummary}</pre>
                : null}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/* ── 文件编辑卡片（预留，diff 视图）── */

function FileEditTrace({ entry, forceExpand }: { entry: TraceEntry; forceExpand?: boolean | null }) {
  const [expanded, setExpanded] = useState(false)
  const effExpanded = forceExpand !== null && forceExpand !== undefined ? !!forceExpand : expanded
  return (
    <div className="trace-card trace-file">
      <div className="trace-header">
        <span className="trace-icon" style={{ color: '#ffab40' }}>✎</span>
        <span className="trace-label" style={{ color: '#ffab40' }}>编辑文件</span>
        <span className="trace-filepath">{entry.filePath || entry.name}</span>
        <span className="trace-spacer" />
        <span className="trace-duration">{fmtDuration(entry.durationMs)}</span>
      </div>
      <div className="trace-section">
        <button className="trace-section-toggle" onClick={() => setExpanded(!expanded)}>
          <span className={`trace-caret ${effExpanded ? 'open' : ''}`}>▸</span> 变更
        </button>
        {effExpanded && (entry.oldText || entry.newText) && (
          <div className="trace-diff">
            {entry.oldText?.split('\n').map((l, i) => (
              <div key={`-${i}`} className="trace-diff-line del"><span className="trace-diff-marker">-</span><span>{l || ' '}</span></div>
            ))}
            {entry.newText?.split('\n').map((l, i) => (
              <div key={`+${i}`} className="trace-diff-line add"><span className="trace-diff-marker">+</span><span>{l || ' '}</span></div>
            ))}
          </div>
        )}
        {effExpanded && !entry.oldText && !entry.newText && (
          <pre className="trace-argbox">{entry.resultSummary || entry.args?.file_path || ''}</pre>
        )}
      </div>
    </div>
  )
}

/* ── 通用工具卡片 ── */

function ToolTrace({ entry, forceExpand }: { entry: TraceEntry; forceExpand?: boolean | null }) {
  const [expanded, setExpanded] = useState(false)
  const effExpanded = forceExpand !== null && forceExpand !== undefined ? !!forceExpand : expanded
  const meta = KIND_META[entry.kind] || KIND_META.tool
  const isPending = entry.status === 'pending'
  const isFail = !isPending && entry.success === false

  return (
    <div
      className={`trace-card trace-tool ${isPending ? 'trace-pending' : ''} ${isFail ? 'trace-fail' : ''}`}
      onClick={() => !isPending && setExpanded(!expanded)}
      role="button"
    >
      <div className="trace-header">
        {isPending ? (
          <span className="trace-icon trace-spin" style={{ color: meta.color }}>
            <svg width="12" height="12" viewBox="0 0 12 12">
              <circle cx="6" cy="6" r="5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="20 10" />
            </svg>
          </span>
        ) : (
          <span className="trace-icon" style={{ color: isFail ? '#ff5555' : '#1ae865' }}>{isFail ? '✗' : '✓'}</span>
        )}
        <span className="trace-label" style={{ color: meta.color }}>{meta.icon} {meta.label}</span>
        <ToolBadge name={entry.name} />
        <span className="trace-spacer" />
        {isPending ? (
          <span className="trace-pending-text">执行中…</span>
        ) : (
          <>
            <span className="trace-duration">{fmtDuration(entry.durationMs)}</span>
            <span className={`trace-caret ${effExpanded ? 'open' : ''}`}>▾</span>
          </>
        )}
      </div>

      {effExpanded && !isPending && (
        <div className="trace-detail">
          {entry.args && Object.keys(entry.args).length > 0 && (
            <div className="trace-block">
              <div className="trace-block-title">参数</div>
              <pre className="trace-argbox">{formatArgs(entry.args)}</pre>
            </div>
          )}
          {entry.resultSummary && (
            <div className="trace-block">
              <div className="trace-block-title">结果</div>
              <pre className={`trace-argbox ${isFail ? 'trace-fail-text' : ''}`}>{entry.resultSummary}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/* ── 主组件 ── */

export default function TraceCard({ entry, forceExpand }: TraceCardProps) {
  if (entry.kind === 'code') return <CodeTrace entry={entry} forceExpand={forceExpand} />
  if (entry.kind === 'file_edit') return <FileEditTrace entry={entry} forceExpand={forceExpand} />
  return <ToolTrace entry={entry} forceExpand={forceExpand} />
}

/** 从旧版 ToolCallEntry 迁移辅助（ChatView 使用） */
export function toTraceEntry(entry: {
  id: string
  name: string
  args: Record<string, any>
  status: 'pending' | 'done'
  resultSummary?: string
  durationMs?: number
  success?: boolean
  round?: number
  messageId?: number | null
  stdout?: string
  stderr?: string
  exit_code?: number | null
  lang?: string
}): TraceEntry {
  return {
    id: entry.id,
    name: entry.name,
    kind: classifyTool(entry.name),
    args: entry.args || {},
    status: entry.status,
    resultSummary: entry.resultSummary,
    durationMs: entry.durationMs,
    success: entry.success,
    round: entry.round,
    messageId: entry.messageId,
    stdout: entry.stdout,
    stderr: entry.stderr,
    exitCode: entry.exit_code,
    lang: entry.lang,
  }
}
