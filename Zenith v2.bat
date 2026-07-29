@echo off
chcp 65001 >nul
cd /d "O:\下载文件\新建文件夹\zenith-v2"

REM 杀掉旧实例
if exist "%TEMP%\zenith_v2.pid" (
    for /f %%p in (%TEMP%\zenith_v2.pid) do taskkill /PID %%p /F 2>nul
    del /q "%TEMP%\zenith_v2.pid" 2>nul
)

REM 清除残留锁文件
if exist "%TEMP%\zenith_v2.lock" del /q "%TEMP%\zenith_v2.lock" 2>nul

REM 启动 Zenith（start.py 内置浏览器自动打开）
start "" ".venv\Scripts\pythonw.exe" "start.py" 8766

exit
