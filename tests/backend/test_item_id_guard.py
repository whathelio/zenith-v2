"""A4：item_id 撞名 guard（N-1 §5）单测 —— 附加式、零迁移。

不依赖真实 ChromaDB：用**假 store**（只提供 `get_by_item`）+ monkeypatch
`load_progress`。覆盖任务要求的 5 组场景，并补 2 组生产实态。

⚠️ 重要事实（源码核实）：真实 `ChromaStore.get_by_item()`（core :188-198）只回传
`id/chunk_index/text/title`，**不含 source_file** ⇒ store 取不回旧路径；
guard 因此回落到 progress.json。test_6 专门锁死这条生产路径。
"""
import sys
from pathlib import Path

# 让 rag/ 可导入（模块以顶层名 zotero_parse_rag_core 被各处引用）
RAG_DIR = Path(__file__).resolve().parents[2] / "rag"
if str(RAG_DIR) not in sys.path:
    sys.path.insert(0, str(RAG_DIR))

import zotero_parse_rag_core as core


class FakeStore:
    """只实现 guard 用到的 get_by_item；返回预先给定的 chunk dict 列表。"""

    def __init__(self, items):
        self._items = list(items)

    def get_by_item(self, item_id):
        return list(self._items)


def _upgraded_id(p, extra=""):
    """guard 命中时的期望 id（与实现同式）：new_extra = extra|pathkey（extra 空则纯 pathkey）。"""
    path_key = str(p.resolve()).replace("\\", "/").lower()
    new_extra = f"{extra}|{path_key}" if extra else path_key
    return core.stable_item_id(p, new_extra)


def _patch_progress(monkeypatch, mapping):
    monkeypatch.setattr(core, "load_progress", lambda: dict(mapping))


# ---------------------------------------------------------------- 用例 1-5（任务要求）
def test_1_no_existing_id_equals_stable(monkeypatch, tmp_path):
    """无同 id → id 与 stable_item_id(p) 逐字节相等（零迁移证据）。"""
    p = tmp_path / "a" / "paper.md"
    _patch_progress(monkeypatch, {})
    item_id, collided = core.resolve_item_id(FakeStore([]), p)
    assert item_id == core.stable_item_id(p)
    assert collided is False
    assert (item_id, collided) == (core.stable_item_id(p), False)


def test_2_same_basename_same_path_idempotent(monkeypatch, tmp_path):
    """同 id、同 basename、同路径（重复入库）→ id 不变（幂等 upsert）。"""
    p = tmp_path / "a" / "paper.md"
    base = core.stable_item_id(p)
    store = FakeStore([{"id": f"{base}_0", "chunk_index": 0, "text": "x", "title": "t"}])
    _patch_progress(monkeypatch, {str(base): {"source_file": str(p)}})
    item_id, collided = core.resolve_item_id(store, p)
    assert item_id == base
    assert collided is False


def test_3_same_basename_diff_path_upgrades(monkeypatch, tmp_path):
    """同 id、同 basename、异路径 → id 改变 且 flag=True。"""
    p = tmp_path / "b" / "paper.md"      # 待入库（第二个）
    prev = tmp_path / "a" / "paper.md"   # 已在库（第一个）
    base = core.stable_item_id(p)
    assert base == core.stable_item_id(prev)  # 仅 basename 为键 ⇒ 必同 id
    store = FakeStore([{"id": f"{base}_0", "chunk_index": 0, "text": "x", "title": "t"}])
    _patch_progress(monkeypatch, {str(base): {"source_file": str(prev)}})
    item_id, collided = core.resolve_item_id(store, p)
    assert collided is True
    assert item_id == _upgraded_id(p)
    assert item_id != base


def test_4_diff_basename_no_upgrade(monkeypatch, tmp_path):
    """同 id、异 basename（不同文件但 hash 碰撞的假设场景）→ id 不变。"""
    p = tmp_path / "paper.md"
    other = tmp_path / "other.pdf"
    base = core.stable_item_id(p)
    store = FakeStore([{"id": f"{base}_0", "chunk_index": 0, "text": "x", "title": "t"}])
    _patch_progress(monkeypatch, {str(base): {"source_file": str(other)}})
    item_id, collided = core.resolve_item_id(store, p)
    assert item_id == base
    assert collided is False


def test_5_empty_or_missing_fields_safe(monkeypatch, tmp_path):
    """existing 为空 / 字段缺失 → 安全不崩，且 id 不变。"""
    p = tmp_path / "paper.md"
    base = core.stable_item_id(p)

    # (a) existing 为空
    _patch_progress(monkeypatch, {})
    assert core.resolve_item_id(FakeStore([]), p) == (base, False)

    # (b) existing 有 chunk 但缺 source_file，progress 也无记录
    store = FakeStore([{"id": f"{base}_0", "chunk_index": 0, "text": "x", "title": "t"}])
    _patch_progress(monkeypatch, {})
    assert core.resolve_item_id(store, p) == (base, False)

    # (c) progress 有记录但缺 source_file 字段
    _patch_progress(monkeypatch, {str(base): {"title": "t"}})
    assert core.resolve_item_id(store, p) == (base, False)

    # (d) store.get_by_item 抛异常 → 退化为「无同 id」
    class _BoomStore:
        def get_by_item(self, item_id):
            raise RuntimeError("hnsw 坏了")

    assert core.resolve_item_id(_BoomStore(), p) == (base, False)


# ---------------------------------------------------------------- 生产实态补充
def test_6_real_chroma_shape_falls_back_to_progress(monkeypatch, tmp_path):
    """生产实态：get_by_item 不带 source_file（同真实 ChromaStore）→ 靠 progress 命中。"""
    p = tmp_path / "b" / "paper.md"
    prev = tmp_path / "a" / "paper.md"
    base = core.stable_item_id(p)
    store = FakeStore([{"id": f"{base}_0", "chunk_index": 0, "text": "x", "title": "t"}])
    _patch_progress(monkeypatch, {str(base): {"source_file": str(prev)}})
    item_id, collided = core.resolve_item_id(store, p)
    assert collided is True
    assert item_id == _upgraded_id(p)


def test_7_pdf_extra_variant(monkeypatch, tmp_path):
    """ingest_pdf 路径：base=stable_item_id(p, source)，撞名后 extra 保留为 `source|全路径`。"""
    p = tmp_path / "b" / "paper.pdf"
    prev = tmp_path / "a" / "paper.pdf"
    base = core.stable_item_id(p, "pypdfium2")
    store = FakeStore([{"id": f"{base}_0", "chunk_index": 0, "text": "x", "title": "t"}])
    _patch_progress(monkeypatch, {str(base): {"source_file": str(prev)}})
    item_id, collided = core.resolve_item_id(store, p, "pypdfium2")
    assert collided is True
    assert item_id == _upgraded_id(p, "pypdfium2")   # `pypdfium2|<路径>`
    assert item_id != _upgraded_id(p)                # 抽取器被保留 ⇒ 与纯路径键不同
    assert item_id != base


def test_8_pdf_same_file_diff_extractor_distinct_ids(monkeypatch, tmp_path):
    """回归锁：同一 PDF 用不同抽取器入库 → 撞名升级后 id 仍不同（extra 语义未丢）。"""
    p = tmp_path / "b" / "paper.pdf"
    prev = tmp_path / "a" / "paper.pdf"
    base_a = core.stable_item_id(p, "pypdfium2")
    base_b = core.stable_item_id(p, "mineru")
    # 两个不同 extra 各自都在库里（模拟先后用两种抽取器灌过）
    store = FakeStore([{"id": f"{base_a}_0", "chunk_index": 0, "text": "x", "title": "t"}])
    _patch_progress(monkeypatch, {
        str(base_a): {"source_file": str(prev)},
        str(base_b): {"source_file": str(prev)},
    })
    id_a, col_a = core.resolve_item_id(store, p, "pypdfium2")
    id_b, col_b = core.resolve_item_id(store, p, "mineru")
    assert col_a is True and col_b is True
    assert id_a != id_b          # 关键：抽取器语义未丢，不会互相覆盖
