import { useState, useEffect, useCallback } from 'react'
import { api } from '../shared/api'
import type { Skill, SkillSuggestion } from '../shared/api'

const CONFIRMED_COLORS: Record<number, { bg: string; text: string; label: string }> = {
  0: { bg: '#ff5c5c22', text: '#ff5c5c', label: '待确认' },
  1: { bg: '#1ae86522', text: '#1ae865', label: '已确认' },
}

export default function SkillsView() {
  const [skills, setSkills] = useState<Skill[]>([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  // 反馈模态框
  const [feedbackSkillId, setFeedbackSkillId] = useState<number | null>(null)
  const [feedbackText, setFeedbackText] = useState('')
  const [feedbackRating, setFeedbackRating] = useState(3)
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false)
  // 改进建议模态框
  const [suggestionSkillId, setSuggestionSkillId] = useState<number | null>(null)
  const [suggestion, setSuggestion] = useState<SkillSuggestion | null>(null)
  const [suggestionLoading, setSuggestionLoading] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(null), 2500) }

  const loadSkills = useCallback(async () => {
    setLoading(true)
    try { setSkills(await api.listSkills(search)) } catch {} finally { setLoading(false) }
  }, [search])

  useEffect(() => { loadSkills() }, [loadSkills])

  const handleConfirm = async (id: number) => { try { await api.confirmSkill(id); loadSkills() } catch {} }
  const handleUse = async (id: number) => { try { await api.useSkill(id); showToast('技能已使用！是否提交反馈？'); loadSkills() } catch {} }
  const handleDelete = async (id: number) => { try { await api.deleteSkill(id); setExpandedId(null); loadSkills() } catch {} }

  // 提交反馈
  const submitFeedback = async () => {
    if (!feedbackSkillId || !feedbackText.trim()) return
    setFeedbackSubmitting(true)
    try {
      await api.feedbackSkill(feedbackSkillId, feedbackText, feedbackRating)
      showToast('反馈已记录 ✓')
      setFeedbackSkillId(null)
      setFeedbackText('')
      setFeedbackRating(3)
      loadSkills()
    } catch { showToast('反馈提交失败') }
    finally { setFeedbackSubmitting(false) }
  }

  // 加载改进建议
  const loadSuggestions = async (sid: number) => {
    setSuggestionSkillId(sid)
    setSuggestion(null)
    setSuggestionLoading(true)
    try {
      const result = await api.getSkillSuggestions(sid)
      setSuggestion(result)
    } catch (e: any) {
      showToast('获取建议失败: ' + (e.message || ''))
      setSuggestionSkillId(null)
    } finally { setSuggestionLoading(false) }
  }

  // 应用改进
  const applyImprovement = async () => {
    if (!suggestion?.improved_steps || !suggestionSkillId) return
    try {
      await api.improveSkill(suggestionSkillId, suggestion.improved_steps)
      showToast('技能已更新 ✓')
      setSuggestion(null)
      setSuggestionSkillId(null)
      loadSkills()
      setExpandedId(null)
    } catch { showToast('更新失败') }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, height: '100%', overflowY: 'auto', padding: '12px 16px' }}>
      {/* Toast */}
      {toast && (
        <div style={{ position: 'fixed', top: 16, left: '50%', transform: 'translateX(-50%)', padding: '8px 20px', borderRadius: 8, background: 'var(--color-bg-panel)', border: '1px solid var(--color-accent-primary)', color: 'var(--color-accent-primary)', fontSize: 13, fontWeight: 600, zIndex: 999, boxShadow: 'var(--shadow-md)' }}>{toast}</div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontSize: 18, fontWeight: 700, color: 'var(--color-text-primary)' }}>⚡ 技能卡片</span>
        <input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索技能..." style={{ flex: 1, fontSize: 12, padding: '6px 10px', background: 'var(--color-bg-input)', border: '1px solid var(--color-border)', borderRadius: 6, color: 'var(--color-text-primary)', outline: 'none' }} />
      </div>

      <div style={{ display: 'flex', gap: 12, fontSize: 11, color: 'var(--color-text-muted)' }}>
        <span>总计 {skills.length}</span>
        <span>已确认 {skills.filter(s => s.confirmed_by_user === 1).length}</span>
      </div>

      {loading ? (
        <div className="spinner"><div className="spinner-dot" /><div className="spinner-dot" /><div className="spinner-dot" /></div>
      ) : skills.length === 0 ? (
        <div className="empty-state"><p style={{ fontSize: 13 }}>暂无技能卡片</p></div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {skills.map(skill => {
            const conf = CONFIRMED_COLORS[skill.confirmed_by_user] || CONFIRMED_COLORS[0]
            const isExpanded = expandedId === skill.id
            return (
              <div key={skill.id} style={{ background: 'var(--color-bg-panel)', borderRadius: 8, padding: isExpanded ? '10px 14px' : '8px 14px', cursor: 'pointer', border: `1px solid ${isExpanded ? 'var(--color-accent-primary)' : 'var(--color-border)'}`, }} onClick={() => setExpandedId(isExpanded ? null : skill.id)}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-primary)', flex: 1 }}>⚡ {skill.name}</span>
                  <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 4, background: conf.bg, color: conf.text }}>{conf.label}</span>
                  <span style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>使用 {skill.usage_count} 次</span>
                </div>
                <div style={{ fontSize: 11, color: 'var(--color-text-secondary)', marginTop: 4 }}>触发场景: {skill.trigger_scene}</div>
                {skill.tags && skill.tags.length > 0 && (
                  <div style={{ display: 'flex', gap: 4, marginTop: 4, flexWrap: 'wrap' }}>
                    {skill.tags.map((tag, i) => (
                      <span key={i} style={{ fontSize: 10, padding: '1px 6px', borderRadius: 4, background: 'rgba(189,147,249,0.12)', color: 'var(--color-accent-primary)' }}>{tag}</span>
                    ))}
                  </div>
                )}
                {isExpanded && skill.steps && skill.steps.length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 4 }}>操作步骤:</div>
                    {skill.steps.map((step, i) => (
                      <div key={i} style={{ fontSize: 12, color: 'var(--color-text-secondary)', paddingLeft: 12, marginBottom: 3 }}>{i + 1}. {step}</div>
                    ))}
                    <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
                      {skill.confirmed_by_user === 0 && (
                        <button onClick={e => { e.stopPropagation(); handleConfirm(skill.id) }} style={{ fontSize: 11, padding: '4px 10px', borderRadius: 5, background: 'rgba(80,250,123,0.12)', color: '#50fa7b', border: '1px solid rgba(80,250,123,0.3)', cursor: 'pointer' }}>✅ 确认</button>
                      )}
                      <button onClick={e => { e.stopPropagation(); handleUse(skill.id) }} style={{ fontSize: 11, padding: '4px 10px', borderRadius: 5, background: 'rgba(189,147,249,0.12)', color: '#bd93f9', border: '1px solid rgba(189,147,249,0.3)', cursor: 'pointer' }}>📋 使用</button>
                      <button onClick={e => { e.stopPropagation(); setFeedbackSkillId(skill.id); setFeedbackText(''); setFeedbackRating(3) }} style={{ fontSize: 11, padding: '4px 10px', borderRadius: 5, background: 'rgba(255,179,71,0.12)', color: '#ffb347', border: '1px solid rgba(255,179,71,0.3)', cursor: 'pointer' }}>💬 反馈</button>
                      <button onClick={e => { e.stopPropagation(); loadSuggestions(skill.id) }} style={{ fontSize: 11, padding: '4px 10px', borderRadius: 5, background: 'rgba(139,233,253,0.12)', color: '#8be9fd', border: '1px solid rgba(139,233,253,0.3)', cursor: 'pointer' }}>🔧 改进建议</button>
                      <button onClick={e => { e.stopPropagation(); handleDelete(skill.id) }} style={{ fontSize: 11, padding: '4px 10px', borderRadius: 5, background: 'rgba(255,85,85,0.12)', color: '#ff5555', border: '1px solid rgba(255,85,85,0.3)', cursor: 'pointer' }}>🗑 删除</button>
                    </div>
                    <div style={{ fontSize: 10, color: 'var(--color-text-muted)', marginTop: 6 }}>来源: {skill.source_conv_id || 'N/A'} | 创建: {skill.created_at?.slice(0, 10)}</div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* ===== 反馈模态框 ===== */}
      {feedbackSkillId && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setFeedbackSkillId(null)}>
          <div style={{ maxWidth: 420, width: '90%', padding: 20, borderRadius: 10, background: 'var(--color-bg-panel)', border: '1px solid var(--color-accent-primary)' }} onClick={e => e.stopPropagation()}>
            <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--color-accent-primary)', marginBottom: 12 }}>💬 技能反馈</div>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 10 }}>记录这次技能使用的体验：什么做得好？什么可以改进？</div>
            <textarea value={feedbackText} onChange={e => setFeedbackText(e.target.value)} placeholder="描述经验或改进建议..." style={{ width: '100%', height: 80, padding: 10, borderRadius: 6, background: 'var(--color-bg-input)', border: '1px solid var(--color-border)', color: 'var(--color-text-primary)', fontSize: 12, resize: 'vertical' }} rows={3} />
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10 }}>
              <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>评分:</span>
              {[1, 2, 3, 4, 5].map(r => (
                <button key={r} onClick={() => setFeedbackRating(r)} style={{ fontSize: 18, cursor: 'pointer', background: 'none', border: 'none', color: r <= feedbackRating ? '#ffb347' : 'var(--color-text-muted)', padding: 2 }}>★</button>
              ))}
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 14, justifyContent: 'flex-end' }}>
              <button className="btn btn-sm" onClick={() => setFeedbackSkillId(null)}>取消</button>
              <button className="btn btn-sm" style={{ background: '#ffb347', color: '#000', fontWeight: 600 }} onClick={submitFeedback} disabled={feedbackSubmitting || !feedbackText.trim()}>提交反馈</button>
            </div>
          </div>
        </div>
      )}

      {/* ===== 改进建议模态框 ===== */}
      {suggestionSkillId && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => { setSuggestion(null); setSuggestionSkillId(null) }}>
          <div style={{ maxWidth: 560, width: '90%', maxHeight: '80vh', overflowY: 'auto', padding: 20, borderRadius: 10, background: 'var(--color-bg-panel)', border: '1px solid var(--color-accent-secondary)' }} onClick={e => e.stopPropagation()}>
            <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--color-accent-secondary)', marginBottom: 12 }}>🔧 技能改进建议</div>

            {suggestionLoading ? (
              <div className="spinner"><div className="spinner-dot" /><div className="spinner-dot" /><div className="spinner-dot" /><span style={{ marginLeft: 8, fontSize: 12 }}>AI 分析中...</span></div>
            ) : suggestion ? (
              suggestion.ready ? (
                <>
                  <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 8 }}>基于 {suggestion.feedback_count} 条反馈 · AI 分析生成</div>
                  {suggestion.analysis && (
                    <div style={{ padding: 10, borderRadius: 6, background: 'rgba(255,179,71,0.08)', border: '1px solid rgba(255,179,71,0.2)', marginBottom: 12, fontSize: 12, color: 'var(--color-text-secondary)', lineHeight: 1.6 }}>{suggestion.analysis}</div>
                  )}

                  {/* 对比：旧 vs 新 */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                    <div>
                      <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 6 }}>旧步骤</div>
                      {suggestion.current_steps?.map((s, i) => (
                        <div key={i} style={{ padding: '6px 10px', borderRadius: 4, background: 'var(--color-bg-input)', border: '1px solid var(--color-border)', fontSize: 11, color: 'var(--color-text-secondary)', marginBottom: 3 }}>{i + 1}. {s}</div>
                      ))}
                    </div>
                    <div>
                      <div style={{ fontSize: 11, fontWeight: 600, color: '#50fa7b', marginBottom: 6 }}>建议新步骤</div>
                      {suggestion.improved_steps?.map((s, i) => (
                        <div key={i} style={{ padding: '6px 10px', borderRadius: 4, background: 'rgba(80,250,123,0.06)', border: '1px solid rgba(80,250,123,0.2)', fontSize: 11, color: '#50fa7b', marginBottom: 3 }}>{i + 1}. {s}</div>
                      ))}
                    </div>
                  </div>

                  {suggestion.reason && (
                    <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 10, fontStyle: 'italic' }}>改进理由: {suggestion.reason}</div>
                  )}

                  <div style={{ display: 'flex', gap: 8, marginTop: 14, justifyContent: 'flex-end' }}>
                    <button className="btn btn-sm" onClick={() => { setSuggestion(null); setSuggestionSkillId(null) }}>关闭</button>
                    <button className="btn btn-sm" style={{ background: '#50fa7b', color: '#000', fontWeight: 600 }} onClick={applyImprovement}>✓ 应用改进</button>
                  </div>
                </>
              ) : (
                <div style={{ textAlign: 'center', padding: 20, color: 'var(--color-text-muted)', fontSize: 13 }}>
                  😅 反馈不足（当前 {suggestion.feedback_count} 条，需要至少 {suggestion.min_required} 条）<br />
                  <span style={{ fontSize: 11 }}>多使用几次技能并提交反馈后再试</span>
                </div>
              )
            ) : null}
          </div>
        </div>
      )}
    </div>
  )
}
