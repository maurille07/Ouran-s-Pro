@echo off
rem Double-click this file to start Ouranos-Pro AND open the projector screen
rem full-screen on the second monitor. Keep this window open during the service.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" -Kiosk %*
echo.
pause
