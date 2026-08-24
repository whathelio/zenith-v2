import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './index.css'
import './zenith-workbuddy.css'

// 全局错误捕获 — 仅开发环境显示红框（生产环境避免暴露内部路径/堆栈）
if (import.meta.env.DEV) {
  window.addEventListener('error', (e) => {
    try {
      const div = document.createElement('div')
      div.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;background:#c0392b;color:#fff;font:12px monospace;padding:8px 12px;white-space:pre-wrap;max-height:120px;overflow:auto'
      div.textContent = `[全局错误] ${e.message}\n${(e.error?.stack || '').split('\n').slice(0, 4).join('\n')}`
      document.body.appendChild(div)
    } catch { /* ignore */ }
  })
  window.addEventListener('unhandledrejection', (e) => {
    try {
      const div = document.createElement('div')
      div.style.cssText = 'position:fixed;top:130px;left:0;right:0;z-index:99999;background:#e67e22;color:#fff;font:12px monospace;padding:8px 12px;white-space:pre-wrap'
      div.textContent = `[未处理Promise] ${String(e.reason)}`
      document.body.appendChild(div)
    } catch { /* ignore */ }
  })
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)
