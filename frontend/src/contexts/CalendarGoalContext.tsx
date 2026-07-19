import { createContext, useContext } from 'react'
import type { CalendarData, CalendarDay, Goal, GoalStats } from '../shared/api'

export type GoalDisplayField = 'target_value' | 'daily_target' | 'current_value'

export interface GoalActiveInfo {
  goalId: number
  color: string
  title: string
}

export interface GoalMarkInfo {
  goalId: number
  color: string
  label: string
  title: string
}

export interface GoalAmountInfo {
  goalId: number
  color: string
  amount: number
}

export interface WeekDayInfo {
  date: Date
  key: string
  day: number
  weekday: string
}

export interface CalendarGoalContextType {
  currentDate: Date
  setCurrentDate: (d: Date) => void
  selectedDate: Date
  setSelectedDate: (d: Date) => void
  todayStr: string
  selectedDayKey: string
  weekDays: WeekDayInfo[]
  data: CalendarData | null
  loading: boolean
  goals: Goal[]
  goalStats: Record<number, GoalStats>
  goalDateMap: Map<string, GoalMarkInfo[]>
  goalAmountMap: Map<string, GoalAmountInfo[]>
  goalActiveMap: Map<string, GoalActiveInfo[]>
  selectedDayData: CalendarDay | null
  prevWeek: () => void
  nextWeek: () => void
  goToday: () => void
  loadCalendar: () => void
  loadGoals: () => void
  updateGoalBalance: (goalId: number, newValue: number) => Promise<void>
  updateGoalEndDate: (goalId: number, newEndDate: string) => Promise<void>
  goalDisplayField: GoalDisplayField
  setGoalDisplayField: (f: GoalDisplayField) => void
  // Goal CRUD
  showGoalModal: boolean
  setShowGoalModal: (v: boolean) => void
  editingGoal: Goal | null
  setEditingGoal: (g: Goal | null) => void
  showGoalDelete: Goal | null
  setShowGoalDelete: (g: Goal | null) => void
  handleGoalSubmit: (form: { title: string; start_value: string; target_value: string; daily_target: string; current_value?: string }) => Promise<void>
  handleGoalDelete: () => Promise<void>
}

export const CalendarGoalContext = createContext<CalendarGoalContextType | null>(null)

export function useCalendarGoal() {
  const ctx = useContext(CalendarGoalContext)
  if (!ctx) throw new Error('useCalendarGoal must be used within AppLayout')
  return ctx
}
