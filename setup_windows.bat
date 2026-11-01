@echo off
setlocal
cd /d "%~dp0" || exit /b 1
if /i "%~1"=="--check" goto check
if exist ".venv\Scripts\python.exe" goto install
where py >nul 2>nul
if errorlevel 1 goto python
py -3 -c "import sys;sys.exit(sys.version_info < (3,10))" >nul 2>nul
if errorlevel 1 goto python
py -3 -m venv ".venv"
if errorlevel 1 goto fail
goto install
:python
where python >nul 2>nul
if errorlevel 1 goto fail
python -c "import sys;sys.exit(sys.version_info < (3,10))" >nul 2>nul
if errorlevel 1 goto fail
python -m venv ".venv"
if errorlevel 1 goto fail
:install
".venv\Scripts\python.exe" -c "import sys;sys.exit(sys.version_info < (3,10))"
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -e ".[ml,signal]"
if errorlevel 1 goto fail
".venv\Scripts\python.exe" windows_launcher.py --check
if errorlevel 1 goto fail
echo Installation completed. Run start_windows.bat or start_demo_windows.bat.
pause
exit /b 0
:check
if not exist ".venv\Scripts\python.exe" exit /b 1
".venv\Scripts\python.exe" windows_launcher.py --check
exit /b %errorlevel%
:fail
echo Installation failed. Check Python 3.10+, pip, network and errors above. Existing environments were not removed.
pause
exit /b 1
