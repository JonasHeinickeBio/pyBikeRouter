@echo off
rem Connect (or switch off) a language model for free-sentence planning. Start the program again afterwards.
cd /d "%~dp0"
if not exist data\segments mkdir data\segments
if not exist data\exports mkdir data\exports
if not exist data\chat mkdir data\chat
docker compose --env-file settings.env --profile setup run --rm setup --skip
if errorlevel 1 (
  echo.
  echo The language model was not set up. Double-click this again to retry.
  pause
  exit /b 1
)
echo.
echo Done. Double-click Stop-Windows.bat and then Start-Windows.bat so the change is used.
pause
