#!/bin/sh
# Double-click this file to stop pyBikeRouter (your map data and routes stay in the data folder).
cd "$(dirname "$0")" || exit 1
docker compose --env-file settings.env down
printf '\nStopped. Double-click Start-Mac.command to start it again.\n'

printf "\nYou can close this window.\n"
