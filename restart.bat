@echo off
chcp 65001 >nul
setlocal

REM Zenith v2 one-click restart (ASCII-only)
REM Stop old instance (with data flush), then start a new one.

cd /d "%~dp0"
set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%.venv\Scripts\python.exe"

echo ============================================
echo  Zenith v2 Restart
echo ============================================
echo.

REM 1. stop old instance gracefully (reuse stop.bat)
echo [1/3] Stopping old instance...
call "%PROJECT_DIR%stop.bat"
if errorlevel 1 (
    echo [WARN] stop.bat exit code %errorlevel%, continuing...
)

REM 2. wait for port release
echo [2/3] Waiting for port release...
timeout /t 3 /nobreak >nul

REM 3. start new instance
echo [3/3] Starting new instance...
start "" /D "%PROJECT_DIR%" "%PYTHON_EXE%" "%PROJECT_DIR%start.py"

echo.
echo ============================================
echo  Restart command issued.
echo  Status: start.py --status
echo  Log: %PROJECT_DIR%zenith.log
echo  Stop: stop.bat or kill.bat
echo ============================================
endlocal
exit
