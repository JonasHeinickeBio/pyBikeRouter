#!/bin/sh
# Stops pyBikeRouter (your map data and routes stay in the data folder).
cd "$(dirname "$0")" || exit 1
docker compose --env-file settings.env --profile ollama down
printf '\nStopped. Run start-linux.sh to start it again.\n'
