/* Zenith Markdown 渲染器 — 完整 GFM 支持（无外部依赖）
 *
 * 支持：标题 / 段落 / 引用(嵌套) / 有序·无序·嵌套列表 / 任务列表 / 表格 /
 *      分割线 / 行内代码 / 代码块(语言标签·复制·长代码折叠) / 加粗·斜体·删除线 /
 *      链接(外部打开) / 裸 URL / {{SEC_xxx}} 安全占位符
 *
 * 安全占位符与外部链接采用容器事件委托（见 ChatMessages 的 onClick），
 * 与旧版行为保持一致。
 */
import { memo, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

/* ─────────────────────────── 类型 ─────────────────────────── */

type InlineToken =
  | { type: 'text'; text: string }
  | { type: 'code'; text: string }
  | { type: 'strong'; children: InlineToken[] }
  | { type: 'em'; children: InlineToken[] }
  | { type: 'del'; children: InlineToken[] }
  | { type: 'link'; text: string; url: string }
  | { type: 'url'; url: string }
  | { type: 'secret'; key: string }

type Block =
  | { type: 'paragraph'; children: InlineToken[] }
  | { type: 'heading'; level: number; children: InlineToken[] }
  | { type: 'blockquote'; children: Block[] }
  | { type: 'list'; items: ListItem[] }
  | { type: 'table'; headers: InlineToken[][]; rows: InlineToken[][][] }
  | { type: 'code'; lang: string; code: string }
  | { type: 'hr' }

interface ListItem {
  ordered: boolean
  task: { checked: boolean; label: InlineToken[] } | null
  children: InlineToken[]
  sub: ListItem[]
}

/* ─────────────────────────── 行内解析 ─────────────────────────── */

// 链接单独匹配（严格紧邻约束，避免对孤立 [ 的灾难性回溯）
// 必须 [文本](http...) 紧邻结构；文本内不允许再嵌套 [
const LINK_RE = /\[([^\[\]]{1,200})\]\((https?:\/\/[^\s)]+)\)/
// 基础行内标记（不含链接分支）
// 单星号 em 用「前后边界」约束：仅当星号两侧是空白/标点边界时才视为强调，
// 避免 Python 代码 `[2023]*4` 或 `per_1m*` 中的 * 触发灾难性回溯
const INLINE_RE = /(\*\*[^*]+\*\*|~~[^~]+~~|(?:^|[^\w*])\*[^*\s][^*]*\*(?=$|[^\w*])|https?:\/\/[^\s<)\]]+|`[^`\n]+`|\{\{SEC_\d{3}\}\})/

function parseInline(text: string): InlineToken[] {
  if (!text) return []
  const tokens: InlineToken[] = []
  let rest = text
  while (rest) {
    // 先尝试链接（严格模式，失败不回溯）
    const linkM = LINK_RE.exec(rest)
    if (linkM && linkM.index === 0) {
      tokens.push({ type: 'link', text: linkM[1], url: linkM[2] })
      rest = rest.slice(linkM[0].length)
      continue
    }
    const m = INLINE_RE.exec(rest)
    if (!m) {
      if (rest) tokens.push({ type: 'text', text: rest })
      break
    }
    if (m.index > 0) tokens.push({ type: 'text', text: rest.slice(0, m.index) })
    const token = m[0]
    if (token.startsWith('**') && token.endsWith('**')) {
      tokens.push({ type: 'strong', children: parseInline(token.slice(2, -2)) })
    } else if (token.startsWith('~~') && token.endsWith('~~')) {
      tokens.push({ type: 'del', children: parseInline(token.slice(2, -2)) })
    } else if (token.includes('*') && token.endsWith('*') && token.slice(1, -1).includes('*') === false) {
      // 单星号 em：token 可能带前置边界字符，取第一个 * 到末尾 *
      const firstStar = token.indexOf('*')
      const inner = token.slice(firstStar + 1, -1)
      tokens.push({ type: 'em', children: parseInline(inner) })
    } else if (token.startsWith('http')) {
      tokens.push({ type: 'url', url: token })
    } else if (token.startsWith('`')) {
      tokens.push({ type: 'code', text: token.slice(1, -1) })
    } else if (token.startsWith('{{SEC_')) {
      tokens.push({ type: 'secret', key: token.slice(2, -2) })
    } else {
      tokens.push({ type: 'text', text: token })
    }
    rest = rest.slice(m.index + token.length)
  }
  return tokens
}

/* ─────────────────────────── 块级解析 ─────────────────────────── */

interface RawLine {
  indent: number
  text: string
}

const FENCE_RE = /^\s*```(\w*)\s*$/
const HEADING_RE = /^(#{1,6})\s+(.*)$/
const HR_RE = /^\s*(\*{3,}|-{3,}|_{3,})\s*$/
const BLOCKQUOTE_RE = /^>\s?(.*)$/
const LIST_RE = /^(\s*)([-*+]|\d+\.)\s+(.*)$/
const TABLE_ROW_RE = /^\s*\|.*\|\s*$/
const TABLE_SEP_RE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/

function toRawLines(text: string): RawLine[] {
  return text.split('\n').map(line => {
    const indentMatch = line.match(/^\s*/)
    return { indent: indentMatch ? indentMatch[0].length : 0, text: line }
  })
}

interface ParseOneResult {
  block: Block
  next: number
  closed: boolean
}

/** 前 k 行的字符偏移（含换行符）。k === lines.length 时不额外计入行尾换行。 */
function lineOffset(lines: RawLine[], k: number): number {
  const end = Math.min(k, lines.length)
  let offset = 0
  for (let j = 0; j < end; j++) offset += lines[j].text.length + 1
  if (end > 0 && end === lines.length) offset -= 1
  return Math.max(0, offset)
}

/**
 * 解析单个块。lines[i] 必须是非空行。
 *
 * closed 表示是否拿到了“确定终止边界”：
 * - true：后续追加任何内容都不会改变这个块的解析结果，可安全缓存（毕业）。
 * - false：块可能尚未结束（如未闭合代码围栏 / 表格末尾 / 列表后悬空），
 *   必须留在尾段，每次流式更新时重算。
 */
function parseOneBlock(lines: RawLine[], i: number): ParseOneResult {
  const n = lines.length
  const line = lines[i]

  // 代码围栏
  const fence = line.text.match(FENCE_RE)
  if (fence) {
    const lang = fence[1]
    const codeLines: string[] = []
    let j = i + 1
    while (j < n && !FENCE_RE.test(lines[j].text)) {
      codeLines.push(lines[j].text)
      j++
    }
    const foundClose = j < n
    if (foundClose) j++ // 跳过结束围栏
    // 只有结束围栏后还跟着换行（存在后续行）才视为闭合；
    // 否则后续 chunk 可能在同一行继续追加，把 ``` 变成非围栏。
    const closed = foundClose && j < n
    return { block: { type: 'code', lang, code: codeLines.join('\n') }, next: j, closed }
  }

  // 分割线
  if (HR_RE.test(line.text.trim())) {
    // 单行块也要等换行到达：行尾可能继续追加字符
    return { block: { type: 'hr' }, next: i + 1, closed: i + 1 < n }
  }

  // 标题
  const heading = line.text.match(HEADING_RE)
  if (heading) {
    return { block: { type: 'heading', level: heading[1].length, children: parseInline(heading[2]) }, next: i + 1, closed: i + 1 < n }
  }

  // 引用块 — 收集连续 > 行
  if (BLOCKQUOTE_RE.test(line.text)) {
    const quoteLines: string[] = []
    let j = i
    while (j < n && BLOCKQUOTE_RE.test(lines[j].text)) {
      quoteLines.push(lines[j].text.replace(BLOCKQUOTE_RE, '$1'))
      j++
    }
    const inner = parseBlocks(quoteLines.join('\n'))
    return {
      block: { type: 'blockquote', children: inner.length ? inner : [{ type: 'paragraph', children: [] }] },
      next: j,
      closed: j + 1 < n,
    }
  }

  // 表格 — 当前行是 | 行且下一行是分隔行
  if (TABLE_ROW_RE.test(line.text) && i + 1 < n && TABLE_SEP_RE.test(lines[i + 1].text)) {
    const header = line.text.trim().replace(/^\||\|$/g, '').split('|').map(c => parseInline(c.trim()))
    let j = i + 2
    const rows: InlineToken[][][] = []
    while (j < n && TABLE_ROW_RE.test(lines[j].text) && !TABLE_SEP_RE.test(lines[j].text)) {
      const cells = lines[j].text.trim().replace(/^\||\|$/g, '').split('|').map(c => parseInline(c.trim()))
      rows.push(cells)
      j++
    }
    return { block: { type: 'table', headers: header, rows }, next: j, closed: j + 1 < n }
  }

  // 列表 — 收集连续的列表候选行（含缩进与空行，直到下一个非列表非空行）
  if (LIST_RE.test(line.text)) {
    const listLines: RawLine[] = []
    let j = i
    let closed = false
    while (j < n) {
      const l = lines[j]
      if (LIST_RE.test(l.text)) {
        listLines.push(l)
        j++
      } else if (!l.text.trim()) {
        // 空行：若后面还跟列表行则并入（支持列表内空行）
        let k = j + 1
        while (k < n && !lines[k].text.trim()) k++
        if (k < n && LIST_RE.test(lines[k].text) && lines[k].indent > line.indent) {
          j = k
        } else {
          // 列表确实终止：要求终止行后面还有换行（保守，防末尾半行后续变回列表项）
          closed = k + 1 < n
          break
        }
      } else {
        closed = j + 1 < n
        break
      }
    }
    const items = parseListLines(listLines)
    if (items.length) {
      return { block: { type: 'list', items }, next: j, closed }
    }
    // 理论不可达：LIST_RE 命中的行必然能解析出至少一个 item；此处仅防御死循环
    return { block: { type: 'paragraph', children: parseInline(line.text) }, next: i + 1, closed: true }
  }

  // 普通段落 — 合并连续非空行
  const paraLines: string[] = []
  let j = i
  while (j < n && lines[j].text.trim() && !FENCE_RE.test(lines[j].text) && !HEADING_RE.test(lines[j].text) && !LIST_RE.test(lines[j].text) && !BLOCKQUOTE_RE.test(lines[j].text)) {
    paraLines.push(lines[j].text)
    j++
  }
  // 段落以“空行或块标记”终止；但只有终止行后还存在换行才视为闭合，
  // 否则末尾半行（单换行后的空占位 / 半截围栏标记）后续仍可能变成段落续行。
  return { block: { type: 'paragraph', children: parseInline(paraLines.join('\n')) }, next: j, closed: j + 1 < n }
}

function parseBlocks(text: string): Block[] {
  const lines = toRawLines(text)
  const blocks: Block[] = []
  let i = 0
  const n = lines.length

  while (i < n) {
    const line = lines[i]

    // 空行
    if (!line.text.trim()) { i++; continue }

    const parsed = parseOneBlock(lines, i)
    blocks.push(parsed.block)
    i = parsed.next
  }

  return blocks
}

interface TrackedBlocks {
  blocks: Block[]
  offsets: number[]
}

/** 与 parseBlocks 同语义，额外记录每个块的起始字符偏移（用于稳定流式 key）。 */
function parseBlocksTracked(text: string): TrackedBlocks {
  const lines = toRawLines(text)
  const blocks: Block[] = []
  const offsets: number[] = []
  let i = 0
  const n = lines.length

  while (i < n) {
    if (!lines[i].text.trim()) { i++; continue }
    offsets.push(lineOffset(lines, i))
    const parsed = parseOneBlock(lines, i)
    blocks.push(parsed.block)
    i = parsed.next
  }

  return { blocks, offsets }
}

interface ScanClosedResult {
  blocks: Block[]
  nextLine: number
}

/** 从 startLine 起，只收取拿到确定终止边界的块；遇到第一个未闭合块即停。 */
function scanClosedBlocks(lines: RawLine[], startLine: number): ScanClosedResult {
  const blocks: Block[] = []
  let i = Math.max(0, Math.min(startLine, lines.length))

  while (i < lines.length) {
    const line = lines[i]
    if (!line.text.trim()) {
      // 末尾空行可能是尚未写完的下一行（split 产生的占位），不可吞进稳定前缀；
      // 只有空行后面还有行，才说明它确实是完整的分隔空行。
      if (i < lines.length - 1) { i++; continue }
      break
    }
    const parsed = parseOneBlock(lines, i)
    if (!parsed.closed) break
    blocks.push(parsed.block)
    i = parsed.next
  }

  return { blocks, nextLine: i }
}

/** 递归解析列表行（按缩进构建嵌套） */
// 注意：与 LIST_RE 保持一致，支持缩进前缀（^\s*），否则缩进列表会因正则不匹配
// 返回空数组导致 parseBlocks 死循环（i 不前进）
function parseListLines(rawLines: RawLine[]): ListItem[] {
  const items: ListItem[] = []
  let i = 0
  while (i < rawLines.length) {
    const line = rawLines[i]
    const m = line.text.match(/^\s*([-*+]|\d+\.)\s+(.*)$/)
    if (!m) { i++; continue }
    const ordered = /\d/.test(m[1])
    const content = m[2]
    const taskMatch = content.match(/^\[([ xX])\]\s+(.*)$/)
    const item: ListItem = {
      ordered,
      task: taskMatch
        ? { checked: taskMatch[1].toLowerCase() === 'x', label: parseInline(taskMatch[2]) }
        : null,
      children: taskMatch ? parseInline(taskMatch[2]) : parseInline(content),
      sub: [],
    }
    // 收集更深缩进的子行
    const subLines: RawLine[] = []
    let j = i + 1
    while (j < rawLines.length && rawLines[j].indent > line.indent) {
      if (rawLines[j].text.trim()) subLines.push(rawLines[j])
      j++
    }
    if (subLines.length) item.sub = parseListLines(subLines)
    items.push(item)
    i = j
  }
  return items
}

/* ─────────────────────────── 渲染 ─────────────────────────── */

function renderInline(tokens: InlineToken[], key: string): ReactNode[] {
  return tokens.map((t, idx) => {
    const k = `${key}-${idx}`
    switch (t.type) {
      case 'text': return <span key={k}>{t.text}</span>
      case 'code': return <code key={k} className="md-inline-code">{t.text}</code>
      case 'strong': return <strong key={k}>{renderInline(t.children, k)}</strong>
      case 'em': return <em key={k}>{renderInline(t.children, k)}</em>
      case 'del': return <del key={k}>{renderInline(t.children, k)}</del>
      case 'link':
        return (
          <a key={k} className="external-link" data-url={t.url} href={t.url}
            onClick={e => e.preventDefault()} title={t.url}>
            {t.text}
          </a>
        )
      case 'url':
        return (
          <a key={k} className="external-link" data-url={t.url} href={t.url}
            onClick={e => e.preventDefault()} title={t.url}>
            {t.url}
          </a>
        )
      case 'secret':
        return (
          <span key={k} className="secret-token" data-secret={t.key} title="点击展开原始值">
            <span className="secret-lock">🔒</span>
            <span className="secret-key" style={{ color: 'var(--color-accent-warning)' }}>{t.key}</span>
            <span className="secret-val" style={{ display: 'none' }} />
          </span>
        )
      default: return null
    }
  })
}

const COLLAPSE_THRESHOLD = 12

function CodeBlock({ lang, code }: { lang: string; code: string }) {
  const [copied, setCopied] = useState(false)
  const lineCount = code.split('\n').length
  const [collapsed, setCollapsed] = useState(lineCount > COLLAPSE_THRESHOLD)

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } catch { /* clipboard 不可用时静默 */ }
  }

  return (
    <div className="md-codeblock">
      <div className="md-codeblock-header">
        <span className="md-codeblock-lang">{lang || 'text'}</span>
        {lineCount > COLLAPSE_THRESHOLD && (
          <button className="md-codeblock-btn" onClick={() => setCollapsed(!collapsed)}>
            {collapsed ? '↕ 展开' : '↕ 收起'}
          </button>
        )}
        <button className="md-codeblock-btn" onClick={handleCopy}>
          {copied ? '✓ 已复制' : '⧉ 复制'}
        </button>
      </div>
      {collapsed ? (
        <div className="md-codeblock-collapsed">
          <pre><code>{code.split('\n').slice(0, 3).join('\n')}</code></pre>
          <div className="md-codeblock-more">… 共 {lineCount} 行，点击展开查看完整代码</div>
        </div>
      ) : (
        <pre className="md-codeblock-body"><code>{code}</code></pre>
      )}
    </div>
  )
}

function renderListItems(items: ListItem[], keyPrefix: string): ReactNode {
  return items.map((item, idx) => {
    const k = `${keyPrefix}-${idx}`
    return (
      <li key={k}>
        {item.task ? (
          <span className="md-task">
            <span className={`md-checkbox ${item.task.checked ? 'checked' : ''}`}>
              {item.task.checked ? '✓' : ''}
            </span>
            <span className="md-task-label">{renderInline(item.task.label, k + '-t')}</span>
          </span>
        ) : (
          <span>{renderInline(item.children, k + '-c')}</span>
        )}
        {item.sub.length > 0 && renderListContainer(item.sub, k + '-s')}
      </li>
    )
  })
}

function renderListContainer(items: ListItem[], key: string): ReactNode {
  const ordered = items.length > 0 && items[0].ordered
  return ordered
    ? <ol key={key} className="md-list">{renderListItems(items, key)}</ol>
    : <ul key={key} className="md-list">{renderListItems(items, key)}</ul>
}

function renderOneBlock(block: Block, k: string): ReactNode {
  switch (block.type) {
    case 'paragraph':
      return <p className="md-p">{renderInline(block.children, k)}</p>
    case 'heading': {
      const content = renderInline(block.children, k)
      switch (block.level) {
        case 1: return <h1 className="md-h1">{content}</h1>
        case 2: return <h2 className="md-h2">{content}</h2>
        case 3: return <h3 className="md-h3">{content}</h3>
        case 4: return <h4 className="md-h4">{content}</h4>
        case 5: return <h5 className="md-h5">{content}</h5>
        default: return <h6 className="md-h6">{content}</h6>
      }
    }
    case 'blockquote':
      return <blockquote className="md-blockquote">{renderBlocks(block.children, k)}</blockquote>
    case 'list':
      return renderListContainer(block.items, k)
    case 'table':
      return (
        <div className="md-table-wrap">
          <table className="md-table">
            <thead>
              <tr>{block.headers.map((h, i) => <th key={i}>{renderInline(h, `${k}-h${i}`)}</th>)}</tr>
            </thead>
            <tbody>
              {block.rows.map((row, ri) => (
                <tr key={ri}>{row.map((cell, ci) => <td key={ci}>{renderInline(cell, `${k}-r${ri}c${ci}`)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )
    case 'code':
      return <CodeBlock lang={block.lang} code={block.code} />
    case 'hr':
      return <hr className="md-hr" />
    default:
      return null
  }
}

const MemoBlock = memo(function MemoBlock({ block, k }: { block: Block; k: string }) {
  return <>{renderOneBlock(block, k)}</>
})

function renderBlocks(blocks: Block[], keyPrefix: string): ReactNode[] {
  return blocks.map((block, idx) => {
    const k = `${keyPrefix}-${idx}`
    return <MemoBlock key={k} block={block} k={k} />
  })
}

/* ─────────────────── 流式增量解析（层次 1） ─────────────────── */

interface IncrementalCache {
  /** 已闭合前缀的原文切片；下一次 content 必须以它开头，否则整体回退全量解析。 */
  stablePrefix: string
  closed: Block[]
  closedIds: number[]
  closedCharLen: number
  closedLineCount: number
  nextId: number
}

const EMPTY_CACHE: IncrementalCache = {
  stablePrefix: '',
  closed: [],
  closedIds: [],
  closedCharLen: 0,
  closedLineCount: 0,
  nextId: 0,
}

interface IncrementalResult {
  closed: Block[]
  closedIds: number[]
  tailBlocks: Block[]
  tailOffsets: number[]
  tailStart: number
  snapshot: IncrementalCache
}

function fallbackResult(content: string): IncrementalResult {
  const full = parseBlocksTracked(content)
  return {
    closed: [],
    closedIds: [],
    tailBlocks: full.blocks,
    tailOffsets: full.offsets,
    tailStart: 0,
    snapshot: EMPTY_CACHE,
  }
}

/**
 * 纯函数：只读 prev 缓存，返回本次渲染数据与下一份缓存快照。
 * 副作用（写 useRef）放到 useEffect，保证 StrictMode 双调用安全。
 */
function computeIncremental(content: string, streaming: boolean, prev: IncrementalCache): IncrementalResult {
  if (!streaming || !content) return fallbackResult(content)

  const lines = toRawLines(content)
  const prefixOk = prev.stablePrefix === ''
    ? true
    : prev.closedCharLen <= content.length
      && prev.closedLineCount <= lines.length
      && content.startsWith(prev.stablePrefix)

  // 内容被重置 / 缩短 / 切换对话 / 重新生成：整体回退全量解析并重置缓存
  if (!prefixOk) return fallbackResult(content)

  // 只扫描“上次已闭合前缀之后”的增量文本；毕业新闭合块
  const scan = scanClosedBlocks(lines, prev.closedLineCount)
  let closed = prev.closed
  let closedIds = prev.closedIds
  let nextId = prev.nextId
  let snapshot = prev

  if (scan.blocks.length > 0) {
    const newIds = scan.blocks.map((_, idx) => prev.nextId + idx)
    closed = prev.closed.concat(scan.blocks)
    closedIds = prev.closedIds.concat(newIds)
    nextId = prev.nextId + scan.blocks.length
    const closedCharLen = lineOffset(lines, scan.nextLine)
    snapshot = {
      stablePrefix: content.slice(0, closedCharLen),
      closed,
      closedIds,
      closedCharLen,
      closedLineCount: scan.nextLine,
      nextId,
    }
  }

  const tailStart = lineOffset(lines, scan.nextLine)
  const tailText = lines.slice(scan.nextLine).map(l => l.text).join('\n')
  const tail = parseBlocksTracked(tailText)

  return {
    closed,
    closedIds,
    tailBlocks: tail.blocks,
    tailOffsets: tail.offsets,
    tailStart,
    snapshot,
  }
}

interface MarkdownProps {
  content: string
  streaming?: boolean
}

export default function Markdown({ content, streaming = false }: MarkdownProps) {
  const cacheRef = useRef<IncrementalCache>(EMPTY_CACHE)

  // 渲染期只读 ref 做纯计算；useEffect 在提交后回写快照（StrictMode 安全）
  const result = useMemo(
    () => computeIncremental(content, streaming, cacheRef.current),
    [content, streaming],
  )

  useEffect(() => {
    cacheRef.current = result.snapshot
  }, [result])

  return (
    <div className="md">
      {result.closed.map((block, i) => (
        <MemoBlock key={`c${result.closedIds[i]}`} block={block} k={`c${result.closedIds[i]}`} />
      ))}
      {result.tailBlocks.map((block, i) => {
        const offset = result.tailStart + result.tailOffsets[i]
        return <MemoBlock key={`t${offset}`} block={block} k={`t${offset}`} />
      })}
      {streaming && <span className="md-stream-cursor" />}
    </div>
  )
}
