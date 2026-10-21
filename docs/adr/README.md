# ADR — 架构决策记录

> 本目录回答**「为什么这么定」**。回答「定成什么样」的是 `docs/规范/`（如还没建，见 §5），
> 回答「先做什么」的是 `docs/planning/`。
>
> 借鉴来源：Study-Mate 的 `docs/adr/` 制度（见
> `知识库-v1.0/日志审查库/zenith/2026-10/Zenith-外部项目借鉴评估-StudyMate-20261009.md` G3）。
> 那份文档的框架 —— **三档定档（抄设计 / 移植实现 / 不采纳）+ 明确不采纳清单 + 「推翻了自己哪些既有决定」** ——
> 同样是本目录推荐的写法。

---

## 1. 命名规则

```
NNNN-<kebab-case-主题>.md
```

- **编号四位数、从 0001 起、递增、不复用**。作废的 ADR **不改号、不删除**，把状态改成 `Superseded by ADR-NNNN`。
- 主题用英文 kebab-case（与仓库代码风格一致），正文用中文。
- **一个 ADR 只回答一个决策**。同主题的演进写新 ADR + 把旧的标 `Superseded by`，不原地改写历史。

## 2. 每份 ADR 必含的六节

顺序固定，缺哪节就说明这个决策还没想清楚：

| 节 | 必须回答 |
|:--|:--|
| **Status** | `Proposed` / `Accepted` / `Superseded by ADR-NNNN` / `Deprecated`，外加日期 |
| **Context** | 现在是什么样、卡在哪。只写事实，不写主张 |
| **Decision** | 定了什么。一句话能说清最好 |
| **Considered Options** | 还考虑过什么、为什么没选。**这一节是 ADR 的价值所在** —— 没有它，半年后会有人重新提一遍 |
| **Consequences** | 换来了什么、付出了什么代价（含已知缺陷与竞态） |
| **难在哪、回退的代价** | 这个决策压在哪些未验证的假设上；要退回去要付多少成本。**没有回退路径的决策不许写 Accepted** |

模板：`template.md`。

## 3. 现有 8 份的编号映射（建议，**尚未执行**）

⚠️ 现有 8 份**没有编号**，且 `docs/index.md` 已按现名建了链接。
**改名会同时改动 `docs/index.md` 与任何引用它们的文档** —— 因此本表只是建议，
等 whathelio 裁定后再一次性改（改前先 `cp` 备份 `docs/index.md`，改后跑链接检查）。

| 现文件名 | 建议编号 | 建议动作 |
|:--|:--|:--|
| `agent-driven-knowledge-workflow.md` | 0001 | 改名为 `0001-agent-driven-knowledge-workflow.md` |
| `agent-driven-knowledge-workflow-v2.md` | 0002 | 改名，并在 0001 上标 `Superseded by ADR-0002` |
| `agent-driven-knowledge-workflow-治理评审.md` | — | **不是决策、是评审** → 建议移到 `docs/audit/` |
| `RAG-落地分析.md` | 0003 | 改名 |
| `DeepSeek对话_Zenith整合分析报告.md` | — | **是分析报告** → 建议移到 `docs/audit/` |
| `zenith-超轻量Agent架构优化-v3.md` | 0004 | 改名 |
| `pdf-ingest-governance.md` | 0005 | 改名 |
| `代码治理工作流借鉴.md` | 0006 | 改名 |

**编号只按「是否是一次决策」来分**：分析报告与评审报告归 `docs/audit/`，ADR 只留决策。

## 4. 新增一份 ADR 的流程

1. **先搜**：`grep -rl "<关键词>" docs/adr/` —— 同一决策不许有两份 ADR。
2. 复制 `template.md` → `NNNN-<slug>.md`，取当前最大编号 +1。
3. 写满六节。**「Considered Options」与「回退的代价」不许空着。**
4. 在 `docs/index.md` 的 ADR 表加一行（含编号与一句话说明）。
5. 如果新决策推翻了某条既有决定，**必须**在旧 ADR 上标 `Superseded by ADR-NNNN`，并在新 ADR 里点名"推翻了什么"。

## 5. 尚未建立、但已被本目录引用

- `docs/规范/文件归属.md`（源 ↔ 路径 ↔ 维护者）—— 已建：见 `../文件归属.md`
- `docs/规范/工程约束.md`（目录约定与唯一出处）—— **待建**，建之前本目录的"定成什么样"部分暂缺

---

_建立日期：2026-10-09。建立原因：ADR 已有 8 份但无编号、无模板、无回退章节，无法作为可查的账。_
