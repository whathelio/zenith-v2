#!/bin/bash
# ============================================================
#  Zenith v2 — 一键安装脚本（Linux / macOS）
#  用法: bash install.sh
#  作用: 检查 Python → 建 .venv → 装依赖 → 生成 config.yaml
#  幂等: 重复运行不会破坏已有 .venv / config.yaml
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================================"
echo "  Zenith v2 — 安装"
echo "============================================================"
echo ""

# ── 1. 找到可用的 Python 3.10+ ──
PY_CMD=""
for cand in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
            PY_CMD="$cand"
            break
        fi
    fi
done

if [ -z "$PY_CMD" ]; then
    echo "[错误] 未找到 Python 3.10+。"
    echo ""
    echo "  macOS : brew install python@3.12"
    echo "  Ubuntu: sudo apt install python3 python3-venv"
    echo "  Fedora: sudo dnf install python3"
    echo ""
    exit 1
fi
echo "[1/4] 已找到 Python: $("$PY_CMD" --version 2>&1)"
echo ""

# ── 2. 虚拟环境 ──
if [ -x ".venv/bin/python" ]; then
    echo "[2/4] .venv 已存在，跳过创建"
else
    echo "[2/4] 创建虚拟环境 .venv ..."
    "$PY_CMD" -m venv .venv
fi
echo ""

# ── 3. 依赖 ──
echo "[3/4] 安装依赖（首次约需数分钟）..."
.venv/bin/python -m pip install --upgrade pip --quiet --disable-pip-version-check
if ! .venv/bin/python -m pip install -r requirements.txt --disable-pip-version-check; then
    echo ""
    echo "[警告] PyPI 直连失败，改用阿里云镜像重试 ..."
    .venv/bin/python -m pip install -r requirements.txt \
        -i https://mirrors.aliyun.com/pypi/simple/ \
        --trusted-host mirrors.aliyun.com --disable-pip-version-check
fi
echo ""

# ── 4. 配置模板 ──
mkdir -p config
if [ ! -f "config/config.yaml" ] && [ -f "config/config.yaml.example" ]; then
    cp "config/config.yaml.example" "config/config.yaml"
    echo "[4/4] 已从模板生成 config/config.yaml"
else
    echo "[4/4] config/config.yaml 已存在，保持不变"
fi

chmod +x zenith.sh 2>/dev/null || true

echo ""
echo "============================================================"
echo "  安装完成。"
echo ""
echo "  下一步:"
echo "    1) bash zenith.sh        启动"
echo "    2) 打开 http://127.0.0.1:8766"
echo "    3) 在「设置」页填入你的 API Key"
echo ""
echo "  可选: cp .env.example .env 进行高级配置"
echo "============================================================"
