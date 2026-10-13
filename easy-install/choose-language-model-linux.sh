#!/bin/sh
# Connect (or switch off) a language model for free-sentence planning. Start the program again afterwards.
cd "$(dirname "$0")" || exit 1
mkdir -p data/segments data/exports
PBR_UID=$(id -u); PBR_GID=$(id -g); export PBR_UID PBR_GID
docker compose --env-file settings.env --profile setup run --rm setup --skip \
  || { printf '\nThe language model was not set up. Run this again to retry.\n'; exit 1; }
printf '\nDone. Run stop-linux.sh and start-linux.sh so the change is used.\n'
