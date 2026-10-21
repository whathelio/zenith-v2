# Zenith v2 文档索引

## 项目文档
| 文件 | 说明 |
|------|------|
| [../README.md](../README.md) | 项目首页，快速开始 |
| [../SECURITY.md](../SECURITY.md) | 🔴 安全策略（承重文档，被代码引用） |
| [文件归属.md](./文件归属.md) | 🧭 **源 ↔ 路径 ↔ 维护者**（改任何文件前先查它有没有权改） |

## 治理评审
| 文件 | 说明 |
|------|------|
| [全量治理评审报告_20260723.md](./全量治理评审报告_20260723.md) | 文档 + 代码全量治理评审 |
| [文档治理评审报告.md](./文档治理评审报告.md) | 2026-07-18 的文档治理评审 |
| [前端模块清单与截图分析.md](./前端模块清单与截图分析.md) | 前端 10 页面的路由/组件/截图分析 |

## ADR — 架构决策记录
| 文件 | 说明 |
|------|------|
| [adr/README.md](./adr/README.md) | 📐 **ADR 规范：编号规则 · 必含六节 · 编号映射建议（待裁定）** |
| [adr/template.md](./adr/template.md) | 新增 ADR 的模板 |
| [agent-driven-knowledge-workflow.md](./adr/agent-driven-knowledge-workflow.md) | 知识工作流设计 v1 |
| [agent-driven-knowledge-workflow-v2.md](./adr/agent-driven-knowledge-workflow-v2.md) | 知识工作流设计 v2（当前版本） |
| [agent-driven-knowledge-workflow-治理评审.md](./adr/agent-driven-knowledge-workflow-治理评审.md) | 知识工作流治理评审（建议移到 audit/） |
| [RAG-落地分析.md](./adr/RAG-落地分析.md) | RAG 知识库落地方案 |
| [DeepSeek对话_Zenith整合分析报告.md](./adr/DeepSeek对话_Zenith整合分析报告.md) | LLM 选型与整合方案（建议移到 audit/） |
| [zenith-超轻量Agent架构优化-v3.md](./adr/zenith-超轻量Agent架构优化-v3.md) | Agent 架构优化 v3 |
| [pdf-ingest-governance.md](./adr/pdf-ingest-governance.md) | PDF 入库治理方案 |
| [代码治理工作流借鉴.md](./adr/代码治理工作流借鉴.md) | 代码治理流程设计 |

## Audit — 审计报告 (4 文件)
| 文件 | 说明 |
|------|------|
| [code-runner-security-audit.md](./audit/code-runner-security-audit.md) | 代码执行安全审计 |
| [本地智能体评审报告V1.0.md](./audit/本地智能体评审报告V1.0.md) | Agent 架构评审 |
| [zenith-ui-integration-review.md](./audit/zenith-ui-integration-review.md) | UI 整合评审 |
| [C盘治理评审报告.md](./audit/C盘治理评审报告.md) | C 盘治理（非 Zenith） |

## Runbook — 操作手册 (5 文件)
| 文件 | 说明 |
|------|------|
| [day1-runbook.md](./runbook/day1-runbook.md) | Day 1：Zotero → RAG 闭环 |
| [day2-prep-runbook.md](./runbook/day2-prep-runbook.md) | Day 2 准备 |
| [day4-runbook.md](./runbook/day4-runbook.md) | Day 4 操作 |
| [week2-vector-store-eval.md](./runbook/week2-vector-store-eval.md) | 向量存储评估 |
| [week2-week3-runbook.md](./runbook/week2-week3-runbook.md) | Week 2-3 合并计划 |

## Archive — 已归档 (4 文件)
| 文件 | 说明 |
|------|------|
| [百尺目标板块移植评估.md](./archive/百尺目标板块移植评估.md) | 已完成的一次性评估 |
| [知识库与笔记库融合核查.md](./archive/知识库与笔记库融合核查.md) | 一次性核查 |
| [文档治理评审SOP模板包.md](./archive/文档治理评审SOP模板包.md) | SOP 模板 |
| [黄金分析报告_20260716.md](./archive/黄金分析报告_20260716.md) | 单次市场分析产出 |

## Planning — 规划 (2 文件)
| 文件 | 说明 |
|------|------|
| [zenith-agent-improvement-roadmap.md](./planning/zenith-agent-improvement-roadmap.md) | Agent 改进路线图 |
| [后续治理建议落实.md](./planning/后续治理建议落实.md) | 治理待办清单 |

## Screenshots — 前端截图 (10 张)
| 文件 | 页面 |
|------|------|
| [01-dashboard.png](./screenshots/01-dashboard.png) | DashboardView — 首页仪表盘 |
| [02-chat.png](./screenshots/02-chat.png) | ChatView — 对话界面 |
| [03-calendar.png](./screenshots/03-calendar.png) | CalendarView — 周视图日历 |
| [04-goals.png](./screenshots/04-goals.png) | GoalsView — 目标追踪 |
| [05-knowledge.png](./screenshots/05-knowledge.png) | KnowledgeView — 知识库 |
| [06-settings.png](./screenshots/06-settings.png) | SettingsView — 设置 |
| [07-notes.png](./screenshots/07-notes.png) | LibraryView — 笔记 |
| [08-memories.png](./screenshots/08-memories.png) | LibraryView — 记忆 |
| [09-skills.png](./screenshots/09-skills.png) | LibraryView — 技能 |
| [10-library.png](./screenshots/10-library.png) | LibraryView — 资料库 |

---

*最后更新：2026-10-09（新增 ADR 规范与模板、文件归属表）*
