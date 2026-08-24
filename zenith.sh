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
export ZENITH_API_KEY="${ZENITH_API_KEY:-zenith-local}"
export KNOWLEDGE_API_KEY="${KNOWLEDGE_API_KEY:-zenith-local}"

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
