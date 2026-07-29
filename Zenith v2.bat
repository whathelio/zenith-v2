@echo off
chcp 65001 >nul
cd /d "O:\下载文件\新建文件夹\zenith-v2"

REM 清除残留锁文件
if exist "%TEMP%\zenith_v2.lock" del /q "%TEMP%\zenith_v2.lock" 2>nul

REM 启动
start "" ".venv\Scripts\pythonw.exe" "start.py" 8766

exit
