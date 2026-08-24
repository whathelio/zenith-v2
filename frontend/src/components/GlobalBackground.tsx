import { useEffect } from 'react'
import { api } from '../shared/api'

// 全局背景图片 URL（带时间戳防缓存）。未设置时为空字符串。
export function globalBgUrl(): string {
  return '/api/settings/background-image?t=' + Date.now()
}

const BG_OVERLAY = 'linear-gradient(rgba(40,44,52,0.55), rgba(40,44,52,0.55))'

/**
 * 全局背景层 — 设置页「外观」上传的背景图片应用为 body 背景（全屏固定、带暗色遮罩保证可读性）。
 * 监听 zenith:global-bg-change 事件，上传/清除后即时刷新。
 *
 * 注：不再用 z-index:-1 的独立 fixed 层——它会被 html/body/#root 的不透明背景
 * (#282c34) 压在下面而永远不可见。改为直接写 body 背景，配合 #root/.app-layout
 * 背景透明，让背景图从最底层透出。
 */
export default function GlobalBackground() {
  useEffect(() => {
    let mounted = true

    const apply = (on: boolean) => {
      if (on) {
        document.body.style.backgroundImage = `${BG_OVERLAY}, url("${globalBgUrl()}")`
        document.body.style.backgroundSize = 'cover'
        document.body.style.backgroundPosition = 'center'
        document.body.style.backgroundAttachment = 'fixed'
        document.body.style.backgroundRepeat = 'no-repeat'
      } else {
        document.body.style.backgroundImage = 'none'
        document.body.style.backgroundSize = ''
        document.body.style.backgroundPosition = ''
        document.body.style.backgroundAttachment = ''
        document.body.style.backgroundRepeat = ''
      }
    }

    api.getSettings()
      .then(s => {
        if (mounted && (s as any).background_image) apply(true)
      })
      .catch(() => {})

    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail
      apply(!!detail)
    }
    window.addEventListener('zenith:global-bg-change', handler as EventListener)

    return () => {
      mounted = false
      window.removeEventListener('zenith:global-bg-change', handler as EventListener)
    }
  }, [])

  return null
}
