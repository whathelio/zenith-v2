@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ============================================================
REM  Zenith v2 - one-click installer (Windows)
REM  ASCII-only on purpose: non-ASCII bytes break cmd parsing
REM  under GBK/CP936 consoles. User-facing docs live in README.
REM ============================================================

cd /d "%~dp0"
echo ============================================================
echo   Zenith v2 - Installer
echo ============================================================
echo.

REM ---- 1. locate a usable Python (3.10+) ----
set "PY_CMD="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY_CMD=py -3"
if not defined PY_CMD (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY_CMD=python"
)
if not defined PY_CMD (
    echo [ERROR] Python 3.10+ not found.
    echo.
    echo   Download: https://www.python.org/downloads/
    echo   During setup, TICK "Add python.exe to PATH".
    echo   Then run this installer again.
    echo.
    pause
    exit /b 1
)
echo [1/4] Python found:
%PY_CMD% --version
echo.

REM ---- 2. create the virtual environment ----
if exist ".venv\Scripts\python.exe" (
    echo [2/4] .venv already exists - skipped.
) else (
    echo [2/4] Creating virtual environment .venv ...
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create .venv
        pause
        exit /b 1
    )
)
echo.

REM ---- 3. install runtime dependencies ----
REM Offline mode: if a "wheels" folder sits next to this script, install from it
REM with no network at all. It ships separately as
REM zenith-v2-deps-win64-py313.zip (44 wheels, ~60MB).
set "OFFLINE="
if exist "wheels\*.whl" set "OFFLINE=1"

if defined OFFLINE (
    echo [3/4] Offline mode - installing from the local wheels folder ...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet --disable-pip-version-check
    ".venv\Scripts\python.exe" -m pip install --no-index --find-links wheels -r requirements.txt --disable-pip-version-check
    if errorlevel 1 (
        echo.
        echo [ERROR] Offline install failed.
        echo         The bundled wheels are built for Python 3.13 64-bit.
        echo         Check:  .venv\Scripts\python.exe --version
        echo         If you use another Python version, delete the wheels folder
        echo         and run install.bat again to download from PyPI instead.
        pause
        exit /b 1
    )
    goto :deps_done
)

echo [3/4] Installing dependencies from PyPI (a few minutes on first run) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet --disable-pip-version-check
".venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 (
    echo.
    echo [WARN] PyPI unreachable or slow, retrying with Aliyun mirror ...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt ^
        -i https://mirrors.aliyun.com/pypi/simple/ ^
        --trusted-host mirrors.aliyun.com --disable-pip-version-check
    if errorlevel 1 (
        echo.
        echo [ERROR] Dependency installation failed.
        echo         Option 1: check your network, then run install.bat again.
        echo         Option 2: download zenith-v2-deps-win64-py313.zip from the
        echo                   Releases page, unzip its "wheels" folder here,
        echo                   then run install.bat again.
        pause
        exit /b 1
    )
)
:deps_done
echo.

REM ---- 4. seed config from template ----
if not exist "config" mkdir "config"
if not exist "config\config.yaml" (
    if exist "config\config.yaml.example" (
        copy "config\config.yaml.example" "config\config.yaml" >nul
        echo [4/4] Created config\config.yaml from template.
    )
) else (
    echo [4/4] config\config.yaml already exists - kept as is.
)

echo.
echo ============================================================
echo   Install finished.
echo.
echo   Next steps:
echo     1) Double-click zenith.bat to start
echo     2) Open http://127.0.0.1:8766
echo     3) Fill in your API Key in the Settings page
echo.
echo   Optional: copy .env.example to .env for advanced config.
echo ============================================================
echo.
pause
endlocal
