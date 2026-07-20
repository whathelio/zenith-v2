/* Zenith v2 — 日程状态/优先级/逾期统一辅助函数 */

export const STATUS_NAMES: Record<string, string> = {
  proposed: '待确认',
  confirmed: '已确认',
  done: '已完成',
  cancelled: '已取消',
}

export const STATUS_ICONS: Record<string, string> = {
  proposed: '⏳',
  confirmed: '✓',
  done: '✅',
  cancelled: '✗',
}

export const STATUS_COLORS: Record<string, string> = {
  proposed: '#f1fa8c',
  confirmed: '#50fa7b',
  done: '#8be9fd',
  cancelled: '#717e95',
}

export const STATUS_BG_COLORS: Record<string, string> = {
  proposed: 'rgba(241,250,140,0.08)',
  confirmed: 'rgba(80,250,123,0.06)',
  done: 'rgba(139,233,253,0.05)',
  cancelled: 'rgba(113,126,149,0.04)',
}

export const PRIORITY_NAMES: Record<string, string> = {
  low: '低',
  normal: '中',
  high: '高',
}

export const PRIORITY_COLORS: Record<string, string> = {
  low: '#6272a4',
  normal: '#bd93f9',
  high: '#ff5555',
}

export const CATEGORY_LABELS: Record<string, string> = {
  economic: '财经',
  market: '市场',
  reminder: '提醒',
  personal: '个人',
  other: '其他',
}

export const IMPACT_LABELS: Record<string, string> = {
  bullish: '利多',
  bearish: '利空',
  neutral: '中性',
}

export const IMPACT_COLORS: Record<string, string> = {
  bullish: '#50fa7b',
  bearish: '#ff5555',
  neutral: '#8be9fd',
}

/** 判断日程是否逾期（未完成且开始时间已过） */
export function isScheduleOverdue(s: { start_time?: string; status?: string }): boolean {
  if (!s.start_time || s.status === 'done' || s.status === 'cancelled') {
    return false
  }
  const start = new Date(s.start_time)
  if (isNaN(start.getTime())) return false
  return start.getTime() < Date.now()
}

/** 返回日程优先级排序值（高→中→低） */
export function priorityValue(p: string): number {
  return { high: 3, normal: 2, low: 1 }[p] || 0
}

/** 统一排序：高优先级在前，同优先级按开始时间升序 */
export function sortSchedules<T extends { priority?: string; start_time?: string }>(list: T[]): T[] {
  return [...list].sort((a, b) => {
    const pv = priorityValue(b.priority || '') - priorityValue(a.priority || '')
    if (pv !== 0) return pv
    return (a.start_time || '').localeCompare(b.start_time || '')
  })
}

/** 过滤掉已完成/已取消/已逾期的日程（用于左侧今日速览） */
export function filterActiveSchedules<T extends { start_time?: string; status?: string }>(list: T[]): T[] {
  return list.filter(s => {
    if (s.status === 'done' || s.status === 'cancelled') return false
    if (isScheduleOverdue(s)) return false
    return true
  })
}

export function formatDateTime(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
