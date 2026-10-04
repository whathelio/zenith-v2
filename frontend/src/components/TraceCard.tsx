/* TraceCard — 对话执行痕迹（对齐 dsh GenericCommandCard 的内联 disclosure 行）
 *
 * 单行形态：状态点(StateDot) + 图标 + 标题 + 分隔点 + 摘要，点击展开代码框 body。
 * 状态：pending（扫光执行中）/ done（成功）/ failed（红字 error）
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
  messageId?: number | null
  stdout?: string
  stderr?: string
  exitCode?: number | null
  lang?: string
  filePath?: string
  oldText?: string
  newText?: string
}

interface TraceCardProps {
  entry: TraceEntry
  forceExpand?: boolean | null
}

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
  code:      { icon: '▶', label: '代码',     color: '#1ae865' },
  file_edit: { icon: '✎', label: '文件',     color: '#ffab40' },
  schedule:  { icon: '📅', label: '日程',     color: '#ffab40' },
  note:      { icon: '📝', label: '笔记',     color: '#f1fa8c' },
  memory:    { icon: '🧠', label: '记忆',     color: '#c792ea' },
  classify:  { icon: '🔀', label: '分类',     color: '#c792ea' },
  web:       { icon: '🌐', label: '搜索',     color: '#4fc3f7' },
  mcp:       { icon: '🔌', label: 'MCP',      color: '#8be9fd' },
  tool:      { icon: '🛠', label: '工具',     color: '#888888' },
}

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

/** 状态点：running 灰/成功绿/失败红 */
function StateDot({ state }: { state: 'running' | 'ok' | 'error' }) {
  const color = state === 'ok' ? '#50fa7b' : state === 'error' ? '#ff5555' : '#717e95'
  return (
    <span className="trace-dot" style={{ background: color }}>
      {state === 'running' && <span className="trace-dot-pulse" style={{ background: color }} />}
    </span>
  )
}

/** 组装展开 body 的文本（代码/参数/结果/输出） */
function buildBody(entry: TraceEntry): { body: string; hasBody: boolean } {
  if (entry.kind === 'code') {
    const parts: string[] = []
    if (typeof entry.args?.code === 'string') parts.push(entry.args.code)
    if (entry.stdout) parts.push(entry.stdout)
    if (entry.stderr) parts.push(`[stderr]\n${entry.stderr}`)
    if (!parts.length && entry.resultSummary) parts.push(entry.resultSummary)
    return { body: parts.join('\n\n'), hasBody: parts.length > 0 }
  }
  if (entry.kind === 'file_edit') {
    const diff: string[] = []
    entry.oldText?.split('\n').forEach(l => diff.push(`- ${l}`))
    entry.newText?.split('\n').forEach(l => diff.push(`+ ${l}`))
    if (diff.length) return { body: diff.join('\n'), hasBody: true }
    const fp = entry.filePath || entry.resultSummary || ''
    return { body: fp, hasBody: !!fp }
  }
  const parts: string[] = []
  if (entry.args && Object.keys(entry.args).length > 0) parts.push(formatArgs(entry.args))
  if (entry.resultSummary) parts.push(entry.resultSummary)
  return { body: parts.join('\n\n'), hasBody: parts.length > 0 }
}

function TraceRow({ entry, forceExpand }: { entry: TraceEntry; forceExpand?: boolean | null }) {
  const [expanded, setExpanded] = useState(false)
  const effExpanded = forceExpand !== null && forceExpand !== undefined ? !!forceExpand : expanded
  const meta = KIND_META[entry.kind] || KIND_META.tool
  const isPending = entry.status === 'pending'
  const isFail = !isPending && entry.success === false
  const state: 'running' | 'ok' | 'error' = isPending ? 'running' : isFail ? 'error' : 'ok'
  const { body, hasBody } = buildBody(entry)
  const hasDetail = hasBody && body.includes('\n')

  // 折叠态摘要：代码执行用退出码/耗时，工具用名称，失败用错误
  const summary = isFail
    ? (entry.resultSummary || '执行失败')
    : entry.kind === 'code'
      ? `${entry.name}${entry.exitCode !== undefined && entry.exitCode !== null ? ` · exit ${entry.exitCode}` : ''}${entry.durationMs !== undefined ? ` · ${fmtDuration(entry.durationMs)}` : ''}`
      : `${entry.name}${entry.durationMs !== undefined ? ` · ${fmtDuration(entry.durationMs)}` : ''}`

  const title = entry.kind === 'code' ? '代码' : meta.label

  return (
    <div
      className="trace-card"
      data-state={state}
      onClick={() => { if (hasDetail && !isPending) setExpanded(v => !v) }}
    >
      <div className="trace-row">
        <span className="trace-leading">
          <StateDot state={state} />
        </span>
        <span className="trace-title">{title}</span>
        <span className="trace-badge">{entry.name}</span>
        <span className="trace-separator" aria-hidden="true" />
        <span className="trace-summary" data-error={isFail || undefined}>{summary}</span>
        {hasDetail && !isPending && (
          <span className={`trace-chevron ${effExpanded ? 'open' : ''}`} aria-hidden="true">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M4 2.5L8 6L4 9.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
        )}
      </div>
      {effExpanded && hasDetail && !isPending && (
        <pre className="trace-body" data-error={isFail || undefined}>{body}</pre>
      )}
    </div>
  )
}

export default function TraceCard({ entry, forceExpand }: TraceCardProps) {
  return <TraceRow entry={entry} forceExpand={forceExpand} />
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
