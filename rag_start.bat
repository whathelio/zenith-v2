@echo off
rem ============================================
rem  Zenith v2 - RAG Gateway standalone starter
rem  Start api_gateway.py (port 8788) with the
rem  root-workspace venv that has RAG deps.
rem  Usage:
rem    rag_start.bat          -> detached (pythonw)
rem    rag_start.bat --debug  -> foreground (python, see errors)
rem ============================================
chcp 65001 >nul
cd /d "%~dp0.."

set "PYW=%~dp0..\.venv\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW=%~dp0..\.venv\Scripts\python.exe"
set "PY=%~dp0..\.venv\Scripts\python.exe"

set "ZENITH_RAG_EMBED_MODEL=%~dp0..\bge-small-model"
set "ZENITH_RAG_WORK_DIR=%~dp0..\zenith_rag_new"
set "ZENITH_API_KEY=test-key"
set "LLM_BASE_URL=https://api.deepseek.com/v1"
set "LLM_MODEL=deepseek-v4-flash"

rem reuse zenith's LLM key from .env
if exist "%~dp0.env" (
    for /f "eol=# tokens=1,* delims==" %%a in ('type "%~dp0.env"') do (
        if "%%a"=="ZENITH_LLM_API_KEY" set "LLM_API_KEY=%%~b"
    )
)

echo [zenith-rag] working dir : %CD%
echo [zenith-rag] embed model  : %ZENITH_RAG_EMBED_MODEL%
echo [zenith-rag] work dir     : %ZENITH_RAG_WORK_DIR%

if /i "%~1"=="--debug" (
    echo [zenith-rag] foreground mode (Ctrl+C to stop)
    "%PY%" "%~dp0..\api_gateway.py"
) else (
    start "zenith-rag-gateway" "%PYW%" "%~dp0..\api_gateway.py"
    echo [zenith-rag] gateway launched in background on port 8788
    echo [zenith-rag] check: curl http://127.0.0.1:8788/health
)
