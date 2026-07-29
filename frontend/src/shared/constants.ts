/** 目标颜色序列 */
export const GOAL_COLORS = ['#50fa7b', '#8be9fd', '#ff79c6', '#f1fa8c', '#bd93f9']

/** 目标默认颜色 */
export const GOAL_DEFAULT_COLOR = '#bd93f9'

/** 获取目标的指定颜色（循环取色） */
export function getGoalColor(index: number): string {
  return GOAL_COLORS[index % GOAL_COLORS.length]
}
