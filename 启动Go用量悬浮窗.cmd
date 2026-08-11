@echo off
start "" "C:\Users\31807\AppData\Local\Programs\Python\Python311\pythonw.exe" "C:\Users\31807\opencode-widget\data_server.py"
timeout /t 2 /nobreak >nul
start "" "C:\Users\31807\opencode-widget\electron\node_modules\electron\dist\electron.exe" "C:\Users\31807\opencode-widget\electron"
