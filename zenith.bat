@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM Zenith v2 启动脚本
REM 用法: 双击启动（会显示启动结果窗口）；或在命令行传入参数，例如:
REM   zenith.bat --no-browser
REM   zenith.bat --reset-lock
REM   zenith.bat --no-aux
REM   zenith.bat --stop
REM   zenith.bat --status

cd /d "%~dp0"
set "PROJECT_DIR=%~dp0"
set "PYTHONW_EXE=%PROJECT_DIR%.venv\Scripts\pythonw.exe"
set "PYTHON_EXE=%PROJECT_DIR%.venv\Scripts\python.exe"
set "START_PY=%PROJECT_DIR%start.py"

REM 优先使用 pythonw.exe（无控制台窗口），失败时回退到 python.exe
set "PY_EXE=%PYTHONW_EXE%"
if not exist "%PY_EXE%" (
    set "PY_EXE=%PYTHON_EXE%"
)

if not exist "%PY_EXE%" (
    echo [ERROR] 未找到 Python 解释器: %PY_EXE%
    echo 请确认 .venv 已创建，或手动运行: python start.py
    pause
    exit /b 1
)

if not exist "%START_PY%" (
    echo [ERROR] 未找到启动脚本: %START_PY%
    pause
    exit /b 1
)

REM 启动 Zenith；所有参数原样传递给 start.py
start "" /D "%PROJECT_DIR%" "%PY_EXE%" "%START_PY%" %*

REM 双击反馈：等待服务就绪并显示结果（有控制台的 python.exe 运行 --wait）
REM 无论启动成功还是"已在运行"，这里都会给出明确提示，避免黑窗一闪
"%PYTHON_EXE%" "%START_PY%" --wait --wait-timeout 25
echo.
echo 日志: %PROJECT_DIR%zenith.log
echo 停止: 双击 stop.bat，或运行 start.py --stop
echo.
pause

endlocal
exit
