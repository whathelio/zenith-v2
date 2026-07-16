import { useState, useEffect, useRef } from 'react'
import { api, type AnalysisDocument } from '../shared/api'

export default function AnalysisView() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [analysisText, setAnalysisText] = useState('')
  const [schedules, setSchedules] = useState<any[]>([])
  const [docId, setDocId] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [toolErrors, setToolErrors] = useState<string[]>([])
  const [documents, setDocuments] = useState<AnalysisDocument[]>([])
  const [dragOver, setDragOver] = useState(false)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const analysisEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => { loadDocuments() }, [])

  useEffect(() => {
    analysisEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [analysisText, schedules])

  const loadDocuments = async () => {
    try {
      const docs = await api.listAnalysisDocuments()
      setDocuments(docs)
    } catch { /* silent */ }
  }

  const handleFileSelect = (file: File) => {
    if (!file.name.toLowerCase().endsWith('.txt')) {
      setError('仅支持 .txt 文件')
      return
    }
    if (file.size > 10 * 1024 * 1024) {
      setError('文件大小超过 10MB 限制')
      return
    }
    setSelectedFile(file)
    setError('')
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFileSelect(file)
  }

  const handleAnalyze = async () => {
    if (!selectedFile || isAnalyzing) return

    setIsAnalyzing(true)
    setAnalysisText('')
    setSchedules([])
    setDocId(null)
    setError('')
    setToolErrors([])

    try {
      const res = await api.analyzeFile(selectedFile)
      const reader = res.body?.getReader()
      if (!reader) throw new Error('No response body')

      const decoder = new TextDecoder()
      let buffer = ''
      let textAccum = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const dataStr = line.slice(6)
          if (!dataStr) continue
          try {
            const data = JSON.parse(dataStr)

            if (data.type === 'text') {
              textAccum += data.content
              setAnalysisText(textAccum)
            } else if (data.type === 'schedule_created') {
              setSchedules(prev => [...prev, data.data])
            } else if (data.type === 'tool_result') {
              // 其他工具结果（如 list_schedule），可忽略或显示
            } else if (data.type === 'tool_error') {
              setToolErrors(prev => [...prev, data.error])
            } else if (data.type === 'analysis_complete') {
              setDocId(data.doc_id)
            } else if (data.type === 'error') {
              setError(data.message)
            } else if (data.type === 'done') {
              // 分析完成
            }
          } catch { /* skip */ }
        }
      }
    } catch (e: any) {
      setError(e.message)
    } finally {
      setIsAnalyzing(false)
      await loadDocuments()
    }
  }

  const handleDownload = (id: number, filename: string) => {
    api.downloadAnalysisDocument(id, filename)
  }

  const handleDelete = async (id: number) => {
    if (!confirm('删除此分析文档？')) return
    try {
      await api.deleteAnalysisDocument(id)
      setDocuments(prev => prev.filter(d => d.id !== id))
    } catch { /* silent */ }
  }

  const handleReset = () => {
    setSelectedFile(null)
    setAnalysisText('')
    setSchedules([])
    setDocId(null)
    setError('')
    setToolErrors([])
  }

  const priorityLabel = (p: string): string => {
    const m: Record<string, string> = { high: '高', normal: '中', low: '低' }
    return m[p] || '中'
  }

  const formatContent = (content: string): string => {
    if (!content) return ''
    // 1. 提取代码块
    const codeBlocks: string[] = []
    let html = content.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
      const escaped = code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      codeBlocks.push(`<pre><code>${escaped}</code></pre>`)
      return `\u0000CB${codeBlocks.length - 1}\u0000`
    })
    // 2. 提取行内代码
    const inlineCodes: string[] = []
    html = html.replace(/`([^`]+)`/g, (_, code) => {
      const escaped = code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      inlineCodes.push(`<code>${escaped}</code>`)
      return `\u0000IC${inlineCodes.length - 1}\u0000`
    })
    // 3. HTML 转义
    html = html.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    // 4. Markdown 链接
    html = html.replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a class="external-link" data-url="$2">$1</a>'
    )
    // 5. 自动识别裸 URL
    html = html.replace(
      /(^|[\s(])(https?:\/\/[^\s<)\]]+)/g,
      '$1<a class="external-link" data-url="$2">$2</a>'
    )
    // 6. 其他格式
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    html = html.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>')
    html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>')
    html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>')
    html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>')
    html = html.replace(/\n\n/g, '</p><p>')
    html = html.replace(/\n/g, '<br/>')
    html = '<p>' + html + '</p>'
    html = html.replace(/<p><\/p>/g, '')
    // 7. 恢复
    html = html.replace(/\u0000IC(\d+)\u0000/g, (_, i) => inlineCodes[parseInt(i)])
    html = html.replace(/\u0000CB(\d+)\u0000/g, (_, i) => codeBlocks[parseInt(i)])
    return html
  }

  const handleLinkClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const link = (e.target as HTMLElement).closest('.external-link') as HTMLElement
    if (link) {
      e.preventDefault()
      const url = link.dataset.url
      if (url) {
        api.openUrl(url).catch(() => {
          window.open(url, '_blank', 'noopener,noreferrer')
        })
      }
    }
  }

  return (
    <div className="app-shell">
      <div className="main-content">
        <div className="topbar">
          <span className="topbar-title">📋 文件分析与规划</span>
          <div className="topbar-actions">
            <a href="/" className="btn btn-sm">🏠 主页</a>
            <a href="/chat" className="btn btn-sm">💬 对话</a>
            <a href="/schedules" className="btn btn-sm">📅 日程</a>
            <a href="/notes" className="btn btn-sm">📝 笔记</a>
            <a href="/memories" className="btn btn-sm">🧠 记忆</a>
            <a href="/settings" className="btn btn-sm">⚙ 设置</a>
          </div>
        </div>

        <div className="content" style={{ padding: '32px', maxWidth: 900, margin: '0 auto', overflowY: 'auto' }}>
          {error && <div className="analysis-error">⚠ {error}</div>}

          {/* 文件上传区 */}
          {!isAnalyzing && !analysisText && (
            <div className="analysis-upload-section">
              <div
                className={`file-upload-zone ${dragOver ? 'dragover' : ''}`}
                onDragOver={e => { e.preventDefault(); setDragOver(true) }}
                onDragLeave={() => setDragOver(false)}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
              >
                <div className="file-upload-icon">📄</div>
                <p>{selectedFile ? selectedFile.name : '拖拽 .txt 文件到此处，或点击选择'}</p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".txt"
                  style={{ display: 'none' }}
                  onChange={e => e.target.files?.[0] && handleFileSelect(e.target.files[0])}
                />
              </div>
              {selectedFile && (
                <button className="btn btn-primary" style={{ marginTop: 16 }} onClick={handleAnalyze}>
                  开始分析
                </button>
              )}
            </div>
          )}

          {/* 分析中 / 分析结果 */}
          {(isAnalyzing || analysisText) && (
            <div className="analysis-result-section">
              <div className="analysis-stream">
                <h3>分析内容</h3>
                <div
                  className="message-bubble"
                  style={{ whiteSpace: 'pre-wrap' }}
                  dangerouslySetInnerHTML={{ __html: formatContent(analysisText) }}
                  onClick={handleLinkClick}
                />
                {isAnalyzing && !analysisText && (
                  <div className="typing-indicator">
                    <div className="typing-dot" />
                    <div className="typing-dot" />
                    <div className="typing-dot" />
                  </div>
                )}
                <div ref={analysisEndRef} />
              </div>

              {/* 工具错误 */}
              {toolErrors.length > 0 && (
                <div className="analysis-schedules" style={{ borderColor: 'var(--color-accent-danger)' }}>
                  <h3 style={{ color: 'var(--color-accent-danger)' }}>⚠ 工具执行错误 ({toolErrors.length})</h3>
                  {toolErrors.map((err, i) => (
                    <div key={i} style={{ fontSize: 12, color: 'var(--color-text-secondary)', padding: '4px 0' }}>
                      {err}
                    </div>
                  ))}
                </div>
              )}

              {/* 已创建日程 */}
              {schedules.length > 0 && (
                <div className="analysis-schedules">
                  <h3>已创建日程 ({schedules.length})</h3>
                  {schedules.map((s, i) => (
                    <div key={i} className="analysis-schedule-item">
                      <span className={`badge badge-${s.priority === 'high' ? 'danger' : s.priority === 'low' ? 'muted' : 'info'}`}>
                        {priorityLabel(s.priority)}
                      </span>
                      <span style={{ flex: 1 }}>{s.title}</span>
                      <span style={{ color: 'var(--color-text-muted)', fontSize: 12 }}>{s.start_time}</span>
                    </div>
                  ))}
                </div>
              )}

              {/* 完成操作 */}
              {docId && !isAnalyzing && (
                <div className="analysis-actions">
                  <button className="btn btn-primary" onClick={() => handleDownload(docId, selectedFile?.name || 'document')}>
                    📥 下载分析报告 (.txt)
                  </button>
                  <button className="btn" onClick={handleReset}>分析新文件</button>
                </div>
              )}
            </div>
          )}

          {/* 历史记录 */}
          {!isAnalyzing && !analysisText && documents.length > 0 && (
            <div className="analysis-history">
              <h3>历史分析</h3>
              {documents.map(doc => (
                <div key={doc.id} className="analysis-history-item">
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 500 }}>{doc.filename}</div>
                    <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                      {new Date(doc.created_at).toLocaleString('zh-CN')}
                    </div>
                  </div>
                  <button className="btn btn-sm" onClick={() => handleDownload(doc.id, doc.filename)}>下载</button>
                  <button className="btn-icon" style={{ width: 28, height: 28 }} onClick={() => handleDelete(doc.id)}>×</button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
