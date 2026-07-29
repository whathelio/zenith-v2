/**
 * 共享工具函数 — 避免在多组件中重复定义
 */

/** 格式化金额 */
export function formatMoney(v: number): string {
  if (v >= 10000) {
    return (v / 10000).toFixed(2) + '万'
  }
  return v.toFixed(2)
}

/** 格式化金额（短版） */
export function formatMoneyShort(v: number): string {
  if (v >= 1e8) {
    return (v / 1e8).toFixed(1) + '亿'
  }
  if (v >= 10000) {
    return (v / 10000).toFixed(1) + 'w'
  }
  return v.toFixed(0)
}

/** 解析本地日期字符串 "YYYY-MM-DD" → Date */
export function parseLocalDate(s: string): Date {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}

/** 计算两个本地日期间隔天数 */
export function daysBetween(a: string, b: string): number {
  const da = parseLocalDate(a)
  const db = parseLocalDate(b)
  return Math.round((db.getTime() - da.getTime()) / 86400000)
}

/** 日期格式化为 "YYYY-MM-DD" */
export function formatDate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/** 获取周的起始日期（周一） */
export function getWeekStart(date: Date): Date {
  const d = new Date(date)
  const day = d.getDay()
  const diff = d.getDate() - day + (day === 0 ? -6 : 1)
  d.setDate(diff)
  return d
}

/** 获取周的日期列表（周一→周日） */
export function getWeekDays(start: Date): Date[] {
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(start)
    d.setDate(d.getDate() + i)
    return d
  })
}

/** 中文星期名 */
export function chineseWeekday(date: Date): string {
  const days = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
  return days[date.getDay()]
}
