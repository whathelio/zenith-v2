@echo off
chcp 65001 >nul
cd /d "%~dp0"

set PYTHON_EXE=%~dp0.venv\Scripts\pythonw.exe

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python not found
    pause
    exit /b 1
)

REM 清除残留锁文件
if exist "%TEMP%\zenith_v2.lock" del /q "%TEMP%\zenith_v2.lock" 2>nul

REM 启动 Zenith
start "" /D "%~dp0" "%PYTHON_EXE%" "%~dp0start.py" 8766

exit
