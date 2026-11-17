@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0internal\launch.ps1" %*
