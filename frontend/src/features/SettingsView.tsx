import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { api, type Settings } from '../shared/api'

const defaultSettings: Settings = {
  api_base: 'https://open.bigmodel.cn/api/paas/v4',
  api_key: '',
  model: 'glm-5.2',
  temperature: 0.7,
  max_tokens: 8192,
  system_prompt: '',
  context_compress_threshold: 20,
  memory_extract_interval: 5,
}

const PRESETS = [
  {
    id: 'glm', name: '智谱 GLM', icon: '🧠', color: '#7c5cfc',
    api_base: 'https://open.bigmodel.cn/api/paas/v4', suggestModel: 'glm-5.2', max_tokens: 8192,
    desc: 'bigmodel.cn | 旗舰 1M 上下文',
  },
  {
    id: 'deepseek', name: 'DeepSeek 官方', icon: '🐋', color: '#4d6bfe',
    api_base: 'https://api.deepseek.com/v1', suggestModel: 'deepseek-v4-pro', max_tokens: 16384,
    desc: 'platform.deepseek.com | deepseek-v4-pro / deepseek-chat',
  },
  {
    id: 'siliconflow', name: '硅基流动', icon: '🌊', color: '#ff79c6',
    api_base: 'https://api.siliconflow.cn/v1', suggestModel: 'deepseek-ai/DeepSeek-V3', max_tokens: 4096,
    desc: 'siliconflow.cn | 聚合平台，模型多价格低',
  },
  {
    id: 'custom', name: '自定义', icon: '⚙️', color: '#717e95',
    api_base: '', suggestModel: '', max_tokens: 4096,
    desc: '任意 OpenAI 兼容端点',
  },
]

// 根据当前 api_base 匹配预设（不要求 model 名称完全一致）
function matchPreset(api_base: string): string {
  const matched = PRESETS.find(p => p.api_base && api_base.startsWith(p.api_base))
  return matched ? matched.id : 'custom'
}

export default function SettingsView() {
  const [settings, setSettings] = useState<Settings>(defaultSettings)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [activePreset, setActivePreset] = useState('glm')
  const [apiKeyInput, setApiKeyInput] = useState('')  // 独立管理 key 输入

  useEffect(() => { loadSettings() }, [])

  const loadSettings = async () => {
    try {
      const s = await api.getSettings()
      const merged = { ...defaultSettings, ...s }
      setSettings(merged)
      // key 输入框初始为空（后端返回掩码），让用户自己填
      setApiKeyInput('')
      // 按 api_base 匹配预设（不要求 model 完全一致）
      const matched = PRESETS.find(p => p.api_base && s.api_base?.startsWith(p.api_base))
      setActivePreset(matched ? matched.id : 'custom')
    } catch (e: any) { setError(e.message) }
    finally { setLoading(false) }
  }

  const handlePresetSelect = (preset: typeof PRESETS[0]) => {
    setActivePreset(preset.id)
    setSettings(prev => ({
      ...prev,
      api_base: preset.api_base || prev.api_base,
      model: preset.suggestModel || prev.model,
      max_tokens: preset.max_tokens,
    }))
  }

  const handleSave = async () => {
    setError('')
    setSaved(false)
    try {
      const toSave = { ...settings }
      // 如果用户填了新的 key，用新的；否则留空（后端保留旧的）
      if (apiKeyInput.trim()) {
        toSave.api_key = apiKeyInput.trim()
      }
      await api.updateSettings(toSave)
      setSaved(true)
      setApiKeyInput('')  // 清空输入框
      setTimeout(() => setSaved(false), 3000)
    } catch (e: any) {
      setError(String(e?.message || e))
    }
  }

  const update = (key: keyof Settings, value: any) => {
    setSettings(prev => ({ ...prev, [key]: value }))
    if (key === 'api_base' || key === 'model') setActivePreset('custom')
  }

  if (loading) {
    return (
      <div className="app-shell">
        <div className="main-content">
          <div className="content" style={{ justifyContent: 'center', alignItems: 'center' }}>
            <div className="spinner"><div className="spinner-dot" /><div className="spinner-dot" /><div className="spinner-dot" /></div>
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
            <Link to="/" className="btn btn-sm">🏠 主页</Link>
            <Link to="/chat" className="btn btn-sm">💬 对话</Link>
            <Link to="/calendar" className="btn btn-sm">📅 日程</Link>
            <Link to="/library" className="btn btn-sm">📚 知识库</Link>
            <Link to="/settings" className="btn btn-sm">⚙ 设置</Link>
          </div>
        </div>

        <div className="content">
          <div className="settings-page">
            {/* 状态消息 */}
            {error && (
              <div style={{ padding: 12, background: 'rgba(255,85,85,0.1)', border: '1px solid var(--color-accent-danger)', borderRadius: 8, marginBottom: 16, fontSize: 13, color: 'var(--color-accent-danger)' }}>
                ✗ {error}
              </div>
            )}
            {saved && (
              <div style={{ padding: 12, background: 'rgba(80,250,123,0.1)', border: '1px solid var(--color-accent-success)', borderRadius: 8, marginBottom: 16, fontSize: 13, color: 'var(--color-accent-success)' }}>
                ✓ 设置已保存，立即生效
              </div>
            )}

            {/* 1. 模型方案选择 */}
            <div className="settings-section">
              <h3>模型方案</h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {PRESETS.map(p => (
                  <div
                    key={p.id}
                    onClick={() => handlePresetSelect(p)}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px',
                      borderRadius: 8, cursor: 'pointer', transition: 'all 0.15s',
                      background: activePreset === p.id ? `${p.color}22` : 'var(--color-bg-input)',
                      border: `1px solid ${activePreset === p.id ? p.color : 'var(--color-border)'}`,
                    }}
                  >
                    <span style={{ fontSize: 20 }}>{p.icon}</span>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: 14, fontWeight: 600, color: activePreset === p.id ? p.color : 'var(--color-text-primary)' }}>
                        {p.name}
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{p.desc}</div>
                    </div>
                    {activePreset === p.id && (
                      <span style={{ color: p.color, fontSize: 16, fontWeight: 700 }}>✓</span>
                    )}
                  </div>
                ))}
              </div>
            </div>

            {/* 2. API 配置 */}
            <div className="settings-section">
              <h3>API 配置</h3>
              <div className="form-group">
                <label className="form-label">API 端点</label>
                <input
                  className="form-input"
                  value={settings.api_base}
                  onChange={e => update('api_base', e.target.value)}
                  placeholder="https://open.bigmodel.cn/api/paas/v4"
                />
              </div>
              <div className="form-group">
                <label className="form-label">模型名称</label>
                <input
                  className="form-input"
                  value={settings.model}
                  onChange={e => update('model', e.target.value)}
                  placeholder="glm-5.2"
                />
                <div className="form-hint">选方案自动填入，也可手动修改</div>
              </div>
              <div className="form-group">
                <label className="form-label">API Key</label>
                <input
                  className="form-input"
                  type="password"
                  value={apiKeyInput}
                  onChange={e => setApiKeyInput(e.target.value)}
                  placeholder="输入 API Key（留空则保持当前 Key 不变）"
                />
                <div className="form-hint">不同方案需对应平台的 Key，留空不覆盖</div>
              </div>
            </div>

            {/* 3. 模型参数 */}
            <div className="settings-section">
              <h3>模型参数</h3>
              <div className="form-row">
                <div className="form-group">
                  <label className="form-label">Temperature ({settings.temperature.toFixed(1)})</label>
                  <input className="form-input" type="range" min="0" max="2" step="0.1" value={settings.temperature}
                    onChange={e => update('temperature', parseFloat(e.target.value))} />
                  <div className="form-hint">0=精确 1=平衡 2=创意</div>
                </div>
                <div className="form-group">
                  <label className="form-label">Max Tokens</label>
                  <input className="form-input" type="number" min="100" max="65536"
                    value={settings.max_tokens}
                    onChange={e => update('max_tokens', parseInt(e.target.value) || 4096)} />
                  <div className="form-hint">建议 GLM≥8192</div>
                </div>
              </div>
            </div>

            {/* 4. 系统提示词 */}
            <div className="settings-section">
              <h3>系统提示词</h3>
              <textarea className="form-input" rows={6} value={settings.system_prompt}
                onChange={e => update('system_prompt', e.target.value)}
                placeholder="定义 AI 助手的行为和角色..." />
            </div>

            {/* 5. 高级 */}
            <div className="settings-section">
              <h3>高级设置</h3>
              <div className="form-row">
                <div className="form-group">
                  <label className="form-label">上下文压缩阈值</label>
                  <input className="form-input" type="number" min="5" max="50"
                    value={settings.context_compress_threshold}
                    onChange={e => update('context_compress_threshold', parseInt(e.target.value) || 20)} />
                </div>
                <div className="form-group">
                  <label className="form-label">记忆提取间隔</label>
                  <input className="form-input" type="number" min="1" max="20"
                    value={settings.memory_extract_interval}
                    onChange={e => update('memory_extract_interval', parseInt(e.target.value) || 5)} />
                </div>
              </div>
            </div>

            {/* 保存 */}
            <div style={{ marginTop: 20, display: 'flex', gap: 12, alignItems: 'center' }}>
              <button className="btn btn-primary" onClick={handleSave} disabled={loading}
                style={{ padding: '10px 24px', fontSize: 14 }}>
                💾 保存设置
              </button>
              <Link to="/" className="btn">返回主页</Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
