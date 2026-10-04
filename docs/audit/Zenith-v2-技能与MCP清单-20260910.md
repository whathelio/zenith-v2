# Zenith v2 技能与 MCP 清单

生成时间：2026-09-10
采集方式：运行中服务（127.0.0.1:8766）+ 直读 `data/zenith.db` + 磁盘/配置核对
原则：**零删除**，本清单只做盘点和标记，不执行任何清理动作

---

## 0. 结论速览

| 维度 | 数量 | 状态 |
|---|---|---|
| 记忆库技能（`memories.type='skill'`） | **50** | 🟢 全部在库 |
| 其中真正参与对话注入的 | **46** | 🟡 有 4 条因前缀不符永不注入 |
| 其中 imp≥3（统计口径 confirmed） | 22 | 🟢 完整注入 |
| 磁盘技能目录（`SKILL.md`） | **4** | 🟡 仅 1 条已入记忆库 |
| Zenith 生效的 MCP 服务器 | **1** | 🔴 桥接存活但鉴权 401 |
| MCP 配置来源文件 | 1 个实际存在（`~/.workbuddy/mcp.json`） | 🟡 其余候选路径均已消失 |
| WorkBuddy 应用侧 MCP 注册表 | **152** | 🟢 与 Zenith 无关，不加载 |

**一句话**：技能侧"能用但有三处错位"（4 条死技能 + 3 个磁盘技能未入库 + 层级标签几乎全缺）；MCP 侧"只有一个且是坏的"（mt5-terminal 令牌失效），而 Zenith 实际取 MT5 数据走的是直连 SDK，**不依赖该 MCP**。

---

## A. 技能清单

### A1. 三层结构（数据流）

```
D:\下载文件\新建文件夹\.workbuddy\skills\   ← 磁盘层（4 个 SKILL.md，人工维护）
              │  scan_skills_dir() / import_all_from_dir()
              ↓
        memories 表 type='skill'（50 条）      ← 记忆层（真正被检索的库）
              │  chat.py:_build_skill_injection()
              ↓
   system prompt「【已启用技能 · 必须遵循】」  ← 注入层（每轮最多 3 条）
```

关键事实：**注入层只认记忆层，不直接读磁盘**。所以磁盘上有 `SKILL.md` ≠ 对话里能用。

### A2. 磁盘技能（4 个）

目录：`D:\下载文件\新建文件夹\.workbuddy\skills`（由 `get_skills_dir()` 解析，候选链命中 workspace 级）

| # | 技能名 | 大小 | 修改时间 | 是否已入记忆库 | 优先级 |
|---|---|---|---|---|---|
| 1 | `zenith-calendar-sync` | 3537 B | 2026-07-18 | ❌ 未入库 | 🟡 |
| 2 | `zenith-memory-consolidation` | 3814 B | 2026-07-18 | ❌ 未入库 | 🟡 |
| 3 | `zenith-rag-planning` | 6098 B | 2026-08-07 | ✅ id=1804 | 🟢 |
| 4 | `zenith-sqlite-patterns` | 2376 B | 2026-08-08 | ❌ 未入库 | 🟡 |

4 个技能 `mcp_required` 均为 `[]`，`has_scripts` 均为 `false`（无附带脚本，纯文档型技能）。

> ⚠️ 注意：`zenith-sqlite-patterns` 和 `zenith-rag-planning` 是**高价值排障经验**，前者的"FTS5 中文不分割→LIKE 兜底""日期字符串比较坑"等条目直接对应本次审计中修掉的问题，却未入库、无法被对话调用。

### A3. 记忆库技能（50 条）

按重要度分组，全部来自 `memories WHERE type='skill'`。

#### 组 1：高重要度技能（imp=5，5 条）🟢

| ID | 技能名 | 来源 |
|---|---|---|
| 1782 | cache-optimizer | WorkBuddy 导入 |
| 1788 | find-skills | WorkBuddy 导入 |
| 1790 | github | WorkBuddy 导入 |
| 1791 | github-trending-cn | WorkBuddy 导入 |
| 1793 | impeccable | WorkBuddy 导入 |

#### 组 2：中重要度技能（imp=4，4 条）🟢

| ID | 技能名 |
|---|---|
| 1792 | ima-skills |
| 1799 | tapd-openapi |
| 1805 | sanitize-organizer |
| 1922 | persona-framework |

#### 组 3：元技能批次 / L1（imp=3，9 条）🟢

唯一带 `层级：L1` 标签的 9 条，属方法论层：

| ID | 技能名 | ID | 技能名 |
|---|---|---|---|
| 2123 | 冷启动 | 2128 | 质量控制 |
| 2124 | 知识压缩 | 2129 | 反思 |
| 2125 | 数据融合 | 2130 | 技能优化演化 |
| 2126 | 迭代工作流 | 2131 | 技能迁移 |
| 2127 | 柳叶刀方法 | | |

#### 组 4：其他 imp=3（4 条）

`2051 dual-ai-review`、`2052 self-improving-agent`、`2053 windows-troubleshooting`、`2054 xray-node-benchmark`

#### 组 5：imp=1（28 条）🟡

其中 **24 条**前缀为 `技能：`（可注入），**4 条**前缀为 `Skill:`（🔴 永不注入，见 A4-①）：

| 可注入（24 条） | 死技能（4 条，🔴） |
|---|---|
| 1779 api-gateway / 1780 apple-notes / 1781 apple-reminders / 1783 cnb-skill / 1784 code-governance-workflow / 1785 cos-vectors / 1786 doc-governance-review / 1787 earnings-tracker / 1789 frontend-dev / 1794 macro-monitor / 1795 mcp-builder / 1797 skill-scanner / 1798 stock-analyzer / 1800 thoughts-memo-style / 1801 wechat-miniprogram / 1802 westockdata / 1803 zenith-auditor / 1804 zenith-rag-planning / 1920 persona-consistency-audit / 1921 persona-deployer / 1923 secure-coding / 2005 ERP分步上线策略 / 2009 高效项目沟通与周例会方法 / 2010 管理语言翻译与改变清单制定 | 1268 `Skill: AI技能成长路线图（6个月版）`<br>1269 `Skill: 投资框架查阅`<br>1271 `Skill: 查阅期货黄金交易三套规则`<br>1272 `Skill: 每日交易复盘` |

> 4 条死技能全部是**金融/交易类**（投资框架、期货黄金规则、每日复盘、成长路线图），且用英文 `Skill:` + `Trigger:` 格式，与 Zenith 国产化后的 `技能：`+`触发：`格式不兼容。

### A4. 注入链路实现（`backend/routers/chat.py:120-188`）

```
用户消息 → 全量取 type='skill' 记忆
        → 过滤 content.startswith("技能：")        ← 【卡点①】
        → 用「触发：」段 + keywords + 前80字 做子串匹配（非 FTS）
        → 按关键词覆盖度排序，取前 3 条              ← 【上限】
        → 读「层级：」标签决定完整注入 / 摘要注入   ← 【卡点②】
        → 拼成 system prompt 硬性指令
```

三级渐进载入设计：L1 全量注入；L2 默认只给"名称+触发+100字摘要"，查询含「详细/步骤/如何/流程」等词才展开；L3 未实现。

### A5. 发现问题

| # | 问题 | 证据 | 影响 | 优先级 |
|---|---|---|---|---|
| S-01 | 4 条技能因前缀 `Skill:` 而非 `技能：` 被注入逻辑完全忽略 | `chat.py:133` `startswith("技能：")`；DB 中 id 1268/1269/1271/1272 命中 | 4 条金融技能永久失效，用户以为"有技能"其实没有 | 🟡 |
| S-02 | 磁盘 4 技能仅 1 条入记忆库，差 3 条 | `/api/modules/skills/files` count=4；DB 仅 id=1804 | 写入 `SKILL.md` 后不点"导入"就不会生效，静默失效 | 🟡 |
| S-03 | 层级标签几乎全缺（41/50 无 `层级：`，代码默认按 L1 全量注入） | 层级统计：L1=9、无标签=41 | 三级渐进载入实际只对 9 条生效，其余不展开/不摘要 | 🟢 |
| S-04 | 技能总量 50 中 28 条为 imp=1，多数是 WorkBuddy 生活/运维类技能，与 Zenith 本地助手场景弱相关 | 组 5 明细 | 匹配面被稀释，可能挤掉更相关的技能（前 3 条上限） | 🟢 |
| S-05 | 无技能淘汰/触达机制生效痕迹 | `last_touched_at` 字段存在但技能侧未见调度写入 | 越积越多，无法自动识别僵尸技能 | 🟢 |

**注入成本实测（不是问题，供参考）**：46 条可注入技能合计 20,520 字，最大单条 590 字（`zenith-auditor`）；因单轮最多 3 条，最坏注入约 1.8k 字，成本可控。

---

## B. MCP 清单

### B1. Zenith 实际加载的 MCP（1 个）

来源：`GET /api/modules/mcp` → `source: "workbuddy"`

| 名称 | 类型 | 地址 | 状态 | 工具数 | 说明 |
|---|---|---|---|---|---|
| `mt5-terminal` | http | `http://127.0.0.1:22346/mcp` | 🔴 **error** | 0 | MetaTrader5 终端桥接 |

健康检查 `GET /api/modules/mcp/health?refresh=1`：

```json
{"name":"mt5-terminal","transport":"http","enabled":true,"ok":false,
 "state":"error","latency_ms":18,"tool_count":0,
 "error":"MCPClientError: HTTP MCP 'mt5-terminal' 初始化失败"}
```

独立复测（curl 直连，绕过 Zenith）：

| 探测 | 结果 | 判定 |
|---|---|---|
| `GET http://127.0.0.1:22346/mcp` | HTTP **401** | 端口在监听，服务进程存活 |
| 带 `Authorization: Bearer <token>` 发 `initialize` | HTTP **401** | **令牌已失效**（非服务宕机、非网络问题） |

结论：**桥接进程活着，令牌过期**。这是外部依赖问题，改 Zenith 代码无解。

对比：2026-08-28 的《MT5-MCP 桥接打通验证报告》记录当时 `tools/list` 返回 **42 个工具**、并成功调用 `get_trading_account_info`（Exness 真实账户）。**能力曾是通的，是令牌后来变了。**

### B2. MCP 配置来源优先级链

`backend/mcp_config.py:150 load_mcp_servers()`

```
① 若 prefer_workbuddy=true（当前 config.yaml 为 true）
     → 读 get_mcp_config_path() 指向的 mcp.json
     → 若文件存在且非空，用它
② 否则回退 config.yaml 的 mcp_servers（当前 = []，空）
最后叠加 config/mcp_overrides.json（当前 = {}，空）
```

`get_mcp_config_path()` 候选链（`backend/config.py`）与实测存在性：

| 候选路径 | 是否存在 | 说明 |
|---|---|---|
| `config.yaml:mcp.workbuddy_config_path` = `~/.workbuddy/mcp.json` | ✅ **命中** | 当前生效 |
| `$WORKBUDDY_CONFIG_DIR/mcp.json` | ❌ 已不存在 | 2026-08-28 曾在此，后被移除 |
| `~/.workbuddy/mcp.json`（兜底） | ✅ | 与第 1 项同一文件 |

> 历史坑复现：`$WORKBUDDY_CONFIG_DIR` 若未定义，`${WORKBUDDY_CONFIG_DIR}/mcp.json` 会退化成字面相对路径并静默读不到。当前已显式改为 `~/.workbuddy/mcp.json`，绕开了这个坑。

### B3. 三个"看起来都是 MCP"的东西（务必区分）

| 层 | 位置 | 数量 | Zenith 是否加载 | 用途 |
|---|---|---|---|---|
| ① **Zenith 生效层** | `C:\Users\xiepu\.workbuddy\mcp.json` | **1** | ✅ 是 | Zenith 对话可调用的 MCP |
| ② WorkBuddy 应用注册表 | `D:\WorkBuddyData\.workbuddy\connectors\d70e4d6d-…/mcp.json` | **152** | ❌ 否 | WorkBuddy 自己的 connector 市场目录（tencent-docs / tapd / github / notion / tushare / wind-finance / tencent-map 等） |
| ③ 代码直连 SDK | `backend/mt5_service.py` | — | — | 直接用 `MetaTrader5` Python 包，**不走 MCP** |

**关键结论**：152 个不是 Zenith 的能力，不会被加载；Zenith 真正能用的 MCP 只有 1 个，而它是坏的。

### B4. MT5 双通道（重要发现）

| 通道 | 实现 | 当前状态 | 被谁调用 |
|---|---|---|---|
| A: 直连 SDK | `backend/mt5_service.py` → `import MetaTrader5` | 取决于本地 MT5 终端 | Zenith 后端行情/持仓逻辑 |
| B: MCP 桥接 | `mt5-terminal` @ 22346 | 🔴 401 | **无任何后端代码引用** |

验证：全仓 `grep -rn "mt5-terminal" backend/ --include=*.py` → **0 命中**。

也就是说按"保留潜在需要"原则，B 通道属于**已配置但暂未被消费**的能力——它的价值在于给 LLM 一个"自己调 MT5"的入口（42 个工具），而 A 通道只能走后端固定函数。修好令牌即可恢复，无需改代码。

### B5. MCP 相关问题

| # | 问题 | 证据 | 影响 | 优先级 |
|---|---|---|---|---|
| M-01 | `mt5-terminal` 令牌失效 | 401（带 token 复测仍 401）；health `ok:false` | Dashboard 显示 error；LLM 无法调 MT5 工具 | 🔴 |
| M-02 | 该 MCP 无任何后端代码引用，属"配置了但没接" | grep 0 命中 | 即使令牌修好，也只在 LLM tool_call 路径生效，非后端自动路径 | 🟡 |
| M-03 | `config.yaml:mcp_servers` 与 `mcp_overrides.json` 均为空占位 | `mcp_servers: []`、`{}` | 属预留结构，无实际作用（**保留，勿删**） | 🟢 |
| M-04 | 原 `$WORKBUDDY_CONFIG_DIR/mcp.json`（曾含 7 个 server）已消失，现仅剩 1 个 | 路径探测 ❌ | 若之前依赖那 7 个，需确认是有意迁移还是丢失 | 🟡 |

---

## C. 处置建议（按优先级，全部为"保留优先"）

| 优先级 | 动作 | 对象 | 类型 | 风险 |
|---|---|---|---|---|
| 🔴 | 更新 `~/.workbuddy/mcp.json` 中 mt5-terminal 的 `Authorization` 令牌（从 MT5 桥接侧取新 token） | M-01 | 配置 | 极低，改前备份 |
| 🟡 | 确认原 `$WORKBUDDY_CONFIG_DIR/mcp.json` 里那 7 个 server 是有意精简还是意外丢失 | M-04 | 核查 | 无 |
| 🟡 | 将磁盘 3 个未入库技能导入记忆库（`POST /api/modules/skills/import` 或前端"导入"按钮） | S-02 | 数据 | 低，仅新增记忆行 |
| 🟡 | 决定 4 条 `Skill:` 前缀死技能如何处理：改前缀为 `技能：` 使其生效，或标记为历史归档 | S-01 | 数据 | 低 |
| 🟡 | 明确 B 通道（MCP）定位：修好即作为 LLM 自助 MT5 入口，还是明确弃用以直连 SDK 为准 | M-02 | 决策 | 无 |
| 🟢 | 为 41 条无层级标签技能补 `层级：` 标签，让三级渐进载入真正生效 | S-03 | 数据 | 低 |
| 🟢 | 考虑给技能加"最后命中时间"写入，配合 `last_touched_at` 做僵尸技能识别 | S-05 | 代码 | 低 |
| 🟢 | `mcp_servers: []` / `mcp_overrides.json {}` 保持原样（预留槽位） | M-03 | — | — |

**本清单未执行任何删除或修改**，所有动作待确认。
