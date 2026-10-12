@echo off
rem Downloads the map data of another area. Start the program again afterwards.
cd /d "%~dp0"
if not exist data\segments mkdir data\segments
if not exist data\exports mkdir data\exports
docker compose --env-file settings.env --profile setup run --rm setup
echo.
echo Done. Double-click Stop-Windows.bat and then Start-Windows.bat so the new area is used.
pause
