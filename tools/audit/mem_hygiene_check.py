"""记忆引擎卫生门禁 — 6 项修复的可复跑验收脚本。

用法：
    python tools/audit/mem_hygiene_check.py                 # 自动复制生产库到临时文件后验证
    python tools/audit/mem_hygiene_check.py --db <路径>      # 指定库（仍会复制，绝不改原库）
    python tools/audit/mem_hygiene_check.py --json out.json

退出码：0 = 全部通过；1 = 有项失败（可直接入 CI）。

刻意设计为「先复制、再验证」：本脚本会执行写操作（归档/衰减/回写），
绝不能跑在生产库上。复制失败即中止，不做降级。

覆盖的修复项（2026-09-11）：
    B1  created_at 写入缺失 / 排序 NULL 垫底
    B2  引用回写缺失（mem_touch 单调用点）
    B3  衰减判据用入库时间冒充引用时间
    B4  每日/每周总结写入时「裸文本 vs 带前缀条目」不对称 → 去重失效
    B5  mem_consolidate 合并分支硬删除（无备份不可逆）
    B6  写入出口不统一，绕过 _is_duplicate 门禁
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_DB = REPO_ROOT / "data" / "zenith.db"

results: list[dict] = []


def check(code: str, name: str, fn):
    try:
        ok, detail = fn()
    except Exception as e:  # noqa: BLE001
        ok, detail = False, f"{type(e).__name__}: {e}"
    results.append({"code": code, "name": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {code} {name}\n        {detail}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    src = Path(args.db)
    if not src.exists():
        print(f"源库不存在: {src}")
        return 1

    tmpdir = tempfile.mkdtemp(prefix="mem_hygiene_")
    dst = Path(tmpdir) / "check.db"
    shutil.copy2(src, dst)
    for suffix in ("-wal", "-shm"):
        p = Path(str(src) + suffix)
        if p.exists():
            shutil.copy2(p, Path(str(dst) + suffix))
    print(f"已复制到临时库（原库不受影响）: {dst}\n")

    import backend.database as d
    d.DB_PATH = dst
    d.init_db()

    from backend import memory_engine as me

    # 记录基线
    with d.db() as c:
        base_total = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        base_imp1 = c.execute("SELECT COUNT(*) FROM memories WHERE importance=1").fetchone()[0]

    # ---- B1: created_at 回填 + NULL 安全排序 ----
    def b1():
        with d.db() as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(memories)").fetchall()}
            assert "archived" in cols, "memories 缺少 archived 列（迁移未生效）"
            null_ct = c.execute(
                "SELECT COUNT(*) FROM memories WHERE (created_at IS NULL OR created_at='') "
                "AND recorded_at IS NOT NULL AND recorded_at<>''"
            ).fetchone()[0]
        inj = d.mem_for_inject(limit=20)
        assert len(inj) == 20, f"mem_for_inject 只返回 {len(inj)} 条"
        return null_ct == 0, f"created_at 可回填却仍为空 = {null_ct}；注入窗口返回 {len(inj)} 条"

    # ---- B2: 引用回写 ----
    def b2():
        mid = d.mem_add(type_="fact", content="卫生门禁 B2 探针：引用回写应当更新 last_touched_at",
                        importance=3, keywords="hygiene,B2", dedup=False)
        assert mid > 0, f"mem_add 返回 {mid}"
        with d.db() as c:
            c.execute("UPDATE memories SET last_touched_at=NULL WHERE id=?", (mid,))
        before = d.mem_get(mid)["last_touched_at"]
        touched = me.mem_mark_read([mid])
        after = d.mem_get(mid)["last_touched_at"]
        return (before is None and after and touched == 1), f"回写前={before!r} 回写后={after!r} rowcount={touched}"

    # ---- B3: 衰减只在「有引用记录且超期」时生效 ----
    def b3():
        with d.db() as c:
            # 一条：有 30 天前的引用记录 → 应衰减
            mid_touched = d.mem_add(type_="fact", content="卫生门禁 B3-A：有引用记录的旧记忆",
                                    importance=3, keywords="hygiene,B3A", dedup=False)
            # 一条：无引用记录（last_touched_at 为空）→ 不应衰减
            mid_never = d.mem_add(type_="fact", content="卫生门禁 B3-B：从未被引用的旧记忆",
                                  importance=3, keywords="hygiene,B3B", dedup=False)
            old = "2026-01-01T00:00:00.000000"
            c.execute("UPDATE memories SET recorded_at=?, created_at=?, last_touched_at=? WHERE id=?",
                      (old, old, old, mid_touched))
            c.execute("UPDATE memories SET recorded_at=?, created_at=?, last_touched_at=NULL WHERE id=?",
                      (old, old, mid_never))
        me.mem_consolidate()
        a = d.mem_get(mid_touched)["importance"]
        b = d.mem_get(mid_never)["importance"]
        return (a == 2 and b == 3), f"有引用记录→importance={a}(期望2)；无引用记录→importance={b}(期望3)"

    # ---- B4: 去重基准对称（带前缀文本必须能被识别为重复） ----
    def b4():
        body = "卫生门禁 B4 探针：跨模块协作时把接口契约写进文档比口头约定更可靠"
        stored = f"[每日总结 2026-01-01] {body}"
        d.mem_add(type_="experience", content=stored, importance=4,
                  keywords="每日总结,2026-01-01", dedup=False)
        same = me._is_duplicate(stored)
        # 反例：完全不同的内容不应被误判为重复
        diff = me._is_duplicate("卫生门禁 B4 反例：用完全不相干的一句话验证误伤率")
        return (same and not diff), f"同形文本判定重复={same}(期望True)；无关文本判定重复={diff}(期望False)"

    # ---- B5: 合并分支归档而非删除 ----
    def b5():
        with d.db() as c:
            before = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        # 造一对高相似记忆（>= MERGE_SIM_THRESHOLD=0.85）
        s = "卫生门禁 B5 探针：合并高相似记忆时必须保留原文，归档而非删除"
        m1 = d.mem_add(type_="experience", content=s, importance=5, keywords="hygiene,B5", dedup=False)
        m2 = d.mem_add(type_="experience", content=s, importance=3, keywords="hygiene,B5", dedup=False)
        me.mem_consolidate()
        with d.db() as c:
            after = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            still = c.execute("SELECT COUNT(*) FROM memories WHERE id IN (?,?)", (m1, m2)).fetchone()[0]
        return (after == before + 2 and still == 2), (
            f"合并前计数={before} 造 2 条后={before+2} 合并后={after}；两条探针仍存在={still}（期望 2 → 未删除）"
        )

    # ---- B6: 写入门禁下沉（近似内容必须被拦，返回 -2） ----
    def b6():
        base = "卫生门禁 B6 探针：所有写入路径都必须经过同一道相似度门禁"
        d.mem_add(type_="fact", content=base, importance=3, keywords="hygiene,B6", dedup=True)
        again = d.mem_add(type_="fact", content=base, importance=3, keywords="hygiene,B6", dedup=True)
        off = d.mem_add(type_="fact", content=base, importance=3, keywords="hygiene,B6", dedup=False)
        return (again == -2 and off > 0), f"重复写入(门禁开)返回={again}(期望-2)；dedup=False 返回={off}(期望>0)"

    check("B1", "created_at 回填 + 排序 NULL 安全", b1)
    check("B2", "引用回写 last_touched_at", b2)
    check("B3", "衰减仅在「有引用记录且超期」时生效", b3)
    check("B4", "去重基准对称（带前缀文本可识别）", b4)
    check("B5", "合并分支归档而非删除", b5)
    check("B6", "写入门禁下沉（近似内容拦截）", b6)

    # ---- 归档可见性（附带项） ----
    def arch():
        mid = d.mem_add(type_="fact", content="卫生门禁附带项：归档后应退出检索但内容仍在",
                        importance=5, keywords="hygiene,archive", dedup=False)
        assert d.mem_archive(mid), "mem_archive 失败"
        hidden = all(m["id"] != mid for m in d.mem_list())
        in_search = all(m["id"] != mid for m in d.mem_search("卫生门禁附带项"))
        content_kept = d.mem_get(mid) is not None
        assert d.mem_unarchive(mid), "mem_unarchive 失败"
        visible = any(m["id"] == mid for m in d.mem_list())
        return (hidden and in_search and content_kept and visible), (
            f"归档后列表隐藏={hidden} 检索隐藏={in_search} 内容保留={content_kept} 取消归档后可见={visible}"
        )

    check("A1", "归档：退出检索但不丢内容（可逆）", arch)

    with d.db() as c:
        end_total = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        end_imp1 = c.execute("SELECT COUNT(*) FROM memories WHERE importance=1").fetchone()[0]

    def no_bulk_decay():
        grew = end_imp1 - base_imp1
        return grew <= 5, f"importance=1 数量 {base_imp1} → {end_imp1}（增量 {grew}，期望 ≤5；旧实现在此会批量下沉）"

    check("A2", "全量衰减已止住（imp=1 未批量增长）", no_bulk_decay)

    print(f"\n库规模：{base_total} → {end_total} 条（本次验证新增的探针条数即增量）")

    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    print(f"\n结果：{passed}/{total} 通过")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"passed": passed, "total": total, "results": results},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"JSON 报告 → {args.json}")

    shutil.rmtree(tmpdir, ignore_errors=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
