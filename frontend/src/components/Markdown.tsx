/* Zenith Markdown 渲染器 — 完整 GFM 支持（无外部依赖）
 *
 * 支持：标题 / 段落 / 引用(嵌套) / 有序·无序·嵌套列表 / 任务列表 / 表格 /
 *      分割线 / 行内代码 / 代码块(语言标签·复制·长代码折叠) / 加粗·斜体·删除线 /
 *      链接(外部打开) / 裸 URL / {{SEC_xxx}} 安全占位符
 *
 * 安全占位符与外部链接采用容器事件委托（见 ChatMessages 的 onClick），
 * 与旧版行为保持一致。
 */
import { useMemo, useState } from 'react'
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

const INLINE_RE = /(\*\*[^*]+\*\*|~~[^~]+~~|\*[^*]+\*|\[[^\]]+\]\((https?:\/\/[^\s)]+)\)|https?:\/\/[^\s<)\]]+|`[^`\n]+`|\{\{SEC_\d{3}\}\})/

function parseInline(text: string): InlineToken[] {
  if (!text) return []
  const tokens: InlineToken[] = []
  let rest = text
  while (rest) {
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
    } else if (token.startsWith('*') && token.endsWith('*')) {
      tokens.push({ type: 'em', children: parseInline(token.slice(1, -1)) })
    } else if (token.startsWith('[')) {
      tokens.push({ type: 'link', text: token.slice(1, token.indexOf(']')), url: m[2] || '' })
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

function parseBlocks(text: string): Block[] {
  const lines = toRawLines(text)
  const blocks: Block[] = []
  let i = 0
  const n = lines.length

  while (i < n) {
    const line = lines[i]

    // 空行
    if (!line.text.trim()) { i++; continue }

    // 代码围栏
    const fence = line.text.match(FENCE_RE)
    if (fence) {
      const lang = fence[1]
      const codeLines: string[] = []
      i++
      while (i < n && !FENCE_RE.test(lines[i].text)) {
        codeLines.push(lines[i].text)
        i++
      }
      if (i < n) i++ // 跳过结束围栏
      blocks.push({ type: 'code', lang, code: codeLines.join('\n') })
      continue
    }

    // 分割线
    if (HR_RE.test(line.text.trim())) {
      blocks.push({ type: 'hr' })
      i++
      continue
    }

    // 标题
    const heading = line.text.match(HEADING_RE)
    if (heading) {
      blocks.push({ type: 'heading', level: heading[1].length, children: parseInline(heading[2]) })
      i++
      continue
    }

    // 引用块 — 收集连续 > 行
    if (BLOCKQUOTE_RE.test(line.text)) {
      const quoteLines: string[] = []
      while (i < n && BLOCKQUOTE_RE.test(lines[i].text)) {
        quoteLines.push(lines[i].text.replace(BLOCKQUOTE_RE, '$1'))
        i++
      }
      const inner = parseBlocks(quoteLines.join('\n'))
      blocks.push({ type: 'blockquote', children: inner.length ? inner : [{ type: 'paragraph', children: [] }] })
      continue
    }

    // 表格 — 当前行是 | 行且下一行是分隔行
    if (TABLE_ROW_RE.test(line.text) && i + 1 < n && TABLE_SEP_RE.test(lines[i + 1].text)) {
      const header = line.text.trim().replace(/^\||\|$/g, '').split('|').map(c => parseInline(c.trim()))
      i += 2
      const rows: InlineToken[][][] = []
      while (i < n && TABLE_ROW_RE.test(lines[i].text) && !TABLE_SEP_RE.test(lines[i].text)) {
        const cells = lines[i].text.trim().replace(/^\||\|$/g, '').split('|').map(c => parseInline(c.trim()))
        rows.push(cells)
        i++
      }
      blocks.push({ type: 'table', headers: header, rows })
      continue
    }

    // 列表 — 收集连续的列表候选行（含缩进与空行，直到下一个非列表非空行）
    if (LIST_RE.test(line.text)) {
      const listLines: RawLine[] = []
      let j = i
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
            break
          }
        } else {
          break
        }
      }
      const items = parseListLines(listLines)
      if (items.length) {
        blocks.push({ type: 'list', items })
        i = j
        continue
      }
    }

    // 普通段落 — 合并连续非空行
    const paraLines: string[] = []
    while (i < n && lines[i].text.trim() && !FENCE_RE.test(lines[i].text) && !HEADING_RE.test(lines[i].text) && !LIST_RE.test(lines[i].text) && !BLOCKQUOTE_RE.test(lines[i].text)) {
      paraLines.push(lines[i].text)
      i++
    }
    blocks.push({ type: 'paragraph', children: parseInline(paraLines.join('\n')) })
  }

  return blocks
}

/** 递归解析列表行（按缩进构建嵌套） */
function parseListLines(rawLines: RawLine[]): ListItem[] {
  const items: ListItem[] = []
  let i = 0
  while (i < rawLines.length) {
    const line = rawLines[i]
    const m = line.text.match(/^([-*+]|\d+\.)\s+(.*)$/)
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

function renderBlocks(blocks: Block[], keyPrefix: string): ReactNode[] {
  return blocks.map((block, idx) => {
    const k = `${keyPrefix}-${idx}`
    switch (block.type) {
      case 'paragraph':
        return <p key={k} className="md-p">{renderInline(block.children, k)}</p>
      case 'heading': {
        const content = renderInline(block.children, k)
        switch (block.level) {
          case 1: return <h1 key={k} className="md-h1">{content}</h1>
          case 2: return <h2 key={k} className="md-h2">{content}</h2>
          case 3: return <h3 key={k} className="md-h3">{content}</h3>
          case 4: return <h4 key={k} className="md-h4">{content}</h4>
          case 5: return <h5 key={k} className="md-h5">{content}</h5>
          default: return <h6 key={k} className="md-h6">{content}</h6>
        }
      }
      case 'blockquote':
        return <blockquote key={k} className="md-blockquote">{renderBlocks(block.children, k)}</blockquote>
      case 'list':
        return renderListContainer(block.items, k)
      case 'table':
        return (
          <div key={k} className="md-table-wrap">
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
        return <CodeBlock key={k} lang={block.lang} code={block.code} />
      case 'hr':
        return <hr key={k} className="md-hr" />
      default:
        return null
    }
  })
}

interface MarkdownProps {
  content: string
  streaming?: boolean
}

export default function Markdown({ content, streaming = false }: MarkdownProps) {
  const blocks = useMemo(() => parseBlocks(content), [content])
  return (
    <div className="md">
      {renderBlocks(blocks, 'md')}
      {streaming && <span className="md-stream-cursor" />}
    </div>
  )
}
