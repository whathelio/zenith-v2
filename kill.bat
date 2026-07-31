@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"
set "PROJECT_DIR=%~dp0"

echo [WARN] 正在强制清理 Zenith v2 相关 Python 进程...

REM 1. 先执行标准 stop（使用 start.py --stop 三级清理：PID -> 端口 -> 进程名）
if exist "stop.bat" call stop.bat >nul 2>&1

REM 2. 强力兜底：结束命令行包含 start.py / api_gateway.py / task_worker.py 的 python 进程
REM    （使用 PowerShell Get-CimInstance；wmic 在 Win11 24H2+ 已移除，不再依赖）
for /f "delims=" %%p in ('powershell -NoProfile -NonInteractive -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\" | Where-Object { $_.CommandLine -like '*start.py*' -or $_.CommandLine -like '*api_gateway.py*' -or $_.CommandLine -like '*task_worker.py*' } | Select-Object -ExpandProperty ProcessId"') do (
    taskkill /PID %%p /F >nul 2>&1
    echo 已终止 PID %%p
)

REM 3. 清理残留文件
del /q "%TEMP%\zenith_v2.lock" 2>nul
del /q "%TEMP%\zenith_v2.pid" 2>nul

echo 清理完成。
endlocal
