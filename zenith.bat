@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM 用 pythonw.exe 启动（无 CMD 窗口），start 使其脱离当前 CMD 独立运行
start "" "%~dp0.venv\Scripts\pythonw.exe" start.py 8766
exit
