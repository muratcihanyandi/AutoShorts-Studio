@echo off
set "CURRENT_DIR=%~dp0"
echo ***** Current directory: %CURRENT_DIR% *****

%CURRENT_DIR%\lib\python\Scripts\pip.exe install -r %CURRENT_DIR%MoneyPrinterTurbo\requirements.txt

echo ##### Update completed #####
pause