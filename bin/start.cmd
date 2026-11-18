@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0internal\launch.ps1" %*
set "chatExitCode=%ERRORLEVEL%"
if "%chatExitCode%"=="0" exit /b 0
if "%~1"=="" pause
exit /b %chatExitCode%
