@echo off
rem Starts pyBikeRouter. Double-click this file.
cd /d "%~dp0"
title pyBikeRouter

where docker >nul 2>nul
if errorlevel 1 (
  echo.
  echo Docker Desktop is not installed yet.
  echo Install it from https://www.docker.com/products/docker-desktop/ and double-click this again.
  start "" https://www.docker.com/products/docker-desktop/
  pause
  exit /b 1
)
docker info >nul 2>nul
if errorlevel 1 (
  echo.
  echo Docker Desktop is installed but not running.
  echo Open Docker Desktop, wait until it says it is running, then double-click this again.
  pause
  exit /b 1
)

if not exist data\segments mkdir data\segments
if not exist data\exports mkdir data\exports
if not exist data\chat mkdir data\chat

set PORT=8000
for /f "tokens=1,* delims==" %%a in ('findstr /b "PORT=" settings.env 2^>nul') do set PORT=%%b

echo.
set IMAGE=%PYBIKEROUTER_IMAGE%
if "%IMAGE%"=="" set IMAGE=ghcr.io/jonasheinickebio/pybikerouter:latest
echo Getting the program (the first time this takes a few minutes) ...
docker compose --env-file settings.env pull
if errorlevel 1 (
  docker image inspect %IMAGE% >nul 2>nul
  if errorlevel 1 (
    echo.
    echo Could not download the program. Is the internet connection working?
    pause
    exit /b 1
  )
  echo Could not check for a newer version; using the one already here.
)

dir /b data\segments\*.rd5 >nul 2>nul
if errorlevel 1 (
  echo.
  echo First start: where do you ride? ^(the map data is downloaded once^)
  docker compose --env-file settings.env --profile setup run --rm setup
  if errorlevel 1 (
    echo.
    echo The map data was not downloaded. Double-click this again to retry.
    pause
    exit /b 1
  )
)

set PROFILES=
findstr /b /c:"LLM_BASE_URL=http://ollama:" settings.env >nul 2>nul
if not errorlevel 1 set PROFILES=--profile ollama

echo.
echo Starting ...
docker compose --env-file settings.env %PROFILES% up -d
if errorlevel 1 (
  echo.
  echo It did not start. If the message above says a port is already allocated,
  echo open settings.env in Notepad, change PORT=%PORT% to another number ^(for example 8001^)
  echo and double-click this again.
  pause
  exit /b 1
)

set /a tries=0
:wait
curl -fsS http://127.0.0.1:%PORT%/healthz >nul 2>nul
if not errorlevel 1 goto ready
set /a tries+=1
if %tries% gtr 90 goto slow
timeout /t 2 /nobreak >nul
goto wait

:slow
echo.
echo It is taking long. Wait a minute and open http://localhost:%PORT%
pause
exit /b 1

:ready
if not "%PROFILES%"=="" (
  set MODEL=
  for /f "tokens=1,* delims==" %%a in ('findstr /b "LLM_MODEL=" settings.env') do set MODEL=%%b
  echo.
  echo Checking the language model %MODEL% ^(a few GB the first time^) ...
  docker compose --env-file settings.env --profile ollama exec -T ollama ollama pull %MODEL%
  if errorlevel 1 echo The model could not be downloaded. Free sentences will not work until it is; start again to retry.
)

echo.
echo Ready:  http://localhost:%PORT%
echo To stop it later double-click Stop-Windows.bat
start "" http://localhost:%PORT%
timeout /t 5 >nul
