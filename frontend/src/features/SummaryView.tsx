import { useState, useEffect, useCallback } from 'react'
import { api, type PeriodicSummary } from '../shared/api'
import Markdown from '../components/Markdown'

const PERIODS = [
  { key: 'daily', label: '日' },
  { key: 'weekly', label: '周' },
  { key: 'monthly', label: '月' },
  { key: 'yearly', label: '年' },
] as const

type PeriodType = (typeof PERIODS)[number]['key']

export default function SummaryView() {
  const [periodType, setPeriodType] = useState<PeriodType>('daily')
  const [list, setList] = useState<PeriodicSummary[]>([])
  const [selected, setSelected] = useState<PeriodicSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [generating, setGenerating] = useState(false)

  const loadList = useCallback(async (type: PeriodType) => {
    setLoading(true)
    try {
      const items = await api.listSummaries(type)
      setList(items)
      setSelected(items.length > 0 ? items[0] : null)
    } catch (e) {
      console.error('加载总结列表失败', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadList(periodType)
  }, [periodType, loadList])

  const handleRegenerate = async () => {
    if (!selected) return
    setGenerating(true)
    try {
      await api.generateSummary(selected.period_type, selected.period_key)
      await loadList(periodType)
    } catch (e) {
      console.error('重新生成失败', e)
    } finally {
      setGenerating(false)
    }
  }

  const periodLabel = PERIODS.find(p => p.key === periodType)?.label

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '12px 16px', overflow: 'hidden' }}>
      {/* 分段控件 + 重新生成 */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
        {PERIODS.map(p => (
          <button
            key={p.key}
            onClick={() => setPeriodType(p.key)}
            style={{
              padding: '6px 16px', borderRadius: 6, fontSize: 13, cursor: 'pointer',
              background: periodType === p.key ? 'var(--color-accent-primary)' : 'var(--color-bg-input)',
              color: periodType === p.key ? '#fff' : 'var(--color-text-primary)',
              border: '1px solid var(--color-border)',
            }}
          >
            {p.label}总结
          </button>
        ))}
        <button
          onClick={handleRegenerate}
          disabled={!selected || generating}
          style={{
            marginLeft: 'auto', padding: '6px 14px', borderRadius: 6, fontSize: 13, cursor: 'pointer',
            background: 'transparent', color: 'var(--color-text-primary)',
            border: '1px solid var(--color-border)',
            opacity: !selected || generating ? 0.5 : 1,
          }}
        >
          {generating ? '生成中...' : '重新生成'}
        </button>
      </div>

      <div style={{ display: 'flex', gap: 12, flex: 1, minHeight: 0 }}>
        {/* 左侧列表 */}
        <div style={{
          width: 220, flexShrink: 0, overflowY: 'auto',
          border: '1px solid var(--color-border)', borderRadius: 8, padding: 8,
          background: 'var(--color-bg-panel)',
        }}>
          {loading && <div style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>加载中...</div>}
          {!loading && list.length === 0 && (
            <div style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
              暂无{periodLabel}总结
            </div>
          )}
          {list.map(s => (
            <div
              key={s.id}
              onClick={() => setSelected(s)}
              style={{
                padding: '8px 10px', borderRadius: 6, cursor: 'pointer', marginBottom: 4, fontSize: 12,
                background: selected?.id === s.id ? 'var(--color-accent-primary)' : 'transparent',
                color: selected?.id === s.id ? '#fff' : 'var(--color-text-primary)',
              }}
            >
              <div style={{ fontWeight: 600 }}>{s.period_key}</div>
              <div style={{ fontSize: 11, opacity: 0.8, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {s.headline}
              </div>
            </div>
          ))}
        </div>

        {/* 右侧正文 */}
        <div style={{
          flex: 1, overflowY: 'auto', border: '1px solid var(--color-border)',
          borderRadius: 8, padding: '16px 20px', background: 'var(--color-bg-panel)',
        }}>
          {selected ? (
            <Markdown content={selected.content || '*（无正文）*'} />
          ) : (
            <div style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>选择左侧条目查看总结正文</div>
          )}
        </div>
      </div>
    </div>
  )
}
