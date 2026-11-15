@echo off
rem Fake winget for the Wine test: "installs" a runtime stub and adds its folder to the user PATH (like real installers do)
echo Found %4 [%4] Version 1.0.0-fake
echo Starting package install...
set BIN=%LOCALAPPDATA%\fakewinget
if not exist "%BIN%" mkdir "%BIN%"
if "%4"=="OpenJS.NodeJS.LTS" (
  > "%BIN%\node.cmd" echo @echo v22.11.0-fake
)
reg add HKCU\Environment /v Path /t REG_EXPAND_SZ /d "%BIN%" /f >nul
echo Successfully installed
