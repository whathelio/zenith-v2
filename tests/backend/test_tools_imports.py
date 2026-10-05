"""架构守卫：模块级 import 无环 + 惰性 import 预算（AST 断言，不测功能）。

为什么需要本文件 —— 循环依赖此前**无任何守护**：`pyproject.toml:14` 用
`ignore = ["E402", ...]` 压制了「模块级以外 import」的告警，注释自述理由是
「规避 tools/confirm_flow 循环依赖」。压制是真，但**没人守住这条线**。

⚠️ 先更正一个流传的说法：曾有人报告「4 个循环依赖」，但**模块级 import 图实测无环**
（`analyze_import_cycles.py` 扫描 58 个模块，2-环与 3+ 环均为 0）。
真实情况是「**函数内** import 图存在环」—— 那正是刻意的延迟导入模式：
`database` 在函数内 import `memory_engine`，从而与「`memory_engine` 模块级 import `database`」
共存而不成环。

本文件的断言针对**模块级**图，因为只有它会真炸：
一旦成环，`import backend.app` 直接 `ImportError`，整个服务起不来。

⚠️ 新增模块级 import 会让本文件失败 —— 这是设计意图，不是误报。
若确需新增，先确认不引入环，再更新白名单。
"""
import ast
import importlib
import pathlib

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2] / "backend"

# tools.py 当前允许的模块级依赖（实测快照）。
# 它是全仓 fan-out 最高的模块（4105 行）；每新增一个模块级依赖都可能引入环。
TOOLS_ALLOWED_MODULE_DEPS = {
    "backend",
    "backend.config",
    "backend.confirm_flow",
    "backend.database",
    "backend.validators.sanitize_guard",
}

# tools.py 的函数内 import 目标数上限（当前实测 15）。留余量但不放任增长。
TOOLS_LAZY_IMPORT_BUDGET = 20


def _iter_modules():
    """遍历 backend 下的 Python 模块（排除归档 / 缓存 / 备份）。"""
    for p in BACKEND_DIR.rglob("*.py"):
        s = str(p)
        if "__pycache__" in s or "_archived" in s or ".bak" in p.name:
            continue
        rel = p.relative_to(BACKEND_DIR)
        name = "backend." + str(rel.with_suffix("")).replace("\\", ".").replace("/", ".")
        if name.endswith(".backend"):
            name = "backend"
        yield name, p


def _resolve_from(mod_name: str, node: ast.ImportFrom) -> list[str]:
    if node.level and node.level > 0:
        parts = mod_name.split(".")
        base = parts[: len(parts) - node.level]
        if node.module:
            return [".".join(base + [node.module])]
        return [".".join(base)]
    return [node.module] if node.module else []


def build_import_graph():
    """返回 (模块级依赖图, 函数内依赖图)，均只含 backend 内部边。"""
    module_level: dict[str, set] = {}
    lazy_level: dict[str, set] = {}

    for mod_name, path in _iter_modules():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        module_level.setdefault(mod_name, set())
        lazy_level.setdefault(mod_name, set())

        def walk(body, lazy: bool):
            for node in body:
                if isinstance(node, ast.ImportFrom):
                    for t in _resolve_from(mod_name, node):
                        if t.startswith("backend"):
                            (lazy_level if lazy else module_level)[mod_name].add(t)
                elif isinstance(node, ast.Import):
                    for a in node.names:
                        if a.name.startswith("backend"):
                            (lazy_level if lazy else module_level)[mod_name].add(a.name)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    walk(node.body, True)
                elif isinstance(node, (ast.If, ast.Try, ast.For, ast.While, ast.With)):
                    walk(node.body, lazy)
                    walk(getattr(node, "orelse", []) or [], lazy)
                    walk(getattr(node, "finalbody", []) or [], lazy)
                    for h in getattr(node, "handlers", []) or []:
                        walk(h.body, lazy)

        walk(tree.body, False)

    return module_level, lazy_level


def _find_cycles(graph: dict, max_len: int = 4):
    """DFS 找环，返回环路径列表（每个环只报一次）。"""
    cycles, seen = [], set()

    def dfs(node, path):
        if len(path) > max_len:
            return
        for nxt in graph.get(node, ()):
            if nxt == path[0] and len(path) >= 2:
                key = tuple(sorted(path))
                if key not in seen:
                    seen.add(key)
                    cycles.append(list(path))
            elif nxt not in path:
                dfs(nxt, path + [nxt])

    for n in sorted(graph):
        dfs(n, [n])
    return cycles


class TestModuleLevelGraphIsAcyclic:
    """模块级 import 不得成环 —— 它一旦成环，服务直接起不来。"""

    def test_backend_dir_is_found(self):
        assert BACKEND_DIR.is_dir(), f"未定位到 backend 目录：{BACKEND_DIR}"

    def test_graph_is_populated(self):
        module_level, _ = build_import_graph()
        assert len(module_level) > 40, "模块数异常，扫描可能失效"
        assert len(module_level["backend.database"]) > 0, "database 应有模块级依赖"

    def test_no_two_cycle(self):
        module_level, _ = build_import_graph()
        pairs = [
            (a, b) for a in sorted(module_level)
            for b in sorted(module_level.get(a, ()))
            if a in module_level.get(b, ()) and a < b
        ]
        assert pairs == [], f"发现 2-环：{pairs}"

    def test_no_longer_cycles(self):
        module_level, _ = build_import_graph()
        cycles = _find_cycles(module_level)
        assert cycles == [], "发现模块级环：" + "; ".join(" -> ".join(c) for c in cycles)

    def test_database_does_not_module_level_import_memory_engine(self):
        """🔴 `database` 是底层模块，模块级 import `memory_engine` 会立即成环。

        当前它只在**函数内** import（刻意的延迟导入，用于打破环）。
        本测试防止有人"顺手"把它提到模块级。
        """
        module_level, lazy_level = build_import_graph()
        assert "backend.memory_engine" not in module_level["backend.database"]
        assert "backend.memory_engine" in lazy_level["backend.database"], (
            "database 对 memory_engine 的依赖应仍在函数内（形态变了要复核）"
        )

    def test_memory_engine_module_level_imports_database(self):
        """反向确认：`memory_engine` 是模块级 import `database` 的（环的一半）。"""
        module_level, _ = build_import_graph()
        assert "backend.database" in module_level["backend.memory_engine"]


class TestCoreModulesImportable:
    """冒烟：核心模块能被 import（有环就会 ImportError）。"""

    def test_all_core_modules_import(self):
        for name in [
            "backend.config",
            "backend.database",
            "backend.memory_engine",
            "backend.unified_distill",
            "backend.llm_client",
            "backend.confirm_flow",
            "backend.tools",
            "backend.validators.sanitize_guard",
        ]:
            importlib.import_module(name)

    def test_import_order_independence(self):
        """`database` 能被单独 import（不依赖 `memory_engine` 先加载）。

        这是延迟导入模式的核心保证 —— 若有人把依赖提到模块级，这条会失败。
        """
        import backend.database  # noqa: F401


class TestLazyImportBudget:
    """惰性 import 预算 —— 防无节制增长。"""

    def test_tools_module_level_deps_within_whitelist(self):
        module_level, _ = build_import_graph()
        deps = module_level["backend.tools"]
        unexpected = deps - TOOLS_ALLOWED_MODULE_DEPS
        assert not unexpected, (
            f"tools.py 出现新的模块级依赖：{sorted(unexpected)}。"
            "若非有意，请改回函数内 import（见 pyproject.toml:14 的 E402 说明）。"
        )

    def test_tools_lazy_import_within_budget(self):
        _, lazy_level = build_import_graph()
        n = len(lazy_level["backend.tools"])
        assert n <= TOOLS_LAZY_IMPORT_BUDGET, (
            f"tools.py 函数内 import 目标数 {n} 超过预算 {TOOLS_LAZY_IMPORT_BUDGET}"
        )

    def test_tools_does_not_lazy_import_itself(self):
        """自引用是明显的写错。"""
        _, lazy_level = build_import_graph()
        assert "backend.tools" not in lazy_level["backend.tools"]


class TestNoModuleImportsItself:
    """任何模块都不应 import 自己（AST 层可查的最低级错误）。"""

    def test_no_self_import(self):
        module_level, lazy_level = build_import_graph()
        offenders = [
            m for m in module_level
            if m in module_level[m] or m in lazy_level.get(m, set())
        ]
        assert offenders == [], f"自引用模块：{offenders}"
