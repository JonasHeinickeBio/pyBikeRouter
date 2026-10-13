#!/bin/sh
# Gets the newest version of pyBikeRouter and restarts it.
cd "$(dirname "$0")" || exit 1
PBR_UID=$(id -u); PBR_GID=$(id -g); export PBR_UID PBR_GID
PROFILES=""
if grep -Eq '^LLM_BASE_URL=http://ollama:' settings.env 2>/dev/null; then PROFILES="--profile ollama"; fi
docker compose --env-file settings.env $PROFILES pull || exit 1
docker compose --env-file settings.env $PROFILES up -d || exit 1
printf '\nUpdated and running.\n'

printf "\nYou can close this window.\n"
