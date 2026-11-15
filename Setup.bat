@echo off
rem Double-click this file ONCE to install Ouranos-Pro (needs internet).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
echo.
pause
