@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Jiv is not installed yet. Running install.bat first...
  call install.bat
)
start "" .venv\Scripts\pythonw.exe run.py %*
if errorlevel 1 .venv\Scripts\python.exe run.py %*
