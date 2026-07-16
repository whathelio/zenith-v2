import { useState } from 'react'
import { api } from '../shared/api'

interface TransformButtonProps {
  sourceType: 'memory' | 'note' | 'schedule'
  sourceId: number
  onTransformed?: (targetType: string) => void
}

/**
 * 共享转化按钮组件 — 记忆/笔记/行程互转
 * 点击展开下拉菜单选择目标类型，调用API后显示预览确认模态框
 */
export function TransformButton({ sourceType, sourceId, onTransformed }: TransformButtonProps) {
  const [showMenu, setShowMenu] = useState(false)
  const [loading, setLoading] = useState(false)
  const [preview, setPreview] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)

  const typeLabels: Record<string, string> = {
    memory: '记忆', note: '笔记', schedule: '行程',
  }
  const typeIcons: Record<string, string> = {
    memory: '🧠', note: '📝', schedule: '📅',
  }

  const targets = ['memory', 'note', 'schedule'].filter(t => t !== sourceType)

  const handleTransform = async (targetType: string) => {
    setShowMenu(false)
    setLoading(true)
    setError(null)
    try {
      const result = await api.transform(sourceType, sourceId, targetType)
      setPreview(result)
    } catch (e: any) {
      setError(e.message || '转化失败')
    } finally {
      setLoading(false)
    }
  }

  const handleConfirm = () => {
    if (preview && onTransformed) {
      onTransformed(preview.target_type)
    }
    setPreview(null)
  }

  const handleCancel = () => {
    // 删除已创建的 proposed 条目
    if (preview?.created_id && preview?.target_type) {
      if (preview.target_type === 'schedule') api.deleteSchedule(preview.created_id)
      else if (preview.target_type === 'note') api.deleteNote(preview.created_id)
      else if (preview.target_type === 'memory') api.deleteMemory(preview.created_id)
    }
    setPreview(null)
  }

  return (
    <>
      {/* 转化按钮 */}
      <div style={{ position: 'relative', display: 'inline-block' }}>
        <button
          className="btn-icon"
          style={{ width: 24, height: 24, fontSize: 11, color: 'var(--color-accent-secondary)' }}
          onClick={(e) => { e.stopPropagation(); setShowMenu(!showMenu) }}
          title="转化为其他类型"
        >
          ⇄
        </button>
        {showMenu && (
          <>
            <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, zIndex: 998 }}
              onClick={(e) => { e.stopPropagation(); setShowMenu(false) }} />
            <div style={{
              position: 'absolute', top: '100%', right: 0, zIndex: 999,
              background: 'var(--color-bg-panel)', border: '1px solid var(--color-border)',
              borderRadius: 6, padding: 4, display: 'flex', flexDirection: 'column', gap: 2,
              boxShadow: 'var(--shadow-md)', minWidth: 120,
            }}>
              <div style={{ fontSize: 10, color: 'var(--color-text-muted)', padding: '2px 8px' }}>转化为</div>
              {targets.map(t => (
                <button
                  key={t}
                  style={{
                    padding: '4px 10px', fontSize: 12, textAlign: 'left',
                    background: 'transparent', border: 'none', cursor: 'pointer',
                    color: 'var(--color-text-primary)', borderRadius: 4,
                  }}
                  onClick={(e) => { e.stopPropagation(); handleTransform(t) }}
                  onMouseEnter={e => { e.currentTarget.style.background = 'var(--color-bg-hover)' }}
                  onMouseLeave={e => { e.currentTarget.style.background = 'transparent' }}
                >
                  {typeIcons[t]} {typeLabels[t]}
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Loading 状态 */}
      {loading && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          background: 'rgba(0,0,0,0.4)', zIndex: 1000,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <div style={{
            padding: 20, borderRadius: 8, background: 'var(--color-bg-panel)',
            border: '1px solid var(--color-accent-primary)', fontSize: 14, color: 'var(--color-text-primary)',
          }}>
            <span className="spinner"><span className="spinner-dot" /><span className="spinner-dot" /><span className="spinner-dot" /></span>
            {'  '}AI 转化中...
          </div>
        </div>
      )}

      {/* 错误提示 */}
      {error && (
        <div style={{
          position: 'fixed', top: 16, left: '50%', transform: 'translateX(-50%)',
          padding: '8px 20px', borderRadius: 8, zIndex: 1000,
          background: 'rgba(255,85,85,0.15)', border: '1px solid #ff5555',
          color: '#ff5555', fontSize: 13, fontWeight: 600,
        }}>
          {error}
          <button style={{ marginLeft: 12, background: 'none', border: 'none', color: '#ff5555', cursor: 'pointer' }}
            onClick={() => setError(null)}>×</button>
        </div>
      )}

      {/* 预览确认模态框 */}
      {preview && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          background: 'rgba(0,0,0,0.5)', zIndex: 1000,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }} onClick={handleCancel}>
          <div style={{
            maxWidth: 500, width: '90%', maxHeight: '80vh', overflowY: 'auto',
            padding: 20, borderRadius: 10, background: 'var(--color-bg-panel)',
            border: '1px solid var(--color-accent-primary)',
          }} onClick={e => e.stopPropagation()}>
            <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--color-accent-primary)', marginBottom: 12 }}>
              {typeIcons[preview.target_type]} 转化为{typeLabels[preview.target_type]}
            </div>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 12 }}>
              从 {typeLabels[preview.source_type]} #{preview.source_id} 转化而来 · 待确认
            </div>

            {/* 预览内容 */}
            <div style={{
              padding: 14, borderRadius: 8, background: 'var(--color-bg-input)',
              border: '1px solid var(--color-border)', fontSize: 13, lineHeight: 1.6,
            }}>
              {preview.target_type === 'schedule' && (
                <>
                  <div style={{ fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 6 }}>
                    {preview.preview.title}
                  </div>
                  {preview.preview.description && <div style={{ color: 'var(--color-text-secondary)' }}>{preview.preview.description}</div>}
                  <div style={{ color: 'var(--color-text-muted)', fontSize: 11, marginTop: 6 }}>
                    {preview.preview.start_time && `⏰ ${preview.preview.start_time}`}
                    {preview.preview.end_time && ` → ${preview.preview.end_time}`}
                    {preview.preview.location && ` 📍 ${preview.preview.location}`}
                  </div>
                  <div style={{ marginTop: 6, display: 'flex', gap: 6 }}>
                    <span style={{ fontSize: 10, background: 'var(--color-bg-panel)', padding: '2px 8px', borderRadius: 4 }}>
                      优先级: {preview.preview.priority}
                    </span>
                    <span style={{ fontSize: 10, background: 'var(--color-bg-panel)', padding: '2px 8px', borderRadius: 4 }}>
                      分类: {preview.preview.category}
                    </span>
                  </div>
                </>
              )}
              {preview.target_type === 'note' && (
                <>
                  <div style={{ fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 6 }}>
                    {preview.preview.title}
                  </div>
                  <div style={{ color: 'var(--color-text-secondary)', whiteSpace: 'pre-wrap' }}>
                    {preview.preview.content}
                  </div>
                  {preview.preview.tags && (
                    <div style={{ marginTop: 8, fontSize: 11, color: 'var(--color-accent-secondary)' }}>
                      #{preview.preview.tags}
                    </div>
                  )}
                </>
              )}
              {preview.target_type === 'memory' && (
                <>
                  <div style={{ marginBottom: 6, display: 'flex', gap: 8 }}>
                    <span style={{ fontSize: 10, background: 'var(--color-bg-panel)', padding: '2px 8px', borderRadius: 4, color: 'var(--color-accent-primary)' }}>
                      {preview.preview.type}
                    </span>
                    <span style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>
                      重要度: {preview.preview.importance}/5
                    </span>
                  </div>
                  <div style={{ color: 'var(--color-text-primary)' }}>{preview.preview.content}</div>
                  {preview.preview.keywords && (
                    <div style={{ marginTop: 8, fontSize: 11, color: 'var(--color-text-muted)' }}>
                      关键词: {preview.preview.keywords}
                    </div>
                  )}
                </>
              )}
            </div>

            {/* 操作按钮 */}
            <div style={{ display: 'flex', gap: 8, marginTop: 16, justifyContent: 'flex-end' }}>
              <button className="btn btn-sm btn-danger" onClick={handleCancel}>取消删除</button>
              <button className="btn btn-sm"
                style={{ background: 'var(--color-accent-primary)', color: '#fff', fontWeight: 600 }}
                onClick={handleConfirm}>
                ✓ 确认保留
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
