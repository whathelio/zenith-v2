@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"
set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
set "START_PY=%~dp0start.py"

if exist "%PYTHON_EXE%" if exist "%START_PY%" (
    "%PYTHON_EXE%" "%START_PY%" --status
) else (
    echo [ERROR] 未找到 Python 环境或启动脚本
)

endlocal
pause
