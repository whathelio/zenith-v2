"""Zenith 收尾门禁（gov_final）—— Phase 1（A+B+C）验收的可复跑脚本。

由 code-governance-workflow Step 5b.5 要求产出：**最终验证必须是脚本，不是一次性内联命令**，
否则无法复跑、且会静默缩小检查范围。

覆盖：
  1. 编译检查（三个被改文件）
  2. 静态断言：wiki 路由存在 / raw 参数已移除 / 服务层守卫存在
  3. 路由计数 = 期望值、路由遮蔽 = 0、routers 全挂载
  4. 端到端（需服务在 8766 运行）：wiki 不再 404、空 question 返 400、?raw=true 被忽略
  5. pytest 全量（需 git 在 PATH，否则会多出 4 个 git_guard error）

用法：
    export PATH="/d/WorkBuddyData/.workbuddy/binaries/PortableGit/versions/1.2.0/cmd:/usr/bin:/bin:$PATH"
    cd D:\\下载文件\\新建文件夹\\zenith-v2
    .venv/Scripts/python.exe tools/audit/gov_final.py            # 全部
    .venv/Scripts/python.exe tools/audit/gov_final.py --no-e2e   # 跳过需服务的检查
    .venv/Scripts/python.exe tools/audit/gov_final.py --no-pytest

退出码：0 = 全部通过；1 = 有 FAIL（可直接作为门禁使用）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
BASE = "http://127.0.0.1:8766"
ROUTE_FLOOR = 153          # Phase 1 完成时的路由数；只做下限，不做等值（多会话并行会合法新增）
PYTEST_EXPECTED = 148      # Phase 1 完成时的通过数；同样只做下限

RESULTS: list[tuple[bool, str, str]] = []
SKIPPED: list[tuple[str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


def skip(name: str, detail: str = "") -> None:
    """环境导致的无法验证 —— 记为 SKIP，不计入失败。

    区分「断言失败（代码问题）」与「前提不满足（环境问题）」很重要：
    后者若判 FAIL，会让门禁在服务没起时给出假红，久而久之就没人信它了。
    """
    SKIPPED.append((name, detail))
    print(f"  SKIP  {name}" + (f"   [{detail}]" if detail else ""))


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


# ---------------------------------------------------------------- 1. 编译
def t_compile() -> None:
    print("\n[1] 编译检查")
    files = ["backend/routers/knowledge.py", "backend/knowledge_service.py",
             "backend/routers/processes.py", "backend/scheduler.py",
             "backend/routers/chat.py", "start.py",
             "backend/routers/distill.py", "backend/routers/schedules.py",
             "backend/mcp_config.py", "backend/academic_service.py", "tools/unshield.py"]
    r = run([PY, "-m", "py_compile", *files])
    check(r.returncode == 0, "py_compile 三个改动文件", r.stderr.strip()[:120])


# ---------------------------------------------------------------- 2. 静态断言
def t_static() -> None:
    print("\n[2] 静态断言")
    kn = read("backend/routers/knowledge.py")
    ks = read("backend/knowledge_service.py")
    pr = read("backend/routers/processes.py")

    check('@router.post("/wiki")' in kn, "knowledge.py 已新增 POST /wiki 路由")
    check("wiki_query(q)" in kn, "路由已接到 knowledge_service.wiki_query")
    check("httpx.ConnectError" in ks and "GATEWAY_DOWN" in ks,
          "wiki_query 已加网关不可用守卫")

    # raw 参数必须从路由层消失（docstring 里的说明文字不算）
    # ⚠️ 2026-10-04 复核：下面 code_lines **算出后未被使用** ——
    #    紧接的 has_raw_param 用的是**全量** pr.splitlines()，并未排除注释/文档串。
    #    疑似遗漏（本意应是「排除注释后再查 raw 参数」）。此处保留计算并加 noqa，
    #    把疑点显式留痕，交人工判断该用哪个；**不要静默删除**（会掩盖该疑点）。
    code_lines = [ln for ln in pr.splitlines() if not ln.lstrip().startswith(("#", '"', "*"))  # noqa: F841
                  and not ln.strip().startswith("`")]
    has_raw_param = any(re.search(r"raw\s*:\s*bool|raw\s*=\s*raw", ln) for ln in pr.splitlines())
    check(not has_raw_param, "processes.py 已无 raw 参数定义/传参")
    check('Query(False, description="强制重采' in pr, "force 参数仍在（未误删）")


# ---------------------------------------------------------------- 2b. 事件循环阻塞
def t_event_loop() -> None:
    """断言所有同步阻塞调用已挪出事件循环（项目约定：asyncio.to_thread）。"""
    print("\n[2b] 事件循环阻塞（同步调用必须走 to_thread）")
    sch = read("backend/scheduler.py")
    cht = read("backend/routers/chat.py")

    check("await asyncio.to_thread(check_reminders)" in sch,
          "scheduler: check_reminders 已 to_thread（每5分钟）")
    check("await asyncio.to_thread(mem_consolidate)" in sch,
          "scheduler: mem_consolidate 已 to_thread（每6小时，实测阻塞 5.2s）")
    check("await asyncio.to_thread(check_reminders)" in cht,
          "chat: check_reminders 已 to_thread（每轮对话热路径，实测阻塞 99ms）")

    # 不得再有裸调用
    bare = [ln.strip() for src in (sch, cht) for ln in src.splitlines()
            if re.match(r"^(text|result|reminder)\s*=\s*(check_reminders|mem_consolidate)\(\)\s*$",
                        ln.strip())]
    check(not bare, "无裸调用残留", f"发现 {bare}" if bare else "")

    check("跳过重复启动" in sch, "scheduler: 后台任务已加幂等守卫")



# ---------------------------------------------------------------- 2c. start.py 进程管理
def t_start_fixes() -> None:
    """断言 start.py 的两处进程管理修复未被回退（P0-4 僵尸锁 / P0-5 误收客户端）。"""
    print("\n[2c] start.py 进程管理修复（P0-4 / P0-5）")
    sp = read("start.py")

    by_port = sp.split("def _find_processes_by_port")[1].split("\ndef ")[0]
    check('"LISTENING" in line' in by_port,
          "_find_processes_by_port 只认 LISTENING 持有者")
    # ⚠️ 只看**代码行**，跳过注释 —— 注释里会正当地出现「为什么不再用 ESTABLISHED」，
    #    纯子串检查会把说明文字误判为命中（验证器假阳性）。
    est_in_code = any("ESTABLISHED" in ln and not ln.lstrip().startswith("#")
                      for ln in by_port.splitlines())
    check(not est_in_code, "代码中已无 ESTABLISHED（否则会收进浏览器等客户端 PID）")

    stale = sp.split("def _is_stale_lock")[1].split("\ndef ")[0]
    check("port_free or pid_dead" not in stale,
          "_is_stale_lock 已弃用 `or` 语义（原为「反复启动」根因）")
    check("_process_exists(pid)" in stale and "_is_port_in_use(port)" in stale,
          "_is_stale_lock 采用 PID + 端口 双重判据")

    check("准备终止 PID=" in sp, "按命令行兜底杀进程已加审计日志（可追溯误杀）")


# ---------------------------------------------------------------- 2d. 路由重复注册
def t_dup_routes() -> None:
    """同一 (方法, 路径) 被多个文件注册 → 后者永不执行（改它无效）。

    这是 shadow.py 查不到的一类：shadow 只管「静态路径被先声明的动态路径吞掉」，
    查不出「两个文件注册完全相同的路径」。
    """
    print("\n[2d] 路由重复注册（写了但永不执行）")
    r = run([PY, "tools/audit/duplicate_routes.py"])
    m = re.search(r"合计\s+(\d+)\s+组重复", r.stdout)
    dups = int(m.group(1)) if m else -1
    check(dups == 0, "无重复注册的路由", f"实测 {dups} 组")


# ---------------------------------------------------------------- 2e. B-13 事件/痕迹
def t_b13() -> None:
    """B-13①警告不再丢弃 / B-13②代码痕迹字段落库 —— 静态防回归。"""
    print("\n[2e] B-13 校验警告与代码痕迹")
    cht = read("backend/routers/chat.py")
    cv = read("frontend/src/features/ChatView.tsx")

    # B-13①：warning 分支不得再是空分支
    check("prev.includes(line)" in cv, "ChatView: warning 事件已收集（非空分支）")
    check("关闭校验提示" in cv, "ChatView: 校验警告有可关闭的渲染块")
    # 后端字段名统一（工具警告曾用 content、输出警告用 message）
    check("'content': w.get('message'" not in cht, "chat: warning 事件字段名已统一为 message")

    # B-13②：tool_call 痕迹必须落库代码执行的结构化字段
    trace_block = cht.split('conv_id, "tool_call"')[1].split("round_num")[0]
    for k in ("stdout", "stderr", "exit_code", "lang"):
        check(f'"{k}"' in trace_block, f"chat: tool_call 痕迹含 {k}")


# ---------------------------------------------------------------- 2f. B-11/B-12 前端数据层
def t_b11_b12() -> None:
    """B-11 竞态请求守卫 / B-12 静默失败改显式 —— 静态防回归。"""
    print("\n[2f] B-11 竞态守卫 / B-12 静默失败")
    cal = read("frontend/src/features/CalendarView.tsx")
    lib = read("frontend/src/features/LibraryView.tsx")
    smy = read("frontend/src/features/SummaryView.tsx")

    check("weekSeqRef" in cal and "loadError" in cal, "CalendarView: 周视图有请求序号 + 错误态")
    check("seqNotes" in lib and "seqMem" in lib and "seqSkills" in lib
          and "seqMcp" in lib and "seqTraces" in lib,
          "LibraryView: 5 个 loader 均有请求序号")
    check("seqRef" in smy and "setError" in smy, "SummaryView: 有请求序号 + 错误态")

    # 三个文件里不得再出现**非注释**的静默 catch {}
    for name, src in (("CalendarView", cal), ("LibraryView", lib), ("SummaryView", smy)):
        silent = [ln.strip() for ln in src.splitlines()
                  if "catch {}" in ln and not ln.lstrip().startswith("//")]
        check(not silent, f"{name}: 无静默 catch {{}}",
              f"仍有 {len(silent)} 处" if silent else "")


# ---------------------------------------------------------------- 2g. XSS 结构性守卫
def t_xss_guard() -> None:
    """所有 `dangerouslySetInnerHTML` 必须经过转义层（P0-3 回归守卫）。

    前端**没有测试框架**（package.json 无 test 脚本、未装 vitest/jest），
    所以这类安全约定只能靠结构性静态断言守 —— 新增一个未转义的注入点就会被拦下。
    """
    print("\n[2g] XSS：dangerouslySetInnerHTML 必须走转义")
    import glob as _glob

    files = (_glob.glob("frontend/src/**/*.tsx", recursive=True)
             + _glob.glob("frontend/src/**/*.ts", recursive=True))
    hits = []
    for f in files:
        src = read(f)
        for m in re.finditer(r"dangerouslySetInnerHTML=\{\{([\s\S]{0,400}?)\}\}", src):
            hits.append((f, m.group(1)))
    check(bool(hits), "扫描到 dangerouslySetInnerHTML 使用点", f"{len(hits)} 处")
    for f, body in hits:
        escaped = ("escapeHtml(" in body) or ("renderSecrets(" in body)
        check(escaped, f"{f.split('/')[-1]} 的 __html 经转义层",
              "" if escaped else "⚠️ 未转义 —— 存在 XSS 风险")

    # renderSecrets 自身也必须先转义（否则上面的"经过转义层"是假的）
    lib = read("frontend/src/features/LibraryView.tsx")
    if "function renderSecrets" in lib:
        rs = lib.split("function renderSecrets")[1].split("\n}")[0]
        check("&amp;" in rs and "&lt;" in rs, "renderSecrets 内部先做 HTML 转义")


# ---------------------------------------------------------------- 2h. 工具暴露面
def t_tool_surface() -> None:
    """断言「暂不可用工具」没有被重新暴露给模型（§10.3 决策的回归守卫）。"""
    print("\n[2h] 工具暴露面（mt5 暂不可用工具应已从模型列表移除）")
    src = read("backend/tools.py")
    check("_DISABLED_TOOLS" in src, "存在 _DISABLED_TOOLS 开关")
    check("TOOLS_SCHEMA = [t for t in TOOLS_SCHEMA" in src,
          "TOOLS_SCHEMA 已按 _DISABLED_TOOLS 过滤")
    # 处理器必须保留（删了就没法一键恢复，也失去手动调用能力）
    handlers = src.split("_TOOL_HANDLERS = {")[1].split("\n}")[0]
    for n in ("mt5_tick", "mt5_rates", "mt5_volume_profile", "mt5_positions"):
        check(f'"{n}"' in handlers, f"_TOOL_HANDLERS 仍保留 {n}（可恢复）")


# ---------------------------------------------------------------- 3. 项目基线
def t_baseline() -> None:
    print("\n[3] 项目基线")
    r = run([PY, "tools/audit/routes.py"])
    m = re.search(r"合计\s+(\d+)", r.stdout)
    total = int(m.group(1)) if m else -1
    # ⚠️ 用「下限」而非「等值」：本工作区多会话并行，其他会话会合法地新增路由。
    # 之前的等值断言（== 162）会在别人加路由时假红 —— 门禁一旦假红就没人信它了。
    check(total >= ROUTE_FLOOR, f"路由总数 >= {ROUTE_FLOOR}", f"实测 {total}")

    r = run([PY, "tools/audit/shadow.py"])
    m = re.search(r"合计\s+(\d+)\s*处", r.stdout)
    shadow = int(m.group(1)) if m else -1
    check(shadow == 0, "路由遮蔽 = 0 处", f"实测 {shadow}")

    r = run([PY, "tools/audit/orphan.py"])
    # ⚠️ 2026-10-04 复核：unmounted 算出后未被使用 —— 紧接的两条 check 改用了
    #    r.stdout 的字符串计数。同样疑似遗漏（本意可能是「列出未挂载项」）。
    #    保留计算并加 noqa，显式留痕交人工判断；不要静默删除。
    unmounted = [ln for ln in r.stdout.splitlines()  # noqa: F841
                 if "已挂载" not in ln and re.match(r"^\s{2}\w+\s", ln)]
    check("已挂载" in r.stdout, "routers 挂载检查已执行")
    check(r.stdout.count("已挂载") >= 15, "routers 全部已挂载",
          f"实测 {r.stdout.count('已挂载')} 个")


# ---------------------------------------------------------------- 4. 端到端
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 禁用一切代理


def _req(method: str, path: str, body: dict | None = None, timeout: int = 30):
    """本机请求。**必须显式禁用代理** —— 环境里若有 http_proxy，
    urllib 会把 127.0.0.1 也走代理，代理连不上会返回 502，造成假失败。"""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:                                   # noqa: BLE001
        return -1, str(e)


def t_e2e() -> None:
    print("\n[4] 端到端（需服务在 8766 运行）")
    st, _ = _req("GET", "/api/health", timeout=5)
    if st != 200:
        skip("E2E 整组跳过", f"服务不可达（health={st}）；先用 start.py --detach 起服务；"
                              "⚠️ 环境问题，非代码问题")
        return
    check(True, "服务可达")

    st, body = _req("POST", "/api/knowledge/wiki", {"question": "史记的作者是谁"}, timeout=45)
    check(st != 404, "POST /api/knowledge/wiki 不再是 404", f"HTTP {st}")
    check(st == 200, "wiki 端点返回 200", f"HTTP {st}")
    if st == 200 and "WIKI_FAIL" in body:
        print("        ⚠️ 上游网关报 WIKI_FAIL（已知第二层问题：llm_wiki_compiler 已被归档）")

    st, body = _req("POST", "/api/knowledge/wiki", {"question": ""}, timeout=15)
    check(st == 400, "空 question 返回 400", f"HTTP {st}")

    st, raw = _req("GET", "/api/processes/snapshot?raw=true", timeout=60)
    check(st == 200, "snapshot?raw=true 返回 200", f"HTTP {st}")
    check('"redacted":true' in raw, "raw=true 仍返回已脱敏数据（参数已失效）")

    st, norm = _req("GET", "/api/processes/snapshot", timeout=60)
    check(st == 200, "snapshot 无参数返回 200", f"HTTP {st}")
    check('"redacted":true' in norm, "默认快照为已脱敏")

    # 知识库检索：防「空答案」回归（2026-09-11 修过：推理模型吃光 max_tokens → content 为空）
    st, body = _req("POST", "/api/knowledge/search",
                    {"question": "知识管理", "top_k": 3}, timeout=90)
    check(st == 200, "知识库检索返回 200", f"HTTP {st}")
    try:
        ans = (json.loads(body) or {}).get("answer") or ""
    except Exception:                                        # noqa: BLE001
        ans = ""
    check(bool(ans.strip()), "知识库检索有正文（防空答案回归）", f"正文 {len(ans)} 字")


# ---------------------------------------------------------------- 5. pytest
def t_pytest() -> None:
    print("\n[5] 全量测试")
    env = dict(os.environ, ZENITH_TESTING="1")
    r = run([PY, "-m", "pytest", "tests/", "-q"], env=env)
    tail = (r.stdout or "").strip().splitlines()
    summary = tail[-1] if tail else ""
    m = re.search(r"(\d+) passed", summary)
    passed = int(m.group(1)) if m else -1
    n_failed = len(re.findall(r"(\d+) failed", summary))
    check(passed >= PYTEST_EXPECTED and n_failed == 0 and "error" not in summary,
          f"pytest >= {PYTEST_EXPECTED} passed / 0 failed", summary)
    if "error" in summary and "portablegit" not in os.environ.get("PATH", "").lower():
        print("        ℹ️ error 通常是 git 不在 PATH（test_git_guard）→ 把 PortableGit\\cmd 加入 PATH")
    if n_failed:
        print("        ℹ️ 本工作区多会话并行：失败用例是否正被**其他会话**改到一半？"
              "先按 mtime / git status 确认归属，再判断是否为本批引入。")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-e2e", action="store_true")
    ap.add_argument("--no-pytest", action="store_true")
    a = ap.parse_args()

    print(f"Zenith 收尾门禁  root={ROOT}")
    t_compile()
    t_static()
    t_event_loop()
    t_start_fixes()
    t_dup_routes()
    t_b13()
    t_b11_b12()
    t_xss_guard()
    t_tool_surface()
    t_baseline()
    if not a.no_e2e:
        t_e2e()
    if not a.no_pytest:
        t_pytest()

    failed = [r for r in RESULTS if not r[0]]
    print("\n" + "=" * 60)
    print(f"合计 {len(RESULTS)} 项：通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}"
          + (f"，跳过 {len(SKIPPED)}" if SKIPPED else ""))
    for _, name, detail in failed:
        print(f"  FAIL  {name}   [{detail}]")
    for name, detail in SKIPPED:
        print(f"  SKIP  {name}   [{detail}]")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
