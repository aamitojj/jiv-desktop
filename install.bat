@echo off
setlocal
title Jiv Desktop - Install
cd /d "%~dp0"
echo === Jiv Desktop installer ===
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY ( where python >nul 2>nul && set PY=python )
if not defined PY goto nopython
%PY% -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" || goto nopython
echo Using: %PY%
if not exist .venv ( %PY% -m venv .venv || goto fail )
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || goto fail
echo.
set /p WV=Install pywebview for a native app window (optional)? [y/N] 
if /i "%WV%"=="y" python -m pip install pywebview
echo.
set /p SC=Create a desktop shortcut? [Y/n] 
if /i not "%SC%"=="n" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_shortcut.ps1"
echo.
echo Done! Start Jiv with start.bat (or the desktop shortcut).
pause
exit /b 0
:nopython
echo Python 3.10 or newer was not found.
echo Install it with this command in a terminal, then re-run install.bat:
echo     winget install -e --id Python.Python.3.12
echo (or download from https://www.python.org/downloads/ and tick "Add python.exe to PATH")
set /p DOIT=Run the winget command now? [y/N] 
if /i "%DOIT%"=="y" winget install -e --id Python.Python.3.12 && echo Close this window and run install.bat again.
pause
exit /b 1
:fail
echo Installation failed - see the messages above.
pause
exit /b 1
