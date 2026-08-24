@echo off
chcp 65001 >nul

REM Zenith v2 launch entry (compatible with old shortcut)
REM Logic unified in zenith.bat

cd /d "%~dp0"
call "%~dp0zenith.bat"
exit
