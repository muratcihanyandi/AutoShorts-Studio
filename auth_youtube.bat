@echo off
setlocal
set "CURRENT_DIR=%~dp0"
echo ***** Current directory: %CURRENT_DIR% *****
%CURRENT_DIR%lib\python\python.exe %CURRENT_DIR%AutoShortsStudio\authenticate_youtube.py
