#!/usr/bin/env bash
# Start the self-hosted routing + geocoding stack (docs/self-hosted.md).
#
#   scripts/self-hosted-bootstrap.sh                    # download extract (if missing), start both, wait until ready
#   scripts/self-hosted-bootstrap.sh --only routing     # openrouteservice only (geocoding stays on public Nominatim)
#   scripts/self-hosted-bootstrap.sh --only geocoding   # Nominatim only (routing stays on the public ORS)
#   scripts/self-hosted-bootstrap.sh status             # health of the selected services
#   scripts/self-hosted-bootstrap.sh --force            # re-download the extract first
#
# Small machine? --only runs half of the stack; docs/self-hosted.md lists the
# memory, disk and time each service needs, and how to size them down.
#
# What it does: places one OSM extract at docker/self-hosted/data/region.osm.pbf
# (verified against the MD5 Geofabrik publishes next to it), then runs
# `docker compose --profile self-hosted up -d` for them -- openrouteservice (bicycle
# profiles) and Nominatim both read that file, and the first start builds the
# routing graphs / imports the geocoding index, which takes minutes for a city
# region and much longer for a federal state. It never deletes volumes: use
# `docker compose -f docker/compose.yaml --profile self-hosted down -v` for that.
#
# Environment:
#   EXTRACT_URL        extract to download (default: Bremen, ~20 MB -- a small
#                      region that makes the first run quick; see the docs for
#                      choosing your own)
#   EXTRACT_MD5        expected MD5; by default fetched from "$EXTRACT_URL.md5"
#   WAIT_TIMEOUT_S     how long to wait for both services (default 3600)
#   ORS_LOCAL_PORT / NOMINATIM_LOCAL_PORT   host ports (defaults 8080 / 8081)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT/docker/compose.yaml"
DATA_DIR="${DATA_DIR:-$ROOT/docker/self-hosted/data}"
EXTRACT_FILE="$DATA_DIR/region.osm.pbf"
EXTRACT_URL="${EXTRACT_URL:-https://download.geofabrik.de/europe/germany/bremen-latest.osm.pbf}"
WAIT_TIMEOUT_S="${WAIT_TIMEOUT_S:-3600}"
ORS_PORT="${ORS_LOCAL_PORT:-8080}"
NOMINATIM_PORT="${NOMINATIM_LOCAL_PORT:-8081}"
ORS_HEALTH="http://127.0.0.1:${ORS_PORT}/ors/v2/health"
NOMINATIM_STATUS="http://127.0.0.1:${NOMINATIM_PORT}/status"

ACTION="up"
FORCE=0
ONLY=""
USAGE="use: up | status | --force | --only routing|geocoding"
while [ $# -gt 0 ]; do
  case "$1" in
    up|status) ACTION="$1" ;;
    --force) FORCE=1 ;;
    --only)
      [ $# -ge 2 ] || { echo "error: --only needs a value ($USAGE)" >&2; exit 2; }
      ONLY="$2"; shift ;;
    --only=*) ONLY="${1#--only=}" ;;
    -h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "error: unknown argument '$1' ($USAGE)" >&2; exit 2 ;;
  esac
  shift
done

# Which services this run manages: both by default, one with --only.
WANT_ORS=1
WANT_NOMINATIM=1
case "$ONLY" in
  "") ;;
  routing) WANT_NOMINATIM=0 ;;
  geocoding) WANT_ORS=0 ;;
  *) echo "error: --only must be 'routing' or 'geocoding' (got '$ONLY')" >&2; exit 2 ;;
esac

die() { echo "error: $*" >&2; exit 1; }

service_ready() {  # <url> <grep pattern>
  curl -fsS --max-time 5 "$1" 2>/dev/null | grep -q "$2"
}

# Prints one line per selected service; succeeds only when all of them answer.
print_status() {
  local not_ready=0 state
  if [ "$WANT_ORS" = 1 ]; then
    state="not ready"
    if service_ready "$ORS_HEALTH" ready; then state="ready"; else not_ready=1; fi
    echo "openrouteservice  ($ORS_HEALTH): $state"
  fi
  if [ "$WANT_NOMINATIM" = 1 ]; then
    state="not ready"
    if service_ready "$NOMINATIM_STATUS" OK; then state="ready"; else not_ready=1; fi
    echo "nominatim         ($NOMINATIM_STATUS): $state"
  fi
  return "$not_ready"
}

# The services handed to `docker compose up`: never a bare `up`, which would
# also start the `api` service.
selected_services() {
  [ "$WANT_ORS" = 1 ] && echo ors-self-hosted
  [ "$WANT_NOMINATIM" = 1 ] && echo nominatim-self-hosted
  return 0
}

if [ "$ACTION" = status ]; then
  print_status
  exit $?
fi

command -v docker >/dev/null 2>&1 || die "docker is not installed"
command -v curl >/dev/null 2>&1 || die "curl is not installed"

md5_of() {
  if command -v md5sum >/dev/null 2>&1; then md5sum "$1" | cut -d' ' -f1; else md5 -q "$1"; fi
}

expected_md5() {
  if [ -n "${EXTRACT_MD5:-}" ]; then echo "$EXTRACT_MD5"; return; fi
  # Geofabrik publishes "<md5>  <file>" next to every extract.
  curl -fsSL --max-time 30 "$EXTRACT_URL.md5" 2>/dev/null | head -n1 | cut -d' ' -f1 || true
}

mkdir -p "$DATA_DIR"
if [ "$FORCE" = 1 ] || [ ! -s "$EXTRACT_FILE" ]; then
  echo "Downloading $EXTRACT_URL"
  partial="$EXTRACT_FILE.part"
  curl -fSL --retry 3 -o "$partial" "$EXTRACT_URL" || { rm -f "$partial"; die "download failed"; }
  want="$(expected_md5)"
  if [ -n "$want" ]; then
    got="$(md5_of "$partial")"
    if [ "$got" != "$want" ]; then
      rm -f "$partial"
      die "checksum mismatch for the downloaded extract (expected $want, got $got)"
    fi
    echo "Checksum OK ($got)"
  else
    echo "warning: no MD5 available for $EXTRACT_URL, extract not verified" >&2
  fi
  mv "$partial" "$EXTRACT_FILE"
else
  echo "Using existing extract $EXTRACT_FILE (pass --force to re-download)"
fi

echo "Starting: $(selected_services | tr '\n' ' ')(first start builds graphs / imports the geocoding index)..."
# Only the selected stack services: a bare `up` would also start the `api` service.
# shellcheck disable=SC2046  # the service names are plain words, split on purpose
docker compose -f "$COMPOSE_FILE" --profile self-hosted up -d $(selected_services)

echo "Waiting for the service(s) (up to ${WAIT_TIMEOUT_S}s)..."
waited=0
while [ "$waited" -lt "$WAIT_TIMEOUT_S" ]; do
  if print_status >/dev/null; then
    echo "Ready. Point the API at it (settings that are not listed stay as they are):"
    if [ "$WANT_ORS" = 1 ]; then
      echo "  ORS_BASE_URL=http://127.0.0.1:${ORS_PORT}/ors"
    else
      echo "  (routing: unchanged -- still the public ORS / BRouter / Valhalla you configured)"
    fi
    if [ "$WANT_NOMINATIM" = 1 ]; then
      echo "  GEOCODER_PROVIDER=nominatim"
      echo "  GEOCODER_BASE_URL=http://127.0.0.1:${NOMINATIM_PORT}"
      echo "  GEOCODER_MIN_CONFIDENCE=0 GEOCODER_AMBIGUITY_MARGIN=0   # see docs/self-hosted.md, 'Geocoder confidence'"
    else
      echo "  (geocoding: unchanged -- still the public Nominatim, keep its usage policy in mind)"
    fi
    echo "(OpenStreetMap data (c) OpenStreetMap contributors, ODbL.)"
    exit 0
  fi
  sleep 10
  waited=$((waited + 10))
done

print_status || true
die "timed out; follow progress with: docker compose -f docker/compose.yaml --profile self-hosted logs -f"
