#!/bin/bash
# Zenith v2 — Linux/macOS 启动脚本
# 用法: bash zenith.sh [port] [--no-browser] [--stop|--status|--reset-lock]

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${1:-8766}"
case "$PORT" in
  -*|"") PORT=8766 ;;
esac
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── 检查 venv ──
if [ ! -f "$VENV_PYTHON" ]; then
    echo "未找到 .venv/bin/python，请先创建虚拟环境："
    echo "  python3 -m venv .venv"
    echo "  .venv/bin/pip install -r requirements.txt"
    exit 1
fi

# 纯控制命令（--stop/--status/--reset-lock/--wait）直接交给 start.py，无需配置与辅助服务
case "${1:-}" in
  --stop|--status|--reset-lock|--wait|--help|-h)
      exec "$VENV_PYTHON" start.py "$@"
      ;;
esac

# ── 检查 config.yaml ──
CONFIG_FILE="$SCRIPT_DIR/config/config.yaml"
CONFIG_EXAMPLE="$SCRIPT_DIR/config/config.yaml.example"
if [ ! -f "$CONFIG_FILE" ]; then
    if [ -f "$CONFIG_EXAMPLE" ]; then
        cp "$CONFIG_EXAMPLE" "$CONFIG_FILE"
        echo "============================================================"
        echo "  首次运行 - 已从模板创建 config.yaml"
        echo "  请编辑 config/config.yaml 填入你的 API Key 后重新运行"
        echo "============================================================"
        exit 0
    else
        echo "错误: 找不到 config/config.yaml"
        exit 1
    fi
fi

# ── 从 config.yaml 提取 API 配置 ──
export LLM_API_KEY=$(grep "api_key:" "$CONFIG_FILE" | head -1 | sed 's/.*api_key:[[:space:]]*//' || true)
export LLM_BASE_URL=$(grep "api_base:" "$CONFIG_FILE" | head -1 | sed 's/.*api_base:[[:space:]]*//' || true)
export LLM_MODEL=$(grep "^model:" "$CONFIG_FILE" | head -1 | sed 's/.*model:[[:space:]]*//' || true)

# ── 知识库网关认证（与 api_gateway.py 保持一致） ──
# 2026-10-14 安全债 D9b：去掉公开默认值（原 `zenith-local`）。
# 取值优先级与 start.py 的 _read_env_key 对齐：`.env` 优先 → 既有 shell 环境变量；
# 两者都缺 → 显式报错退出，绝不静默回落公开默认值（=已知后门）。
_read_env_value() {
    _f="$SCRIPT_DIR/.env"
    [ -f "$_f" ] || return 0
    sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$_f" \
        | head -1 | sed 's/[[:space:]]*#.*$//; s/^["'"'"']//; s/["'"'"']$//'
}
_ENV_Z="$(_read_env_value ZENITH_API_KEY)"
_ENV_K="$(_read_env_value KNOWLEDGE_API_KEY)"
ZENITH_API_KEY="${_ENV_Z:-${ZENITH_API_KEY:-}}"
KNOWLEDGE_API_KEY="${_ENV_K:-${KNOWLEDGE_API_KEY:-}}"
# 两个键名须同值：只配了一个时以已配置的那个补齐另一个
ZENITH_API_KEY="${ZENITH_API_KEY:-$KNOWLEDGE_API_KEY}"
KNOWLEDGE_API_KEY="${KNOWLEDGE_API_KEY:-$ZENITH_API_KEY}"
if [ -z "$ZENITH_API_KEY" ]; then
    echo "错误: .env 未设置 ZENITH_API_KEY / KNOWLEDGE_API_KEY（两者须同值）。" >&2
    echo "     请在 $SCRIPT_DIR/.env 中设置后重试。可如此生成：" >&2
    echo "       python3 -c \"import secrets;print('ZENITH_API_KEY='+secrets.token_urlsafe(32))\"" >&2
    exit 1
fi
unset _ENV_Z _ENV_K
export ZENITH_API_KEY KNOWLEDGE_API_KEY

# ── embedding 模型本地路径（若存在） ──
if [ -d "$WORKSPACE_ROOT/bge-small-model" ]; then
    export ZENITH_RAG_EMBED_MODEL="$WORKSPACE_ROOT/bge-small-model"
    export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
fi

echo "============================================================"
echo "  Zenith v2 - 本地智能助手"
echo "============================================================"
echo "  后端地址: http://localhost:$PORT"
echo "  API 文档: http://localhost:$PORT/docs"
echo ""
echo "  数据完全存储在本地，不会上传到任何云端服务器。"
echo "  你的 API Key 和对话数据仅保存在本机。"
echo "============================================================"
echo ""

# ── 启动 Zenith v2 主服务（前台运行） ──
# 知识库中台与 task_worker 由 start.py 统一托管：健康检查通过后按需拉起，
# 并带 watchdog 自动重启。这样避免 shell 与 start.py 双进程管理的重复/竞态。
echo ">> 启动 Zenith v2 主服务（含知识库中台 / worker 托管）..."
echo ""

exec "$VENV_PYTHON" start.py "$@"
