@echo off
chcp 65001 >nul
cd /d "O:\下载文件\新建文件夹\zenith-v2"

REM 启动 Zenith
start "" ".venv\Scripts\pythonw.exe" "start.py" 8766

exit
