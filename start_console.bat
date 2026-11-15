@echo off
rem Same as start.bat but keeps a console window open (useful for troubleshooting)
cd /d "%~dp0"
.venv\Scripts\python.exe run.py %*
pause
