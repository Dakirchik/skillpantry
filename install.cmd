@echo off
rem skillpantry: one-click install for Windows (double-click or run from cmd)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0pantryctl.ps1" install %*
echo.
pause
