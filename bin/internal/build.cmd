@echo off
setlocal
python -B -m zipapp "%~dp0..\..\src" -c -o "%~dp0AgentChatSystem.pyz"
