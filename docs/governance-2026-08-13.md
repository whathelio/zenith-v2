# Zenith v2「对话→记忆」模块 代码治理报告

> 日期：2026-08-13
> 触发：对话→记忆模块优化（6 文件改动）的事后治理审计
> 基准提交：`5dfea78`
> 范围：`memory_engine.py` / `llm_client.py` / `database.py` / `tools.py` / `routers/chat.py` / `app.py` / `scheduler.py` / `unified_distill.py`
> 性质：**只读审计**，本报告不含新代码改动（已完成的改动见「最小安全第一刀」的落地路径）

---

## 0. 治理成熟度评估（6 维度）

| 维度 | 现状 | 评级 |
|---|---|---|
| 1. 术语即合约 | 中文注释 + 类型标注存在，但无强制命名规范/术语词典 | 🟡 |
| 2. 信息即架构 | 模块分层清晰（database/engine/tools/routers），但「相关记忆检索」逻辑分散 4 处 | 🟡 |
| 3. 创作即流水线 | 提取→去重→入库→合并→注入链路存在，但触发时机此前有重复 | 🟡 |
| 4. 错误即门禁 | **无 lint / pre-commit / CI / mypy**，机器可执行门禁缺失 | 🔴 |
| 5. 引用即依赖 | 无 SBOM；`requirements.txt` 全量冻结（含传递依赖，可审计性差） | 🔴 |
| 6. 态度即韧性 | `sanitize_guard`（明文密钥守卫）+ `validators`（输入/输出/执行）为亮点 | 🟢 |

**治理成熟度：Level 1**（有 README + 目录结构；缺 CI/pre-commit/lint）。代码项目的 P0 维度（错误门禁、术语）恰是当前最弱项。

---

## 1. 事实核查表

状态标注：`[已核实事实]` / `[基于事实的推理]` / `[你的判断]` / `[仍需人工拍板的决策]`

| # | 结论 | 状态 | 证据 |
|---|---|---|---|
| 1 | 记忆写入唯一入口是 `database.mem_add`，带 `sanitize_guard` 明文密钥守卫 | [已核实事实] | `database.py:791-810` |
| 2 | 记忆查询核心是 `database.mem_search`（FTS5 + LIKE 兜底），被 14 处调用 | [已核实事实] | `database.py:828` + 全局 grep |
| 3 | 记忆 FTS 由 `memories_ai/ad/au` 三触发器自动同步，无需手动维护 | [已核实事实] | `database.py:217-226` |
| 4 | 相似度 `_similarity` 被 3 个模块共用（去重/consolidate/output_validator） | [已核实事实] | `memory_engine.py`、`tools.py:2218`、`output_validator.py:5` |
| 5 | 项目无 CI、无自定义 git hooks、无 pyproject/setup.cfg/flake8/mypy | [已核实事实] | `.github/` 不存在；`.git/hooks` 无自定义 |
| 6 | `requirements.txt` 缺 `pytest`，测试套件实际无法运行 | [已核实事实] | `requirements.txt` 全量冻结、无 pytest 条目 |
| 7 | 测试仅 4 个文件，覆盖相似度/去重/注入/合并/FTS/chat UI 对齐 | [已核实事实] | `tests/` 目录 4 个 `.py` |
| 8 | `_idf_cache` 仅当 `mem_consolidate` 发生合并时才更新，平时为空 → IDF 加权实际不生效 | [已核实事实] | `memory_engine.py:462`（唯一调用点） |
| 9 | `distilled_from` 字段在 `mem_add` 支持，但全项目无实际赋值 → 蒸馏来源链未打通 | [基于事实的推理] | `database.py:806` 定义；grep 无赋值 |
| 10 | 记忆整理存在两条并存路径：`mem_consolidate`（自动，阈值 0.7）与 `generate/apply_consolidate_plan`（LLM+相似度，阈值 0.85） | [已核实事实] | `memory_engine.py:315`、`tools.py:2216` |
| 11 | 本次改动后，后台任务唯一来源是 `scheduler.start_all_background_tasks()` | [已核实事实] | `app.py` lifespan；死代码已删 |

---

## 2. 承重风险表

### 2.1 承重代码（改动需格外谨慎）

| 组件 | 位置 | 承重原因 | 风险 |
|---|---|---|---|
| `mem_search` | database.py:828 | 14 处调用，注入/去重/搜索/consolidate/蒸馏共用 | 🟡 改动影响面广 |
| `mem_add` | database.py:791 | 所有记忆写入必经 + 密钥守卫 | 🟢 已加守卫，稳健 |
| `_similarity` | memory_engine.py | 3 模块共用；本次重写改变其数值语义 | 🔴 阈值语义变化 |
| `memories_fts` 触发器 | database.py:217 | 增删改自动同步，失效会导致搜索静默失效 | 🟡 静默失败 |
| `maybe_extract_memories` | chat.py:328 | 每轮对话必经，触发后台提取任务 | 🟡 并发/异常吞噬 |
| `mem_consolidate` | memory_engine.py:315 | 6 小时循环，衰减/合并误删不可逆 | 🔴 物理删除 |

### 2.2 幽灵 / 半激活组件

| 组件 | 位置 | 状态 | 建议 |
|---|---|---|---|
| `_idf_cache` / `_update_idf_weights` | memory_engine.py:288 | 半激活：仅合并后更新，平时为空 | 启动时/惰性初始化，让 IDF 真正生效 |
| `_pending_tasks` | memory_engine.py:17 | 幽灵：只 add/discard，从不等待/检查 | 用于优雅关停等待，或移除 |
| `distilled_from` 字段 | database.py 表 | 半激活：定义但从未赋值 | 打通蒸馏来源链或移除 |
| `_legacy_jaccard` | memory_engine.py | 地位下降：仅作混合兜底 | 保留（兜底有价值） |

### 2.3 重复实现

| 重复点 | 位置 | 说明 |
|---|---|---|
| 记忆整理两条路径 | `mem_consolidate` vs `generate_consolidate_plan` | 阈值不同（0.7/0.85）、触发不同（自动/手动）、结果不同（合并 vs 删除） |
| 相关记忆检索 4 处 | `_retrieve_related_memories`、`build_memory_injection`、`_distill_raw_note`、`_build_skill_injection` | 均「关键词→mem_search→补充」，各自实现 |

---

## 3. 最小安全第一刀（已完成的改动如何落地）

> 本次优化**已经完成**。最小安全动作是「补齐回滚路径 + 补测试」，而不是继续加码改动。

1. **先提交、后验证**：当前 7 个文件未提交。最小动作 = 分两个 commit 落地：
   - Commit A（记忆优化核心）：`memory_engine.py`、`llm_client.py`、`database.py`、`routers/chat.py`、`app.py`、`tools.py`
   - Commit B（若需）：`confirm_flow.py`、`tools.py` 其余部分（前次会话遗留，非本主题）
2. **补测试**：为本次改动的 3 个新行为各补一条测试（见「验收标准」K1–K3）。
3. **不继续改代码**：收敛重复实现、补 IDF 初始化等属「分阶段实施」的后续阶段，不在第一刀内。

红队自检：若相似度重写导致构建/页面异常 → `git checkout backend/memory_engine.py` 即回滚；若 schema 迁移异常 → 迁移是纯加列（`ALTER TABLE ADD COLUMN`），对旧库幂等，回滚只需删除 `last_touched_at` 列或恢复备份 `data/zenith.db`。

---

## 4. 分阶段实施（后续改进，非本次必做）

| 阶段 | 动作 | 验证 |
|---|---|---|
| A（质量门禁） | 引入 `pyproject.toml` + `ruff`/`flake8` + `pre-commit`，补 `pytest` 到 dev 依赖 | `pre-commit run --all-files` 通过 |
| B（IDF 生效） | `_update_idf_weights` 改为启动时/记忆数达阈值时惰性初始化 | 相似度在 IDF 权重下区分度提升 |
| C（收敛重复） | 合并两处记忆整理路径；抽取统一的 `retrieve_related_memories` | 单一入口 + 阈值统一 |
| D（来源链） | 打通 `distilled_from`，记录记忆的蒸馏来源 | 记忆可溯源 |
| E（优雅关停） | 用 `_pending_tasks` 在 lifespan 关闭时 `await` 未完成任务 | 退出时无丢失提取 |

---

## 5. 验收标准

### 功能（本次改动）
- **K1** `flush_conversation_memories`：删除对话后，不足 interval 的残余文本被提取、无丢失。
- **K2** `_retrieve_related_memories`：给定对话文本，返回相关已有记忆（含 preference/experience）。
- **K3** `mem_touch`：`search_memory` 命中后，记忆 `importance` 提升（封顶 5）且 `last_touched_at` 更新。
- **K4** `mem_consolidate` 衰减：跳过 `user_edited=1`，按 `last_touched_at` 判断。
- **K5** 相似度：近重复中文句在默认阈值 0.75 下被判定为重复；不同语义句判定为不重复。

### 非功能
- 性能：`_shared_text_vectors` 对候选集（≤10 条）逐个计算，无全库 O(n²)。
- 安全：`mem_add`/`mem_update` 仍走 `sanitize_guard`，明文密钥不入库。

### 回归
- 既有 `test_memory_engine.py` 断言仍通过（相似度 `>=0.15`、`<0.4`、substring `>=0.40` 等）。

---

## 6. 回滚预案

| 改动 | 回滚命令 |
|---|---|
| 全部改动 | `git checkout backend/`（丢弃全部未提交改动） |
| 相似度重写 | `git checkout backend/memory_engine.py` |
| schema 迁移 | `last_touched_at` 为纯加列，旧库幂等；如需彻底回滚：恢复 `data/zenith.db` 备份 |
| 删除每轮全量提取 | `git checkout backend/routers/chat.py` |
| 死代码清理 | `git checkout backend/app.py` |

---

## 7. 人类决策清单

| # | 决策项 | 结果 | 负责人 | 日期 |
|---|---|---|---|---|
| 1 | 是否立即提交本次记忆优化改动 | ⬜ 待定 | whathelio | 2026-08-13 |
| 2 | 是否引入 pre-commit + ruff + pytest（质量门禁） | ⬜ 待定 | whathelio | — |
| 3 | 是否收敛两处记忆整理路径（阈值统一） | ⬜ 待定 | whathelio | — |
| 4 | `distilled_from` 来源链：打通 or 移除 | ⬜ 待定 | whathelio | — |
| 5 | `confirm_flow.py` / `tools.py` 前次遗留改动是否一并提交 | ⬜ 待定 | whathelio | — |

---

## 8. 改动表（落实清单，2026-08-13 更新）

> 状态说明：🟢 已解决 / 🟡 部分解决 / ⬜ 待办。优先级 P0=根因级优先 / P1=高 / P2=低。

| # | 改动项 | 涉及文件 | 具体动作 | 风险 | 优先级 | 状态 | 验收标准 | 回滚 |
|---|---|---|---|---|---|---|---|---|
| 1 | 补测试依赖 | `requirements.txt` | 加 `pytest`（建议拆 `requirements-dev.txt`） | 🟢 | **P0** | ⬜ | 测试套件可运行 | `git checkout requirements.txt` |
| 2 | IDF 权重生效 | `memory_engine.py` | `_update_idf_weights` 改为启动时/记忆数达阈值时惰性初始化 | 🟡 | **P0** | ⬜ | 大库下相似度区分度提升 | `git checkout backend/memory_engine.py` |
| 3 | 收敛记忆整理路径 | `memory_engine.py`+`tools.py` | 统一 `mem_consolidate`(0.7) 与 `generate_consolidate_plan`(0.85) 阈值/入口 | 🔴 | **P0** | ⬜ | 单一整理入口，阈值一致 | `git checkout` 两文件 |
| 4 | 统一相关记忆检索 | `memory_engine.py`+`tools.py`+`routers/chat.py` | 抽取 `retrieve_related_memories`，收敛 4 处重复 | 🟡 | P1 | ⬜ | 单一检索函数 | `git checkout` 三文件 |
| 5 | `distilled_from` 来源链 | `database.py`+`unified_distill.py` | 蒸馏入库时回填来源 id，或删除该字段 | 🟡 | P1 | ⬜ | 记忆可溯源 / 字段移除 | `git checkout` |
| 6 | pre-commit + ruff | 新增 `pyproject.toml`+`.pre-commit-config.yaml` | 引入 lint 门禁 + pre-commit hook | 🟢 | P1 | ⬜ | `pre-commit run --all-files` 通过 | 删除新增文件 |
| 7 | 优雅关闭等待任务 | `memory_engine.py`+`start.py` | 关闭时 flush 残余对话 buffer | 🟡 | P1 | 🟢 | 关闭不丢最后 1~2 轮 | `git checkout start.py` |
| 8 | `_pending_tasks` 收敛 | `memory_engine.py` | 移除集合或用于生命周期管理（关闭 await） | 🟢 | P2 | 🟡 | 无死代码/幽灵状态 | `git checkout` |
| 9 | 补回归测试 | `tests/` | 为记忆改动补 K1–K5 五条测试 | 🟢 | P1 | ⬜ | 新行为有回归保护 | `git checkout tests/` |

### 治理流程自身的三点优化

1. **待办项需更新**：报告第 4 节「阶段 E（优雅关停）」与第 2.2 节「`_pending_tasks` 幽灵」已被后续「启动/关闭自动化增强」部分解决（#7、#8 已更新状态）。
2. **治理范围偏窄**：上次治理仅覆盖「对话→记忆」模块，**启动/关闭逻辑（start.py）未纳入**，导致其后才发现需补自动化控制。下次治理应把启动器/进程管理纳入范围。
3. **根因优先**：`requirements.txt` 缺 pytest 是「测试不可运行」的根因，应作为 P0 最先落地，否则后续改动只能靠手动断言脚本验证，回归风险累积。
