@echo off
rem Stops pyBikeRouter (your map data and routes stay in the data folder).
cd /d "%~dp0"
docker compose --env-file settings.env down
echo.
echo Stopped. Double-click Start-Windows.bat to start it again.
pause
