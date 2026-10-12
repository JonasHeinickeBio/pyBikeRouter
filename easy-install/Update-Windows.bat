@echo off
rem Gets the newest version of pyBikeRouter and restarts it.
cd /d "%~dp0"
docker compose --env-file settings.env pull
if errorlevel 1 goto failed
docker compose --env-file settings.env up -d
if errorlevel 1 goto failed
echo.
echo Updated and running.
pause
exit /b 0
:failed
echo.
echo The update did not work. Is Docker Desktop running and the internet connection up?
pause
exit /b 1
