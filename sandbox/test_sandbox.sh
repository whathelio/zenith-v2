#!/bin/bash
# Zenith v2 代码沙箱验证脚本
# 用法: bash sandbox/test_sandbox.sh [base_url]
# 默认: http://localhost:8766

set -e
BASE_URL="${1:-http://localhost:8766}"
CODE_ENDPOINT="$BASE_URL/api/code/run"

echo "=== Zenith v2 代码沙箱验证 ==="
echo "目标: $CODE_ENDPOINT"
echo ""

# 检查服务是否运行
if ! curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/api/health" | grep -q "200"; then
    echo "❌ Zenith 服务未运行，请先启动: bash zenith.sh"
    exit 1
fi

# 检查代码执行是否启用
ENABLED=$(curl -s -X POST "$CODE_ENDPOINT" -H "Content-Type: application/json" -d '{"code":"print(1)"}' | grep -o '"success":[a-z]*' | head -1)
if echo "$ENABLED" | grep -q "false"; then
    echo "⚠️  代码执行已禁用 (code_execution_enabled=false)"
    echo "   在 config.yaml 设 code_execution_enabled: true 后重启"
    exit 0
fi

PASS=0
FAIL=0
TOTAL=0

run_test() {
    local name="$1"
    local code="$2"
    local expect_success="$3"
    local expect_pattern="$4"

    TOTAL=$((TOTAL + 1))
    local response
    response=$(curl -s -X POST "$CODE_ENDPOINT" \
        -H "Content-Type: application/json" \
        -d "{\"code\": $(echo "$code" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}" \
        2>/dev/null)

    local success=$(echo "$response" | grep -o '"success":[a-z]*' | head -1 | cut -d: -f2)
    local output=$(echo "$response" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read()).get("output",""))' 2>/dev/null || echo "$response")

    if [ "$success" = "$expect_success" ]; then
        if [ -z "$expect_pattern" ] || echo "$output" | grep -q "$expect_pattern"; then
            echo "✅ $name"
            PASS=$((PASS + 1))
        else
            echo "❌ $name (输出不匹配: 期望包含 '$expect_pattern')"
            echo "   实际: ${output:0:100}"
            FAIL=$((FAIL + 1))
        fi
    else
        echo "❌ $name (期望 success=$expect_success, 实际 success=$success)"
        echo "   输出: ${output:0:100}"
        FAIL=$((FAIL + 1))
    fi
}

echo "--- 功能验证 ---"
run_test "正常代码 print(1+1)" "print(1+1)" "true" "2"
run_test "多行代码 math" "import math
print(math.pi)" "true" "3.14"
run_test "列表推导" "nums=[1,2,3]
print(sum(nums))" "true" "6"
run_test "JSON 处理" "import json
print(json.dumps({'a':1}))" "true" '"a": 1'

echo ""
echo "--- 安全验证（降级模式：黑名单拦截）---"
run_test "subprocess 拦截" "import subprocess" "false" "安全检查拒绝"
run_test "os.system 拦截" "os.system('echo hack')" "false" "安全检查拒绝"
run_test "socket 拦截" "import socket" "false" "安全检查拒绝"
run_test "ctypes 拦截" "import ctypes" "false" "安全检查拒绝"
run_test "eval 拦截" "eval('1+1')" "false" "安全检查拒绝"

echo ""
echo "--- 边界验证 ---"
run_test "超时拦截" "import time
time.sleep(35)" "false" "超时"

echo ""
echo "=== 结果: $PASS/$TOTAL 通过, $FAIL 失败 ==="

if [ $FAIL -gt 0 ]; then
    exit 1
fi
