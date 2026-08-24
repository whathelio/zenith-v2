@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"
set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%.venv\Scripts\python.exe"
set "START_PY=%PROJECT_DIR%start.py"

echo Stopping Zenith v2...

REM prefer start.py --stop (3-level cleanup: PID file -> port -> process name)
if exist "%PYTHON_EXE%" if exist "%START_PY%" (
    "%PYTHON_EXE%" "%START_PY%" --stop
    goto :done
)

REM fallback: manual cleanup if python is missing
echo [WARN] .venv python not found, using fallback cleanup...

REM kill by PID file
if exist "%TEMP%\zenith_v2.pid" (
    for /f %%p in (%TEMP%\zenith_v2.pid) do (
        taskkill /PID %%p /F >nul 2>&1
        echo Killed PID %%p
    )
    del /q "%TEMP%\zenith_v2.pid" 2>nul
)

REM kill by port
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8766" ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
    echo Killed port owner PID %%a
)

REM cleanup lock file
if exist "%TEMP%\zenith_v2.lock" del /q "%TEMP%\zenith_v2.lock" 2>nul

:done
timeout /t 1 /nobreak >nul
endlocal
exit
