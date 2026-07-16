import { useState, useEffect, useCallback } from 'react'
import { api, Skill } from '../shared/api'

const TYPE_ICONS: Record<string, string> = {
  skill: '⚡',
}
const CONFIRMED_COLORS: Record<number, { bg: string; text: string; label: string }> = {
  0: { bg: '#ff5c5c22', text: '#ff5c5c', label: '待确认' },
  1: { bg: '#1ae86522', text: '#1ae865', label: '已确认' },
}

export default function SkillsView() {
  const [skills, setSkills] = useState<Skill[]>([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [expandedId, setExpandedId] = useState<number | null>(null)

  const loadSkills = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.listSkills(search)
      setSkills(data)
    } catch (e) {
      console.error('Failed to load skills:', e)
    } finally {
      setLoading(false)
    }
  }, [search])

  useEffect(() => { loadSkills() }, [loadSkills])

  const handleConfirm = async (id: number) => {
    try {
      await api.confirmSkill(id)
      loadSkills()
    } catch (e) {
      console.error('Failed to confirm skill:', e)
    }
  }

  const handleUse = async (id: number) => {
    try {
      await api.useSkill(id)
      loadSkills()
    } catch (e) {
      console.error('Failed to mark skill usage:', e)
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await api.deleteSkill(id)
      setExpandedId(null)
      loadSkills()
    } catch (e) {
      console.error('Failed to delete skill:', e)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, height: '100%', overflowY: 'auto', padding: '12px 16px' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontSize: 18, fontWeight: 700, color: '#e0e0e0' }}>⚡ 技能卡片</span>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="搜索技能..."
          style={{
            flex: 1, fontSize: 12, padding: '6px 10px',
            background: '#1a1a2e', border: '1px solid #333', borderRadius: 6, color: '#ccc',
            outline: 'none',
          }}
        />
      </div>

      {/* Stats */}
      <div style={{ display: 'flex', gap: 12, fontSize: 11, color: '#888' }}>
        <span>总计 {skills.length}</span>
        <span>已确认 {skills.filter(s => s.confirmed_by_user === 1).length}</span>
        <span>待确认 {skills.filter(s => s.confirmed_by_user === 0).length}</span>
      </div>

      {/* Skills List */}
      {loading ? (
        <div style={{ textAlign: 'center', color: '#666', fontSize: 12 }}>加载中...</div>
      ) : skills.length === 0 ? (
        <div style={{ textAlign: 'center', color: '#666', fontSize: 12 }}>暂无技能卡片</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {skills.map(skill => {
            const conf = CONFIRMED_COLORS[skill.confirmed_by_user] || CONFIRMED_COLORS[0]
            const isExpanded = expandedId === skill.id
            return (
              <div
                key={skill.id}
                style={{
                  background: '#1a1a2e', borderRadius: 8,
                  padding: isExpanded ? '10px 14px' : '8px 14px',
                  cursor: 'pointer',
                  border: `1px solid ${isExpanded ? '#6c5ce7' : '#2a2a4a'}`,
                }}
                onClick={() => setExpandedId(isExpanded ? null : skill.id)}
              >
                {/* Summary row */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: '#e0e0e0', flex: 1 }}>
                    {TYPE_ICONS.skill} {skill.name}
                  </span>
                  <span style={{
                    fontSize: 10, padding: '2px 6px', borderRadius: 4,
                    background: conf.bg, color: conf.text,
                  }}>
                    {conf.label}
                  </span>
                  <span style={{ fontSize: 10, color: '#888' }}>
                    使用 {skill.usage_count} 次
                  </span>
                </div>

                {/* Trigger scene */}
                <div style={{ fontSize: 11, color: '#aaa', marginTop: 4 }}>
                  触发场景: {skill.trigger_scene}
                </div>

                {/* Tags */}
                {skill.tags && skill.tags.length > 0 && (
                  <div style={{ display: 'flex', gap: 4, marginTop: 4, flexWrap: 'wrap' }}>
                    {skill.tags.map((tag, i) => (
                      <span key={i} style={{
                        fontSize: 10, padding: '1px 4px', borderRadius: 3,
                        background: '#6c5ce722', color: '#6c5ce7',
                      }}>{tag}</span>
                    ))}
                  </div>
                )}

                {/* Expanded content */}
                {isExpanded && (
                  <div style={{ marginTop: 8 }}>
                    {/* Steps */}
                    {skill.steps && skill.steps.length > 0 && (
                      <div>
                        <div style={{ fontSize: 11, fontWeight: 600, color: '#aaa', marginBottom: 4 }}>操作步骤:</div>
                        {skill.steps.map((step, i) => (
                          <div key={i} style={{ fontSize: 12, color: '#ccc', paddingLeft: 12, marginBottom: 3 }}>
                            {i + 1}. {step}
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Actions */}
                    <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                      {skill.confirmed_by_user === 0 && (
                        <button
                          onClick={(e) => { e.stopPropagation(); handleConfirm(skill.id) }}
                          style={{
                            fontSize: 11, padding: '4px 10px', borderRadius: 5,
                            background: '#1ae86522', color: '#1ae865', border: '1px solid #1ae86544',
                            cursor: 'pointer',
                          }}
                        >✅ 确认</button>
                      )}
                      <button
                        onClick={(e) => { e.stopPropagation(); handleUse(skill.id) }}
                        style={{
                          fontSize: 11, padding: '4px 10px', borderRadius: 5,
                          background: '#6c5ce722', color: '#6c5ce7', border: '1px solid #6c5ce744',
                          cursor: 'pointer',
                        }}
                      >📋 使用</button>
                      <button
                        onClick={(e) => { e.stopPropagation(); handleDelete(skill.id) }}
                        style={{
                          fontSize: 11, padding: '4px 10px', borderRadius: 5,
                          background: '#ff5c5c22', color: '#ff5c5c', border: '1px solid #ff5c5c44',
                          cursor: 'pointer',
                        }}
                      >🗑 删除</button>
                    </div>

                    {/* Meta */}
                    <div style={{ fontSize: 10, color: '#666', marginTop: 6 }}>
                      来源对话: {skill.source_conv_id || 'N/A'} | 创建: {skill.created_at?.slice(0, 10)}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
