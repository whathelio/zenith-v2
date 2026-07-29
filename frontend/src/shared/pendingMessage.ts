const KEY = 'zenith:pending-message'

export const setPendingMessage = (t: string) => {
  try { sessionStorage.setItem(KEY, t) } catch { /* ignore private mode */ }
}

export const takePendingMessage = (): string | null => {
  try {
    const v = sessionStorage.getItem(KEY)
    if (v) sessionStorage.removeItem(KEY)
    return v
  } catch { return null }
}
