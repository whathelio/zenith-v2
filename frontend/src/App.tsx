import { Routes, Route, Navigate } from 'react-router-dom'
import AppLayout from './components/AppLayout'
import DashboardView from './features/DashboardView'
import ChatView from './features/ChatView'
import SettingsView from './features/SettingsView'
import CalendarView from './features/CalendarView'
import GoalsView from './features/GoalsView'
import LibraryView from './features/LibraryView'
import KnowledgeView from './features/KnowledgeView'

export default function App() {
  return (
    <Routes>
      {/* 主页面组 — 共享左侧面板布局（含对话页） */}
      <Route element={<AppLayout />}>
        <Route path="/" element={<DashboardView />} />
        <Route path="/chat" element={<ChatView />} />
        <Route path="/chat/:convId" element={<ChatView />} />
        <Route path="/calendar" element={<CalendarView />} />
        <Route path="/schedules" element={<Navigate to="/calendar" replace />} />
        <Route path="/library" element={<LibraryView />} />
        <Route path="/knowledge" element={<KnowledgeView />} />
        <Route path="/notes" element={<Navigate to="/library?tab=notes" replace />} />
        <Route path="/memories" element={<Navigate to="/library?tab=memories" replace />} />
        <Route path="/skills" element={<Navigate to="/library?tab=skills" replace />} />
        <Route path="/goals" element={<GoalsView />} />
      </Route>

      {/* 独立页面 — 自己管理布局 */}
      <Route path="/settings" element={<SettingsView />} />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
