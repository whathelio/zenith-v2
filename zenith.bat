@echo off
chcp 65001 >nul
cd /d "%~dp0"
set ZENITH_ROOT=%~dp0..
set WORKSPACE_VENV=%ZENITH_ROOT%\.venv\Scripts\pythonw.exe

REM ── 从 config.yaml 提取 API key ──
for /f "tokens=2 delims=: " %%k in ('findstr "api_key:" "config\config.yaml"') do set LLM_API_KEY=%%k
for /f "tokens=2 delims=: " %%k in ('findstr "api_base:" "config\config.yaml"') do set LLM_BASE_URL=%%k
for /f "tokens=2 delims=: " %%k in ('findstr /b "model:" "config\config.yaml"') do set LLM_MODEL=%%k


REM ── 知识库网关认证 ──
set ZENITH_API_KEY=zenith-local
set KNOWLEDGE_API_KEY=zenith-local
set ZENITH_RAG_EMBED_MODEL=%ZENITH_ROOT%\bge-small-model

REM ── 启动 Zenith v2 主服务（端口 8766） ──
start "" "%~dp0.venv\Scripts\pythonw.exe" start.py 8766

REM ── 启动知识库 API 中台（端口 8788） ──
timeout /t 3 /nobreak >nul
start "" "%WORKSPACE_VENV%" "%ZENITH_ROOT%\api_gateway.py"

REM ── 启动异步任务 worker ──
timeout /t 2 /nobreak >nul
start "" "%WORKSPACE_VENV%" "%ZENITH_ROOT%\task_worker.py" --poll 2.0

exit
