@echo off
cd /d "%~dp0" || exit /b 1
"%~dp0RailVehicleLab.exe" --demo %*
exit /b %errorlevel%
