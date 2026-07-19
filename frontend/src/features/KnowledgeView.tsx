import { useState, useEffect, useCallback } from 'react'
import { api } from '../shared/api'

/* ══════════════════════════════════════════
   KnowledgeView — 本地知识库问答 + 异步任务
   调用 /api/knowledge/* 薄代理，后端转发到 api_gateway
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

  const checkHealth = useCallback(async () => {
    try {
      const r = await api.knowledgeHealth()
      setHealth(r.status === 'ok' ? '在线' : '异常')
    } catch {
      setHealth('离线')
    }
  }, [])

  useEffect(() => { checkHealth() }, [checkHealth])

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    setUploading(true)
    setUploadResult(null)
    try {
      const r = await api.knowledgeIngest(f)
      if (r.status === 'ok') {
        setUploadResult(`✅ 入库成功：${r.chunks} 个片段，覆盖率 ${(r.coverage * 100).toFixed(0)}%`)
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
        ? await api.knowledgeSearch(question)
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
        {/* PDF 上传入库 */}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <label className="btn btn-sm" style={{ cursor: 'pointer' }}>
            📄 上传 PDF 入库
            <input type="file" accept="application/pdf" onChange={handleUpload} style={{ display: 'none' }} disabled={uploading} />
          </label>
          {uploading && <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>入库中…</span>}
          {uploadResult && <span style={{ fontSize: 12 }}>{uploadResult}</span>}
        </div>

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
            placeholder={mode === 'rag' ? '问知识库里的文献…' : '问 Wiki 专题…'}
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
            提示：RAG 模式从本地向量库检索文献片段；Wiki 模式查询已编译的专题页面。
            <br />
            如果状态显示“离线”，请先启动 <code>api_gateway.py</code>。
          </div>
        )}
      </div>
    </div>
  )
}
