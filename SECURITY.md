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

多用户部署必须先用 Docker 隔离代码执行：

```bash
docker run --rm --memory=256m --cpus=0.5 --read-only \
  --network=none --cap-drop=ALL \
  -v /tmp/script.py:/script.py:ro \
  zenith-sandbox python /script.py
```

参考 `sandbox/Dockerfile`（Phase 3，待实现）。

---

## 报告漏洞

发现安全问题请提 GitHub Issue 或发邮件，不要公开讨论。

## 已知限制

| 组件 | 限制 |
|------|------|
| `code_runner.py` | 非沙箱，详见上文 |
| `mt5_service.py` | 仅 Windows，已有优雅降级 |
| `knowledge_service.py` | 转发到外部 `api_gateway`，依赖外部进程安全 |
| `/api/knowledge/ingest` | 上传 PDF 经 `review_pdf` 审查（覆盖率/大小），但不扫描恶意内容 |

## 配置建议

| 场景 | `code_execution_enabled` | 服务监听 | 备注 |
|------|--------------------------|----------|------|
| 本地个人使用 | `true` | `localhost` | 可接受 |
| 本地开发团队 | `false` | `localhost` | 用 Docker 跑代码 |
| 公网部署 | `false` | `0.0.0.0` + 反代 + 鉴权 | 必须 Docker 隔离 |
