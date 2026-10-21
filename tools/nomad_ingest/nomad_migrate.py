# -*- coding: utf-8 -*-
"""NOMAD → Zenith 知识库迁移工具（单一菜单式脚本）

覆盖三步计划第 ③ 步「优化之后再注入 NOMAD 的内容」。

设计要点（全部来自 2026-10-08 实测踩坑，见
`知识库-v1.0/日志审查库/zenith/2026-10/NOMAD内容移植-实测与可行性-2026-10-08.md`）：
  1. libzim 3.13.1 的 `Entry.path` 对文章返回**标题**，不是 "A/..." 命名空间路径
     → 正文判定只用 `get_item().mimetype`，并排除 X/ M/ -/ 前缀
  2. ZIM 内 92% 的 text/html 是 `<meta http-equiv="refresh">` 重定向桩页
     → 必须显式剔除（本例 1301 条里 1200 条是桩页）
  3. 🔴 绝不用正则做 class 子串匹配 —— Vector 2022 根元素 class 含 `vector-toc-available`，
     子串命中 `.toc` 会把整篇文档删空（实测 37,439 字符 → 0）
     → 用 stdlib `html.parser` 做 classList **精确 token** 匹配

用法（默认 **dry-run**，不加 --apply 不写任何东西）：
    python nomad_migrate.py                          # 交互菜单
    python nomad_migrate.py --steps json-md,verify   # 选步
    python nomad_migrate.py --steps all --apply      # 全跑并真写入
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

# ---------------------------------------------------------------- 默认配置
_HERE = Path(__file__).resolve().parent
_PROJ = _HERE.parents[1]                      # zenith-v2/
_ROOT = _PROJ.parent                          # 工作区根（放 _nomad_src）

DEFAULT_SRC = _ROOT / "_nomad_src"            # NOMAD collections/*.json
DEFAULT_ZIM = _ROOT / "_diag_archive" / "2026-10-08" / "nomad_sample.zim"
# 🔴 输出目录必须纯 ASCII：ChromaDB 的 Rust HNSW reader 在含中文的路径下读不出索引
DEFAULT_OUT = Path(r"D:\dshs\nomad_md") if os.name == "nt" else Path("./nomad_md")
DEFAULT_COLLECTION = "nomad_ref"
DEFAULT_GATEWAY = os.environ.get("KNOWLEDGE_API_BASE", "http://127.0.0.1:8788")
# 不设公开默认值（2026-10-14 安全债 D9b）：未提供 key 时，对需要网关鉴权的
# upload 步骤（--via http/task）由 run_steps() 显式报错退出，绝不静默回落公开默认值。
DEFAULT_KEY = os.environ.get("ZENITH_API_KEY", "")
PAGE_URL = "https://github.com/Crosstalk-Solutions/project-nomad"

# ------------------------------------------------- NOMAD zim_extraction.ts 移植
REMOVE_TAGS = {"script", "style", "nav", "header", "footer", "noscript", "iframe",
               "svg", "head", "title"}
REMOVE_CLASS_TOKENS = {
    "navbox", "sidebar", "infobox", "mw-editsection", "reference", "reflist", "toc",
    "noprint", "mw-jump-link", "mw-headline-anchor", "navbar", "hatnote", "ambox",
    "sistersitebox", "portal", "authority-control", "catlinks", "printfooter",
    "mw-empty-elt",
}
REMOVE_IDS = {"toc", "catlinks", "mw-navigation", "footer", "siteSub", "jump-to-nav"}
NON_CONTENT_HEADING = re.compile(
    r"^\s*(see also|references|external links|further reading|notes|bibliography|"
    r"navigation|sources|citations)\s*$", re.I)
RE_META_REFRESH = re.compile(r'http-equiv=["\']?refresh', re.I)

VOID = {"br", "img", "hr", "meta", "link", "input", "area", "base", "col", "embed",
        "source", "track", "wbr"}
BLOCKY = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section",
          "table", "ul", "ol", "blockquote", "dd", "dt", "pre"}

_WIN_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                 *(f"LPT{i}" for i in range(1, 10))}


class _Stripper(HTMLParser):
    """DOM 级剥离：按 tag / class-token / id **精确**匹配，并支持标题分段截断。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.buf: list[str] = []
        self.skip = 0
        self.cut = False

    @staticmethod
    def _classes_ids(attrs):
        cls, idv = set(), ""
        for k, v in attrs:
            if k == "class" and v:
                cls |= {t for t in v.split() if t}
            elif k == "id" and v:
                idv = v
        return cls, idv

    def _removable(self, tag, attrs):
        if tag in REMOVE_TAGS:
            return True
        cls, idv = self._classes_ids(attrs)
        return bool(cls & REMOVE_CLASS_TOKENS) or idv in REMOVE_IDS

    def handle_starttag(self, tag, attrs):
        if self.cut:
            return
        if self.skip:
            if tag not in VOID:
                self.skip += 1
            return
        if self._removable(tag, attrs):
            if tag not in VOID:
                self.skip = 1
            return
        if tag in BLOCKY:
            self.buf.append("\n")
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.buf.append(f"\n\n@@H{tag[1]}@@")

    def handle_startendtag(self, tag, attrs):
        if self.skip or self.cut:
            return
        if tag in BLOCKY:
            self.buf.append("\n")

    def handle_endtag(self, tag):
        if self.cut or tag in VOID:
            return
        if self.skip:
            self.skip -= 1
            return
        if tag in BLOCKY:
            self.buf.append("\n")

    def handle_data(self, data):
        if self.skip or self.cut:
            return
        self.buf.append(data)

    def text(self) -> str:
        s = "".join(self.buf)
        s = re.sub(r"[ \t\r\f\v]+", " ", s)
        out = []
        for line in s.split("\n"):
            m = re.match(r"\s*@@H([1-6])@@\s*(.*)", line)
            if m:
                title = m.group(2).strip()
                if NON_CONTENT_HEADING.match(title):
                    break                      # 非内容小节起，之后全部丢弃
                out.append(f"\n{'#' * (int(m.group(1)) + 1)} {title}\n")
            else:
                out.append(line)
        s = "\n".join(out)
        return re.sub(r"\n\s*\n+", "\n\n", s).strip()


def html_to_text(html: str) -> str:
    p = _Stripper()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    return p.text()


def safe_filename(title: str, used: set[str], maxlen: int = 120) -> str:
    name = _WIN_BAD.sub("_", (title or "").strip()) or "untitled"
    name = re.sub(r"\s+", "_", name).strip("._") or "untitled"
    if name.upper() in _WIN_RESERVED:
        name = "_" + name
    if len(name) > maxlen:
        name = name[:maxlen]
    base, i = name, 1
    while name.lower() in used:
        i += 1
        name = f"{base}_{i}"
    used.add(name.lower())
    return name + ".md"


# ---------------------------------------------------------------- 分块口径
def _import_split():
    """复用生产 split_text（512 窗口 / 64 重叠），保证与既有语料同口径。"""
    try:
        sys.path.insert(0, str(_ROOT))
        from zotero_parse_rag_core import split_text
        return split_text
    except Exception:
        def split_text(text, max_size=512, overlap=64):
            chunks, start = [], 0
            while start < len(text):
                end = min(start + max_size, len(text))
                chunks.append(text[start:end])
                if end >= len(text):
                    break
                start += max_size - overlap
            return chunks
        return split_text


# ---------------------------------------------------------------- 步骤 1：结构化 JSON → MD
def step_json_md(src: Path, out: Path, apply: bool) -> dict:
    split_text = _import_split()
    out_dir = out / "structured"
    files: list[tuple[str, str]] = []          # (filename, text)

    cond_f = src / "conditions.json"
    if cond_f.exists():
        d = json.loads(cond_f.read_text(encoding="utf-8"))
        for c in d.get("conditions", []):
            terms = "、".join(c.get("searchTerms", []))
            txt = (f"# {c['label']}\n\n"
                   f"- 类别：{c['category']}\n"
                   f"- 标识：{c['slug']}\n"
                   f"- 常见检索词：{terms}\n\n"
                   f"该条目属于「{c['category']}」类的常见症状/状况。")
            files.append((f"cond__{c['slug']}.md", txt))

    for fn, key, label in (("home_remedies.json", "remedies", "家庭疗法"),
                           ("natural_remedies.json", "remedies", "天然草药")):
        f = src / fn
        if not f.exists():
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        src_meta = d.get("source", {})
        for r in d.get(key, []):
            if not isinstance(r, dict):
                continue
            parts = [f"# {r['name']}", "",
                     f"- 类别：{label}",
                     f"- 标识：{r.get('slug','')}"]
            if r.get("commonNames"):
                parts.append(f"- 别名：{'、'.join(r['commonNames'])}")
            if r.get("conditions"):
                parts.append(f"- 适用症状：{'、'.join(r['conditions'])}")
            parts.append("")
            if r.get("uses"):
                parts += ["## 用途", r["uses"], ""]
            if r.get("how"):
                parts += ["## 用法", r["how"], ""]
            if r.get("evidence"):
                parts += ["## 依据", r["evidence"], ""]
            if r.get("cautions"):
                parts += ["## 注意事项", r["cautions"], ""]
            if r.get("sourceUrl"):
                parts.append(f"来源：{r['sourceUrl']}")
            if src_meta.get("name"):
                parts.append(f"数据来源：{src_meta['name']}（{src_meta.get('license','')}）")
            files.append((f"{fn.split('.')[0]}__{r.get('slug','x')}.md",
                          "\n".join(parts)))

    total_chars = sum(len(t) for _, t in files)
    pred_chunks = sum(len(split_text(t)) for _, t in files)
    rep = {"step": "json-md", "apply": apply, "out_dir": str(out_dir),
           "files": len(files), "chars": total_chars, "pred_chunks": pred_chunks}
    if not apply:
        rep["sample"] = [{"file": n, "chars": len(t)} for n, t in files[:5]]
        return rep

    out_dir.mkdir(parents=True, exist_ok=True)
    for n, t in files:
        (out_dir / n).write_text(t, encoding="utf-8")
    (out_dir / "_MANIFEST.json").write_text(json.dumps(
        {"source": PAGE_URL, "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
         "files": [n for n, _ in files]}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    rep["written"] = len(files)
    return rep


# ---------------------------------------------------------------- 步骤 2：ZIM → MD
def step_zim_md(zim: Path, out: Path, apply: bool, limit: int = 0) -> dict:
    try:
        from libzim.reader import Archive
    except ImportError as e:
        return {"step": "zim-md", "error": f"libzim 未安装: {e}"}
    if not zim.exists():
        return {"step": "zim-md", "error": f"ZIM 不存在: {zim}"}

    split_text = _import_split()
    out_dir = out / "zim"
    a = Archive(str(zim))

    total_entries = real = stubs = redir = 0
    files: list[tuple[str, str, str]] = []     # (filename, path, text)
    for i in range(a.all_entry_count):
        total_entries += 1
        try:
            e = a._get_entry_by_id(i)
        except Exception:
            continue
        if e.is_redirect:
            redir += 1
            continue
        path = e.path or ""
        if path.startswith(("X/", "M/", "-/")):
            continue                            # 元数据命名空间
        try:
            item = e.get_item()
        except Exception:
            continue
        if not item.mimetype.startswith("text/html"):
            continue
        try:
            html = bytes(item.content).decode("utf-8", "ignore")
        except Exception:
            continue
        if RE_META_REFRESH.search(html) or len(html) < 1000:
            stubs += 1                          # 坑 2：meta-refresh 桩页
            continue
        txt = html_to_text(html)
        if len(txt) < 50:
            continue
        real += 1
        files.append((path, path, txt))
        if limit and len(files) >= limit:
            break

    used: set[str] = set()
    named = [(safe_filename(p, used), p, t) for p, _, t in files]
    total_chars = sum(len(t) for _, _, t in named)
    pred_chunks = sum(len(split_text(t)) for _, _, t in named)
    rep = {"step": "zim-md", "apply": apply, "zim": str(zim),
           "zim_mb": round(zim.stat().st_size / 1024 / 1024, 2),
           "entries": total_entries, "redirect": redir, "stubs": stubs,
           "articles": real, "chars": total_chars,
           "avg_chars": round(total_chars / max(real, 1), 1),
           "pred_chunks": pred_chunks,
           "avg_chunks": round(pred_chunks / max(real, 1), 2),
           "out_dir": str(out_dir)}
    if not apply:
        rep["sample"] = [{"file": n, "entry": p, "chars": len(t)}
                         for n, p, t in named[:5]]
        return rep

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for n, p, t in named:
        (out_dir / n).write_text(t, encoding="utf-8")
        manifest.append({"file": n, "zim_entry": p})
    (out_dir / "_MANIFEST.json").write_text(json.dumps(
        {"source": f"{PAGE_URL} (ZIM {zim.name})",
         "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
         "count": len(manifest), "files": manifest}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    rep["written"] = len(named)
    return rep


# ---------------------------------------------------------------- 步骤 3：上传入库
def _md_files(out: Path) -> list[Path]:
    got = []
    for sub in ("structured", "zim"):
        d = out / sub
        if d.is_dir():
            got += sorted(p for p in d.glob("*.md"))
    return got


def step_upload(out: Path, collection: str, apply: bool, via: str,
                gateway: str, api_key: str, batch: int = 50) -> dict:
    files = _md_files(out)
    if not files:
        return {"step": "upload", "error": f"没有可上传的 .md（先跑 json-md / zim-md）：{out}"}
    split_text = _import_split()
    pred = sum(len(split_text(p.read_text(encoding="utf-8"))) for p in files)
    rep = {"step": "upload", "apply": apply, "via": via, "collection": collection,
           "files": len(files), "pred_chunks": pred}

    if not apply:
        total_mb = sum(p.stat().st_size for p in files) / 1024 / 1024
        rep["total_mb"] = round(total_mb, 2)
        # 按 52 chunk/s（实测 CPU）估算嵌入耗时
        rep["est_embed_seconds"] = round(pred / 52.0, 1)
        return rep

    t0 = time.time()
    if via == "local":
        sys.path.insert(0, str(_ROOT))
        from zotero_parse_rag_core import ingest_paths
        res = ingest_paths([str(p) for p in files], collection=collection)
        rep.update(res)
    elif via == "task":
        import httpx
        r = httpx.post(f"{gateway}/tasks", headers={"X-API-Key": api_key},
                       json={"type": "ingest",
                             "payload": {"paths": [str(p) for p in files],
                                         "collection": collection}},
                       timeout=30.0)
        rep["task"] = r.json()
        rep["note"] = "任务已建，用 GET /tasks/{id} 轮询；或直接看 worker 日志"
    elif via == "http":
        import httpx
        ok = failed = chunks = 0
        fails = []
        for i in range(0, len(files), batch):
            grp = files[i:i + batch]
            mf = [("files", (p.name, p.read_bytes(), "text/markdown")) for p in grp]
            r = httpx.post(f"{gateway}/ingest/batch",
                           headers={"X-API-Key": api_key},
                           params={"collection": collection},
                           files=mf, timeout=600.0)
            j = r.json()
            ok += j.get("ingested", 0)
            failed += j.get("failed", 0)
            chunks += j.get("chunks", 0)
            fails += j.get("failures", [])
            print(f"    [batch {i//batch+1}] {len(grp)} 文件 → ok={j.get('ingested')} "
                  f"chunks={j.get('chunks')} failed={j.get('failed')}")
        rep.update({"ingested": ok, "failed": failed, "chunks": chunks,
                    "failures": fails[:20]})
    else:
        rep["error"] = f"未知上传方式: {via}"
        return rep

    rep["seconds"] = round(time.time() - t0, 1)
    return rep


# ---------------------------------------------------------------- 步骤 4：检索验证
PROBES = [
    ("结构化", "honey for cough",            ["structured"]),
    ("结构化", "oral rehydration solution",  ["structured"]),
    ("结构化", "turmeric uses",              ["structured"]),
    ("ZIM",    "Catholic Church history",    ["zim"]),
    ("ZIM",    "amphibian",                  ["zim"]),
    ("跨库",   "What is used for a cough?",  ["structured", "zim"]),
]


def step_verify(collection: str, gateway: str, api_key: str, via: str) -> dict:
    """LLM-free 检索验证：直接看向量检索的 top-3 命中与相似度。

    ⚠️ 2026-10-08 修正两处工具缺陷（复核暴露）：
    1) 原先用全局 `MIN_SCORE` 判定 —— 但生产 `answer()` 走的是
       `min_score_for(collection)`（支持按集合覆盖）。工具报的地板值与
       实际生效值可能不同 → 改为调用 `min_score_for(collection)`。
    2) 原先 `pass = sim >= 地板`、`any_pass = any(pass)` ——
       **完全不判断内容相不相关**，只要 top-3 里有一条过地板就记「通过」。
       实测因此把一次**检索完全失败**（top-3 全无关）误报成通过。
       现在额外给出 `top1` 与 `any_pass` 的语义说明，由人判断相关性；
       工具不再宣称「探针通过」等价于「检索正确」。
    """
    sys.path.insert(0, str(_ROOT))
    from zotero_parse_rag_core import get_store, get_embedder, MIN_SCORE, min_score_for
    store = get_store(collection)
    emb = get_embedder()
    floor = min_score_for(collection)
    out = {"step": "verify", "collection": collection, "count": store.count(),
           "min_score": floor, "global_min_score": MIN_SCORE,
           "floor_note": "pass 仅表示「相似度过地板」，不代表内容相关；请人工核对 top1",
           "probes": []}
    for tag, q, _src in PROBES:
        v = emb.encode([q], normalize_embeddings=True).tolist()[0]
        r = store.query(v, top_k=3)
        docs = (r.get("documents") or [[]])[0]
        metas = (r.get("metadatas") or [[]])[0]
        dist = (r.get("distances") or [[]])[0]
        hits = []
        for i, d in enumerate(docs):
            sim = 1.0 - float(dist[i]) if i < len(dist) else None
            hits.append({"sim": round(sim, 4) if sim is not None else None,
                         "pass": bool(sim is not None and sim >= floor),
                         "title": (metas[i] or {}).get("title", ""),
                         "head": (d or "")[:110].replace("\n", " ")})
        out["probes"].append({"tag": tag, "q": q, "hits": hits,
                              "top1": hits[0]["title"] if hits else "",
                              "top1_sim": hits[0]["sim"] if hits else None,
                              "any_pass": any(h["pass"] for h in hits)})
    return out


# ---------------------------------------------------------------- 菜单
MENU = """
════════ NOMAD → Zenith 迁移 ════════
  1) json-md   结构化内容 → MD（conditions / home_remedies / natural_remedies）
  2) zim-md    ZIM → MD（含三坑加固）
  3) upload    上传 MD 到指定集合
  4) verify    检索验证（LLM-free）
  5) all       1+2+3+4
  q) 退出
"""


def run_steps(steps: list[str], args) -> None:
    src, out = Path(args.src), Path(args.out)
    zim = Path(args.zim)
    reports = []
    # 2026-10-14 安全债 D9b：upload 走网关（--via http/task）必须有 key，
    # 缺失时显式报错退出，不再静默使用公开默认值（=已知后门）。
    if "upload" in steps and args.via != "local" and not args.api_key:
        print(f"[nomad_migrate] 错误：--via {args.via} 需要 API Key，但未提供。"
              "请设置 ZENITH_API_KEY 环境变量，或显式传 --api-key。", file=sys.stderr)
        raise SystemExit(1)
    for s in steps:
        print(f"\n───── {s} ─────")
        if s == "json-md":
            r = step_json_md(src, out, args.apply)
        elif s == "zim-md":
            r = step_zim_md(zim, out, args.apply, limit=args.limit)
        elif s == "upload":
            r = step_upload(out, args.collection, args.apply, args.via,
                            args.gateway, args.api_key, args.batch)
        elif s == "verify":
            r = step_verify(args.collection, args.gateway, args.api_key, args.via)
        else:
            print(f"  未知步骤: {s}")
            continue
        reports.append(r)
        print(json.dumps(r, ensure_ascii=False, indent=2)[:4000])
    rep_file = Path(args.report) if args.report else (
        Path(_ROOT) / "_diag_archive" / "2026-10-08" / "nomad_migrate_report.json")
    rep_file.parent.mkdir(parents=True, exist_ok=True)
    rep_file.write_text(json.dumps(
        {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "apply": args.apply,
         "collection": args.collection, "via": args.via, "reports": reports},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[报告] {rep_file}")


def main():
    ap = argparse.ArgumentParser(description="NOMAD → Zenith 迁移")
    ap.add_argument("--steps", default="",
                    help="逗号分隔：json-md,zim-md,upload,verify,all")
    ap.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run）")
    ap.add_argument("--src", default=str(DEFAULT_SRC))
    ap.add_argument("--zim", default=str(DEFAULT_ZIM))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--collection", default=DEFAULT_COLLECTION)
    ap.add_argument("--via", default="local", choices=["local", "http", "task"])
    ap.add_argument("--gateway", default=DEFAULT_GATEWAY)
    ap.add_argument("--api-key", default=DEFAULT_KEY)
    ap.add_argument("--batch", type=int, default=50)
    ap.add_argument("--limit", type=int, default=0, help="zim-md 只处理前 N 篇")
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    if args.steps:
        steps = ["json-md", "zim-md", "upload", "verify"] if args.steps == "all" \
            else [s.strip() for s in args.steps.split(",") if s.strip()]
        run_steps(steps, args)
        return

    print(MENU)
    while True:
        try:
            c = input("选择 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if c in ("q", "Q", ""):
            break
        if c == "5":
            steps = ["json-md", "zim-md", "upload", "verify"]
        elif c in ("1", "2", "3", "4"):
            steps = [{"1": "json-md", "2": "zim-md",
                      "3": "upload", "4": "verify"}[c]]
        else:
            print("  无效选项")
            continue
        if not args.apply and any(s in ("json-md", "zim-md", "upload") for s in steps):
            ans = input("  ⚠️ 当前为 dry-run（不写入）。要真正执行吗？输入 yes 继续 > ").strip()
            if ans.lower() == "yes":
                args.apply = True
        run_steps(steps, args)
        args.apply = False


if __name__ == "__main__":
    main()
