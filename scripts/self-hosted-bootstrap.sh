#!/usr/bin/env bash
# Start the self-hosted routing + geocoding stack (docs/self-hosted.md).
#
#   scripts/self-hosted-bootstrap.sh            # download extract (if missing), start, wait until ready
#   scripts/self-hosted-bootstrap.sh status     # health of the two services
#   scripts/self-hosted-bootstrap.sh --force    # re-download the extract first
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
for arg in "$@"; do
  case "$arg" in
    up|status) ACTION="$arg" ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,22p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "error: unknown argument '$arg' (use: up | status | --force)" >&2; exit 2 ;;
  esac
done

die() { echo "error: $*" >&2; exit 1; }

service_ready() {  # <url> <grep pattern>
  curl -fsS --max-time 5 "$1" 2>/dev/null | grep -q "$2"
}

print_status() {
  local ors="not ready" nominatim="not ready"
  service_ready "$ORS_HEALTH" ready && ors="ready"
  service_ready "$NOMINATIM_STATUS" OK && nominatim="ready"
  echo "openrouteservice  ($ORS_HEALTH): $ors"
  echo "nominatim         ($NOMINATIM_STATUS): $nominatim"
  [ "$ors" = ready ] && [ "$nominatim" = ready ]
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

echo "Starting the self-hosted stack (first start builds graphs and imports the geocoding index)..."
# Only the two stack services: a bare `up` would also start the `api` service.
docker compose -f "$COMPOSE_FILE" --profile self-hosted up -d ors-self-hosted nominatim-self-hosted

echo "Waiting for both services (up to ${WAIT_TIMEOUT_S}s)..."
waited=0
while [ "$waited" -lt "$WAIT_TIMEOUT_S" ]; do
  if print_status >/dev/null; then
    echo "Ready. Point the API at the stack:"
    echo "  ORS_BASE_URL=http://127.0.0.1:${ORS_PORT}/ors"
    echo "  GEOCODER_PROVIDER=nominatim"
    echo "  GEOCODER_BASE_URL=http://127.0.0.1:${NOMINATIM_PORT}"
    echo "  GEOCODER_MIN_CONFIDENCE=0 GEOCODER_AMBIGUITY_MARGIN=0   # see docs/self-hosted.md, 'Geocoder confidence'"
    echo "(OpenStreetMap data (c) OpenStreetMap contributors, ODbL.)"
    exit 0
  fi
  sleep 10
  waited=$((waited + 10))
done

print_status || true
die "timed out; follow progress with: docker compose -f docker/compose.yaml --profile self-hosted logs -f"
