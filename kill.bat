@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"
set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%.venv\Scripts\python.exe"
set "START_PY=%PROJECT_DIR%start.py"

echo [WARN] Stopping Zenith v2 (via start.py --stop: PID file + ports 8766/8788 + process-name)...

REM start.py --stop cleans: PID file -> ports(8766/8788) -> process-name match
REM (start.py uses Python subprocess to call PowerShell/taskkill, no cmd escaping issues)
if exist "%PYTHON_EXE%" if exist "%START_PY%" (
    "%PYTHON_EXE%" "%START_PY%" --stop
)

REM fallback: remove leftover lock/pid files
del /q "%TEMP%\zenith_v2.lock" 2>nul
del /q "%TEMP%\zenith_v2.pid" 2>nul

echo.
echo Done. If python processes still remain, they are WorkBuddy MCP
echo connectors (cache-scheduler/fact-check/guard etc.), NOT zenith -
echo close them from WorkBuddy's connector settings.
endlocal
