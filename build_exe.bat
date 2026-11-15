@echo off
rem Optional: build a single JivDesktop.exe with PyInstaller (run install.bat first)
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name JivDesktop --add-data "jiv\static;jiv\static" --collect-submodules uvicorn run.py
echo.
echo Built: dist\JivDesktop.exe
pause
