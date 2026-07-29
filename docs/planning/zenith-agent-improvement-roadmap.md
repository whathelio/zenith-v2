# Zenith 智能体改善工作流（借鉴 OpenClaw / LightClaw）

> 目标：借用成熟开源智能体的工具/渠道/路由设计，以代码治理流程安全升级 Zenith。

## 阶段 A：工具扩展 —— 1~2 周
- 新增 `backend/tools/` 子目录：`file_tools.py`、`web_tools.py`（只读操作）
- 通过统一注册表挂载，不修改旧工具
- 回滚：删除新文件
- 风险：🟡 中（新功能，不影响旧逻辑）

## 阶段 B：消息渠道接入 —— 1~2 周
- 新增 `backend/channels/`：`TelegramChannel`、`WeWorkChannel`
- 抽象 `BaseChannel`，实现 `process_message` 统一接口
- 与现有 `llm_client` 和 `memory_engine` 对接
- 回滚：配置开关关闭
- 风险：🟡 中（非侵入式）

## 阶段 C：智能路由与确认流增强 —— 2 周
- `llm_client.py` 增加 `router` 模块
- 简单问题 → 本地小模型，复杂问题 → 大模型
- 扩展 `confirm_flow.py`：文件/命令操作强制确认
- 前端增加确认弹窗
- 风险：🔴 高（涉及核心对话流程）

## 阶段 D：高级特性 —— 后续迭代
- 多智能体协作（`agents/` 子模块）
- 插件化架构（`plugins/` 目录扫描）
- 定时任务可视化
- 本地模型一键部署（Ollama 探测）
- 数据加密（SQLCipher）
- 性能监控（Token/耗时）

## 承重代码（不可轻易改动）
- `llm_client.py`：所有对话流程依赖，改动路由需保流式响应不受影响
- `database.py`：全模块调用，加密需保证数据迁移无丢失
- `tools.py`：被日程/笔记/记忆等内部功能使用

## 安全规则
1. 先读后写
2. 增量提交（add → delete → modify 分开）
3. 不批量删旧工具
4. 测试通过 ≠ 语义安全
5. 每步可回滚（git checkout / git revert）
6. 红队自检：1 分钟内能否回滚？是不是最小改动？

## 人类决策清单
- [ ] 数据库加密是否启用？
- [ ] 路由策略默认模型与小模型阈值？
- [ ] 渠道接入消息限流/防刷？
- [ ] 是否允许 AI 执行系统命令（如 rm -rf）？
