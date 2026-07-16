@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ================================================
echo   Zenith v2 - Local AI Assistant
echo ================================================
echo.

"%~dp0.venv\Scripts\python.exe" start.py 8766
pause
