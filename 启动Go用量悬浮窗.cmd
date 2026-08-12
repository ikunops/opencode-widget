@echo off
start "" "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" "%~dp0data_server.py"
timeout /t 2 /nobreak >nul
start "" "%~dp0electron\node_modules\electron\dist\electron.exe" "%~dp0electron"
