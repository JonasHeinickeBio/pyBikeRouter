#!/bin/sh
# Downloads the map data of another area. Start the program again afterwards.
cd "$(dirname "$0")" || exit 1
mkdir -p data/segments data/exports data/chat
PBR_UID=$(id -u); PBR_GID=$(id -g); export PBR_UID PBR_GID
docker compose --env-file settings.env --profile setup run --rm setup \
  || { printf '\nThe map data was not downloaded. Run this again to retry.\n'; exit 1; }
printf '\nDone. Double-click Stop-Mac.command and then Start-Mac.command so the new area is used.\n'

printf "\nYou can close this window.\n"
