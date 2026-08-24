@echo off
chcp 65001 >nul
setlocal

REM Zenith v2 launcher (ASCII-only to avoid GBK/UTF-8 cmd parsing breakage)

cd /d "%~dp0"
set "PROJECT_DIR=%~dp0"
set "PYTHONW_EXE=%PROJECT_DIR%.venv\Scripts\pythonw.exe"
set "PYTHON_EXE=%PROJECT_DIR%.venv\Scripts\python.exe"
set "START_PY=%PROJECT_DIR%start.py"

REM prefer pythonw (no console); fallback to python.exe
set "PY_EXE=%PYTHONW_EXE%"
if not exist "%PY_EXE%" set "PY_EXE=%PYTHON_EXE%"

REM feedback window uses console python.exe; fallback to pythonw
set "WAIT_EXE=%PYTHON_EXE%"
if not exist "%WAIT_EXE%" set "WAIT_EXE=%PY_EXE%"

REM pure control commands run in foreground
echo %* | findstr /c:"--stop" >nul
if not errorlevel 1 (
    "%WAIT_EXE%" "%START_PY%" %*
    goto :done
)
echo %* | findstr /c:"--status" >nul
if not errorlevel 1 (
    "%WAIT_EXE%" "%START_PY%" %*
    goto :done
)
echo %* | findstr /c:"--wait" >nul
if not errorlevel 1 (
    "%WAIT_EXE%" "%START_PY%" %*
    goto :done
)
echo %* | findstr /c:"--help" >nul
if not errorlevel 1 (
    "%WAIT_EXE%" "%START_PY%" %*
    goto :done
)

if not exist "%PY_EXE%" (
    echo [ERROR] Python interpreter not found: %PY_EXE%
    echo Please ensure .venv exists, or run: python start.py
    pause
    exit /b 1
)

if not exist "%START_PY%" (
    echo [ERROR] start.py not found: %START_PY%
    pause
    exit /b 1
)

REM launch Zenith; pass all args through to start.py
start "" /D "%PROJECT_DIR%" "%PY_EXE%" "%START_PY%" %*

REM double-click feedback: wait for readiness and show result
"%WAIT_EXE%" "%START_PY%" --wait --wait-timeout 25
echo.
echo Log: %PROJECT_DIR%zenith.log
echo Stop: double-click stop.bat, or run start.py --stop
echo.
:done

pause

endlocal
exit
