#!/bin/sh
# Starts pyBikeRouter. Run:  sh start-linux.sh   (or double-click it in your file manager)
cd "$(dirname "$0")" || exit 1

say() { printf '\n%s\n' "$*"; }

if ! command -v docker >/dev/null 2>&1; then
  say "Docker is not installed yet."
  say "Install it from https://docs.docker.com/engine/install/ and run this again."
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  say "Docker is installed but not running (or you may not be allowed to use it)."
  say "Start Docker, or add yourself to the 'docker' group, then run this again."
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

# A language model on this computer (chosen in the setup question): start it with the program.
PROFILES=""
if grep -Eq '^LLM_BASE_URL=http://ollama:' settings.env 2>/dev/null; then
  PROFILES="--profile ollama"
fi

say "Starting ..."
if ! $COMPOSE $PROFILES up -d; then
  say "It did not start. If the message above says a port is already allocated,"
  say "open settings.env, change PORT=$PORT to another number (for example 8001) and run this again."
  exit 1
fi

i=0
until curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -gt 90 ] && { say "It is taking long. Wait a minute and open http://localhost:$PORT"; exit 1; }
  sleep 2
done

if [ -n "$PROFILES" ]; then
  MODEL=$(grep -E '^LLM_MODEL=' settings.env | tail -n1 | cut -d= -f2 | tr -d '\r ')
  j=0
  until $COMPOSE $PROFILES exec -T ollama ollama list >/dev/null 2>&1; do
    j=$((j + 1)); [ "$j" -gt 30 ] && break; sleep 2
  done
  if ! $COMPOSE $PROFILES exec -T ollama ollama list 2>/dev/null | grep -q "^$MODEL"; then
    say "Downloading the language model $MODEL (a few GB, only the first time) ..."
    $COMPOSE $PROFILES exec -T ollama ollama pull "$MODEL" \
      || say "The model could not be downloaded. Free sentences will not work until it is; start again to retry."
  fi
fi

say "Ready:  http://localhost:$PORT"
say "To stop it later run stop-linux.sh"
( xdg-open "http://localhost:$PORT" >/dev/null 2>&1 || true )
