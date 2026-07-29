# Day 4 运行手册：LLM Wiki 编译实验

> 目标：把重要主题从“原始 PDF/笔记”编译成结构化、可生长、可审计的 Markdown wiki，验证 Karpathy LLM Wiki 范式在 Zenith 里的用法。  
> 与 Day 1 RAG 的关系：RAG 负责“全库粗检索”，LLM Wiki 负责“专题精编”。二者互补。

---

## 0. 前置条件

| 项 | 要求 |
|----|------|
| Python | 3.10+ |
| 依赖 | `openai` `pypdfium2`（Day1 venv 已装） |
| LLM | DeepSeek API（SiliconFlow / 官方 / 无问芯穹） |
| 原始资料 | 1 篇 PDF / Markdown / TXT，建议先选一个重要主题 |

---

## 1. 目录结构

```
zenith_wiki/
├── raw/            # 原始资料（只读）
│   └── 某论文.pdf
└── wiki/
    ├── index.md    # 内容索引（每页一行）
    ├── log.md      # 操作日志
    ├── transformer-架构.md
    └── ...其他页面.md
```

首次运行 `ingest` 会自动创建这些目录。

---

## 2. 配置环境变量

```bash
export LLM_API_KEY="sk-..."
export LLM_BASE_URL="https://api.siliconflow.cn/v1"
export LLM_MODEL="deepseek-ai/DeepSeek-V3"
# 可选：自定义 wiki 目录
export ZENITH_WIKI_DIR="./zenith_wiki"
```

---

## 3. 常用命令

```bash
# 摄入一篇资料，编译成 wiki 页面
python llm_wiki_compiler.py ingest "某论文.pdf"

# 查询 wiki
python llm_wiki_compiler.py query "Transformer 的核心原理是什么？"

# 健康检查：找矛盾 / 孤立页面 / 缺失概念
python llm_wiki_compiler.py lint

# 列出当前 wiki 页面
python llm_wiki_compiler.py list
```

---

## 4. 页面格式（LLM 自动生成）

```markdown
---
title: "Transformer 架构"
aliases: ["Self-Attention"]
tags: ["deep-learning", "attention"]
created: "2026-07-18"
updated: "2026-07-18"
sources: ["某论文.pdf"]
related: ["[[attention-mechanism]]"]
confidence: "high"
summary: "Transformer 用自注意力替代循环结构，实现并行化序列建模。"
---

## 概述
...

## 关键概念
...

## 关键事实/数据
...

## 与已有页面的关联
- 与 [[attention-mechanism]] 的区别：...

## 待补充/矛盾点
- 原始资料未提及位置编码细节。
```

---

## 5. 验收标准

1. `ingest` 后 `zenith_wiki/wiki/` 下出现一个 `.md` 页面，含完整 frontmatter。
2. `index.md` 新增一行，`log.md` 记录 ingest 操作。
3. `query` 能返回带 `[[页面名]]` 引用的回答。
4. `lint` 能输出至少 1 条健康检查建议。
5. 重复 `ingest` 同一主题的不同资料时，LLM 会尝试在 `related` 里引用已有页面。

---

## 6. 与 RAG 的边界

| 场景 | 用 RAG（Day1） | 用 LLM Wiki（Day4） |
|------|----------------|---------------------|
| 上千篇论文里找一句话 | ✅ | ❌ |
| 把一个主题编译成可读综述 | ❌ | ✅ |
| 随时溯源到原文页码 | ✅ | ⚠️（通过 sources 字段） |
| 知识“生长”与交叉引用 | ❌ | ✅ |
| 离线、零基础设施 | ✅（本地） | ✅（只要 LLM API） |

建议工作流：先用 RAG 检索到相关文献片段，再把重要片段 `ingest` 到 LLM Wiki 编译成专题页。

---

## 7. 常见问题

| 问题 | 处理 |
|------|------|
| LLM 输出带代码块包裹 | prompt 已要求不包裹；若仍出现，手动去掉即可 |
| 页面标题重复 | slugify 会截断；建议 ingest 前给文件起清晰名字 |
| 原始资料太长 | 脚本截断到 12000 字，超长资料先拆分或先用 RAG 检索片段 |
| `query` 找不到相关页面 | wiki 太少，先多 ingest 几篇；或先用 RAG 找原文 |
| LLM 编造数据 | 检查页面“待补充/矛盾点”段；关键数据人工核对 |

---

## 8. 下一步

跑通 Day 4 后，可进入 Week 2：评估是否把向量库从 Chroma 升级到 LEANN/Zvec/Cairn。
