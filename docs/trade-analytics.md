# 交易分析套件（Trade Analytics）

> 2026-10-19 新增。把 MT5 原始流水加工成可复算的交易习惯结论，并提供多角色复盘。

## 1. 能力边界

| 需求 | 状态 | 说明 |
|:--|:--|:--|
| 读取交易记录 | 🟢 可用 | `deals`（成交）/ `orders`（委托）/ 挂单 / 持仓 / 账户 |
| 查询跨年区间 | 🟢 已修 | `date_from` / `date_to`，不再受 365 天限制 |
| 可读历史范围探测 | 🟢 已加 | `/api/mt5/history/span` 一次返回最早/最晚成交 |
| 盈亏口径正确 | 🟢 已修 | 入金/出金不再计入交易盈亏（见 §3） |
| 平仓原因（止损/止盈/强平） | 🟢 已加 | 映射 `deal.reason`，可回答「有没有被强平」 |
| 持仓生命周期配对 | 🟢 可用 | 按 `position_id` 配对开平仓，算持仓时长 |
| 结合行情分析 | 🟢 可用 | 用 M15 K 线算日内入场分位、MFE/MAE、趋势一致率 |
| 交易习惯结论 | 🟢 可用 | 规则化标签，每条带可复算证据 |
| 多 AI 协同 | 🟡 单模型多角色 | 只配了 1 个 provider；加第二个 provider 才是多模型 |
| 前端页面 | ❌ 未做 | 目前通过对话工具或 HTTP 调用 |

## 2. 接口

```
GET  /api/mt5/history/span                          # 该账户可读的最早/最晚成交
GET  /api/mt5/history/deals?days=30                 # 相对窗口
GET  /api/mt5/history/deals?date_from=2025-01-01&date_to=2025-12-31
GET  /api/mt5/history/orders?date_from=...&date_to=...
GET  /api/trade/profile?days=90                     # 或 date_from/date_to
POST /api/trade/review      {"days":90,"date_from":"","date_to":"","use_background":true}
```

`/profile` 返回：`overview`（胜率/PF/盈亏比/期望/平仓原因）、`timing`（小时/星期/时段）、
`behavior`（持仓时长/加仓/隔夜/超短线）、`risk`（回撤/连亏/单笔风险）、
`market_context`（入场分位/MFE/MAE/趋势一致率）、`habits`（规则标签）、`data_quality`。

`/review` 返回：三个角色的原始报告 + `meta`（耗时、是否多模型）：
- `behavior`：行为分析师
- `market`：行情对照官
- `auditor`：风控审计官（逐条标记 supported / unsupported / contradicted / rejected）

## 3. 关键口径（踩过的坑）

**MT5 的 `deals` 不等于「交易」**。入金/出金/信用调整也是 deal：

| 字段 | 真实交易 | 资金流水 |
|:--|:--|:--|
| `symbol` | `XAUUSDm` | `""` |
| `volume` | > 0 | `0.0` |
| `type` | `buy` / `sell` | `balance` / `credit` / `charge` … |
| `profit` | 交易盈亏 | 本金增减 |

旧实现 `"buy" if type == 0 else "sell"` 会把 32 条入金标成 32 笔卖单，并把
`total_profit` 算成「交易盈亏 + 入金」。实测某账户 180 天：

```
真实交易盈亏  -155.55
入金净额      +366.56
旧接口 total_profit  +211.01   ← 错
新接口 total_profit  -155.55
新接口 funding_net   +366.56
```

**持仓时长必须靠 `position_id`**。`deals` 是成交腿，一个 position 可能多次加仓 /
多次部分平仓；没有 `position_id` 就无法配对，也就无法算持仓时长、MFE/MAE。

**捕捉率只对盈利单聚合**。亏损单的 `exit_pct` 为负，比值会出现 -134% 这种无意义数字
（曾误触发「过早止盈」规则）。

### 3.1 为什么「查不到一年前」——真实案例（2026-10-19）

用户要求「读取分析一下去年 2025 年的交易记录」，模型连续调了 **11 次**
`mt5_trade_history`（days = 7 / 365 / 1 / 150 / 135 …），最后给出结论：

> 「365 天与 180 天返回条数完全一样（338 条）→ 账户只有 135 天历史 → 2025 年没有任何交易」

**这个结论是错的。** 实际数据分布：

| 区间 | deal 数 | 说明 |
|:--|--:|:--|
| 2025-06-06 ~ 2025-09-17 | 487 | 账户 2025-06-06 开户并交易 |
| 2025-09-18 ~ 2026-06-10 | 0 | 停用约 9 个月 |
| 2026-06-11 ~ 2026-10-19 | 338 | 恢复交易 |

365 天窗口的起点是 **2025-10-19**，距离 2025 年最后一条记录（09-17）差 **32 天** ——
整个 2025 年样本全部落在窗口外。

**根因不是「没数据」，是接口只能表达「最近 N 天」**。模型无法指定绝对区间，
只能靠缩小 days 反推边界；而「两个窗口条数相同」并不等于「没有更早数据」，
它只说明这两个窗口的交集之外没有数据 —— 恰好这次就是窗口边界差 32 天。

修复后同一问题一次调用即可回答：

```
GET /api/mt5/history/span
→ earliest 2025-06-06 10:59:50 UTC+8 / 825 条 deal

GET /api/trade/profile?date_from=2025-01-01&date_to=2025-12-31
→ 234 笔平仓，净 +64.61
```

**教训**：任何「历史上限」类结论，必须先用 `history/span` 确认真实边界，
不能用「不同窗口返回相同」反推。

**已加防护（2026-10-19）**：`mt5_trade_history` / `trade_profile` 在区间无数据时，
**自动附上**「该账户可读历史：X ~ Y（N 条 deal）」，不再要求模型额外调一次。
否则模型会把「区间外」直接说成「从来没交易」——实测它甚至反过来「纠正」用户：
把 2025-06 说成 2026-06，并让人去查「另一个账户」。

### 3.2 `balance` 有三层含义，必须分开读

| 出现位置 | 字段 | 真实含义 |
|:--|:--|:--|
| `deal.type` | `balance` | **资金流水事件**（入金/出金），不是账户余额 |
| 账户信息 | `account.balance` | **当前余额**：已结算资金，不含浮动盈亏，出金从这里扣 |
| 账户信息 | `account.equity` | **净值**：余额 + 浮动盈亏，无持仓时与余额相等 |

`type=balance` 的 deal **同时表示入金和出金**，方向只能看 `profit` 正负：

- `profit > 0` → 入金（comment 前缀通常是 `D-`）
- `profit < 0` → 出金（comment 前缀通常是 `W-`）
- `D-NULL` → 几分钱的余额调整（汇率/分润），归为「余额调整」

只报 `funding_net` 会把「入 500 出 500」误读成「没动过本金」，所以拆成：

```
deposit_total 1616.15 | withdrawal_total -1323.71 | adjustment_total 9.52 | net 301.96
```

**对账公式**（判断数据读全了没有）：

```
净入金 + 交易净额 = 当前余额
301.96 + (-90.94) = 211.02   vs   实际 211.01   差 1 分（四舍五入）
```

`profile.reconciliation.applicable=false` 表示窗口从账户中途开始（没有初始余额），
此时 diff 不为 0 属正常，不能判成数据缺失。

### 3.3 回撤必须按权益曲线重放，不能只累加交易盈亏

出金会让本金变小，所以「回撤 / 当时本金」必须按 deal 时间顺序重放
`出入金 + 已实现盈亏`，得到余额曲线后再算峰值与回撤。
只用交易 net 累加算的是「盈亏曲线回撤」，分母永远偏大。

实测全区间：`max_drawdown 863.80`，占峰值 **102.56%** —— 超过 100% 说明
权益曾跌破峰值本金的全部，即**穿仓/爆仓**（该账户确实有 39 笔强平）。

## 4. 多角色为什么能减少幻觉

1. 数字全部由 `trade_analytics` 先算好，LLM 不允许自己算；
2. 行为 / 行情两个角色并行，只看同一份事实摘要（digest）；
3. 审计官专职挑错，实测驳回了一条「多数交易为短线」的结论
   （事实是 `scalp_ratio_pct = 22.22%`）。

配置第二个 provider 后，改造成真多模型只需给 `_role()` 传不同的 `provider_name`
（三个角色是三次独立调用，互不共享状态）；当前所有角色共用 `background_provider`，
`meta.is_multi_model` 会如实返回 `false`。

## 5. 对话内工具

- `mt5_history_span()`：用户问「某年有没有交易 / 能不能查更早」时**先调这个**
- `mt5_trade_history(days, limit, date_from, date_to)`：成交明细
- `trade_profile(days, symbol, date_from, date_to)`：日常问「我的胜率/交易习惯/为什么总亏」
- `trade_review(days, symbol)`：明确要求「深度复盘 / 多 AI 分析」时用（20–90 秒）

两者都只把**文本摘要**回灌上下文，不返回完整 profile（避免撑爆 token）。

### 5.1 命令行（不依赖服务重启）

`start.py` 是 `reload=False`，改完必须重启才生效；排查往往等不了。这个脚本
直接调 backend 模块、自己连 MT5 IPC，随时可用：

```bash
cd zenith-v2

# 1. 先确认账户到底能读到多早（关键第一步）
.venv/Scripts/python.exe tools/trade_report.py --span

# 2. 分析指定区间（可跨年）
.venv/Scripts/python.exe tools/trade_report.py --from 2025-01-01 --to 2025-12-31

# 3. 最近 90 天，跳过行情对照（更快）
.venv/Scripts/python.exe tools/trade_report.py --days 90 --no-market

# 4. 原始 JSON（给图表/下游脚本）
.venv/Scripts/python.exe tools/trade_report.py --from 2025-01-01 --json > 2025.json

# 5. 追加多角色复盘（3 次 LLM，20–90 秒）
.venv/Scripts/python.exe tools/trade_report.py --from 2025-01-01 --review
```

输出与对话工具一致（共用 `trade_analytics.format_profile`）。

### 5.2 一年前分析的推荐流程

```
① --span            → 确认最早成交时间（避免白查）
② --from/--to       → 拉区间画像（确定性统计）
③ 看 reconciliation → 区间末推算余额是否吻合，判断数据完整度
④ --review          → 需要归因时才跑多角色，日常不用
```

## 6. 回滚

```bash
# 三个被修改的文件有备份（时间戳可能不同）
backend/mt5_service.py.bak-20261019-132155
backend/tools.py.bak-20261019-132155
backend/app.py.bak-20261019-132155

# 新增文件删除即回滚
backend/trade_analytics.py
backend/trade_review.py
backend/routers/trade.py
```

## 7. 验证记录（2026-10-19）

- `py_compile`：6 个文件全过
- `TestClient`：`/api/trade/profile` → 200；`/api/mt5/history/deals` 口径正确
- `pytest -q`：**1023 passed / 0 failed**
- 端到端：180 天 338 条 deal → 306 交易 / 32 资金流水 → 153 笔 round trip，未配对 0
- 多角色复盘：13–15 秒完成三角色，审计官输出合法 JSON

**2026-10-19 第二轮（跨年查询修复）**

- `history/span`：825 条 deal，2025-06-06 ~ 2026-10-19
- `date_from=2025-01-01&date_to=2025-12-31`：487 条 deal / 234 笔平仓 / 净 +64.61
- `date_from=2026-01-01&date_to=2026-12-31`：153 笔平仓 / 净 -155.55
- 全区间：387 笔平仓 / 净 -90.94 / 强平 39 笔（10.08%）/ 止损触发 3 笔 / 止盈触发 0 笔
- 平仓原因分布（全区间）：手动 249 · 手机端 96 · 强平 39 · 止损 3
- `pytest -q`：**1023 passed / 0 failed**

**2026-10-19 第三轮（balance 语义 + 对账）**

- deal 级 `funding_direction` / `funding_label`：入金 35 条、出金 16 条，与 comment 的 `D-`/`W-` 100% 对应
- 汇总：deposit 1616.15 · withdrawal -1323.71 · adjustment 9.52 · net 301.96
- 对账：`301.96 + (-90.94) = 211.02` vs 实际 `211.01`，差 **-0.01** ✅
- 回撤改为权益曲线重放（含出入金）：863.80 / **102.56%**（>100% = 穿仓，与 39 笔强平一致）
- `pytest -q`：**1023 passed / 0 failed**
