#!/bin/bash

# 切换到脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 设置根目录和虚拟环境路径
ZENITH_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKSPACE_VENV="$ZENITH_ROOT/.venv/bin/python"

# ── 从 config.yaml 提取 API key ──
LLM_API_KEY=$(grep "api_key:" config/config.yaml | awk -F': ' '{print $2}' | tr -d ' ')
LLM_BASE_URL=$(grep "api_base:" config/config.yaml | awk -F': ' '{print $2}' | tr -d ' ')
LLM_MODEL=$(grep "^model:" config/config.yaml | awk -F': ' '{print $2}' | tr -d ' ')

export LLM_API_KEY
export LLM_BASE_URL
export LLM_MODEL

# ── 知识库网关认证 ──
export ZENITH_API_KEY=zenith-local
export KNOWLEDGE_API_KEY=zenith-local
export ZENITH_RAG_EMBED_MODEL="$ZENITH_ROOT/bge-small-model"

# ── 启动 Zenith v2 主服务（端口 8766） ──
nohup "$SCRIPT_DIR/.venv/bin/python" start.py 8766 > /dev/null 2>&1 &

# ── 启动知识库 API 中台（端口 8788） ──
sleep 3
nohup "$WORKSPACE_VENV" "$ZENITH_ROOT/api_gateway.py" > /dev/null 2>&1 &

# ── 启动异步任务 worker ──
sleep 2
nohup "$WORKSPACE_VENV" "$ZENITH_ROOT/task_worker.py" --poll 2.0 > /dev/null 2>&1 &

exit 0
