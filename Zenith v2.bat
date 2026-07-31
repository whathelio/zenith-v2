@echo off
chcp 65001 >nul

REM Zenith v2 启动入口（兼容旧快捷方式）
REM 实际逻辑已统一至 zenith.bat

cd /d "%~dp0"
call "%~dp0zenith.bat"
exit
