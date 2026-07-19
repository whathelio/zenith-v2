#!/bin/bash
# Zenith v2 — Linux/macOS 启动脚本
# 用法: bash zenith.sh [port]   (默认端口 8766)

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${1:-8766}"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── 检查 venv ──
if [ ! -f "$VENV_PYTHON" ]; then
    echo "未找到 .venv/bin/python，请先创建虚拟环境："
    echo "  python3 -m venv .venv"
    echo "  .venv/bin/pip install -r requirements.txt"
    exit 1
fi

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
export LLM_API_KEY=$(grep "api_key:" "$CONFIG_FILE" | head -1 | sed 's/.*api_key:[[:space:]]*//')
export LLM_BASE_URL=$(grep "api_base:" "$CONFIG_FILE" | head -1 | sed 's/.*api_base:[[:space:]]*//')
export LLM_MODEL=$(grep "^model:" "$CONFIG_FILE" | head -1 | sed 's/.*model:[[:space:]]*//')

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

# ── 启动知识库 API 中台（若存在） ──
GATEWAY_PID=""
if [ -f "$WORKSPACE_ROOT/api_gateway.py" ]; then
    echo ">> 启动 api_gateway (端口 8788)..."
    (cd "$WORKSPACE_ROOT" && "$VENV_PYTHON" api_gateway.py > /tmp/zenith_api.log 2>&1) &
    GATEWAY_PID=$!
    sleep 3
else
    echo ">> (跳过) 未找到 $WORKSPACE_ROOT/api_gateway.py，知识库功能不可用"
fi

# ── 启动异步任务 worker（若存在） ──
WORKER_PID=""
if [ -f "$WORKSPACE_ROOT/task_worker.py" ]; then
    echo ">> 启动 task_worker..."
    (cd "$WORKSPACE_ROOT" && "$VENV_PYTHON" task_worker.py --poll 2.0 > /tmp/zenith_worker.log 2>&1) &
    WORKER_PID=$!
    sleep 2
fi

# ── 启动 Zenith v2 主服务（前台运行） ──
echo ">> 启动 Zenith v2 主服务..."
echo ""

# 2 秒后自动打开浏览器
(sleep 2 && (xdg-open "http://localhost:$PORT" || open "http://localhost:$PORT") 2>/dev/null) &

# 清理子进程的退出 trap
trap '[ -n "$GATEWAY_PID" ] && kill $GATEWAY_PID 2>/dev/null; [ -n "$WORKER_PID" ] && kill $WORKER_PID 2>/dev/null' EXIT

"$VENV_PYTHON" start.py "$PORT"
