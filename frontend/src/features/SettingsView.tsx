import { useState, useEffect } from 'react'
import { api, type Settings } from '../shared/api'

const defaultSettings: Settings = {
  api_base: 'https://api.siliconflow.cn/v1',
  api_key: '',
  model: 'deepseek-ai/DeepSeek-V3',
  temperature: 0.7,
  max_tokens: 4096,
  system_prompt: '',
  context_compress_threshold: 20,
  memory_extract_interval: 5,
}

export default function SettingsView() {
  const [settings, setSettings] = useState<Settings>(defaultSettings)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    loadSettings()
  }, [])

  const loadSettings = async () => {
    try {
      const s = await api.getSettings()
      setSettings({ ...defaultSettings, ...s })
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleSave = async () => {
    try {
      setError('')
      await api.updateSettings(settings)
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch (e: any) {
      setError(e.message)
    }
  }

  const update = (key: keyof Settings, value: any) => {
    setSettings(prev => ({ ...prev, [key]: value }))
  }

  if (loading) {
    return (
      <div className="app-shell">
        <div className="main-content">
          <div className="content" style={{ justifyContent: 'center', alignItems: 'center' }}>
            <div className="spinner">
              <div className="spinner-dot" />
              <div className="spinner-dot" />
              <div className="spinner-dot" />
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="app-shell">
      <div className="main-content">
        <div className="topbar">
          <span className="topbar-title">⚙ 设置</span>
          <div className="topbar-actions">
            <a href="/" className="btn btn-sm">🏠 主页</a>
            <a href="/chat" className="btn btn-sm">💬 对话</a>
            <a href="/schedules" className="btn btn-sm">📅 日程</a>
            <a href="/notes" className="btn btn-sm">📝 笔记</a>
            <a href="/memories" className="btn btn-sm">🧠 记忆</a>
            <a href="/analysis" className="btn btn-sm">📋 文件分析</a>
          </div>
        </div>
        <div className="content">
          <div className="settings-page">
            {error && (
              <div style={{ padding: 12, background: 'rgba(255,85,85,0.1)', border: '1px solid var(--color-accent-danger)', borderRadius: 8, marginBottom: 16, fontSize: 13 }}>
                ⚠ {error}
              </div>
            )}
            {saved && (
              <div style={{ padding: 12, background: 'rgba(80,250,123,0.1)', border: '1px solid var(--color-accent-success)', borderRadius: 8, marginBottom: 16, fontSize: 13 }}>
                ✓ 设置已保存
              </div>
            )}

            <div className="settings-section">
              <h3>API 配置</h3>
              <div className="form-group">
                <label className="form-label">API Base URL</label>
                <input
                  className="form-input"
                  value={settings.api_base}
                  onChange={e => update('api_base', e.target.value)}
                  placeholder="https://api.siliconflow.cn/v1"
                />
                <div className="form-hint">支持 OpenAI 兼容的 API 端点</div>
              </div>
              <div className="form-group">
                <label className="form-label">API Key</label>
                <input
                  className="form-input"
                  type="password"
                  value={settings.api_key}
                  onChange={e => update('api_key', e.target.value)}
                  placeholder="sk-..."
                />
              </div>
              <div className="form-group">
                <label className="form-label">模型</label>
                <input
                  className="form-input"
                  value={settings.model}
                  onChange={e => update('model', e.target.value)}
                  placeholder="deepseek-ai/DeepSeek-V3"
                />
              </div>
            </div>

            <div className="settings-section">
              <h3>模型参数</h3>
              <div className="form-row">
                <div className="form-group">
                  <label className="form-label">Temperature ({settings.temperature})</label>
                  <input
                    className="form-input"
                    type="range"
                    min="0"
                    max="2"
                    step="0.1"
                    value={settings.temperature}
                    onChange={e => update('temperature', parseFloat(e.target.value))}
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Max Tokens</label>
                  <input
                    className="form-input"
                    type="number"
                    min="100"
                    max="32768"
                    value={settings.max_tokens}
                    onChange={e => update('max_tokens', parseInt(e.target.value) || 4096)}
                  />
                </div>
              </div>
            </div>

            <div className="settings-section">
              <h3>系统提示词</h3>
              <div className="form-group">
                <textarea
                  className="form-input"
                  rows={8}
                  value={settings.system_prompt}
                  onChange={e => update('system_prompt', e.target.value)}
                  placeholder="定义 AI 助手的行为和角色..."
                />
              </div>
            </div>

            <div className="settings-section">
              <h3>高级设置</h3>
              <div className="form-row">
                <div className="form-group">
                  <label className="form-label">上下文压缩阈值</label>
                  <input
                    className="form-input"
                    type="number"
                    min="5"
                    max="50"
                    value={settings.context_compress_threshold}
                    onChange={e => update('context_compress_threshold', parseInt(e.target.value) || 20)}
                  />
                  <div className="form-hint">超过此消息数自动压缩</div>
                </div>
                <div className="form-group">
                  <label className="form-label">记忆提取间隔</label>
                  <input
                    className="form-input"
                    type="number"
                    min="1"
                    max="20"
                    value={settings.memory_extract_interval}
                    onChange={e => update('memory_extract_interval', parseInt(e.target.value) || 5)}
                  />
                  <div className="form-hint">每 N 轮对话提取记忆</div>
                </div>
              </div>
            </div>

            <div style={{ marginTop: 24 }}>
              <button className="btn btn-primary" onClick={handleSave}>
                保存设置
              </button>
              <a href="/" style={{ marginLeft: 12 }} className="btn">
                取消
              </a>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
