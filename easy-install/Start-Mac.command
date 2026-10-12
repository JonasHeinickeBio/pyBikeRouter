#!/bin/sh
# Double-click this file to start pyBikeRouter.
cd "$(dirname "$0")" || exit 1

say() { printf '\n%s\n' "$*"; }

if ! command -v docker >/dev/null 2>&1; then
  say "Docker is not installed yet."
  say "Install it from https://www.docker.com/products/docker-desktop/ and double-click this again."
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  say "Docker Desktop is installed but not running."
  say "Open Docker Desktop, wait until it says it is running, then double-click this again."
  exit 1
fi

# The files in data/ should belong to you, not to root.
mkdir -p data/segments data/exports
PBR_UID=$(id -u)
PBR_GID=$(id -g)
export PBR_UID PBR_GID
COMPOSE="docker compose --env-file settings.env"
PORT=$(grep -E '^PORT=' settings.env 2>/dev/null | tail -n1 | cut -d= -f2)
PORT=${PORT:-8000}

IMAGE=${PYBIKEROUTER_IMAGE:-ghcr.io/jonasheinickebio/pybikerouter:latest}
say "Getting the program (the first time this takes a few minutes) ..."
if ! $COMPOSE pull; then
  if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    say "Could not check for a newer version; using the one already here."
  else
    say "Could not download the program. Is the internet connection working?"
    exit 1
  fi
fi

if ! ls data/segments/*.rd5 >/dev/null 2>&1; then
  say "First start: where do you ride? (the map data is downloaded once)"
  $COMPOSE --profile setup run --rm setup || { say "The map data was not downloaded. Run this again."; exit 1; }
fi

say "Starting ..."
if ! $COMPOSE up -d; then
  say "It did not start. If the message above says a port is already allocated,"
  say "open settings.env, change PORT=$PORT to another number (for example 8001) and double-click this again."
  exit 1
fi

i=0
until curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -gt 90 ] && { say "It is taking long. Wait a minute and open http://localhost:$PORT"; exit 1; }
  sleep 2
done

say "Ready:  http://localhost:$PORT"
say "To stop it later double-click Stop-Mac.command"
( open "http://localhost:$PORT" >/dev/null 2>&1 || true )

printf "\nYou can close this window.\n"
