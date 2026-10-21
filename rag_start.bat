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
rem 🔴 2026-10-08 对齐 start.py:_RAG_WORK_DIR 的默认值。
rem 原值 "%~dp0..\zenith_rag_new" 指向「工作区根\zenith_rag_new」，
rem 而 start.py 走的是 D:\dshs\zenith_rag_new —— **两个不同的向量库**，
rem 于是「双击 bat 起网关」与「走 launcher/start.py 起主服务」看到的是两个库。
rem 改为与 start.py 一致；若外部已显式设置该变量则以其为准。
if not defined ZENITH_RAG_WORK_DIR set "ZENITH_RAG_WORK_DIR=D:\dshs\zenith_rag_new"
rem 🔴 2026-10-14：不再硬编码 test-key（公开默认密钥 = 认证面后门，D9/R2）。
rem   网关读 ZENITH_API_KEY、主服务读 KNOWLEDGE_API_KEY —— 两个名字必须同值，
rem   否则「给网关设真 key」会让主服务仍发旧值 → /api/knowledge/* 全量 401。
rem   只从 .env 取；取不到则由网关生成一次性随机密钥（显式失败，不静默放行）。
set "LLM_BASE_URL=https://api.deepseek.com/v1"
set "LLM_MODEL=deepseek-flash"

rem 从 .env 读取：LLM 端点 + 网关运行的 RAG 变量 + 查询翻译桥开关 + 集合语言声明
rem 注意这里用硬编码兜底、再让 .env 覆盖 —— 即 .env 为唯一真源。
rem 背景：core 的内置默认 LLM_BASE_URL 是 api.siliconflow.cn（SiliconFlow），
rem 而 ZENITH_LLM_API_KEY 是 DeepSeek 官方 key ⇒ 配错时表现为
rem 401 {"code":30014,"message":"Token is invalid."}（看着像 key 坏，实为 base_url 错）。
if exist "%~dp0.env" (
    for /f "eol=# tokens=1,* delims==" %%a in ('type "%~dp0.env"') do (
        if "%%a"=="ZENITH_LLM_API_KEY" set "LLM_API_KEY=%%~b"
        if "%%a"=="LLM_BASE_URL" set "LLM_BASE_URL=%%~b"
        if "%%a"=="LLM_MODEL" set "LLM_MODEL=%%~b"
        if "%%a"=="ZENITH_INGEST_ROOTS" set "ZENITH_INGEST_ROOTS=%%~b"
        if "%%a"=="ZENITH_RAG_COLLECTION" set "ZENITH_RAG_COLLECTION=%%~b"
        if "%%a"=="ZENITH_RAG_MIN_SCORE_MAP" set "ZENITH_RAG_MIN_SCORE_MAP=%%~b"
        if "%%a"=="ZENITH_QUERY_BRIDGE" set "ZENITH_QUERY_BRIDGE=%%~b"
        if "%%a"=="ZENITH_RAG_ANSWER_PROMPT_V2" set "ZENITH_RAG_ANSWER_PROMPT_V2=%%~b"
        if "%%a"=="ZENITH_COLLECTION_LANG" set "ZENITH_COLLECTION_LANG=%%~b"
        if "%%a"=="ZENITH_API_KEY" set "ZENITH_API_KEY=%%~b"
    )
)
rem 主服务读的是另一个名字 —— 始终镜像，避免两个 env 名漂移（R2）
set "KNOWLEDGE_API_KEY=%ZENITH_API_KEY%"
if not defined ZENITH_API_KEY echo [zenith-rag] WARNING: .env 未设 ZENITH_API_KEY - gateway will use a random key and the main service will 401

echo [zenith-rag] working dir : %CD%
echo [zenith-rag] embed model  : %ZENITH_RAG_EMBED_MODEL%
echo [zenith-rag] work dir     : %ZENITH_RAG_WORK_DIR%
rem 启动自检：把关键开关回显出来 —— 对抗「在 .env 里开了却没反应」这类坑
rem （同类事故：LLM_BASE_URL 曾在 .env 里配了但 bat 没透传）
echo [zenith-rag] switches    : PROMPT_V2=%ZENITH_RAG_ANSWER_PROMPT_V2% BRIDGE=%ZENITH_QUERY_BRIDGE% COLL_LANG=%ZENITH_COLLECTION_LANG% MINSCORE=%ZENITH_RAG_MIN_SCORE_MAP%

if /i "%~1"=="--debug" (
    echo [zenith-rag] foreground mode (Ctrl+C to stop)
    "%PY%" "%~dp0..\api_gateway.py"
) else (
    start "zenith-rag-gateway" "%PYW%" "%~dp0..\api_gateway.py"
    echo [zenith-rag] gateway launched in background on port 8788
    echo [zenith-rag] check: curl http://127.0.0.1:8788/health
)
