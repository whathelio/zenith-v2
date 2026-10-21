import { useState, useEffect, useCallback } from 'react'
import { api } from '../shared/api'
import type { KnowledgeCollection } from '../shared/api'

/* ══════════════════════════════════════════
   KnowledgeView — 本地知识库问答 + 异步任务
   调用 /api/knowledge/* 薄代理，后端转发到 api_gateway
   多集合：顶部集合选择器，搜索/文档列表/逐段学习全部联动当前集合。
   ══════════════════════════════════════════ */
export default function KnowledgeView() {
  const [question, setQuestion] = useState('')
  const [mode, setMode] = useState<'rag' | 'wiki'>('rag')
  const [answer, setAnswer] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [health, setHealth] = useState<string>('未知')
  const [uploading, setUploading] = useState(false)
  const [uploadResult, setUploadResult] = useState<string | null>(null)
  const [docs, setDocs] = useState<Array<{ item_id: number; title: string; chunks: number; source?: string }>>([])

  // ── 集合选择（多集合改造） ──
  const [collections, setCollections] = useState<KnowledgeCollection[]>([])
  const [defaultCollection, setDefaultCollection] = useState<string>('')
  const [collection, setCollection] = useState<string>('')
  const [archiving, setArchiving] = useState(false)
  const [archiveMsg, setArchiveMsg] = useState<string | null>(null)

  const loadCollections = useCallback(async () => {
    try {
      const r = await api.listCollections()
      const list = r.collections || []
      setCollections(list)
      setDefaultCollection(r.default || '')
      // 首次加载（或当前选择已失效）时回落到默认集合
      setCollection(prev => {
        if (prev && list.some(c => c.collection === prev)) return prev
        return r.default || list[0]?.collection || ''
      })
    } catch { /* 中台未启动时不报错 */ }
  }, [])

  const loadDocs = useCallback(async () => {
    try {
      const r = await api.knowledgeListDocs(collection || undefined)
      if (r.docs) setDocs(r.docs)
    } catch { /* 中台未启动时不报错 */ }
  }, [collection])

  const checkHealth = useCallback(async () => {
    try {
      const r = await api.knowledgeHealth()
      setHealth(r.status === 'ok' ? '在线' : '异常')
    } catch {
      setHealth('离线')
    }
  }, [])

  useEffect(() => { checkHealth() }, [checkHealth])
  useEffect(() => { loadCollections() }, [loadCollections])
  // 集合切换时重新拉取文档列表（联动）
  useEffect(() => { loadDocs() }, [loadDocs])

  const currentStats = collections.find(c => c.collection === collection)
  const isDefault = !!collection && collection === defaultCollection

  const handleArchive = async () => {
    if (!collection) return
    setArchiveMsg(null)
    if (!window.confirm(`确定归档集合「${collection}」吗？归档后该集合将被改名保留、不再出现在可选列表中。`)) return
    setArchiving(true)
    try {
      await api.archiveCollection(collection)
      setArchiveMsg(`✅ 已归档集合「${collection}」`)
      const fallback = defaultCollection
      await loadCollections()
      setCollection(fallback)
      await loadDocs()
    } catch (e: any) {
      // 默认集合会返回 PROTECTED，此处把后端文案原样展示
      setArchiveMsg(`❌ 归档失败：${e?.message || '未知错误'}`)
    } finally {
      setArchiving(false)
    }
  }

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    setUploading(true)
    setUploadResult(null)
    try {
      const r = await api.knowledgeIngest(f, collection || undefined)
      if (r.status === 'ok') {
        setUploadResult(`✅ 入库成功：${r.chunks} 个片段，覆盖率 ${(r.coverage * 100).toFixed(0)}%`)
        loadDocs()
        loadCollections()
      } else if (r.status === 'rejected') {
        setUploadResult(`⚠ 审查未通过：${r.reason || '未知'}（覆盖率 ${((r.coverage || 0) * 100).toFixed(0)}%）`)
      } else {
        setUploadResult(`❌ 失败：${r.error || JSON.stringify(r)}`)
      }
    } catch (err: any) {
      setUploadResult(`❌ 上传失败：${err.message}`)
    } finally {
      setUploading(false)
      e.target.value = ''
    }
  }

  const ask = async () => {
    if (!question.trim()) return
    setLoading(true)
    setError(null)
    setAnswer('')
    try {
      const r = mode === 'rag'
        ? await api.knowledgeSearch(question, 5, collection || undefined)
        : await api.knowledgeWiki(question)
      setAnswer(r.answer || r.error || '(空)')
    } catch (e: any) {
      setError(e.message || '请求失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="main-area" style={{ maxWidth: '100vw' }}>
      <div className="topbar">
        <span className="topbar-title">📚 知识库</span>
        <div className="topbar-actions">
          <span style={{ fontSize: 12, color: health === '在线' ? 'var(--color-success)' : 'var(--color-danger)' }}>
            ● {health}
          </span>
          <button className="btn btn-sm" onClick={checkHealth}>刷新</button>
          <a href="/" className="btn btn-sm">🏠 主页</a>
        </div>
      </div>

      <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
        {/* 集合选择器（多集合） */}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>集合：</span>
          <select
            className="cal-form-input"
            style={{ width: 'auto', minWidth: 220 }}
            value={collection}
            onChange={e => { setCollection(e.target.value); setArchiveMsg(null) }}
            disabled={collections.length === 0}
          >
            {collections.length === 0 && <option value="">（无可用集合）</option>}
            {collections.map(c => (
              <option key={c.collection} value={c.collection}>
                {c.collection === defaultCollection ? '★ ' : ''}{c.collection}（{c.chunks} 段 / {c.docs} 篇）
              </option>
            ))}
          </select>

          {currentStats && (
            <span className="badge badge-info" title="当前集合统计">
              {currentStats.chunks} 片段 · {currentStats.docs} 文档
            </span>
          )}
          {isDefault && <span className="badge badge-muted">默认集合</span>}
          {collection && !isDefault && <span className="badge badge-warning">非默认集合</span>}

          {collection && !isDefault && (
            <button
              className="btn btn-sm btn-danger"
              onClick={handleArchive}
              disabled={archiving}
            >
              {archiving ? '归档中…' : '🗄 归档集合'}
            </button>
          )}
          {archiveMsg && (
            <span style={{ fontSize: 12, color: archiveMsg.startsWith('✅') ? 'var(--color-success)' : 'var(--color-danger)' }}>
              {archiveMsg}
            </span>
          )}
        </div>

        {/* 文件上传入库 */}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <label className="btn btn-sm" style={{ cursor: 'pointer' }}>
            📄 上传 PDF / 文本 入库{collection ? `（→ ${collection}）` : ''}
            <input type="file" accept="application/pdf,.md,.txt" onChange={handleUpload} style={{ display: 'none' }} disabled={uploading} />
          </label>
          {uploading && <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>入库中…</span>}
          {uploadResult && <span style={{ fontSize: 12 }}>{uploadResult}</span>}
        </div>

        {/* 已入库文档列表 — 逐段学习入口（当前集合） */}
        {docs.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
              集合「{collection}」已入库文档（{docs.length}）：
            </div>
            {docs.map(d => (
              <div key={d.item_id} style={{
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '8px 12px', background: 'var(--color-bg-panel)',
                border: '1px solid var(--color-border)', borderRadius: 6,
              }}>
                <span style={{ flex: 1, fontSize: 12, color: 'var(--color-text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  📄 {d.title} <span style={{ color: 'var(--color-text-muted)', fontSize: 10 }}>（{d.chunks} 段）</span>
                </span>
                <button
                  className="btn btn-sm"
                  onClick={async () => {
                    try {
                      const r = await api.startLearning('document', d.item_id, collection || undefined)
                      window.location.href = `/chat/${r.conversation_id}`
                    } catch (e: any) { setError(e?.message || '创建学习对话失败') }
                  }}
                  style={{ background: 'var(--color-accent-primary)', color: '#fff', cursor: 'pointer' }}
                >
                  📖 逐段学习
                </button>
              </div>
            ))}
          </div>
        )}

        {/* 模式切换 */}
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className="btn btn-sm"
            onClick={() => setMode('rag')}
            style={{ background: mode === 'rag' ? 'var(--color-accent-primary)' : 'var(--color-bg-input)', color: mode === 'rag' ? '#fff' : 'var(--color-text-muted)' }}
          >
            RAG 检索
          </button>
          <button
            className="btn btn-sm"
            onClick={() => setMode('wiki')}
            style={{ background: mode === 'wiki' ? 'var(--color-accent-primary)' : 'var(--color-bg-input)', color: mode === 'wiki' ? '#fff' : 'var(--color-text-muted)' }}
          >
            LLM Wiki
          </button>
        </div>

        {/* 输入框 */}
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            className="cal-form-input"
            placeholder={mode === 'rag' ? `问「${collection || '知识库'}」里的文献…` : '问 Wiki 专题…'}
            value={question}
            onChange={e => setQuestion(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') ask() }}
            style={{ flex: 1 }}
            autoFocus
          />
          <button className="btn btn-accent" onClick={ask} disabled={loading}>
            {loading ? '思考中…' : '提问'}
          </button>
        </div>

        {/* 错误 */}
        {error && (
          <div style={{ color: 'var(--color-danger)', fontSize: 13 }}>⚠ {error}</div>
        )}

        {/* 回答 */}
        {answer && (
          <div style={{
            background: 'var(--color-bg-panel)',
            border: '1px solid var(--color-border)',
            borderRadius: 10,
            padding: 16,
            whiteSpace: 'pre-wrap',
            lineHeight: 1.7,
          }}>
            {answer}
          </div>
        )}

        {/* 提示 */}
        {!answer && !loading && (
          <div style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>
            提示：RAG 模式从本地向量库检索文献片段（当前集合：{collection || '—'}）；Wiki 模式查询已编译的专题页面。
            <br />
            如果状态显示“离线”，请先启动 <code>api_gateway.py</code>。
          </div>
        )}
      </div>
    </div>
  )
}
