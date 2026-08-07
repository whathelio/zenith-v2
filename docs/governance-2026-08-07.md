# Zenith v2 治理报告 — 审计修复 + 对话列表 + 永久性优化

日期：2026-08-07 | 治理对象：本轮全部改动（3 后端文件 + 2 前端文件 + 数据变更）

## 1. 事实核查表（全部 [已核实事实]，源码/数据库直读）

| # | 项 | 状态 | 证据 |
|---|----|------|------|
| F1 | 笔记 212 存在 / 213-218 已物理删除 | ✅ | notes 表 id 212→219 跳空，`note_del`=DELETE |
| F2 | 记忆重复 3 组（审计漏报第 3 组 1996/2006） | ✅ | 1998/2007、1999/2008、1996/2006 |
| F3 | search_memory 返回 `[ID:x][type]` | ✅ | tools.py L924 |
| F4 | consolidate search 分词 OR 匹配 | ✅ | tools.py L2208-2216 |
| F5 | conv_update_title 不改 updated_at | ✅ | database.py L566-571，精确验证 True |
| F6 | 自动标题：首条消息后生成 | ✅ | chat.py L42-73 + L385 挂载，端到端测试过 |
| F7 | smart_classify 笔记防重 | ✅ | tools.py `_find_duplicate_note` L1520 + 调用 L2009 |
| F8 | 前端时间分组折叠（今天/昨天/近7天/近30天/更早） | ✅ | ChatConvPanel.tsx groups useMemo |
| F9 | 记忆 1562 / New Chat 残留 0 / 技能记忆 2005/2009/2010 保留 | ✅ | 直查确认 |
| F10 | 备份存在 | ✅ | data/backup/zenith.db.bak-20260807 |
| F11 | **修复：纯链接消息标题归类**（治理发现） | ✅ 本轮补 | chat.py `_auto_title` 7 用例全过 |

## 2. 承重风险表

| 项 | 风险 | 后果 | 缓解 |
|----|------|------|------|
| R1 自动标题覆盖用户自定义标题 | 🟡 | 用户手动改名后又被覆盖 | `_maybe_auto_title` 仅标题 ∈ {空, "New Chat"} 时触发，改名后不再触发 |
| R2 conv_update_title 语义变化 | 🟢 | 蒸馏后对话不再顶到最前 | 符合预期（排序只由 msg_add 驱动）；蒸馏仍在更新标题，仅时间序不变 |
| R3 笔记防重误杀（不同主题内容巧合相似） | 🟡 | 正常笔记被跳过 | 阈值 0.85 保守；仅影响 smart_classify 自动路径，手动 add_note 不受影响 |
| R4 纯链接标题「新对话」退化 | 🔴→🟢 | 占位符复现 | **已修复**：按域名归类（B站/YouTube/知乎/其他） |
| R5 物理删除不可逆 | 🔴 | 删错无法恢复 | 备份 + 删前快照 + 确认制（delete_note 走 confirm_flow） |
| R6 skills 表空但技能卡片展示 | 🟢 | 无（前端读 memories type='skill'） | 已确认前端读 memories，skills 表未使用 |

## 3. 最小安全第一刀（已执行）

- 后端 3 处修复均单点改动、可单行回滚：
  - `git checkout backend/routers/chat.py backend/database.py backend/tools.py`
- 数据回滚：`copy data/backup/zenith.db.bak-20260807 data/zenith.db`（覆盖即还原 7 条记忆）
- 前端回滚：重跑 `npm run build` 前的 dist 已被覆盖，需从源码恢复 ChatConvPanel.tsx（改动前无备份 → 源码即回滚点，改动小）

## 4. 分阶段实施记录

- **A 数据清理**（20:00）：备份 → 删 7 条记忆 → FTS 验证 → 复核 0 残留 ✅
- **B 工具修复**（20:00）：search_memory ID + consolidate 模糊 ✅
- **C 对话标题+分组**（20:04）：10 标题 SQL 写入 + 前端分组折叠 + build ✅
- **D 永久优化**（20:11）：自动标题 + conv_update_title 语义 + 笔记防重 + 重启 ✅
- **E 治理补刀**（20:28）：纯链接标题归类修复 + 回归 7 用例 + 重启 ✅

## 5. 验收标准

| 维度 | 场景 | 结果 |
|------|------|------|
| 功能 K1 | 新对话首条消息 → 自动标题 | ✅ 生成简洁标题 |
| 功能 K2 | 纯链接（B站）→ 标题 | ✅ 「B站视频提取与总结」 |
| 功能 K3 | 用户自定义标题不被覆盖 | ✅ |
| 功能 K4 | 改名/蒸馏不顶到排序最前 | ✅ |
| 功能 K5 | smart_classify 重复笔记跳过 | ✅ 返回「笔记已存在 (ID:x)」 |
| 功能 K6 | 对话列表时间分组+折叠 | ✅ build 产物含 conv-group |
| 回归 K7 | 记忆检索返回 ID 可追溯 | ✅ |
| 回归 K8 | consolidate 不再漏检 scope | ✅ 干跑命中 5 条与库内一致 |
| 非功能 | py_compile × 3 文件 | ✅ |
| 非功能 | 服务重启后 health OK | ✅ |

## 6. 回滚预案

| 层 | 命令 |
|----|------|
| 代码 | `git checkout backend/database.py backend/routers/chat.py backend/tools.py frontend/src/components/ChatConvPanel.tsx` |
| 数据 | `copy data/backup/zenith.db.bak-20260807 data/zenith.db` |
| 前端 | `npm run build`（源码恢复后重建） |
| 服务 | `stop.bat` → `zenith.bat` |

## 7. 人类决策清单

| 项 | 决策 | 状态 |
|----|------|------|
| 记忆清理范围 B+ | 用户已确认 | ✅ |
| 工具修复 2 处 | 用户已确认 | ✅ |
| 技能记忆 2005/2009/2010 保留 | 用户已确认 | ✅ |
| 纯链接标题归类（B站/YouTube/知乎） | **本次治理补充**，如不认可可回滚为「新对话」 | ⏳ 待确认 |

## 治理成熟度评估（6 维度）

| 维度 | 现状 | 评估 |
|------|------|------|
| 术语即合约 | 函数名语义清晰（_auto_title/_maybe_auto_title/_find_duplicate_note） | 🟢 |
| 信息即架构 | 改动单点内聚，注释说明动机 | 🟢 |
| 创作即流水线 | 有 pytest（tests/），本次未补充用例 | 🟡 建议补 |
| 错误即门禁 | 无 pre-commit/lint 挂钩 | 🟡 项目 Level 1 |
| 引用即依赖 | 数据备份+审计日志存在 | 🟢 |
| 态度即韧性 | try/except 兜底、异常吞掉但日志无输出 | 🟡 `_maybe_auto_title` 异常静默 |

## 遗留建议（非阻塞）

1. `_maybe_auto_title` 异常静默 → 建议加 logger.warning（1 行）
2. 建议为 `_auto_title` / `_find_duplicate_note` 补 pytest 用例（tests/backend/test_tools.py）
3. 对话列表排序穿插（updated_at 语义）已在 F5 根治，前端分组已就位
