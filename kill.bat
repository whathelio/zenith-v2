taskkill /F /IM pythonw.exe 2>nul
taskkill /F /IM python.exe /FI "WINDOWTITLE eq " 2>nul
echo 已清除
