@echo off
chcp 65001 >nul
echo Stopping Zenith v2...

REM 找到占用 8766 端口的进程并杀掉
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":8766"') do (
    taskkill /PID %%a /F 2>nul && echo Killed PID %%a
)

REM 清除锁文件
if exist "%TEMP%\zenith_v2.lock" del /q "%TEMP%\zenith_v2.lock" 2>nul

echo Zenith stopped.
timeout /t 2 /nobreak >nul
exit
