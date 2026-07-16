import { Routes, Route, Navigate } from 'react-router-dom'
import AppLayout from './components/AppLayout'
import DashboardView from './features/DashboardView'
import ChatView from './features/ChatView'
import SettingsView from './features/SettingsView'
import MemoriesView from './features/MemoriesView'
import SchedulesView from './features/SchedulesView'
import CalendarView from './features/CalendarView'
import GoalsView from './features/GoalsView'
import NotesView from './features/NotesView'
import AnalysisView from './features/AnalysisView'
import SkillsView from './features/SkillsView'

export default function App() {
  return (
    <Routes>
      {/* 主页面组 — 共享左侧面板布局 */}
      <Route element={<AppLayout />}>
        <Route path="/" element={<DashboardView />} />
        <Route path="/schedules" element={<SchedulesView />} />
        <Route path="/notes" element={<NotesView />} />
        <Route path="/memories" element={<MemoriesView />} />
        <Route path="/calendar" element={<CalendarView />} />
        <Route path="/goals" element={<GoalsView />} />
        <Route path="/analysis" element={<AnalysisView />} />
        <Route path="/skills" element={<SkillsView />} />
      </Route>

      {/* 独立页面 — 自己管理布局 */}
      <Route path="/chat" element={<ChatView />} />
      <Route path="/chat/:convId" element={<ChatView />} />
      <Route path="/settings" element={<SettingsView />} />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
