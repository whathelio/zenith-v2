import { useState, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { api, type Settings } from '../shared/api'
import GlobalBackground from '../components/GlobalBackground'

const defaultSettings: Settings = {
  api_base: 'https://open.bigmodel.cn/api/paas/v4',
  api_key: '',
  model: 'glm-5.2',
  temperature: 0.7,
  max_tokens: 8192,
  system_prompt: '',
  context_compress_threshold: 20,
  memory_extract_interval: 5,
  providers: [],
  default_provider: '',
  background_provider: '',
  personas: [],
  socratic_mode: true,
  background_image: '',
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
  const [bgPreview, setBgPreview] = useState('')        // 全局背景预览 URL
  const bgInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { loadSettings() }, [])

  const loadSettings = async () => {
    try {
      const s = await api.getSettings()
      const merged = { ...defaultSettings, ...s }
      setSettings(merged)
      // 全局背景预览
      setBgPreview((s as any).background_image ? `/api/settings/background-image?t=${Date.now()}` : '')
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
      // 全局背景图由上传/清除即时持久化，不在这条路径覆盖真实文件名
      delete (toSave as any).background_image
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

  // ── 全局背景图片（外观常用设置）──
  const applyGlobalBg = (url: string) => {
    setBgPreview(url)
    setSettings(prev => ({ ...prev, background_image: url ? 'global' : '' }))
    window.dispatchEvent(new CustomEvent('zenith:global-bg-change', { detail: url ? '1' : '' }))
  }

  const handleBgSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    try {
      await api.uploadGlobalBackgroundImage(file)
      applyGlobalBg(`/api/settings/background-image?t=${Date.now()}`)
    } catch (err: any) {
      setError('背景图上传失败：' + (err?.message || err))
    } finally {
      if (bgInputRef.current) bgInputRef.current.value = ''
    }
  }

  const handleBgClear = async () => {
    try {
      await api.clearGlobalBackgroundImage()
      applyGlobalBg('')
    } catch (err: any) {
      setError('清除背景图失败：' + (err?.message || err))
    }
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
      <GlobalBackground />
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

            {/* 0. 外观（常用设置，始终存在） */}
            <div className="settings-section">
              <h3>外观</h3>
              <div className="form-hint" style={{ marginBottom: 12 }}>
                设置全局背景图片，所有页面共享（固定全屏显示，带暗色遮罩保证可读性）。
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                <div
                  onClick={() => bgInputRef.current?.click()}
                  style={{
                    width: 120, height: 72, borderRadius: 8, cursor: 'pointer',
                    border: '1px dashed var(--color-border)',
                    background: bgPreview
                      ? `center/cover no-repeat url("${bgPreview}")`
                      : 'var(--color-bg-input)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    color: 'var(--color-text-muted)', fontSize: 12, textAlign: 'center',
                    overflow: 'hidden',
                  }}
                  title="点击上传 / 更换背景图片"
                >
                  {!bgPreview && '点击上传背景图'}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <button className="btn btn-sm" onClick={() => bgInputRef.current?.click()}>
                    {bgPreview ? '🖼 更换背景图' : '🖼 上传背景图'}
                  </button>
                  {bgPreview && (
                    <button className="btn btn-sm" style={{ color: 'var(--color-accent-danger)' }}
                      onClick={handleBgClear}>
                      🗑 清除背景图
                    </button>
                  )}
                  <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>支持 png/jpg/webp/gif，≤15MB</span>
                </div>
                <input
                  ref={bgInputRef}
                  type="file"
                  accept="image/png,image/jpeg,image/webp,image/gif"
                  style={{ display: 'none' }}
                  onChange={handleBgSelect}
                />
              </div>
            </div>

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

            {/* 5. Provider 管理 */}
            <div className="settings-section">
              <h3>Provider 管理</h3>
              <div className="form-hint" style={{ marginBottom: 12 }}>
                配置多个 LLM Provider，对话时可切换。openai 类型兼容 DeepSeek/SiliconFlow/Ollama；anthropic 类型使用 Claude API。
              </div>
              {settings.providers?.map((p, i) => (
                <div key={i} style={{
                  display: 'flex', gap: 8, marginBottom: 8, padding: 10,
                  background: 'var(--color-bg-input)', borderRadius: 8,
                  border: '1px solid var(--color-border)', flexWrap: 'wrap'
                }}>
                  <input className="form-input" style={{ width: 100 }} value={p.name}
                    onChange={e => {
                      const next = [...settings.providers]
                      next[i] = { ...next[i], name: e.target.value }
                      update('providers', next)
                    }} placeholder="名称" />
                  <select className="form-input" style={{ width: 90 }} value={p.type}
                    onChange={e => {
                      const next = [...settings.providers]
                      next[i] = { ...next[i], type: e.target.value as 'openai' | 'anthropic' }
                      update('providers', next)
                    }}>
                    <option value="openai">OpenAI</option>
                    <option value="anthropic">Anthropic</option>
                  </select>
                  <input className="form-input" style={{ flex: 1, minWidth: 180 }} value={p.api_base}
                    onChange={e => {
                      const next = [...settings.providers]
                      next[i] = { ...next[i], api_base: e.target.value }
                      update('providers', next)
                    }} placeholder="API 端点" />
                  <input className="form-input" style={{ width: 130 }} value={p.model}
                    onChange={e => {
                      const next = [...settings.providers]
                      next[i] = { ...next[i], model: e.target.value }
                      update('providers', next)
                    }} placeholder="模型名" />
                  <input className="form-input" type="password" style={{ width: 140 }}
                    value={p.api_key} placeholder="API Key"
                    onChange={e => {
                      const next = [...settings.providers]
                      next[i] = { ...next[i], api_key: e.target.value }
                      update('providers', next)
                    }} />
                  <button className="btn btn-sm" style={{ color: 'var(--color-accent-danger)' }}
                    onClick={() => {
                      const next = settings.providers.filter((_, j) => j !== i)
                      update('providers', next)
                    }}>✕</button>
                </div>
              ))}
              <button className="btn btn-sm"
                onClick={() => {
                  const next = [...(settings.providers || []), { name: '', type: 'openai' as const, api_base: '', api_key: '', model: '' }]
                  update('providers', next)
                }}
              >+ 添加 Provider</button>
              <div className="form-row" style={{ marginTop: 12 }}>
                <div className="form-group">
                  <label className="form-label">默认 Provider（前台对话）</label>
                  <select className="form-input" value={settings.default_provider}
                    onChange={e => update('default_provider', e.target.value)}>
                    <option value="">自动（第一个）</option>
                    {settings.providers?.map(p => <option key={p.name} value={p.name}>{p.name}</option>)}
                  </select>
                </div>
                <div className="form-group">
                  <label className="form-label">后台 Provider（蒸馏/记��提取）</label>
                  <select className="form-input" value={settings.background_provider}
                    onChange={e => update('background_provider', e.target.value)}>
                    <option value="">自动（同默认）</option>
                    {settings.providers?.map(p => <option key={p.name} value={p.name}>{p.name}</option>)}
                  </select>
                  <div className="form-hint">推荐用便宜模型（如 Ollama/qwen 或硅基流动免费模型）处理后台任务</div>
                </div>
              </div>
            </div>

            {/* 6. Persona 管理 */}
            <div className="settings-section">
              <h3>工作模式 (Persona)</h3>
              <div className="form-hint" style={{ marginBottom: 12 }}>
                不同的工作模式定义不同的助手语气和回答风格。对话中可选择，不选则使用默认模式。
              </div>
              {settings.personas?.map((p, i) => (
                <div key={i} style={{
                  display: 'flex', gap: 8, alignItems: 'flex-start', marginBottom: 8,
                  padding: 10, background: 'var(--color-bg-input)', borderRadius: 8,
                  border: '1px solid var(--color-border)',
                }}>
                  <div style={{ flex: 1 }}>
                    <input className="form-input" style={{ fontWeight: 600, marginBottom: 4 }} value={p.name}
                      onChange={e => {
                        const next = [...settings.personas]
                        next[i] = { ...next[i], name: e.target.value }
                        update('personas', next)
                      }} placeholder="模式名称" />
                    <textarea className="form-input" rows={3} value={p.system_prompt}
                      onChange={e => {
                        const next = [...settings.personas]
                        next[i] = { ...next[i], system_prompt: e.target.value }
                        update('personas', next)
                      }} placeholder="系统提示词（定义语气和风格）" />
                    <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                      <input className="form-input" style={{ width: 100 }} value={p.tone}
                        onChange={e => {
                          const next = [...settings.personas]
                          next[i] = { ...next[i], tone: e.target.value }
                          update('personas', next)
                        }} placeholder="语气" />
                      <input className="form-input" style={{ width: 120 }} value={p.style}
                        onChange={e => {
                          const next = [...settings.personas]
                          next[i] = { ...next[i], style: e.target.value }
                          update('personas', next)
                        }} placeholder="风格" />
                    </div>
                  </div>
                  <button className="btn btn-sm" style={{ color: 'var(--color-accent-danger)' }}
                    onClick={() => {
                      const next = settings.personas.filter((_, j) => j !== i)
                      update('personas', next)
                    }}>✕</button>
                </div>
              ))}
              <button className="btn btn-sm"
                onClick={() => {
                  const next = [...(settings.personas || []), { name: '', system_prompt: '', tone: '', style: '' }]
                  update('personas', next)
                }}
              >+ 添加 Persona</button>
            </div>

            {/* 7. 高级 */}
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
