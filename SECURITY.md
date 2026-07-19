# Security Policy

## ⚠️ 重要安全声明

### 代码执行功能（`/api/code/run` + `execute_code` 工具）

Zenith v2 内置的 Python 代码执行功能**不是沙箱**，不提供任何真正的隔离：

| 风险 | 说明 |
|------|------|
| 权限继承 | 子进程继承主服务全部系统权限，可读写任意文件、执行系统命令 |
| 无资源限制 | 仅 `timeout` 限制，无内存/CPU/磁盘配额（OOM/磁盘填满风险） |
| 无网络隔离 | 用户代码可 `import socket`/`requests` 访问内网/外网，外带数据 |
| 模块未受限 | `subprocess`/`os`/`ctypes` 等危险模块可直接 import |

**默认关闭**：`config.yaml` 中 `code_execution_enabled: false`。开启前请确认你理解上述风险。

---

## 适用场景

### ✅ 本地单用户（可开启）

```yaml
# config.yaml
code_execution_enabled: true
```

适用条件（全部满足）：
- 仅你自己使用，不暴露给其他人
- 服务只监听 `localhost`（默认）
- 你信任自己提交给 LLM 的代码

### ❌ 多用户 / 公网部署（必须 Docker 隔离）

**禁止**在以下场景直接开启 `code_execution_enabled: true`：
- 服务监听 `0.0.0.0` 且端口对外可达
- 多人共用同一实例
- LLM 可被不可信用户提示词注入

多用户部署必须先用 Docker 隔离代码执行。Zenith v2 已内置 Docker 集成（`code_runner.py` 自动检测 Docker 可用性）：

```bash
# 1. 安装 Docker
#    Windows: Docker Desktop
#    Linux:   sudo apt install docker.io
#    macOS:   Docker Desktop

# 2. 拉取沙箱镜像（首次约 50MB）
docker pull python:3.13-slim

# 3. 在 config.yaml 启用代码执行
code_execution_enabled: true

# 4. 启动 Zenith（zenith.bat / zenith.sh）
#    code_runner.py 会自动检测 Docker 并走容器路径
```

Docker 执行参数（已内置，无需手动配置）：
- `--read-only` — 根文件系统只读
- `--network=none` — 完全断网
- `--memory=256m --cpus=0.5` — 资源配额
- `--cap-drop=ALL --security-opt=no-new-privileges` — 丢弃所有 Linux capabilities
- `--tmpfs=/tmp:rw,size=64m` — 临时目录可写但限 64MB
- `--rm` — 执行完即销毁

验证沙箱是否生效：
```bash
bash sandbox/test_sandbox.sh
```

### 降级模式（无 Docker 时）

若 Docker 不可用，`code_runner.py` 自动降级到加固子进程：
- Unix：`resource.setrlimit` 限制内存 256MB / CPU 10s / 文件 10MB
- 模块黑名单静态扫描（拦截 `subprocess`/`os.system`/`socket`/`ctypes`/`eval`/`exec`）
- 清空危险环境变量（不继承 `ZENITH_*`/`LLM_*`/`API_*`/`TOKEN*`/`SECRET*`/`KEY*`）

⚠️ 降级模式的黑名单可被绕过（如字符串拼接 import），仅作为权宜之计。生产环境必须安装 Docker。

---

## 报告漏洞

发现安全问题请提 GitHub Issue 或发邮件，不要公开讨论。

## 已知限制

| 组件 | 限制 |
|------|------|
| `code_runner.py` | Docker 模式：真隔离；降级模式：黑名单可被绕过 |
| `mt5_service.py` | 仅 Windows，已有优雅降级 |
| `knowledge_service.py` | 转发到外部 `api_gateway`，依赖外部进程安全 |
| `/api/knowledge/ingest` | 上传 PDF 经 `review_pdf` 审查（覆盖率/大小），但不扫描恶意内容 |

## 配置建议

| 场景 | `code_execution_enabled` | Docker | 服务监听 | 备注 |
|------|--------------------------|--------|----------|------|
| 本地个人使用 | `true` | 可选 | `localhost` | Docker 可用时自动真隔离 |
| 本地开发团队 | `true` | **必须** | `localhost` | 用 Docker 跑代码 |
| 公网部署 | `true` | **必须** | `0.0.0.0` + 反代 + 鉴权 | Docker + 用户认证 |
| 不需要代码执行 | `false` | 不需要 | 任意 | 最安全 |
