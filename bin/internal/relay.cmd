@echo off
setlocal
if not exist "%~dp0AgentChatSystem.pyz" (
    call "%~dp0build.cmd"
    if errorlevel 1 exit /b 1
)
python -X utf8 "%~dp0AgentChatSystem.pyz" %*
