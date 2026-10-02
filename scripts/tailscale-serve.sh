#!/usr/bin/env bash
# Publish the local bike-router API to your tailnet over HTTPS (docs/mobile.md).
#
#   scripts/tailscale-serve.sh            # start proxying https://<host>.<tailnet>.ts.net:8443
#   scripts/tailscale-serve.sh status     # show what is served
#   scripts/tailscale-serve.sh off        # stop (only removes the entry this script created)
#
# The app itself stays bound to 127.0.0.1; `tailscale serve` is the only thing
# that exposes it, and only to devices on your tailnet (never the public
# internet -- this script does not use `tailscale funnel`). The app has no
# login of its own, so tailnet membership / ACLs are the access control.
#
# Environment:
#   APP_PORT        local port of the running app          (default 8000)
#   TS_HTTPS_PORT   HTTPS port on the tailnet side; Tailscale allows 443,
#                   8443 and 10000. Defaults to 8443 so an existing
#                   `tailscale serve` on 443 is left alone.
set -euo pipefail

APP_PORT="${APP_PORT:-8000}"
TS_HTTPS_PORT="${TS_HTTPS_PORT:-8443}"
ACTION="${1:-start}"

die() { echo "error: $*" >&2; exit 1; }

command -v tailscale >/dev/null 2>&1 || die "tailscale is not installed (https://tailscale.com/download)"

case "$TS_HTTPS_PORT" in
  443|8443|10000) ;;
  *) die "TS_HTTPS_PORT must be 443, 8443 or 10000 (got $TS_HTTPS_PORT)" ;;
esac

# MagicDNS name of this machine, e.g. mybox.tail1234.ts.net (no trailing dot).
dns_name() {
  tailscale status --json 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'
}

url() {
  local host port_suffix=""
  host="$(dns_name)" || die "could not read this machine's tailnet name; is Tailscale running and logged in?"
  [ "$TS_HTTPS_PORT" = "443" ] || port_suffix=":${TS_HTTPS_PORT}"
  echo "https://${host}${port_suffix}"
}

case "$ACTION" in
  start)
    if ! curl -fsS -o /dev/null "http://127.0.0.1:${APP_PORT}/healthz"; then
      echo "warning: nothing answered on http://127.0.0.1:${APP_PORT}/healthz --" >&2
      echo "         start the app first:  bike-router serve start --port ${APP_PORT}" >&2
    fi
    tailscale serve --bg --https="${TS_HTTPS_PORT}" "http://127.0.0.1:${APP_PORT}"
    echo
    echo "Open on your iPhone (Tailscale connected):  $(url)"
    ;;
  status)
    tailscale serve status
    ;;
  off)
    tailscale serve --https="${TS_HTTPS_PORT}" off
    echo "stopped serving on HTTPS port ${TS_HTTPS_PORT}"
    ;;
  *)
    die "unknown action '$ACTION' (use start, status or off)"
    ;;
esac
