"""shiji-kb → zenith 本地知识库导入脚本（P1）

用法：
  python import_shiji.py --index-chapters [--limit N]    索引 N 篇 tagged.md 到 chromadb
  python import_shiji.py --import-entities [N]           导入 top-N 核心实体到 memories(type=fat)
  python import_shiji.py --stats                         查看当前库状态
  python import_shiji.py --index-chapters --dry-run      只打印不执行

说明：
  - index-chapters 直接 import 根目录 zotero_parse_rag_core.ingest_text_file（绕过 8788 网关）。
  - import-entities 读 shiji-kb/kg/entity_index.json，按出现次数排序取 top-N 进 memories。
  - 标注数据 CC BY-NC-SA 4.0，仅限个人非商用本地使用；用 source_conv_id 前缀打标。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

SHIJI_KB = Path(os.environ.get("SHIJI_KB_DIR", "./shiji-kb"))
ZENITH_DB = Path(os.environ.get("ZENITH_DB_PATH", "./data/zenith.db"))
CHAPTER_DIR = SHIJI_KB / "chapter_md"
ENTITY_INDEX = SHIJI_KB / "kg" / "entity_index.json"

# 实体类型 → 中文
TYPE_CN = {
    "person": "人物", "event": "事件", "place": "地名", "official": "官职",
    "time": "时间", "dynasty": "朝代", "institution": "制度", "tribe": "部族",
    "artifact": "器物", "astronomy": "天文", "mythical": "神话", "biology": "生物",
}


def index_chapters(limit: int | None = None, dry_run: bool = False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from zotero_parse_rag_core import ingest_text_file

    files = sorted(CHAPTER_DIR.glob("*.tagged.md"))
    if limit:
        files = files[:limit]
    if dry_run:
        print(f"[dry-run] 将索引 {len(files)} 篇：")
        for f in files[:10]:
            print(f"  - {f.name}")
        return

    ok = fail = 0
    for f in files:
        r = ingest_text_file(f, title=f.stem)
        if r.get("status") == "ok":
            ok += 1
        else:
            fail += 1
            print(f"  [FAIL] {f.name}: {r.get('reason')}")
        if ok % 10 == 0:
            print(f"  ... 已索引 {ok} 篇（失败 {fail}）")
    print(f"完成：{ok} 成功 / {fail} 失败")


def import_entities(top_n: int = 300, dry_run: bool = False):
    data = json.loads(ENTITY_INDEX.read_text(encoding="utf-8"))
    entities = []
    for etype, items in data.items():
        for name, info in items.items():
            refs = info.get("refs", [])
            aliases = info.get("aliases", [name]) if isinstance(info.get("aliases"), list) else [name]
            entities.append((name, etype, len(refs), aliases))

    entities.sort(key=lambda x: -x[2])
    top = entities[:top_n]

    if dry_run:
        print(f"[dry-run] 将导入 top-{top_n} 实体：")
        for name, etype, refs, _ in top[:20]:
            print(f"  - {name}（{TYPE_CN.get(etype, etype)}，出现 {refs} 次）")
        return

    conn = sqlite3.connect(ZENITH_DB)
    cur = conn.cursor()
    imported = skipped = 0
    for name, etype, refs, aliases in top:
        content = f"{name}——{TYPE_CN.get(etype, etype)}（《史记》实体，出现 {refs} 次）"
        keywords = ",".join([name] + [a for a in aliases if a != name][:4])
        cur.execute(
            "SELECT id FROM memories WHERE source_conv_id='shiji-kb:kg' AND content = ?",
            (content,),
        )
        if cur.fetchone():
            skipped += 1
            continue
        cur.execute(
            "INSERT INTO memories (type, content, importance, keywords, source_conv_id, recorded_at, created_at) "
            "VALUES ('fact', ?, 3, ?, 'shiji-kb:kg', datetime('now','localtime'), datetime('now','localtime'))",
            (content, keywords),
        )
        imported += 1
    conn.commit()
    conn.close()
    print(f"导入 {imported} 个实体（跳过已存在 {skipped}）")


def stats():
    # chromadb 片段数
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from zotero_parse_rag_core import get_store, load_progress
        store = get_store()
        print(f"chromadb 片段数: {store.count()}")
        prog = load_progress()
        print(f"progress.json 记录: {len(prog)} 篇")
    except Exception as e:
        print(f"chromadb 状态读取失败: {e}")

    # memories 实体数
    try:
        conn = sqlite3.connect(ZENITH_DB)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM memories WHERE source_conv_id LIKE 'shiji-kb:%'")
        print(f"memories 中 shiji-kb 来源记录: {cur.fetchone()[0]}")
        conn.close()
    except Exception as e:
        print(f"memories 状态读取失败: {e}")


def main():
    ap = argparse.ArgumentParser(description="shiji-kb → zenith 导入")
    ap.add_argument("--index-chapters", action="store_true", help="索引 chapter_md/*.tagged.md 到 chromadb")
    ap.add_argument("--import-entities", nargs="?", const=300, type=int, help="导入 top-N 核心实体（默认 300）")
    ap.add_argument("--stats", action="store_true", help="查看库状态")
    ap.add_argument("--limit", type=int, default=None, help="限制索引篇数（测试用）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不执行")
    args = ap.parse_args()

    if args.index_chapters:
        index_chapters(limit=args.limit, dry_run=args.dry_run)
    if args.import_entities:
        import_entities(top_n=args.import_entities, dry_run=args.dry_run)
    if args.stats:
        stats()
    if not (args.index_chapters or args.import_entities or args.stats):
        ap.print_help()


if __name__ == "__main__":
    main()
