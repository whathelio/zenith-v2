export interface ConfirmOption {
  label: string
  value: string
  confirmText: string
  variant?: 'primary' | 'danger' | 'default'
}

export interface ConfirmCardData {
  id: string
  title?: string
  description?: string
  options: ConfirmOption[]
}

interface ConfirmCardProps {
  data: ConfirmCardData
  onSelect: (option: ConfirmOption) => void
}

export default function ConfirmCard({ data, onSelect }: ConfirmCardProps) {
  return (
    <div style={{
      marginTop: 10,
      padding: 14,
      background: 'var(--color-bg-panel)',
      border: '1px solid var(--color-border)',
      borderRadius: 12,
      maxWidth: 480,
    }}>
      {data.title && (
        <div style={{
          fontSize: 14,
          fontWeight: 700,
          color: 'var(--color-text-primary)',
          marginBottom: 6,
        }}>
          {data.title}
        </div>
      )}
      {data.description && (
        <div style={{
          fontSize: 12,
          color: 'var(--color-text-secondary)',
          marginBottom: 12,
          lineHeight: 1.5,
        }}>
          {data.description}
        </div>
      )}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        {data.options.map(opt => {
          const base = {
            padding: '8px 16px',
            borderRadius: 8,
            fontSize: 13,
            fontWeight: 600,
            cursor: 'pointer',
            border: 'none',
            transition: 'all 0.15s',
          }
          let bg = 'var(--color-bg-muted)'
          let color = 'var(--color-text-primary)'
          if (opt.variant === 'primary') {
            bg = 'var(--color-accent-primary)'
            color = '#fff'
          } else if (opt.variant === 'danger') {
            bg = '#ff5555'
            color = '#fff'
          }
          return (
            <button
              key={opt.value}
              style={{ ...base, background: bg, color }}
              onMouseEnter={e => {
                e.currentTarget.style.filter = 'brightness(1.1)'
              }}
              onMouseLeave={e => {
                e.currentTarget.style.filter = 'brightness(1)'
              }}
              onClick={() => onSelect(opt)}
            >
              {opt.label}
            </button>
          )
        })}
      </div>
    </div>
  )
}

// 从 AI 消息内容中提取确认卡片标记
// 支持格式：<!-- zenith-confirm-card:{...} -->
export function extractConfirmCard(content: string): ConfirmCardData | null {
  const match = content.match(/<!--\s*zenith-confirm-card:\s*([\s\S]*?)\s*-->/)
  if (!match) return null
  try {
    const data = JSON.parse(match[1])
    if (!data.id || !Array.isArray(data.options) || data.options.length === 0) return null
    return data as ConfirmCardData
  } catch {
    return null
  }
}

// 从消息内容中移除确认卡片标记，返回纯文本
export function stripConfirmCard(content: string): string {
  return content.replace(/<!--\s*zenith-confirm-card:\s*[\s\S]*?\s*-->/g, '').trim()
}
