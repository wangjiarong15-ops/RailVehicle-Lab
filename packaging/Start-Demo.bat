@echo off
cd /d "%~dp0" || exit /b 1
"%~dp0RailVehicle-Lab.exe" --demo %*
exit /b %errorlevel%
