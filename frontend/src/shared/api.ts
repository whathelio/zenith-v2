/* Zenith v2 API Client */
const BASE = '/api'

interface Conversation {
  id: string
  title: string
  persona_name?: string
  background?: string
  source_type?: string
  source_id?: string
  created_at: string
  updated_at: string
  msg_count: number
  messages?: Message[]
}

interface Message {
  id: number
  conversation_id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  thinking?: string
  created_at: string
}

interface Schedule {
  id: number
  title: string
  description: string
  start_time: string
  end_time: string
  location: string
  status: string
  priority: string
  importance: number
  category: string
  impact: string
  country: string
  remind_before: number
  goal_id: number | null
  recurrence: string
  parent_id: number | null
  source: string
  confirmed_at: string | null
  created_at: string
  /** 外部财经事件（来自金十日历缓存，非本地日程行） */
  is_event?: boolean
  is_external?: boolean
  /** 前值/预期/实际 三值 + 利多利空 */
  finance?: {
    previous: string
    consensus: string
    actual: string
    revised: string
    affect_txt: string
  }
}

interface Note {
  id: number
  title: string
  content: string
  tags: string
  status: string
  source: string
  stage: string
  recorded_at: string
  distilled_at: string
  distilled_into: string
  created_at: string
  updated_at: string
}

interface Memory {
  id: number
  type: string
  content: string
  importance: number
  keywords: string
  source_conv_id: string
  recorded_at: string
  distilled_from: number | null
  created_at: string
}

interface ConversationSummary {
  conversation_id: string
  message_count: number
  title: string
  summary: string
  key_decisions: string[]
  experiences: { content: string; importance: number; keywords: string }[]
  knowledge: string[]
  action_items: string[]
  tags: string[]
  experiences_saved: number
}

interface DistillResult {
  success?: boolean
  txt_content: string
  txt_path?: string
  saved_count?: number
  skip_count?: number
  result?: string
  headline?: string
  conv_count?: number
  schedule_count?: number
  note_count?: number
  memory_count?: number
  // conv distill fields
  title?: string
  summary?: string
  key_decisions?: string[]
  experiences?: { content: string; importance: number; keywords: string }[]
  knowledge?: string[]
  action_items?: string[]
  tags?: string[]
  // schedule distill fields
  schedule_summary?: string
  patterns?: string[]
  gaps?: string[]
  suggestions?: string[]
  important_upcoming?: string[]
  categories?: Record<string, number>
  priority_distribution?: Record<string, number>
  // memory distill fields
  memory_summary?: string
  core_insights?: string[]
  merged_items?: { original_ids?: number[]; merged_content: string; type: string; importance: number }[]
  outdated?: string[]
  growth_stats?: { total: number; by_type: Record<string, number> }
  // all distill fields
  overall_summary?: string
  cross_insights?: string[]
  conv_distill?: Record<string, any>
  schedule_distill?: Record<string, any>
  memory_distill?: Record<string, any>
}

interface DistillFile {
  name: string
  path: string
  size: number
  modified: string
}

interface PeriodicSummary {
  id: number
  period_type: string
  period_key: string
  headline: string
  content: string
  summary: string
  created_at: string
  updated_at: string
}

interface Goal {
  id: number
  title: string
  start_value: number
  target_value: number
  current_value: number
  daily_target: number
  strategy: string
  status: string
  start_date: string
  end_date: string
  active_days?: string[]
  created_at: string
  updated_at: string
}

interface GoalStats {
  progress: number
  days_total: number
  days_passed: number
  daily_return: number
  remaining: number
  on_track: boolean
  schedule_count: number
  completed_schedule_count: number
}

interface CalendarTemplate {
  label: string
  title: string
  category: string
  importance: number
  remind_before: number
  default_time: string
}

interface CalendarWeek {
  monday: string
  sunday: string
  events: Schedule[]
}

interface CalendarMonth {
  month: string
  date_counts: Record<string, number>
}

interface Proposal {
  type: 'schedule' | 'note' | 'action'
  id: number
  title: string
  time?: string
  description?: string
  content?: string
  tags?: string
  created_at: string
}

interface Settings {
  api_base: string
  api_key: string
  model: string
  temperature: number
  max_tokens: number
  system_prompt: string
  context_compress_threshold: number
  memory_extract_interval: number
  providers: Provider[]
  default_provider: string
  background_provider: string
  personas: Persona[]
  socratic_mode: boolean
  background_image: string
}

interface Provider {
  name: string
  type: 'openai' | 'anthropic'
  api_base: string
  api_key: string
  model: string
}

interface Persona {
  name: string
  system_prompt: string
  tone: string
  style: string
}

interface AnalysisDocument {
  id: number
  filename: string
  original_content?: string
  analysis_text?: string
  schedule_ids: string
  export_text?: string
  created_at: string
  analysis_len?: number
}

interface CalendarDay {
  schedules: { id: number; title: string; start_time: string; end_time: string; status: string; priority: string; location: string }[]
  notes: { id: number; title: string; tags: string }[]
  conversations: { id: string; title: string; msg_count: number }[]
  memories: { id: number; type: string; content: string; importance: number }[]
  analyses: { id: number; filename: string }[]
}

interface CalendarData {
  year: number
  month: number
  days: Record<string, CalendarDay>
  summary: { schedules: number; notes: number; conversations: number; memories: number; analyses: number }
}

interface CreatedSchedule {
  schedule_id: number
  title: string
  start_time: string
  end_time: string
  priority: string
}

// Market Analysis Types
interface MarketIndicator {
  id: number
  indicator: string
  value: string
  change_pct: string
  source: string
  created_at: string
}

interface CFTCPosition {
  contract: string
  section: string
  category: string
  report_date: string
  net: number
  net_z: number
  net_ww: number
  net_ww_z: number
  long: number
  long_z: number
  long_ww: number
  long_ww_z: number
  short: number
  short_z: number
  short_ww: number
  short_ww_z: number
  flow_state: string
  crowding: string
  divergence: boolean
  price_chg: number | null
  price_start: number | null
  price_end: number | null
}

interface MarketReport {
  id: number
  report_date: string
  gold_price: string
  factor_data: string
  events_overdue: string
  events_upcoming: string
  analysis_text: string
  daily_advice: string
  weekly_advice: string
  markdown_text?: string
  created_at: string
}

interface MarketPrediction {
  id: number
  report_date: string
  event_name: string
  predicted_direction: string
  predicted_strength: number
  predicted_range: string
  actual_direction: string
  actual_change_pct: string
  actual_close: string
  verified: string
  verified_at: string | null
  created_at: string
}

interface HitRateResult {
  total: number
  hit: number
  miss: number
  hit_rate: number
}

interface Skill {
  id: number
  name: string
  trigger_scene: string
  steps: string[]
  tags: string[]
  usage_count: number
  confirmed_by_user: number
  source_conv_id: string
  created_at: string
}

interface SkillSuggestion {
  ready: boolean
  feedback_count: number
  min_required: number
  analysis?: string
  current_steps?: string[]
  improved_steps?: string[]
  reason?: string
}

interface McpServer {
  name: string
  url?: string
  enabled: boolean
  serverUrl?: string
  command?: string
  args?: string[]
  headers?: Record<string, string>
  description?: string
  disabled?: boolean
}

interface ImportSkillResult extends Partial<ModuleSkill> {
  success: boolean
  id: number
  source: string
  name: string
  mcp_required: string[]
}

interface ImportMcpResult {
  success: boolean
  server: McpServer
  replaced: boolean
  detection: { type: string; confident: boolean; note?: string }
  source: string
}

interface SkillFileEntry {
  name: string
  directory: string
  description: string
  body: string
  mcp_required: string[]
  has_scripts: boolean
  file_size: number
  last_modified: string
}

interface ModulesStats {
  skills: number
  mcp_servers: number
  mcp_enabled: number
  skill_files?: number
}

interface ModuleSkill {
  id: number
  name: string
  trigger_scene: string
  steps: string[]
  mcp_required?: string[]
  tags: string[]
  usage_count: number
  confirmed_by_user: number
  source_conv_id: string
  created_at: string
  importance: number
  content: string
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(BASE + url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error || err.detail || 'Request failed')
  }
  return res.json()
}

export const api = {
  // Conversations
  listConversations: () => request<Conversation[]>('/conversations'),
  getConversation: (id: string) => request<Conversation>(`/conversations/${id}`),
  getConvTraces: (convId: string) =>
    request<any[]>(`/audit/conv-traces?conv_id=${encodeURIComponent(convId)}&limit=200`),
  getTraceHistory: (params: { keyword?: string; trace_type?: string; conv_id?: string; limit?: number; offset?: number } = {}) => {
    const qs = Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== '')
      .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&')
    return request<any[]>(`/audit/trace-history${qs ? '?' + qs : ''}`)
  },
  createConversation: (title?: string, personaName?: string) =>
    request<Conversation>('/conversations', {
      method: 'POST',
      body: JSON.stringify({ title: title || 'New Chat', persona_name: personaName || '' }),
    }),
  deleteConversation: (id: string) =>
    request<{ success: boolean }>(`/conversations/${id}`, { method: 'DELETE' }),
  renameConversation: (id: string, title: string, personaName?: string, background?: string) => {
    const body: Record<string, string> = { title }
    if (personaName !== undefined) body.persona_name = personaName
    if (background !== undefined) body.background = background
    return request<{ success: boolean; title: string }>(`/conversations/${id}`, {
      method: 'PUT', body: JSON.stringify(body),
    })
  },
  uploadBackgroundImage: async (id: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch(BASE + `/conversations/${id}/background-image`, {
      method: 'POST', body: fd,
    })
    if (!res.ok) throw new Error('上传失败: ' + res.status)
    return res.json()
  },
  clearBackgroundImage: (id: string) =>
    request<{ success: boolean }>(`/conversations/${id}/background-image`, { method: 'DELETE' }),

  // 全局背景图片（设置页「外观」常用设置）
  uploadGlobalBackgroundImage: async (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch(BASE + '/settings/background-image', {
      method: 'POST', body: fd,
    })
    if (!res.ok) throw new Error('上传失败: ' + res.status)
    return res.json()
  },
  clearGlobalBackgroundImage: () =>
    request<{ success: boolean }>('/settings/background-image', { method: 'DELETE' }),
  // 对话独立背景（per-conversation）— 统计与批量清除，用于统一回落到全局背景
  countConversationBackgrounds: () =>
    request<{ count: number }>('/conversations/backgrounds'),
  clearAllConversationBackgrounds: () =>
    request<{ success: boolean; cleared: number }>('/conversations/backgrounds', { method: 'DELETE' }),
  // 学习对话工厂 — 从笔记/记忆/文档一键创建学习对话
  startLearning: (sourceType: 'note' | 'memory' | 'document', sourceId: number) =>
    request<{ success: boolean; conversation_id: string; title: string }>('/learning/start', {
      method: 'POST',
      body: JSON.stringify({ source_type: sourceType, source_id: sourceId }),
    }),

  // Chat (SSE via POST fetch)
  chat: async (message: string, conversationId: string, signal?: AbortSignal, providerName?: string) => {
    const body: Record<string, string> = { message, conversation_id: conversationId }
    if (providerName) body.provider_name = providerName
    const res = await fetch(BASE + '/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    })
    return res
  },

  // 重新生成最后一条 AI 回复（返回 SSE 流）
  regenerate: async (conversationId: string, providerName?: string, signal?: AbortSignal) => {
    const body: Record<string, string> = { conversation_id: conversationId }
    if (providerName) body.provider_name = providerName
    const res = await fetch(BASE + '/chat/regenerate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    })
    return res
  },

  // 编辑消息并重新生成（user 消息返回 SSE 流；assistant 消息返回 JSON）
  editMessage: async (conversationId: string, msgId: number, content: string, providerName?: string) => {
    const body: Record<string, any> = { conversation_id: conversationId, msg_id: msgId, content }
    if (providerName) body.provider_name = providerName
    const res = await fetch(BASE + '/chat/edit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    return res
  },

  // 删除消息及其后所有消息
  deleteMessage: (msgId: number) =>
    request<{ success: boolean; deleted: number }>(`/chat/messages/${msgId}`, { method: 'DELETE' }),

  // 停止当前对话生成
  stopChat: (conversationId: string) =>
    request<{ success: boolean; stopped: boolean }>('/chat/stop', {
      method: 'POST',
      body: JSON.stringify({ conversation_id: conversationId }),
    }),

  // Schedules
  listSchedules: (status = '') =>
    request<Schedule[]>(`/schedules?status=${encodeURIComponent(status)}`),
  createSchedule: (data: Partial<Schedule>) =>
    request<Schedule>('/schedules', { method: 'POST', body: JSON.stringify(data) }),
  updateSchedule: (id: number, data: Partial<Schedule>) =>
    request<{ success: boolean }>(`/schedules/${id}`, {
      method: 'PUT', body: JSON.stringify(data),
    }),
  deleteSchedule: (id: number) =>
    request<{ success: boolean }>(`/schedules/${id}`, { method: 'DELETE' }),

  // Calendar
  getCalendarWeek: (date = '') =>
    request<CalendarWeek>(`/calendar/week?date=${encodeURIComponent(date)}`),
  getCalendarMonth: (month = '') =>
    request<CalendarMonth>(`/calendar/month?month=${encodeURIComponent(month)}`),
  getCalendarTemplates: () =>
    request<CalendarTemplate[]>('/calendar/templates'),

  // Goals
  listGoals: (status = '') =>
    request<Goal[]>(`/goals?status=${encodeURIComponent(status)}`),
  getGoal: (id: number) =>
    request<Goal>(`/goals/${id}`),
  createGoal: (data: Partial<Goal>) =>
    request<Goal>('/goals', { method: 'POST', body: JSON.stringify(data) }),
  updateGoal: (id: number, data: Partial<Goal>) =>
    request<{ success: boolean }>(`/goals/${id}`, {
      method: 'PUT', body: JSON.stringify(data),
    }),
  deleteGoal: (id: number) =>
    request<{ success: boolean }>(`/goals/${id}`, { method: 'DELETE' }),
  getGoalStats: (id: number) =>
    request<GoalStats>(`/goals/${id}/stats`),
  listGoalSchedules: (id: number, status = '') =>
    request<Schedule[]>(`/goals/${id}/schedules?status=${encodeURIComponent(status)}`),

  // Notes
  listNotes: (search = '') =>
    request<Note[]>(`/notes?search=${encodeURIComponent(search)}`),
  createNote: (data: Partial<Note>) =>
    request<Note>('/notes', { method: 'POST', body: JSON.stringify(data) }),
  updateNote: (id: number, data: Partial<Note>) =>
    request<{ success: boolean }>(`/notes/${id}`, {
      method: 'PUT', body: JSON.stringify(data),
    }),
  deleteNote: (id: number) =>
    request<{ success: boolean }>(`/notes/${id}`, { method: 'DELETE' }),
  distillNote: (id: number) =>
    request<{ success: boolean; result: string; created_ids: Record<string, number> }>(`/notes/${id}/distill`, { method: 'POST' }),

  // Proposals
  getProposals: () => request<Proposal[]>('/proposals'),
  confirmProposal: (type: string, id: number) =>
    request<{ success: boolean }>('/proposals/confirm', {
      method: 'POST', body: JSON.stringify({ type, id }),
    }),
  rejectProposal: (type: string, id: number) =>
    request<{ success: boolean }>('/proposals/reject', {
      method: 'POST', body: JSON.stringify({ type, id }),
    }),
  modifyProposal: (type: string, id: number, changes: Record<string, string>) =>
    request<{ success: boolean }>('/proposals/modify', {
      method: 'POST', body: JSON.stringify({ type, id, changes }),
    }),

  // Memories
  listMemories: (type = '', search = '') =>
    request<Memory[]>(`/memories?type_=${type}&search=${encodeURIComponent(search)}`),
  updateMemory: (id: number, data: Partial<Memory>) =>
    request<{ success: boolean; memory: Memory }>(`/memories/${id}`, {
      method: 'PUT', body: JSON.stringify(data),
    }),
  deleteMemory: (id: number) =>
    request<{ success: boolean }>(`/memories/${id}`, { method: 'DELETE' }),

  // Conversation Summarize
  summarizeConversation: (convId: string) =>
    request<ConversationSummary>(`/conversations/${convId}/summarize`, { method: 'POST' }),

  // Distill (Unified Distillation Module)
  distillConversation: (convId: string, saveTxt = true) =>
    request<DistillResult>(`/distill/conversation/${convId}?save_txt=${saveTxt}`, { method: 'POST' }),

  distillSchedules: (params: { status?: string; date_from?: string; date_to?: string; save_txt?: boolean } = {}) => {
    const qs = Object.entries(params).filter(([_, v]) => v).map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&')
    return request<DistillResult>(`/distill/schedules${qs ? '?' + qs : ''}`, { method: 'POST' })
  },

  distillMemories: (params: { type_?: string; search?: string; save_txt?: boolean } = {}) => {
    const qs = Object.entries(params).filter(([_, v]) => v).map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&')
    return request<DistillResult>(`/distill/memories${qs ? '?' + qs : ''}`, { method: 'POST' })
  },

  distillAll: (params: { conv_id?: string; schedule_status?: string; memory_type?: string; save_txt?: boolean } = {}) => {
    const qs = Object.entries(params).filter(([_, v]) => v).map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&')
    return request<DistillResult>(`/distill/all${qs ? '?' + qs : ''}`, { method: 'POST' })
  },

  distillDaily: (date: string, saveTxt = true) =>
    request<DistillResult>(`/distill/daily/${date}?save_txt=${saveTxt}`, { method: 'POST' }),

  distillWeekly: (weekStart: string, saveTxt = true) =>
    request<DistillResult>(`/distill/weekly/${weekStart}?save_txt=${saveTxt}`, { method: 'POST' }),

  // Periodic Summaries (日/周/月/年总结)
  listSummaries: (periodType = '') =>
    request<PeriodicSummary[]>(`/summaries${periodType ? `?period_type=${periodType}` : ''}`),
  getSummary: (periodType: string, periodKey: string) =>
    request<PeriodicSummary>(`/summaries/${periodType}/${periodKey}`),
  generateSummary: (periodType: string, periodKey: string) =>
    request<PeriodicSummary>(`/summaries/${periodType}/${periodKey}/generate`, { method: 'POST' }),

  listDistillFiles: () =>
    request<{ files: DistillFile[]; count: number }>('/distill/files'),

  downloadDistillFile: (filename: string) => {
    const link = document.createElement('a')
    link.href = BASE + `/distill/file/${encodeURIComponent(filename)}`
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  },

  // Settings
  getSettings: () => request<Settings>('/settings'),
  updateSettings: (data: Partial<Settings>) =>
    request<{ success: boolean }>('/settings', {
      method: 'PUT', body: JSON.stringify(data),
    }),

  // Code
  runCode: (code: string, timeout = 30) =>
    request<{ success: boolean; output: string }>('/code/run', {
      method: 'POST', body: JSON.stringify({ code, timeout }),
    }),

  // File Analysis
  analyzeFile: async (file: File): Promise<Response> => {
    const formData = new FormData()
    formData.append('file', file)
    const res = await fetch(BASE + '/analyze-file', {
      method: 'POST',
      body: formData,
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: res.statusText }))
      throw new Error(err.error || 'Upload failed')
    }
    return res
  },

  listAnalysisDocuments: () =>
    request<AnalysisDocument[]>('/analysis-documents'),

  getAnalysisDocument: (id: number) =>
    request<AnalysisDocument>(`/analysis-documents/${id}`),

  deleteAnalysisDocument: (id: number) =>
    request<{ success: boolean }>(`/analysis-documents/${id}`, { method: 'DELETE' }),

  // Calendar Dashboard
  getCalendar: (year: number, month: number) =>
    request<CalendarData>(`/calendar?year=${year}&month=${month}`),

  // Open URL in system default browser
  openUrl: (url: string) =>
    request<{ success: boolean }>('/open-url', {
      method: 'POST', body: JSON.stringify({ url }),
    }),

  downloadAnalysisDocument: (id: number, filename: string) => {
    const link = document.createElement('a')
    link.href = BASE + `/analysis-documents/${id}/download`
    link.download = `分析报告_${filename.replace(/\.txt$/i, '')}.txt`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  },

  // Market Analysis
  getMarketStatus: () =>
    request<{ indicators: MarketIndicator[]; latest_report: { id: number | null; report_date: string | null; gold_price: string; daily_advice: string } | null }>('/market/status'),

  getCFTCData: () =>
    request<{ data: CFTCPosition[]; report_date: string; freshness: string }>('/market/cftc'),

  getCFTCGold: () =>
    request<any>('/market/cftc/gold'),

  getMarketReports: (limit = 30) =>
    request<MarketReport[]>(`/market/reports?limit=${limit}`),

  getLatestReport: () =>
    request<MarketReport>('/market/reports/latest'),

  getMarketReport: (id: number) =>
    request<MarketReport>(`/market/reports/${id}`),

  runAnalysis: () =>
    request<{ success: boolean; report_id: number; report_date: string }>('/market/run-analysis', { method: 'POST' }),

  refreshMarketData: () =>
    request<{ success: boolean; cftc: any; macro_count: number }>('/market/refresh-data'),

  getPredictions: (date = '', verified = '') =>
    request<MarketPrediction[]>(`/market/predictions?date=${date}&verified=${verified}`),

  getHitRate: (days = 30) =>
    request<HitRateResult>(`/market/predictions/hit-rate?days=${days}`),

  verifyPredictions: () =>
    request<{ success: boolean; total: number; hit: number; miss: number; hit_rate: number; details: any[] }>('/market/predictions/verify', { method: 'POST' }),

  // Skills (from memories via /api/modules/skills)
  listSkills: (search = '') =>
    request<ModuleSkill[]>(`/modules/skills?search=${encodeURIComponent(search)}`),
  deleteSkill: (id: number) =>
    request<{ success: boolean }>(`/modules/skills/${id}`, { method: 'DELETE' }),
  importSkillFile: (filePath: string) =>
    request<ImportSkillResult>('/modules/skills/import-file', {
      method: 'POST',
      body: JSON.stringify({ file_path: filePath }),
    }),
  listSkillFiles: (dir = '') =>
    request<{ skills_dir: string; count: number; skills: SkillFileEntry[] }>(
      `/modules/skills/files${dir ? `?dir=${encodeURIComponent(dir)}` : ''}`
    ),
  importSkillsFromDir: (dir = '') =>
    request<{ scanned: number; imported: number; errors: number }>('/modules/skills/import', {
      method: 'POST',
      body: JSON.stringify({ dir }),
    }),

  // MCP Configurations
  listMcpServers: () =>
    request<{ servers: McpServer[]; count: number; enabled: number }>('/modules/mcp'),
  addMcpServer: (data: { name: string; url?: string; serverUrl?: string; command?: string; args?: string[]; enabled?: boolean; description?: string }) =>
    request<{ success: boolean; server: McpServer }>('/modules/mcp', { method: 'POST', body: JSON.stringify(data) }),
  updateMcpServer: (name: string, data: Partial<McpServer>) =>
    request<{ success: boolean }>(`/modules/mcp/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  deleteMcpServer: (name: string) =>
    request<{ success: boolean }>(`/modules/mcp/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  importMcpFile: (filePath: string, options?: { name?: string; args?: string[]; description?: string; disabled?: boolean }) =>
    request<ImportMcpResult>('/modules/mcp/import-file', {
      method: 'POST',
      body: JSON.stringify({ file_path: filePath, ...options }),
    }),

  // Modules Stats (for dashboard)
  getModulesStats: () =>
    request<ModulesStats>('/modules/stats'),

  // ── Transform API ── 记忆/笔记/行程互转
  transform: (sourceType: string, sourceId: number, targetType: string) =>
    request<{ success: boolean; source_type: string; source_id: number; target_type: string; created_id: number; created_item: any; preview: any }>(
      '/transform', { method: 'POST', body: JSON.stringify({ source_type: sourceType, source_id: sourceId, target_type: targetType }) }
    ),

  // ── Knowledge API ── 转发到外部 api_gateway
  knowledgeHealth: () => request<{ status: string; service?: string; version?: string }>('/knowledge/health'),
  knowledgeListDocs: () => request<{ docs: Array<{ item_id: number; title: string; chunks: number; source?: string }> }>('/knowledge/documents'),
  knowledgeSearch: (question: string, top_k = 5) =>
    request<{ answer?: string; error?: string }>('/knowledge/search', { method: 'POST', body: JSON.stringify({ question, top_k }) }),
  knowledgeWiki: (question: string) =>
    request<{ answer?: string; error?: string }>('/knowledge/wiki', { method: 'POST', body: JSON.stringify({ question }) }),
  knowledgeCreateTask: (type: string, payload: Record<string, any>) =>
    request<{ task_id: string; status: string }>('/knowledge/tasks', { method: 'POST', body: JSON.stringify({ type, payload }) }),
  knowledgeGetTask: (id: string) =>
    request<Record<string, any>>(`/knowledge/tasks/${id}`),
  knowledgeListTasks: (status?: string, limit = 20) =>
    request<{ tasks: any[] }>(`/knowledge/tasks${status ? `?status=${status}&limit=${limit}` : `?limit=${limit}`}`),
  knowledgeIngest: async (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch(BASE + '/knowledge/ingest', { method: 'POST', body: fd })
    const text = await res.text()
    let json: any
    try {
      json = text ? JSON.parse(text) : {}
    } catch {
      throw new Error(`上传失败：服务返回非 JSON 响应 (${res.status})`)
    }
    if (!res.ok) {
      throw new Error(json.error || `上传失败 (${res.status})`)
    }
    return json
  },
}

export type { Conversation, Message, Schedule, Note, Memory, Proposal, Settings, AnalysisDocument, CreatedSchedule, CalendarData, CalendarDay, MarketIndicator, CFTCPosition, MarketReport, MarketPrediction, HitRateResult, ConversationSummary, Goal, GoalStats, CalendarTemplate, CalendarWeek, CalendarMonth, DistillResult, DistillFile, PeriodicSummary, Skill, SkillSuggestion, ModuleSkill, McpServer, ImportSkillResult, ImportMcpResult, SkillFileEntry, ModulesStats }
