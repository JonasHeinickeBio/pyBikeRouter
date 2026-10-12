#!/bin/sh
# Gets the newest version of pyBikeRouter and restarts it.
cd "$(dirname "$0")" || exit 1
PBR_UID=$(id -u); PBR_GID=$(id -g); export PBR_UID PBR_GID
docker compose --env-file settings.env pull || exit 1
docker compose --env-file settings.env up -d || exit 1
printf '\nUpdated and running.\n'
