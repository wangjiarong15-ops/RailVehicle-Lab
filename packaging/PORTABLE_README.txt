RailVehicle-Lab Windows directory build (experimental)

Run RailVehicleLab.exe for the full application; double-click Start-Demo.bat for the fictional-data-only demonstration.
Open http://127.0.0.1:8501 in a browser. The console window must remain open; press Ctrl+C to stop.
If the port is occupied, run RailVehicleLab.exe --port 8502 from a terminal.
Only one portable instance can run at once.

Database, configuration and launcher log: %LOCALAPPDATA%\RailVehicle-Lab\
AI is disabled by default. Optional AI settings belong in .streamlit\secrets.toml under that user directory.
Do not include secrets or databases when sharing the application directory.

This is an experimental build. It must be independently verified on a clean Windows machine before public release.
