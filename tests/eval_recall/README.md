# 召回评估集（C 阶段）

纯新增评估资产，不修改 zenith-v2 任何生产代码。删除本目录 + `D:\dshs\eval_recall\` 即完全回滚。

## 运行

```bat
cd /d D:\dshs
D:\下载文件\新建文件夹\.venv\Scripts\python.exe ^
  D:\下载文件\新建文件夹\zenith-v2\tests\eval_recall\build_dataset.py
D:\下载文件\新建文件夹\.venv\Scripts\python.exe ^
  D:\下载文件\新建文件夹\zenith-v2\tests\eval_recall\run_eval.py
```

有 pytest 的环境等价于：
```bat
python -m pytest tests\eval_recall -q
```

## 产物（D:\dshs\eval_recall\）

| 文件 | 说明 |
|---|---|
| `snapshot.db` | 生产 zenith.db 快照（含 WAL 回放），测试只写快照 |
| `pilot.json` | 16 条查询 + 机器初标候选（`label_status=draft`） |
| `PILOT_REVIEW.md` | 人工审查表：勾选确实相关记忆后回填 `confirmed_relevant_ids` |
| `baseline_draft.json` | 现有 n-gram 召回对 draft 标签的 Recall@k / Precision@k / 截断率 |

## 口径

- 相关集：人工确认的 `confirmed_relevant_ids`（正式基线）；确认前全部数字为 draft。
- Recall@k = 检索 top-k 命中相关集比例；Precision@k 同口径。
- 查询级截断率 = 相关记忆被召回但经 `MAX_INJECT_CHARS=1500` 组装后未完整进入最终 prompt 的查询占比。
- 条目级截断率 = 被截断的相关记忆条目 / 全部相关记忆条目。
- 可行性门槛（计划既定）：≥60% 查询拥有至少 1 条可确认相关记忆；确认后若不达标，30-50 规模不成立。
