# Day 1 运行手册：Zotero → 本地 RAG 闭环

> 目标：在自己电脑上跑通 `zotero_parse_rag_core.py`，能对 Zotero 文献提问并得到带引用的回答。  
> 对应 v3 评审报告“最小安全第一刀”：先用 Chroma + bge-small + DeepSeek 跑通，不部署 Docker 服务。

---

## 0. 前置条件

| 项 | 要求 |
|----|------|
| 操作系统 | Windows / macOS / Linux 任一 |
| Python | 3.10+（推荐 3.13） |
| Zotero | 已安装，且有本地 PDF 附件（不是只有链接） |
| DeepSeek API | 任一 OpenAI 兼容端点：SiliconFlow / DeepSeek 官方 / 无问芯穹 |
| 硬件 | CPU 即可；8GB 内存起步；磁盘留 1–2GB 给模型与索引 |

---

## 1. 安装依赖

建议用虚拟环境，避免污染系统 Python。

```bash
# Windows (Git Bash)
python -m venv .venv
source .venv/Scripts/activate

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements_day1.txt
```

首次运行时 `sentence-transformers` 会自动下载 `BAAI/bge-small-zh-v1.5`（约 100MB），需联网。

可选（扫描件/公式）：
```bash
pip install paddlepaddle paddleocr
# 或按 MinerU 官方文档安装
```

---

## 2. 配置

全部用环境变量，不用改代码。

```bash
# Windows PowerShell
$env:ZOTERO_DATA_DIR="C:\Users\你的用户名\Zotero"
$env:LLM_API_KEY="sk-你的DeepSeek或SiliconFlow密钥"
$env:LLM_BASE_URL="https://api.siliconflow.cn/v1"
$env:LLM_MODEL="deepseek-ai/DeepSeek-V3"

# Git Bash
export ZOTERO_DATA_DIR="/c/Users/你的用户名/Zotero"
export LLM_API_KEY="sk-..."
export LLM_BASE_URL="https://api.siliconflow.cn/v1"
export LLM_MODEL="deepseek-ai/DeepSeek-V3"
```

不设 `ZOTERO_DATA_DIR` 时默认 `~/Zotero`。

---

## 3. 常用命令

```bash
# 列出 Zotero Collection（挑选要索引的收藏夹）
python zotero_parse_rag_core.py --list-collections

# 先用小集合验证（推荐 50–100 篇）
python zotero_parse_rag_core.py --build --collection "投资" --limit 50

# 完整索引（可晚上跑，支持断点续跑）
python zotero_parse_rag_core.py --build --workers 4

# 查看索引统计
python zotero_parse_rag_core.py --stats

# 提问
python zotero_parse_rag_core.py --query "这篇论文的主要贡献是什么？"

# 测试单个 PDF 提取（不依赖 Zotero/API）
python zotero_parse_rag_core.py --test-pdf "某论文.pdf"

# 清空重建
python zotero_parse_rag_core.py --rebuild
```

---

## 4. 断点续跑说明

- 进度文件：`zenith_rag/progress.json`，记录每个 `item_id` 的 `mtime` 与处理状态。
- 重跑时自动跳过未变更的 PDF。
- 用 `--force` 可强制重处理；`--rebuild` 清空向量库与进度后从零开始。
- 每处理完一篇就落盘，可随时 `Ctrl+C` 中断，下次继续。

---

## 5. 验收标准

1. `--stats` 显示库内片段数 > 0。
2. `--query` 能返回带 `[来源: ...]` 的回答。
3. 故意问一个知识库里没有的问题，模型应回答“知识库中未找到相关内容”。
4. `--test-pdf` 对扫描件应显示 `source: ocr` 或 `mineru`（若装了 OCR）。

---

## 6. 常见问题

| 问题 | 处理 |
|------|------|
| 找不到 `zotero.sqlite` | 确认 `ZOTERO_DATA_DIR` 路径，Zotero 默认在 `~/Zotero` |
| `database is locked` | 关闭 Zotero 客户端再跑，或用只读模式（脚本已默认只读） |
| 附件路径解析为 None | 该条目可能只存了链接，没同步 PDF 到本地 storage |
| 覆盖率低、检索不准 | 装 paddleocr 或 MinerU；或对该 PDF 单独 `--test-pdf` 排查 |
| bge-small 下载慢 | 设 `HF_ENDPOINT=https://hf-mirror.com` |
| Chroma 报错 | 删 `zenith_rag/chroma_db` 后 `--rebuild` |
| DeepSeek API 报 401 | 检查 `LLM_API_KEY` 与 `LLM_BASE_URL` 是否匹配 |

---

## 7. 下一步

跑通 Day 1 后，进入 Day 2–3：接 OpenClaw 或 WeChatFerry，让手机能问。
