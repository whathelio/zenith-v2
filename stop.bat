@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"
set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%.venv\Scripts\python.exe"
set "START_PY=%PROJECT_DIR%start.py"

echo Stopping Zenith v2...

REM 优先调用 start.py --stop（三级清理：PID 文件 -> 端口 -> 进程名）
if exist "%PYTHON_EXE%" if exist "%START_PY%" (
    "%PYTHON_EXE%" "%START_PY%" --stop
    goto :done
)

REM 兜底：未找到 python 时手动清理
echo [WARN] 未找到 .venv python，使用兜底清理...

REM 通过 PID 文件结束进程
if exist "%TEMP%\zenith_v2.pid" (
    for /f %%p in (%TEMP%\zenith_v2.pid) do (
        taskkill /PID %%p /F >nul 2>&1
        echo 已终止 PID %%p
    )
    del /q "%TEMP%\zenith_v2.pid" 2>nul
)

REM 通过端口结束进程
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8766" ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
    echo 已终止端口占用 PID %%a
)

REM 清理锁文件
if exist "%TEMP%\zenith_v2.lock" del /q "%TEMP%\zenith_v2.lock" 2>nul

:done
timeout /t 1 /nobreak >nul
endlocal
exit
