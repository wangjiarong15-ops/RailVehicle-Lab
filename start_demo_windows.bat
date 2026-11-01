@echo off
setlocal
cd /d "%~dp0" || exit /b 1
if not exist ".venv\Scripts\python.exe" goto missing
".venv\Scripts\python.exe" windows_launcher.py --demo %*
set "result=%errorlevel%"
if not "%result%"=="0" if "%~1"=="" pause
exit /b %result%
:missing
echo Project virtual environment not found. Run setup_windows.bat first.
pause
exit /b 1
