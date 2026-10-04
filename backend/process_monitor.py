"""本机进程 / 端口监控 —— 采集层（纯 stdlib，不依赖 Zenith 内部模块）。

为什么放在 backend 而不是 tools：
    tools/ 里装的是离线脚本（audit / shield / unshield / zotero_parse），
    本模块是**运行时服务**，由 routers/processes.py 在请求期调用。

三条实测结论，改代码前先读，别踩回去：
1. 采集经 PowerShell + Win32_Process，PS 侧把结果写 UTF-8 文件再读回，
   规避中文系统 GBK 控制台编码。
2. 单次采集约 1.75s（PowerShell 0.65s + netstat 0.13s，其余为解释器启动与分类）。
   因此**必须有 TTL 缓存**，且**调用方必须 asyncio.to_thread** —— 见 routers/processes.py。
3. 命令行是密钥外泄通道（sk-… / Bearer … / --token=…）。实测当前 207 条命令行
   未命中任何密钥，但默认仍然脱敏；需要原样查看时用 raw=True（仅本机调试）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

# ------------------------------------------------------------------ 路径

# backend/process_monitor.py → parents[2] = 工作区根
_WORKSPACE = Path(__file__).resolve().parents[2]
_HOME = Path.home()
_DSH_HOME = _HOME / ".dsh"
_DSH_WORKSPACE = Path(r"D:\dshs")
_WORKBUDDY_DATA = Path(r"D:\WorkBuddyData\.workbuddy")

TTL_SECONDS = 15          # 快照缓存时长
_PS_TIMEOUT = 60

# ------------------------------------------------------------------ 端口用途

PORT_PURPOSE = {
    135: "Windows RPC 端点映射",
    139: "NetBIOS 会话服务",
    445: "SMB 文件共享",
    5040: "Windows 连接设备平台",
    7680: "Windows 更新传递优化",
    27036: "Steam 客户端 / 局域网发现",
    3080: "dsh Web UI",
    5173: "Vite 开发服务器",
    8766: "Zenith 主服务",
    8788: "Zenith 知识库中台 (RAG gateway)",
    3000: "通用 Node 开发服务",
    3306: "MySQL",
    5432: "PostgreSQL",
    5938: "TeamViewer",
    6379: "Redis",
    8000: "通用 HTTP 开发服务",
    8080: "通用 HTTP 代理 / 服务",
    9222: "Chrome DevTools 调试端口",
    11434: "Ollama 本地模型服务",
}

SYSTEM_NAMES = {
    "system", "registry", "memory compression", "smss.exe", "csrss.exe",
    "wininit.exe", "services.exe", "lsass.exe", "winlogon.exe",
    "fontdrvhost.exe", "dwm.exe", "svchost.exe", "explorer.exe",
    "spoolsv.exe", "taskhostw.exe", "sihost.exe", "ctfmon.exe",
    "runtimebroker.exe", "searchindexer.exe", "conhost.exe",
    "wmiprvse.exe", "dllhost.exe", "msdtc.exe", "audiodg.exe",
    "securityhealthservice.exe", "msmpeng.exe", "nissrv.exe",
    "rtkauduservice64.exe", "nvcontainer.exe", "audiosrv",
    "startmenuexperiencehost.exe", "shellexperiencehost.exe",
    "textinputhost.exe", "applicationframehost.exe",
    "crashpad_handler.exe", "usocoreworker.exe", "usoclient.exe",
    "wmiregistrationservice.exe", "unsecapp.exe", "wsqmcons.exe",
    "compattelrunner.exe", "backgroundtaskhost.exe", "moshostw.exe",
}

# ------------------------------------------------------------------ 项目签名

# 顺序即优先级：越具体的排越前。全部按小写子串匹配「命令行」。
PROJECTS: list[tuple[str, tuple[str, ...]]] = [
    ("Zenith",    ("zenith-v2", "zenith_rag", "api_gateway.py", "task_worker.py")),
    ("dsh",       ("deepseek-harness", "\\dshs", "d:/dshs", ".dsh\\", ".dsh/",
                   "dsh-mcp-client", "dsh-sandbox", "@deepseek-ai",
                   "dsh-native-mcp", "mcp-academic")),
    ("my-neuro",  ("my-neuro",)),
    ("airi",      ("\\airi", "/airi")),
    ("B站字幕",    ("bili-sub-ext", "bilibili-subtitle-extractor")),
    ("磁盘健康",   ("disk-health",)),
    ("文献知识库", ("shiji-kb", "shiji-zenith-skills", "知识库-v1.0")),
    ("PDF工具",   ("zsxq-pdf-tool",)),
    ("客户项目",   ("开发文件1",)),
    ("Claw",      ("\\claw", "claw_main")),
    ("devproject", ("devproject",)),
    ("work1/work2", ("work1.1024", "\\work2", "/work2")),
    ("AI记录室",   ("ai记录室",)),
    ("WorkBuddy", ("workbuddy", "workbudbydata")),
]

GROUP_SYSTEM = "系统"
GROUP_OTHER = "其他应用"

# ------------------------------------------------------------------ 脱敏

_REDACT_RULES = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{12,}"), "sk-***"),
    (re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._\-]{12,}"), r"\1***"),
    (re.compile(r"(?i)((?:api[_-]?key|token|password|passwd|secret)\s*[=:]\s*)\S{6,}"),
     r"\1***"),
    (re.compile(r"(?i)(--(?:api-?key|token|password|secret)[= ])\S{6,}"), r"\1***"),
]


def redact(text: str) -> str:
    """对命令行做防御性脱敏（当前未发现真实泄漏，属额外一层保险）。"""
    if not text:
        return ""
    for pat, repl in _REDACT_RULES:
        text = pat.sub(repl, text)
    return text


# ------------------------------------------------------------------ 进程采集

_PS_SCRIPT = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
    "Get-CimInstance Win32_Process | ForEach-Object { "
    "  [PSCustomObject]@{ "
    "    pid=[int]$_.ProcessId; ppid=[int]$_.ParentProcessId; "
    "    name=$_.Name; "
    "    ws=[long]$(if ($_.WorkingSetSize) { $_.WorkingSetSize } else { 0 }); "
    "    created=$(if ($_.CreationDate) "
    "      { $_.CreationDate.ToString('yyyy-MM-dd HH:mm:ss') } else { '' }); "
    "    cmd=$_.CommandLine "
    "  } "
    "} | ConvertTo-Json -Depth 3 -Compress | "
    "Out-File -Encoding utf8 -FilePath '__OUT__'"
)

# 注意：脚本里大量 PowerShell 花括号，绝不能用 str.format() 拼装
# （会报 "unexpected '{' in field name"）。用占位符替换。
_PS_OUT_TOKEN = "__OUT__"


def _collect_processes() -> list[dict]:
    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="zenith_proc_")
    os.close(fd)
    try:
        script = _PS_SCRIPT.replace(_PS_OUT_TOKEN, tmp)
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=_PS_TIMEOUT)
        raw = Path(tmp).read_text(encoding="utf-8-sig", errors="replace")
        if not raw.strip():
            raise RuntimeError(
                "PowerShell 未产出结果: "
                + r.stderr.decode("utf-8", "replace")[:200])
        data = json.loads(raw)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if isinstance(data, dict):
        data = [data]
    return data


def _collect_ports() -> list[dict]:
    r = subprocess.run(["netstat", "-ano"], capture_output=True,
                       timeout=_PS_TIMEOUT)
    txt = r.stdout.decode("cp936", "replace")
    rows = []
    for line in txt.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        proto = parts[0].upper()
        if proto not in ("TCP", "UDP"):
            continue
        if proto == "TCP" and (len(parts) < 5 or parts[-2] != "LISTENING"):
            continue
        local, pid = parts[1], parts[-1]
        if ":" not in local or not pid.isdigit():
            continue
        addr, _, port = local.rpartition(":")
        if not port.isdigit():
            continue
        rows.append({"proto": proto, "addr": addr,
                     "port": int(port), "pid": int(pid)})
    return rows


# ------------------------------------------------------------------ 分类

def _plugin_name(cmd: str) -> str:
    """从内置 MCP 的插件路径取可读插件名（去版本号与 mcp/dist 这类噪声目录）。"""
    noise = {"mcp", "dist", "src", "lib", "bin", "build", "out", "cache",
             "node_modules", "workbuddy-builtin"}
    norm = cmd.replace("\\", "/")
    segs = [s.strip().strip('"\'') for s in norm.split("/")]
    segs = [s for s in segs if s and not re.match(r"^\d+(\.\d+)", s)]
    while segs and (segs[-1].endswith((".mjs", ".js", ".py", ".exe"))
                    or segs[-1] in noise):
        segs.pop()
    return segs[-1] if segs else "(未知插件)"


def classify(name: str, cmd: str) -> tuple[str, str]:
    """返回 (分组, 明细)。识别不了就落「其他应用」，不做脑补。"""
    n, c = (name or "").lower(), (cmd or "").lower()

    for label, signs in PROJECTS:
        if not any(s in c for s in signs):
            continue
        if label == "Zenith":
            if "api_gateway.py" in c:
                return label, "知识库中台 8788"
            if "task_worker.py" in c:
                return label, "异步任务 worker"
            if "start.py" in c:
                return label, "启动器 / 主服务"
            return label, "相关进程"
        if label == "dsh":
            if "dsh-sandbox" in c or "runner.js" in c:
                return label, "沙箱执行器"
            if "mcp-academic" in c:
                return label, "学术情报 MCP"
            if "dsh-native-mcp" in c:
                return label, "原生 MCP"
            if "dsh-mcp-client" in c:
                return label, "MCP 客户端"
            if "bin.js" in c and "web" in c:
                return label, "Web UI 3080"
            return label, "相关进程"
        if label == "WorkBuddy":
            if "_mcp.py" in c:
                tail = c.replace("/", "\\").split("\\")[-1]
                return label, f"MCP: {tail.replace('_mcp.py', '')}"
            if "mcp-server.mjs" in c or "start.mjs" in c:
                return label, f"内置 MCP: {_plugin_name(c)}"
            if "workbuddy.exe" in n:
                return label, "主程序"
            return label, "相关进程"
        return label, "项目进程"

    if n in SYSTEM_NAMES or c.startswith("c:\\windows\\") or c == "":
        return GROUP_SYSTEM, ""
    return GROUP_OTHER, ""


# ------------------------------------------------------------------ dsh 侧信息

def read_dsh_profiles() -> dict:
    """抽取 dsh 各 profile 注入了哪些 MCP。

    dsh 会自行重写 cordis.patch.yml（实测 2026-09-11 17:08 改过一次），
    所以必须同时给出 mtime，否则基于它的判断会悄悄过期。
    """
    info = {"profiles": [], "skill_dirs": []}
    for prof in ("web", "headless"):
        p = _DSH_HOME / "profiles" / prof / "cordis.patch.yml"
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        mcp, cur = [], None
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            m = re.match(r"serverName:\s*['\"]?([\w\-.]+)", s)
            if m:
                cur = {"name": m.group(1), "script": ""}
                mcp.append(cur)
                continue
            if cur is None:
                continue
            m2 = re.match(r"args:\s*\[(.*)\]", s)
            if m2:
                cur["script"] = m2.group(1).split(",")[0].strip().strip("'\"")
                cur.pop("_block", None)
                continue
            if s == "args:":
                cur["_block"] = True
                continue
            if s.startswith("- ") and cur.get("_block"):
                cur["script"] = s[2:].strip().strip("'\"")
                cur.pop("_block", None)
        for block in re.findall(r"customSkillDirs:\s*\n((?:\s*-\s*.+\n?)+)", text):
            for ln in block.splitlines():
                v = ln.strip().lstrip("-").strip().strip("'\"")
                if v:
                    info["skill_dirs"].append(v)
        for inline in re.findall(r"customSkillDirs:\s*\[(.*?)\]", text):
            for v in inline.split(","):
                v = v.strip().strip("'\"")
                if v:
                    info["skill_dirs"].append(v)
        for m3 in mcp:
            m3.pop("_block", None)
        info["profiles"].append({
            "profile": prof,
            "path": str(p),
            "mtime": datetime.fromtimestamp(p.stat().st_mtime)
                       .strftime("%Y-%m-%d %H:%M:%S"),
            "mcp": mcp,
        })
    info["skill_dirs"] = sorted(set(info["skill_dirs"]))
    return info


def _recent_entries(root: Path, limit: int = 12) -> list[dict]:
    if not root.exists():
        return []
    items = []
    try:
        for child in root.iterdir():
            try:
                st = child.stat()
            except OSError:
                continue
            items.append({
                "name": child.name,
                "kind": "dir" if child.is_dir() else "file",
                "mtime": datetime.fromtimestamp(st.st_mtime)
                           .strftime("%Y-%m-%d %H:%M"),
                "_ts": st.st_mtime,
            })
    except OSError:
        return []
    items.sort(key=lambda x: -x["_ts"])
    for it in items:
        it.pop("_ts", None)
    return items[:limit]


def _file_size_mb(p: Path):
    try:
        return round(p.stat().st_size / 1048576, 2)
    except OSError:
        return None


# ------------------------------------------------------------------ 汇总

def collect(raw: bool = False) -> dict:
    """真采一次（约 1.75s）。不要直接在 async 端点里调用，见 routers/processes.py。"""
    t0 = time.monotonic()
    procs_raw = _collect_processes()
    ports = _collect_ports()

    procs = []
    for p in procs_raw:
        name = p.get("name") or ""
        cmd = (p.get("cmd") or "").strip()
        group, detail = classify(name, cmd)
        procs.append({
            "pid": p.get("pid"),
            "ppid": p.get("ppid"),
            "name": name,
            "mem_mb": round((p.get("ws") or 0) / 1048576, 1),
            "created": p.get("created") or "",
            "cmd": cmd if raw else redact(cmd),
            "group": group,
            "detail": detail,
        })

    by_pid = {p["pid"]: p for p in procs}
    for row in ports:
        owner = by_pid.get(row["pid"])
        row["proc"] = owner["name"] if owner else "(已退出)"
        row["group"] = owner["group"] if owner else "未知"
        row["purpose"] = PORT_PURPOSE.get(row["port"], "未识别")
    ports.sort(key=lambda x: x["port"])

    groups: dict[str, dict] = {}
    for p in procs:
        g = groups.setdefault(p["group"],
                              {"count": 0, "mem_mb": 0.0, "procs": []})
        g["count"] += 1
        g["mem_mb"] = round(g["mem_mb"] + p["mem_mb"], 1)
        g["procs"].append(p)
    for g in groups.values():
        g["procs"].sort(key=lambda x: -x["mem_mb"])

    def _g(name):
        return groups.get(name, {}).get("procs", [])

    dsh_procs = _g("dsh")
    dsh_block = {
        "web": next((p for p in dsh_procs if "Web UI" in p["detail"]), None),
        "sandbox": [p for p in dsh_procs if p["detail"] == "沙箱执行器"],
        "profiles": read_dsh_profiles(),
        "recent": _recent_entries(_DSH_WORKSPACE),
        "sessions": len(list((_DSH_HOME / "sessions").iterdir()))
                    if (_DSH_HOME / "sessions").exists() else 0,
    }

    zenith_procs = _g("Zenith")
    zenith_block = {
        "running": bool(zenith_procs),
        "listening": [r["port"] for r in ports if r["port"] in (8766, 8788)],
        "db_mb": _file_size_mb(_WORKSPACE / "zenith-v2" / "data" / "zenith.db"),
    }

    wb_procs = _g("WorkBuddy")
    mcp_counts, builtin_counts, main_procs = {}, {}, 0
    for p in wb_procs:
        d = p["detail"]
        if d.startswith("MCP: "):
            k = d.split("MCP: ", 1)[1]
            mcp_counts[k] = mcp_counts.get(k, 0) + 1
        elif d.startswith("内置 MCP: "):
            k = d.split("内置 MCP: ", 1)[1]
            builtin_counts[k] = builtin_counts.get(k, 0) + 1
        elif d == "主程序":
            main_procs += 1
    workbuddy_block = {
        "main_procs": main_procs,
        "mcp_counts": dict(sorted(mcp_counts.items())),
        "mcp_total": sum(mcp_counts.values()),
        "builtin_counts": dict(sorted(builtin_counts.items())),
        "builtin_total": sum(builtin_counts.values()),
        "proc_total": len(wb_procs),
        "mem_total_mb": round(sum(p["mem_mb"] for p in wb_procs), 1),
    }

    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "redacted": not raw,
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
        "summary": {
            "proc_total": len(procs),
            "port_total": len(ports),
            "mem_total_mb": round(sum(p["mem_mb"] for p in procs), 1),
            "groups": {k: {"count": v["count"], "mem_mb": v["mem_mb"]}
                       for k, v in groups.items()},
        },
        "ports": ports,
        "groups": {k: v["procs"] for k, v in groups.items()},
        "dsh": dsh_block,
        "zenith": zenith_block,
        "workbuddy": workbuddy_block,
    }


# ------------------------------------------------------------------ TTL 缓存

_cache_lock = threading.Lock()
_cache: dict = {"ts": 0.0, "data": None}


def snapshot(force: bool = False, raw: bool = False, ttl: int = TTL_SECONDS) -> dict:
    """带 TTL 缓存的快照。

    - 默认返回脱敏结果并命中缓存（页面秒开）。
    - force=True 强制重采（「立即刷新」按钮）。
    - raw=True 绕过缓存且不脱敏（仅本机调试）。
    """
    if raw:
        data = collect(raw=True)
        data["cached"] = False
        return data

    now = time.monotonic()
    if not force:
        with _cache_lock:
            if _cache["data"] is not None and (now - _cache["ts"]) < max(ttl, 0):
                data = dict(_cache["data"])
                data["cached"] = True
                data["age_s"] = round(now - _cache["ts"], 1)
                return data

    data = collect(raw=False)
    with _cache_lock:
        _cache["ts"] = time.monotonic()
        _cache["data"] = data
    out = dict(data)
    out["cached"] = False
    out["age_s"] = 0.0
    return out


def invalidate_cache() -> None:
    with _cache_lock:
        _cache["ts"] = 0.0
        _cache["data"] = None


if __name__ == "__main__":       # 便于单独冒烟
    print(json.dumps(snapshot(force=True)["summary"],
                     ensure_ascii=False, indent=2))
    sys.stdout.flush()
