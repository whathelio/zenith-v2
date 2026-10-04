import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../shared/api'

/* ══════════════════════════════════════════
   ProcessView — 本机进程 / 端口地图
   数据源：GET /api/processes/snapshot（后端 15s TTL 缓存，命令行默认脱敏）

   注意：采集在后端是阻塞调用（约 1.75s），已走 asyncio.to_thread；
   本页因此默认「按需 + 手动刷新」，不主动轮询。
   ══════════════════════════════════════════ */

interface Proc {
  pid: number
  ppid: number
  name: string
  mem_mb: number
  created: string
  cmd: string
  group: string
  detail: string
}

interface PortRow {
  proto: string
  addr: string
  port: number
  pid: number
  proc: string
  group: string
  purpose: string
}

interface DshProfile {
  profile: string
  path: string
  mtime: string
  mcp: { name: string; script: string }[]
}

interface Snapshot {
  generated_at: string
  redacted: boolean
  elapsed_ms: number
  cached: boolean
  age_s: number
  summary: {
    proc_total: number
    port_total: number
    mem_total_mb: number
    groups: Record<string, { count: number; mem_mb: number }>
  }
  ports: PortRow[]
  groups: Record<string, Proc[]>
  dsh: {
    web: Proc | null
    sandbox: Proc[]
    profiles: { profiles: DshProfile[]; skill_dirs: string[] }
    recent: { name: string; kind: string; mtime: string }[]
    sessions: number
  }
  zenith: { running: boolean; listening: number[]; db_mb: number | null }
  workbuddy: {
    main_procs: number
    mcp_counts: Record<string, number>
    mcp_total: number
    builtin_counts: Record<string, number>
    builtin_total: number
    proc_total: number
    mem_total_mb: number
  }
}

const SYS_GROUP = '系统'
const OTHER_GROUP = '其他应用'
const TAIL_ORDER = [OTHER_GROUP, SYS_GROUP]

function groupColor(g: string): string {
  const map: Record<string, string> = {
    WorkBuddy: 'var(--color-accent-info)',
    dsh: 'var(--color-accent-primary)',
    Zenith: 'var(--color-accent-success)',
    [SYS_GROUP]: 'var(--color-text-muted)',
    [OTHER_GROUP]: 'var(--color-accent-warning)',
  }
  return map[g] || 'var(--color-accent-warning)'
}

function orderGroups(groups: Record<string, unknown>): string[] {
  const names = Object.keys(groups)
  const proj = names.filter(n => !TAIL_ORDER.includes(n))
  const tail = TAIL_ORDER.filter(n => names.includes(n))
  return [...proj, ...tail]
}

const fmt = (n: number) => n.toLocaleString('zh-CN')

export default function ProcessView() {
  const [data, setData] = useState<Snapshot | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [group, setGroup] = useState<string>('__all')
  const [auto, setAuto] = useState(false)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  const load = useCallback(async (force = false) => {
    setLoading(true)
    setError(null)
    try {
      // 走共享 api 客户端（其它视图同源风格）：由 request() 统一处理
      // Content-Type、非 2xx 抛错（并把 status 挂在 Error 上）。
      const j = await api.getProcessSnapshot<Snapshot & { error?: string }>(force)
      if (j.error) throw new Error(j.error)
      setData(j)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (timer.current) { clearInterval(timer.current); timer.current = null }
    if (auto) timer.current = setInterval(() => { load() }, 15000)
    return () => { if (timer.current) clearInterval(timer.current) }
  }, [auto, load])

  if (!data) {
    return (
      <div style={{ padding: 24, color: 'var(--color-text-muted)', fontSize: 13 }}>
        {error ? `采集失败：${error}` : '正在采集本机进程与端口…'}
      </div>
    )
  }

  const groups = orderGroups(data.summary.groups)
  const chips = ['__all', ...groups]
  const filter = q.trim().toLowerCase()

  const visibleGroups = groups.filter(g => group === '__all' || g === group)
  const groupPorts: Record<string, number> = {}
  data.ports.forEach(p => { groupPorts[p.group] = (groupPorts[p.group] || 0) + 1 })

  return (
    <div style={{ padding: '4px 16px 40px', overflowY: 'auto', height: '100%' }}>
      {/* 头部 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
        <span style={{ fontSize: 17, fontWeight: 600 }}>🖥 进程 / 端口地图</span>
        <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
          {data.generated_at} · 后端采集 {data.elapsed_ms} ms ·
          {data.cached ? ` 命中缓存（${data.age_s}s 前）` : ' 实时采集'}
          {data.redacted ? ' · 命令行已脱敏' : ' · ⚠ 未脱敏'}
        </span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
          <button className="btn btn-sm" onClick={() => setAuto(v => !v)}
            title="每 15 秒刷新（命中后端缓存时开销极小）"
            style={auto ? { background: 'var(--color-accent-primary)', color: '#fff' } : undefined}>
            {auto ? '自动刷新 开' : '自动刷新 关'}
          </button>
          <button className="btn btn-sm" onClick={() => load(true)} disabled={loading}>
            {loading ? '采集中…' : '立即刷新'}
          </button>
        </div>
      </div>

      {error && (
        <div style={{ marginBottom: 12, fontSize: 12, color: 'var(--color-accent-danger)' }}>
          采集失败：{error}
        </div>
      )}

      {/* 概览卡 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(132px, 1fr))', gap: 10, marginBottom: 16 }}>
        {[
          ['进程总数', fmt(data.summary.proc_total), '个'],
          ['监听端口', fmt(data.summary.port_total), '个'],
          ['内存合计', fmt(data.summary.mem_total_mb), 'MB（工作集）'],
          ['项目/分组', String(groups.length), '组'],
          ['WorkBuddy', fmt(data.workbuddy.proc_total), `${fmt(data.workbuddy.mem_total_mb)} MB`],
          ['dsh', String((data.groups.dsh || []).length), '个进程'],
        ].map(([k, v, n]) => (
          <div key={k} style={{
            background: 'var(--color-bg-panel)', border: '1px solid var(--color-border)',
            borderRadius: 10, padding: '10px 13px',
          }}>
            <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{k}</div>
            <div style={{ fontSize: 19, fontWeight: 600, marginTop: 2 }}>{v}</div>
            <div style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>{n}</div>
          </div>
        ))}
      </div>

      {/* 项目总览 */}
      <Section title="项目总览" tag={`${groups.length} 组`}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ color: 'var(--color-text-muted)' }}>
              <Th>项目 / 分组</Th><Th right>进程数</Th><Th right>内存 MB</Th>
              <Th right>占用端口</Th><Th>状态</Th>
            </tr>
          </thead>
          <tbody>
            {groups.map(g => {
              const s = data.summary.groups[g]
              return (
                <tr key={g} onClick={() => setGroup(g)} style={{ cursor: 'pointer' }}>
                  <Td>
                    <Dot color={groupColor(g)} />{g}
                  </Td>
                  <Td right>{s.count}</Td>
                  <Td right>{fmt(s.mem_mb)}</Td>
                  <Td right>{groupPorts[g] || '—'}</Td>
                  <Td>
                    <span style={{ color: s.count > 0 ? 'var(--color-accent-success)' : 'var(--color-text-muted)' }}>
                      {s.count > 0 ? '运行中' : '未运行'}
                    </span>
                  </Td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </Section>

      {/* 监听端口 */}
      <Section title="监听端口" tag={`${data.ports.length} 个`}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ color: 'var(--color-text-muted)' }}>
              <Th right>端口</Th><Th>协议</Th><Th>绑定地址</Th><Th>进程</Th><Th>归属</Th><Th>用途</Th>
            </tr>
          </thead>
          <tbody>
            {data.ports.map((p, i) => (
              <tr key={`${p.port}-${p.proto}-${i}`}>
                <Td right><b>{p.port}</b></Td>
                <Td>{p.proto}</Td>
                <Td><Mono>{p.addr}</Mono></Td>
                <Td>{p.proc} <span style={{ color: 'var(--color-text-muted)' }}>#{p.pid}</span></Td>
                <Td><Dot color={groupColor(p.group)} />{p.group}</Td>
                <Td>{p.purpose}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      {/* dsh 专项 */}
      <Section title="dsh 在做什么"
        tag={data.dsh.web ? `运行中 · PID ${data.dsh.web.pid}` : '未运行'}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <tbody>
            <Row label="Web UI">
              {data.dsh.web ? (
                <>
                  <span style={{ color: 'var(--color-accent-success)' }}>运行中</span>
                  {' · '}<Mono>http://127.0.0.1:3080</Mono>
                  {` · PID ${data.dsh.web.pid} · ${data.dsh.web.mem_mb} MB · 启动 ${data.dsh.web.created}`}
                </>
              ) : <span style={{ color: 'var(--color-text-muted)' }}>未运行</span>}
            </Row>
            <Row label="沙箱执行器">
              {data.dsh.sandbox.length
                ? data.dsh.sandbox.map(s => <div key={s.pid}><Mono>{s.cmd.slice(0, 240)}</Mono></div>)
                : <span style={{ color: 'var(--color-text-muted)' }}>当前无</span>}
            </Row>
            <Row label="会话数">{data.dsh.sessions} 个（~/.dsh/sessions）</Row>
            <Row label="Skill 源目录">
              {data.dsh.profiles.skill_dirs.length
                ? data.dsh.profiles.skill_dirs.map(d => <div key={d}><Mono>{d}</Mono></div>)
                : <span style={{ color: 'var(--color-text-muted)' }}>未读到</span>}
            </Row>
            {data.dsh.profiles.profiles.map(pf => (
              <Row key={pf.profile} label={`profile ${pf.profile}`}>
                <div style={{ color: 'var(--color-text-muted)', fontSize: 11 }}>改于 {pf.mtime}</div>
                {pf.mcp.length
                  ? pf.mcp.map(m => (
                    <div key={m.name}>· <Mono>{m.name}</Mono> → <Mono>{m.script || '(见 profile)'}</Mono></div>
                  ))
                  : <span style={{ color: 'var(--color-text-muted)' }}>无注入</span>}
                <div style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>{pf.path}</div>
              </Row>
            ))}
            <Row label="工作区最近改动">
              {data.dsh.recent.length
                ? data.dsh.recent.map(r => (
                  <div key={r.name}>{r.name} <span style={{ color: 'var(--color-text-muted)' }}>{r.mtime}</span></div>
                ))
                : <span style={{ color: 'var(--color-text-muted)' }}>无</span>}
            </Row>
          </tbody>
        </table>
      </Section>

      {/* 子系统状态 */}
      <Section title="子系统状态">
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <tbody>
            <Row label="Zenith">
              {data.zenith.running
                ? <><span style={{ color: 'var(--color-accent-success)' }}>进程在跑</span>
                  {` · 监听 ${data.zenith.listening.length ? data.zenith.listening.join(' / ') : '8766/8788 未监听'}`}
                  {` · 库 ${data.zenith.db_mb == null ? '未找到' : data.zenith.db_mb + ' MB'}`}</>
                : <><span style={{ color: 'var(--color-text-muted)' }}>未运行</span>
                  {` · 库 ${data.zenith.db_mb == null ? '未找到' : data.zenith.db_mb + ' MB'}`}</>}
            </Row>
            <Row label="WorkBuddy">
              {`进程 ${data.workbuddy.proc_total} 个 / ${fmt(data.workbuddy.mem_total_mb)} MB（主程序 ${data.workbuddy.main_procs} · 自建 MCP ${data.workbuddy.mcp_total} · 内置 MCP ${data.workbuddy.builtin_total}）`}
              {data.workbuddy.mcp_total > 0 && (
                <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                  自建：{Object.entries(data.workbuddy.mcp_counts).map(([k, v]) => `${k} × ${v}`).join(' · ')}
                </div>
              )}
              {data.workbuddy.builtin_total > 0 && (
                <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                  内置：{Object.entries(data.workbuddy.builtin_counts).map(([k, v]) => `${k} × ${v}`).join(' · ')}
                </div>
              )}
            </Row>
          </tbody>
        </table>
      </Section>

      {/* 进程明细 */}
      <Section title="进程明细" tag={`${data.summary.proc_total} 个`}>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
          <input
            className="form-input"
            placeholder="过滤：进程名 / 命令行关键字（如 mcp、python、dsh）"
            value={q}
            onChange={e => setQ(e.target.value)}
            style={{ flex: 1, minWidth: 200, fontSize: 12 }}
          />
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
          {chips.map(c => (
            <button
              key={c}
              className="btn btn-sm"
              onClick={() => setGroup(c)}
              style={{
                fontSize: 11,
                background: group === c ? 'var(--color-accent-primary)' : 'var(--color-bg-input)',
                color: group === c ? '#fff' : 'var(--color-text-secondary)',
                border: `1px solid ${group === c ? 'var(--color-accent-primary)' : 'var(--color-border)'}`,
              }}
            >
              {c === '__all' ? '全部' : c}
            </button>
          ))}
        </div>

        {visibleGroups.map(g => {
          const list = (data.groups[g] || []).filter(p =>
            !filter || p.name.toLowerCase().includes(filter) ||
            p.cmd.toLowerCase().includes(filter) ||
            p.detail.toLowerCase().includes(filter))
          if (!list.length) return null
          const mem = list.reduce((a, b) => a + b.mem_mb, 0)
          return (
            <details key={g} open={list.length <= 40} style={{ marginBottom: 10 }}>
              <summary style={{ cursor: 'pointer', fontSize: 13, fontWeight: 600, marginBottom: 6 }}>
                <Dot color={groupColor(g)} />{g}
                <span style={{
                  fontWeight: 400, fontSize: 11, color: 'var(--color-text-muted)', marginLeft: 8,
                }}>
                  {list.length} 个 · {fmt(mem)} MB
                </span>
              </summary>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                  <tr style={{ color: 'var(--color-text-muted)' }}>
                    <Th right>PID</Th><Th right>父PID</Th><Th right>内存 MB</Th>
                    <Th>启动时间</Th><Th>进程</Th><Th>说明</Th><Th>命令行</Th>
                  </tr>
                </thead>
                <tbody>
                  {list.map(p => (
                    <tr key={p.pid}>
                      <Td right>{p.pid}</Td>
                      <Td right><span style={{ color: 'var(--color-text-muted)' }}>{p.ppid}</span></Td>
                      <Td right>{p.mem_mb}</Td>
                      <Td><span style={{ color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>{p.created}</span></Td>
                      <Td>{p.name}</Td>
                      <Td><span style={{ whiteSpace: 'nowrap' }}>{p.detail}</span></Td>
                      <Td><Mono title={p.cmd}>{p.cmd.length > 150 ? p.cmd.slice(0, 150) + '…' : p.cmd}</Mono></Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )
        })}
      </Section>
    </div>
  )
}

/* ── 小组件 ── */

function Section({ title, tag, children }: {
  title: string; tag?: string; children: React.ReactNode
}) {
  return (
    <div style={{
      background: 'var(--color-bg-panel)', border: '1px solid var(--color-border)',
      borderRadius: 12, padding: '13px 15px', marginBottom: 14,
    }}>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
        {title}
        {tag && (
          <span style={{
            fontSize: 10, fontWeight: 400, color: 'var(--color-text-muted)',
            border: '1px solid var(--color-border)', borderRadius: 999, padding: '1px 8px',
          }}>{tag}</span>
        )}
      </div>
      {children}
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <tr>
      <th style={{
        width: 150, textAlign: 'left', verticalAlign: 'top', fontWeight: 500,
        color: 'var(--color-text-secondary)', padding: '5px 8px',
        borderBottom: '1px solid var(--color-border)',
      }}>{label}</th>
      <td style={{ padding: '5px 8px', borderBottom: '1px solid var(--color-border)', verticalAlign: 'top' }}>
        {children}
      </td>
    </tr>
  )
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th style={{
      textAlign: right ? 'right' : 'left', fontWeight: 400, padding: '5px 8px',
      borderBottom: '1px solid var(--color-border)', whiteSpace: 'nowrap',
    }}>{children}</th>
  )
}

function Td({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <td style={{
      textAlign: right ? 'right' : 'left', padding: '4px 8px',
      borderBottom: '1px solid var(--color-border)', verticalAlign: 'top',
      fontVariantNumeric: right ? 'tabular-nums' : undefined,
    }}>{children}</td>
  )
}

function Dot({ color }: { color: string }) {
  return (
    <span style={{
      display: 'inline-block', width: 7, height: 7, borderRadius: 2,
      background: color, marginRight: 6,
    }} />
  )
}

function Mono({ children, title }: { children: React.ReactNode; title?: string }) {
  return (
    <code title={title} style={{
      fontFamily: 'ui-monospace, Consolas, monospace', fontSize: 11,
      color: 'var(--color-text-secondary)', wordBreak: 'break-all',
    }}>{children}</code>
  )
}
