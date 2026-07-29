@echo off
chcp 65001 >nul
echo Stopping Zenith v2...

REM 通过 PID 文件杀掉旧实例
if exist "%TEMP%\zenith_v2.pid" (
    for /f %%p in (%TEMP%\zenith_v2.pid) do taskkill /PID %%p /F 2>nul
    del /q "%TEMP%\zenith_v2.pid" 2>nul
    echo Zenith stopped.
) else (
    echo No running instance found.
)

REM 清除锁文件
if exist "%TEMP%\zenith_v2.lock" del /q "%TEMP%\zenith_v2.lock" 2>nul

timeout /t 2 /nobreak >nul
exit
