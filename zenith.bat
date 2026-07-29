@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM ── 路径定义 ──
set ZENITH_ROOT=%~dp0..
set PYTHON_EXE=%~dp0.venv\Scripts\pythonw.exe
set PYTHON_CONSOLE=%~dp0.venv\Scripts\python.exe

REM ── 前置检查 ──
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python not found: %PYTHON_EXE%
    pause
    exit /b 1
)
if not exist "%~dp0config\config.yaml" (
    echo [ERROR] config.yaml not found: %~dp0config\config.yaml
    pause
    exit /b 1
)

REM ── 从 .env 加载密钥（优先于 config.yaml）──
if exist ".env" (
    for /f "tokens=1,2 delims==" %%a in (.env) do (
        if not "%%a"=="" if not "%%a"=="#" (
            set %%a=%%b
        )
    )
)

REM ── 从 config.yaml 提取非敏感配置 ──
for /f "tokens=2 delims=: " %%k in ('findstr "api_base:" "config\config.yaml"') do set LLM_BASE_URL=%%k
for /f "tokens=2 delims=: " %%k in ('findstr /b "model:" "config\config.yaml"') do set LLM_MODEL=%%k

REM ── 知识库网关认证（随机生成内部令牌）──
set ZENITH_API_KEY=zenith-internal-v2
set KNOWLEDGE_API_KEY=zenith-internal-v2
set ZENITH_RAG_EMBED_MODEL=%ZENITH_ROOT%\bge-small-model

REM ── 清除可能残留的锁文件 ──
if exist "%TEMP%\zenith_v2.lock" del /q "%TEMP%\zenith_v2.lock" 2>nul

REM ── 启动 Zenith v2 主服务（端口 8766，start.py 内置浏览器自动打开） ──
start "" /D "%~dp0" "%PYTHON_EXE%" "%~dp0start.py" 8766

REM ── 启动知识库 API 中台（端口 8788）─ 用 zenith-v2 的 venv ──
timeout /t 2 /nobreak >nul
if exist "%ZENITH_ROOT%\api_gateway.py" (
    start "" /D "%ZENITH_ROOT%" "%PYTHON_EXE%" "%ZENITH_ROOT%\api_gateway.py"
)

REM ── 启动异步任务 worker ──
timeout /t 2 /nobreak >nul
if exist "%ZENITH_ROOT%\task_worker.py" (
    start "" /D "%ZENITH_ROOT%" "%PYTHON_EXE%" "%ZENITH_ROOT%\task_worker.py" --poll 2.0
)

exit
